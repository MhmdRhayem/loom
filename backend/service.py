from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain.chat_models import init_chat_model

from backend.core import usage
from backend.core.graph import build_initial_state
from backend.core.prompt_builder import message_text
from backend.learning.scoring import record_score
from backend.learning.signals import (
    reward_from_agent_eval,
    reward_from_eval,
    reward_from_feedback,
)
from backend.memory.consolidation import consolidate, should_dream
from backend.storage import TurnAgentRecord

if TYPE_CHECKING:
    from backend.agents.registry import AgentRegistry
    from backend.core.config import Settings
    from backend.storage.base import Database

logger = logging.getLogger(__name__)

# How many stored turns get replayed into the model's context when a conversation
# continues (each turn is a user + assistant message pair).
_MAX_HISTORY_TURNS = 10

# Graph nodes whose model calls are plumbing, not the answer — their tokens
# (router, critic, memory extractor) must never stream to the client.
_NON_ANSWER_NODES = {"load_memory", "route", "evaluate", "revise", "save_memory"}


@dataclass
class TurnOutcome:
    """Everything the /chat route needs to build its response."""

    conversation_id: str
    agents: list[str]
    response: str | None
    eval: dict | None
    # Orchestration trace (already computed on the graph state; surfaced for the client).
    routing_confidence: float | None = None
    routing_reason: str | None = None
    query_category: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    agent_runs: list[dict] = field(default_factory=list)
    retry_count: int = 0
    # Every model call the turn made, split for pricing (see core/usage.py).
    usage: dict | None = None


@dataclass
class FeedbackOutcome:
    recorded: bool
    agents: list[str] = field(default_factory=list)


@dataclass
class DreamOutcome:
    ran: bool
    merged: int = 0
    pruned: int = 0


async def run_turn(
    *,
    graph: Any,
    db: Database | None,
    settings: Settings | None,
    registry: AgentRegistry | None,
    message: str,
    conversation_id: str | None,
    owner_id: str | None,
    allowed_agents: list[str] | None = None,
) -> TurnOutcome:
    """Run one message through the graph, then record telemetry and learning.

    An existing conversation_id pulls its stored turns back into the model's context,
    so follow-ups (and conversations resumed in a later session) keep their history.
    If db is None (Postgres was down at boot) the turn still runs and only
    persistence is skipped.
    """
    # Every model call this turn makes, wherever it is made, folds into here.
    turn_usage = usage.start_turn()
    conversation_id = await _owned_conversation_id(db, conversation_id, owner_id)
    history = await _load_history(db, settings, conversation_id)
    state = build_initial_state(
        message, conversation_id, owner_id, history=history, allowed_agents=allowed_agents
    )

    started = time.perf_counter()
    result = await graph.ainvoke(state)
    latency_ms = int((time.perf_counter() - started) * 1000)

    await _finish_turn(
        db=db,
        settings=settings,
        registry=registry,
        message=message,
        owner_id=owner_id,
        result=result,
        latency_ms=latency_ms,
        first_turn=not history,
        turn_usage=turn_usage,
    )
    return _outcome_from(result, turn_usage)


