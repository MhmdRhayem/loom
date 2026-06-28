"""HTTP routes: health check and the main chat endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from multi_agent_framework.api.models import ChatRequest, ChatResponse, HealthResponse
from multi_agent_framework.core.graph import build_initial_state

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
    result = await graph.ainvoke(state)
    return ChatResponse(
        conversation_id=result["conversation_id"],
        agent=result.get("current_agent"),
        response=result.get("agent_response"),
        eval=result.get("eval_result"),
    )
