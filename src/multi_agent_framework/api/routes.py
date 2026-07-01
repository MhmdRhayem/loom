"""HTTP routes: health check and the chat/feedback/dream endpoints.

Thin by design: each endpoint pulls its dependencies off ``app.state`` and hands off to a
function in :mod:`multi_agent_framework.service`, then maps the result to a response model.
The orchestration (graph invocation, telemetry, learning, consolidation) lives in the
service layer, not here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from multi_agent_framework import service
from multi_agent_framework.api.models import (
    ChatRequest,
    ChatResponse,
    DreamResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
)

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
    state = request.app.state
    outcome = await service.run_turn(
        graph=state.graph,
        db=getattr(state, "db", None),
        settings=getattr(state, "settings", None),
        registry=getattr(state, "registry", None),
        message=payload.message,
        conversation_id=payload.conversation_id,
        owner_id=payload.owner_id,
    )
    return ChatResponse(
        conversation_id=outcome.conversation_id,
        agent=", ".join(outcome.agents),
        response=outcome.response,
        eval=outcome.eval,
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(request: Request, payload: FeedbackRequest) -> FeedbackResponse:
    """Record explicit thumbs feedback for a conversation's last turn (Phase 6 signal). Fail-soft."""
    outcome = await service.record_feedback(
        db=getattr(request.app.state, "db", None),
        conversation_id=payload.conversation_id,
        rating=payload.rating,
    )
    return FeedbackResponse(
        recorded=outcome.recorded,
        agent=", ".join(outcome.agents) if outcome.agents else None,
    )


@router.post("/dream", response_model=DreamResponse)
async def dream(request: Request, owner_id: str, force: bool = False) -> DreamResponse:
    """Run memory consolidation ("dreaming") for an owner — when due, or force=true (Layer 4)."""
    outcome = await service.run_dream(
        db=getattr(request.app.state, "db", None),
        settings=getattr(request.app.state, "settings", None),
        owner_id=owner_id,
        force=force,
    )
    return DreamResponse(ran=outcome.ran, merged=outcome.merged, pruned=outcome.pruned)