async def stream_turn(
    *,
    graph: Any,
    db: Database | None,
    settings: Settings | None,
    registry: AgentRegistry | None,
    message: str,
    conversation_id: str | None,
    owner_id: str | None,
    allowed_agents: list[str] | None = None,
):
    """Streaming twin of run_turn: yields ("stage", info) as each pipeline node
    finishes, ("token", {text}) for each model token of the answer as it is
    generated, then ("done", TurnOutcome) after persistence.

    Token filtering: model calls in the plumbing nodes (router, critic, memory)
    never stream. On a single-agent turn the agent's own tokens stream live; on a
    multi-agent turn only the synthesizer's tokens do (the parallel specialists
    would interleave). The final response in "done" is authoritative — a client
    should replace the streamed draft with it (they differ e.g. after a critic
    retry, which is signalled by a "revise" stage event).
    """
    # Every model call this turn makes, wherever it is made, folds into here.
    turn_usage = usage.start_turn()
    conversation_id = await _owned_conversation_id(db, conversation_id, owner_id)
    history = await _load_history(db, settings, conversation_id)
    state = build_initial_state(
        message, conversation_id, owner_id, history=history, allowed_agents=allowed_agents
    )

    result: dict = dict(state)
    started = time.perf_counter()
    async for mode, update in graph.astream(state, stream_mode=["updates", "messages"]):
        if mode == "messages":
            chunk, metadata = update
            node = metadata.get("langgraph_node")
            if node in _NON_ANSWER_NODES:
                continue
            # Inner-agent tokens carry the inner graph's node name; only pass them
            # through when exactly one agent is answering. The synthesizer runs
            # directly in execute_agents, so multi-agent turns stream that instead.
            single = len(result.get("current_agents") or []) == 1
            if node != "execute_agents" and not single:
                continue
            text = message_text(chunk)
            if text:
                yield "token", {"text": text}
            continue
        for node, out in update.items():
            if isinstance(out, dict):
                result.update(out)
            info: dict = {"node": node}
            if node == "route":
                info["agents"] = result.get("current_agents") or []
            elif node == "evaluate":
                info["eval_pass"] = (result.get("eval_result") or {}).get("pass")
            yield "stage", info
    latency_ms = int((time.perf_counter() - started) * 1000)

    await _finish_turn(
        db=db,
        settings=settings,
        registry=registry,
        message=message,
        owner_id=owner_id,
        result=result,
        latency_ms=latency_ms,
        first_turn=not history,
        turn_usage=turn_usage,
    )
    yield "done", _outcome_from(result, turn_usage)


async def _finish_turn(
    *,
    db: Database | None,
    settings: Settings | None,
    registry: AgentRegistry | None,
    message: str,
    owner_id: str | None,
    result: dict,
    latency_ms: int,
    first_turn: bool,
    turn_usage: usage.TokenUsage,
) -> None:
    """Post-turn bookkeeping shared by both chat paths: telemetry, learning, titling.

    Titling runs before persistence, not after: it is a model call like any other, and
    the turn row is where its tokens get billed. Writing the title through record_turn
    also saves the extra UPDATE the old order needed.
    """
    # A first turn (no stored history) names the conversation, like ChatGPT/Claude do.
    title = (
        await _title_conversation(
            settings=settings,
            user_message=message,
            agent_response=result.get("agent_response"),
        )
        if first_turn
        else None
    )
    await _persist_turn(
        db=db,
        settings=settings,
        registry=registry,
        message=message,
        owner_id=owner_id,
        result=result,
        latency_ms=latency_ms,
        title=title,
        turn_usage=turn_usage,
    )


def _outcome_from(result: dict, turn_usage: usage.TokenUsage | None = None) -> TurnOutcome:
    """Map the graph's final state onto the outcome the HTTP layer returns."""
    return TurnOutcome(
        conversation_id=result["conversation_id"],
        agents=result.get("current_agents") or [],
        response=result.get("agent_response"),
        eval=result.get("eval_result"),
        routing_confidence=result.get("routing_confidence"),
        routing_reason=result.get("routing_reason"),
        query_category=result.get("query_category"),
        tool_calls=result.get("tool_calls") or [],
        agent_runs=result.get("agent_runs") or [],
        retry_count=result.get("retry_count", 0) or 0,
        usage=turn_usage.as_dict() if turn_usage else None,
    )


async def _title_conversation(
    *,
    settings: Settings | None,
    user_message: str,
    agent_response: str | None,
) -> str | None:
    """A short fast-tier title for a new conversation, or None. Never raises."""
    if settings is None:
        return None
    try:
        model = init_chat_model(
            settings.model_id_for_tier("fast"), model_provider=settings.default_provider
        )
        out = await model.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Write a title for this conversation: 3-6 words, plain text, no quotes "
                        "or trailing punctuation, same language as the user. Reply with the "
                        "title only."
                    ),
                },
                {
                    "role": "user",
                    "content": f"User: {user_message}\n\nAssistant: {agent_response or ''}",
                },
            ]
        )
        usage.record(out)
        return " ".join(message_text(out).split()).strip("\"' ")[:80] or None
    except Exception:  # noqa: BLE001 - a missing title must never break the turn
        logger.warning("could not title a conversation", exc_info=True)
        return None


