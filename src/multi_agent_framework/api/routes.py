"""HTTP routes: health check and the main chat endpoint."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Request

from multi_agent_framework.api.models import ChatRequest, ChatResponse, HealthResponse
from multi_agent_framework.core.graph import build_initial_state

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
        agent=result.get("current_agent"),
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

    agent = result.get("current_agent")
    registry = getattr(request.app.state, "registry", None)
    model_tier = registry.get(agent).model if registry and agent and agent in registry else None
    routing_scores = result.get("routing_scores") or {}
    eval_result = result.get("eval_result") or {}

    try:
        await db.conversations.record_turn(
            conversation_id=conversation_id,
            owner_id=payload.owner_id or "anonymous",
            user_message=payload.message,
            agent_name=agent,
            routing_confidence=routing_scores.get(agent),
            agent_response=result.get("agent_response"),
            eval_score=eval_result.get("score"),
            retry_count=result.get("retry_count", 0) or 0,
            model_tier=model_tier,
            latency_ms=latency_ms,
        )
    except Exception:  # noqa: BLE001 - persistence is best-effort
        logger.warning("turn persistence failed", exc_info=True)
