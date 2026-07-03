from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain.chat_models import init_chat_model

from multi_agent_framework.core.graph import build_initial_state
from multi_agent_framework.core.prompt_builder import message_text
from multi_agent_framework.learning.scoring import record_score
from multi_agent_framework.learning.signals import (
    reward_from_agent_eval,
    reward_from_eval,
    reward_from_feedback,
)
from multi_agent_framework.memory.consolidation import consolidate, should_dream
from multi_agent_framework.storage.repositories.conversations import TurnAgentRecord

if TYPE_CHECKING:
    from multi_agent_framework.agents.registry import AgentRegistry
    from multi_agent_framework.core.config import Settings
    from multi_agent_framework.storage.base import Database

logger = logging.getLogger(__name__)

# How many stored turns get replayed into the model's context when a conversation
# continues (each turn is a user + assistant message pair).
_MAX_HISTORY_TURNS = 10


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
    db: "Database | None",
    settings: "Settings | None",
    registry: "AgentRegistry | None",
    message: str,
    conversation_id: str | None,
    owner_id: str | None,
) -> TurnOutcome:
    """Run one message through the graph, then record telemetry and learning.

    An existing conversation_id pulls its stored turns back into the model's context,
    so follow-ups (and conversations resumed in a later session) keep their history.
    If db is None (Postgres was down at boot) the turn still runs and only
    persistence is skipped.
    """
    history = await _load_history(db, conversation_id)
    state = build_initial_state(message, conversation_id, owner_id, history=history)

    started = time.perf_counter()
    result = await graph.ainvoke(state)
    latency_ms = int((time.perf_counter() - started) * 1000)

    await _persist_turn(
        db=db,
        settings=settings,
        registry=registry,
        message=message,
        owner_id=owner_id,
        result=result,
        latency_ms=latency_ms,
    )

    # A first turn (no stored history) names the conversation, like ChatGPT/Claude do.
    if not history:
        await _title_conversation(
            db=db,
            settings=settings,
            conversation_id=result["conversation_id"],
            user_message=message,
            agent_response=result.get("agent_response"),
        )

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
    )


async def _title_conversation(
    *,
    db: "Database | None",
    settings: "Settings | None",
    conversation_id: str,
    user_message: str,
    agent_response: str | None,
) -> None:
    """Name a new conversation with a short fast-tier-generated title. Never raises."""
    if db is None or settings is None:
        return
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
        title = " ".join(message_text(out).split()).strip("\"' ")[:80]
        if title:
            await db.conversations.set_title(uuid.UUID(conversation_id), title)
    except Exception:  # noqa: BLE001 - a missing title must never break the turn
        logger.warning("could not title conversation %s", conversation_id, exc_info=True)


async def _load_history(db: "Database | None", conversation_id: str | None) -> list[dict]:
    """The conversation so far as user/assistant messages. Never raises — [] on any miss."""
    if db is None or not conversation_id:
        return []
    try:
        turns = await db.conversations.get_turns(uuid.UUID(conversation_id))
    except Exception:  # noqa: BLE001 - malformed id / storage error -> just start fresh
        logger.warning("could not load history for %s", conversation_id, exc_info=True)
        return []
    history: list[dict] = []
    for turn in turns[-_MAX_HISTORY_TURNS:]:
        history.append({"role": "user", "content": turn.user_message})
        if turn.agent_response:
            history.append({"role": "assistant", "content": turn.agent_response})
    return history


async def _persist_turn(
    *,
    db: "Database | None",
    settings: "Settings | None",
    registry: "AgentRegistry | None",
    message: str,
    owner_id: str | None,
    result: dict,
    latency_ms: int,
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
            agents=turn_agents,
        )
    except Exception:  # noqa: BLE001 - persistence is best-effort
        logger.warning("turn persistence failed", exc_info=True)

    # Phase 6 learning signal: fold each agent's verdict into its own per-category EMA score.
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
    *, db: "Database | None", conversation_id: str, rating: str
) -> FeedbackOutcome:
    """Record a thumbs rating against the conversation's last turn. Never raises."""
    reward = reward_from_feedback(rating)
    if db is None or reward is None:
        return FeedbackOutcome(recorded=False)
    try:
        turns = await db.conversations.get_turns(uuid.UUID(str(conversation_id)))
    except (ValueError, TypeError):
        return FeedbackOutcome(recorded=False)
    if not turns:
        return FeedbackOutcome(recorded=False)
    # Credit every agent that took part in the last turn; fall back to the parent row's agent.
    last = turns[-1]
    participants = [ta.agent_name for ta in await db.conversations.get_turn_agents(last.id)]
    if not participants and last.agent_name:
        participants = [last.agent_name]
    if not participants:
        return FeedbackOutcome(recorded=False)
    for agent in participants:
        await record_score(db.performance, agent, "overall", reward)
    return FeedbackOutcome(recorded=True, agents=participants)


async def run_dream(
    *, db: "Database | None", settings: "Settings | None", owner_id: str, force: bool = False
) -> DreamOutcome:
    """Run memory consolidation for an owner, when it's due or force is set."""
    if db is None or settings is None or not settings.enable_dreaming:
        return DreamOutcome(ran=False)
    if not force and not await should_dream(db.memory, db.dreams, owner_id, settings):
        return DreamOutcome(ran=False)
    result = await consolidate(db.memory, db.dreams, owner_id, settings)
    return DreamOutcome(ran=True, merged=result["merged"], pruned=result["pruned"])