async def _owned_conversation_id(
    db: Database | None, conversation_id: str | None, owner_id: str | None
) -> str | None:
    """The conversation id this turn may use: the given one when it's absent, brand new,
    or owned by the caller — None (start fresh) when it belongs to someone else.

    This is the /chat counterpart of the ownership checks on the /conversations and
    /feedback routes: without it, any authenticated caller could resume — and read,
    through the model's replayed context — another owner's conversation just by
    posting its UUID, and write their turns into it. Never raises.
    """
    if db is None or not conversation_id or owner_id is None:
        return conversation_id
    try:
        conv = await db.conversations.get_conversation(uuid.UUID(conversation_id))
    except Exception:  # noqa: BLE001 - malformed id / storage error -> let the turn proceed
        return conversation_id
    if conv is not None and conv.owner_id != owner_id:
        logger.warning(
            "conversation %s belongs to another owner; starting a fresh one", conversation_id
        )
        return None
    return conversation_id


async def _load_history(
    db: Database | None, settings: Settings | None, conversation_id: str | None
) -> list[dict]:
    """The conversation so far as user/assistant messages. Never raises — [] on any miss.

    Layer 3: only the last _MAX_HISTORY_TURNS are replayed verbatim; anything older is
    folded into a rolling summary (stored on the conversation, regenerated only when
    more turns age out) and prepended as a context block instead of being dropped.
    """
    if db is None or not conversation_id:
        return []
    try:
        cid = uuid.UUID(conversation_id)
        turns = await db.conversations.get_turns(cid)
    except Exception:  # noqa: BLE001 - malformed id / storage error -> just start fresh
        logger.warning("could not load history for %s", conversation_id, exc_info=True)
        return []

    history: list[dict] = []
    aged_out = turns[:-_MAX_HISTORY_TURNS] if len(turns) > _MAX_HISTORY_TURNS else []
    if aged_out:
        summary = await _rolling_summary(db, settings, cid, aged_out)
        if summary:
            history.append(
                {
                    "role": "user",
                    "content": (
                        f"<system-reminder>Summary of the earlier conversation "
                        f"(turns 1-{len(aged_out)}): {summary}</system-reminder>"
                    ),
                }
            )
    for turn in turns[-_MAX_HISTORY_TURNS:]:
        history.append({"role": "user", "content": turn.user_message})
        if turn.agent_response:
            history.append({"role": "assistant", "content": turn.agent_response})
    return history


async def _rolling_summary(
    db: Database, settings: Settings | None, conversation_id: uuid.UUID, aged_out: list
) -> str | None:
    """The stored summary covering the aged-out turns, regenerating it when stale."""
    try:
        conv = await db.conversations.get_conversation(conversation_id)
        if conv is None:
            return None
        if conv.summary and conv.summarized_turns >= len(aged_out):
            return conv.summary
        if settings is None:
            return conv.summary
        # New turns have aged out since the last compaction — fold them in.
        fresh = aged_out[conv.summarized_turns or 0 :]
        transcript = "\n".join(
            f"User: {t.user_message}\nAssistant: {t.agent_response or ''}" for t in fresh
        )
        model = init_chat_model(
            settings.model_id_for_tier("fast"), model_provider=settings.default_provider
        )
        base = f"Existing summary: {conv.summary}\n\n" if conv.summary else ""
        out = await model.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Summarize this conversation in at most 150 words of plain prose. "
                        "Keep concrete facts: identifiers, names, preferences, "
                        "decisions, and anything promised to the user."
                    ),
                },
                {"role": "user", "content": f"{base}New turns:\n{transcript}"},
            ]
        )
        usage.record(out)
        summary = message_text(out).strip()
        if summary:
            await db.conversations.set_summary(conversation_id, summary, len(aged_out))
            return summary
        return conv.summary
    except Exception:  # noqa: BLE001 - summarization must never break the turn
        logger.warning("rolling summary failed for %s", conversation_id, exc_info=True)
        return None


