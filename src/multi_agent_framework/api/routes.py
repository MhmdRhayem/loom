"""HTTP routes: health check and the main chat endpoint."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Request

from multi_agent_framework.api.models import (
    ChatRequest,
    ChatResponse,
    DreamResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
)
from multi_agent_framework.core.graph import build_initial_state
from multi_agent_framework.learning.scoring import record_score
from multi_agent_framework.learning.signals import reward_from_agent_eval, reward_from_eval, reward_from_feedback
from multi_agent_framework.memory.consolidation import consolidate, should_dream
from multi_agent_framework.storage.repositories.conversations import TurnAgentRecord

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Liveness + dependency check. Fail-soft: reports component status, never raises."""
    components: dict[str, str] = {}

    redis_store = getattr(request.app.state, "redis", None)
    if redis_store is None:
        components["redis"] = "disabled"
    else:
        try:
            await redis_store.client.ping()
            components["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001 - health must never throw
            components["redis"] = f"error: {exc}"

    db = getattr(request.app.state, "db", None)
    if db is None:
        components["postgres"] = "disabled"
    else:
        try:
            await db.ping()
            components["postgres"] = "ok"
        except Exception as exc:  # noqa: BLE001 - health must never throw
            components["postgres"] = f"error: {exc}"

    status = "ok" if all(v in ("ok", "disabled") for v in components.values()) else "degraded"
    return HealthResponse(status=status, components=components)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    """Send one user message through the full graph and return the result."""
    graph = request.app.state.graph
    state = build_initial_state(payload.message, payload.conversation_id, payload.owner_id)

    started = time.perf_counter()
    result = await graph.ainvoke(state)
    latency_ms = int((time.perf_counter() - started) * 1000)

    await _record_turn(request, payload, result, latency_ms)

    return ChatResponse(
        conversation_id=result["conversation_id"],
        agent=", ".join(result.get("current_agents") or []),
        response=result.get("agent_response"),
        eval=result.get("eval_result"),
    )


async def _record_turn(request: Request, payload: ChatRequest, result: dict, latency_ms: int) -> None:
    """Persist this turn to Postgres. Fail-soft: telemetry must never break the response."""
    db = getattr(request.app.state, "db", None)
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

    registry = getattr(request.app.state, "registry", None)

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
            owner_id=payload.owner_id or "anonymous",
            user_message=payload.message,
            agent_name=primary,
            routing_confidence=result.get("routing_confidence"),
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
    settings = getattr(request.app.state, "settings", None)
    if agents and getattr(settings, "enable_learning", True):
        category = result.get("query_category") or "general"
        turn_reward = None if agent_evals else reward_from_eval(eval_result)
        for name in agents:
            reward = reward_from_agent_eval(agent_evals[name]) if name in agent_evals else turn_reward
            await record_score(db.performance, name, category, reward)


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(request: Request, payload: FeedbackRequest) -> FeedbackResponse:
    """Record explicit thumbs feedback for a conversation's last turn (Phase 6 signal). Fail-soft."""
    db = getattr(request.app.state, "db", None)
    reward = reward_from_feedback(payload.rating)
    if db is None or reward is None:
        return FeedbackResponse(recorded=False, agent=None)
    try:
        turns = await db.conversations.get_turns(uuid.UUID(str(payload.conversation_id)))
    except (ValueError, TypeError):
        return FeedbackResponse(recorded=False, agent=None)
    if not turns:
        return FeedbackResponse(recorded=False, agent=None)
    # Credit every agent that took part in the last turn; fall back to the parent row's agent.
    last = turns[-1]
    participants = [ta.agent_name for ta in await db.conversations.get_turn_agents(last.id)]
    if not participants and last.agent_name:
        participants = [last.agent_name]
    if not participants:
        return FeedbackResponse(recorded=False, agent=None)
    for agent in participants:
        await record_score(db.performance, agent, "overall", reward)
    return FeedbackResponse(recorded=True, agent=", ".join(participants))


@router.post("/dream", response_model=DreamResponse)
async def dream(request: Request, owner_id: str, force: bool = False) -> DreamResponse:
    """Run memory consolidation ("dreaming") for an owner — when due, or force=true (Layer 4)."""
    db = getattr(request.app.state, "db", None)
    settings = getattr(request.app.state, "settings", None)
    if db is None or settings is None or not settings.enable_dreaming:
        return DreamResponse(ran=False, merged=0, pruned=0)
    if not force and not await should_dream(db.memory, db.dreams, owner_id, settings):
        return DreamResponse(ran=False, merged=0, pruned=0)
    result = await consolidate(db.memory, db.dreams, owner_id, settings)
    return DreamResponse(ran=True, merged=result["merged"], pruned=result["pruned"])