async def _persist_turn(
    *,
    db: Database | None,
    settings: Settings | None,
    registry: AgentRegistry | None,
    message: str,
    owner_id: str | None,
    result: dict,
    latency_ms: int,
    title: str | None = None,
    turn_usage: usage.TokenUsage | None = None,
) -> None:
    """Persist this turn and fold in its learning signal. Never raises, so telemetry
    can't break the response."""
    if db is None:
        return
    try:
        conversation_id = uuid.UUID(str(result["conversation_id"]))
    except (ValueError, TypeError):
        return  # a non-UUID (custom client) conversation id -> skip persistence

    agents = result.get("current_agents") or []
    eval_result = result.get("eval_result") or {}
    # Per-agent verdicts the graph already computed (one per agent that ran); empty on a
    # turn-level outcome (structural fail / evaluation disabled / nothing judged).
    agent_evals = {e.get("agent"): e for e in (eval_result.get("agent_evals") or [])}

    def model_tier(name: str | None) -> str | None:
        return registry.get(name).model if registry and name and name in registry else None

    # One turn_agents row per agent that ran, each carrying its own verdict.
    turn_agents = [
        TurnAgentRecord(
            agent_name=name,
            model_tier=model_tier(name),
            eval_score=(agent_evals.get(name) or {}).get("score"),
            eval_pass=(agent_evals.get(name) or {}).get("pass"),
        )
        for name in agents
    ]
    # The parent turn row represents the user-facing reply: for a single agent it carries that
    # agent's name/tier; for a synthesized multi-agent turn those are left null (see turn_agents).
    primary = agents[0] if len(agents) == 1 else None

    try:
        await db.conversations.record_turn(
            conversation_id=conversation_id,
            owner_id=owner_id or "anonymous",
            user_message=message,
            agent_name=primary,
            routing_confidence=result.get("routing_confidence"),
            query_category=result.get("query_category"),
            agent_response=result.get("agent_response"),
            eval_score=eval_result.get("score"),
            retry_count=result.get("retry_count", 0) or 0,
            model_tier=model_tier(primary),
            latency_ms=latency_ms,
            # The turn total, not the agents' subtotal: routing, critique, synthesis,
            # memory extraction and titling are real spend and were previously invisible.
            tokens_used=(turn_usage.total if turn_usage else None) or None,
            input_tokens=turn_usage.input if turn_usage else None,
            output_tokens=turn_usage.output if turn_usage else None,
            cached_input_tokens=turn_usage.cached_input if turn_usage else None,
            model_calls=turn_usage.calls if turn_usage else None,
            title=title,
            agents=turn_agents,
        )
    except Exception:  # noqa: BLE001 - persistence is best-effort
        logger.warning("turn persistence failed", exc_info=True)

    # Learning signal: fold each agent's verdict into its own per-category EMA score.
    # With per-agent verdicts, score each from its own verdict; otherwise (a turn-level outcome
    # such as a structural failure) attribute that single signal to every agent that ran.
    if agents and getattr(settings, "enable_learning", True):
        category = result.get("query_category") or "general"
        turn_reward = None if agent_evals else reward_from_eval(eval_result)
        for name in agents:
            reward = (
                reward_from_agent_eval(agent_evals[name]) if name in agent_evals else turn_reward
            )
            await record_score(db.performance, name, category, reward)


async def record_feedback(
    *, db: Database | None, conversation_id: str, rating: str
) -> FeedbackOutcome:
    """Record a thumbs rating against the conversation's last turn. Never raises."""
    reward = reward_from_feedback(rating)
    if db is None or reward is None:
        return FeedbackOutcome(recorded=False)
    try:
        turns = await db.conversations.get_turns(uuid.UUID(str(conversation_id)))
        if not turns:
            return FeedbackOutcome(recorded=False)
        # Credit every agent that took part in the last turn; fall back to the parent row's agent.
        last = turns[-1]
        participants = [ta.agent_name for ta in await db.conversations.get_turn_agents(last.id)]
    except Exception:  # noqa: BLE001 - malformed id / storage error -> just not recorded
        logger.warning("feedback lookup failed for %s", conversation_id, exc_info=True)
        return FeedbackOutcome(recorded=False)
    if not participants and last.agent_name:
        participants = [last.agent_name]
    if not participants:
        return FeedbackOutcome(recorded=False)
    for agent in participants:
        await record_score(db.performance, agent, "overall", reward)
    return FeedbackOutcome(recorded=True, agents=participants)


async def run_dream(
    *, db: Database | None, settings: Settings | None, owner_id: str | None, force: bool = False
) -> DreamOutcome:
    """Run memory consolidation for an owner, when it's due or force is set.

    owner_id is optional because memory is owner-scoped: with no identity resolver and
    no explicit owner there is nothing to consolidate, so the run is a no-op.
    """
    if db is None or settings is None or not owner_id or not settings.enable_dreaming:
        return DreamOutcome(ran=False)
    if not force and not await should_dream(db.memory, db.dreams, owner_id, settings):
        return DreamOutcome(ran=False)
    result = await consolidate(db.memory, db.dreams, owner_id, settings)
    return DreamOutcome(ran=True, merged=result["merged"], pruned=result["pruned"])
