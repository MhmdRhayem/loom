from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from backend import service
from backend.api.models import (
    AgentInfo,
    AgentRun,
    AgentsResponse,
    ChatRequest,
    ChatResponse,
    DreamResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def resolve_owner(request: Request, fallback: str | None = None) -> str | None:
    """The verified owner id when an identity resolver is configured, else the fallback.

    With a resolver set (see create_app), the client-supplied owner id is never
    trusted; the resolver may raise HTTPException(401) to reject the request.
    """
    resolver = getattr(request.app.state, "identity_resolver", None)
    if resolver is None:
        return fallback
    return await resolver(request)


async def resolve_allowed_agents(request: Request) -> list[str] | None:
    """The agent names this request may use, or None when unrestricted.

    Mirror of resolve_owner for the agent_visibility hook (see create_app); with no
    hook configured every agent is visible.
    """
    resolver = getattr(request.app.state, "agent_visibility", None)
    if resolver is None:
        return None
    allowed = await resolver(request)
    return None if allowed is None else sorted(allowed)


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Liveness and dependency check. Reports component status, never raises."""
    components: dict[str, str] = {}

    # The component values are a fixed vocabulary, never the driver's message: this
    # endpoint is unauthenticated, and a psycopg or redis failure string carries the
    # host, port, user and the server's own error text. The log keeps the detail.
    redis_store = getattr(request.app.state, "redis", None)
    if redis_store is None:
        components["redis"] = "disabled"
    else:
        try:
            await redis_store.client.ping()
            components["redis"] = "ok"
        except Exception:  # noqa: BLE001 - health must never throw
            logger.warning("redis health check failed", exc_info=True)
            components["redis"] = "error"

    db = getattr(request.app.state, "db", None)
    if db is None:
        components["postgres"] = "disabled"
    else:
        try:
            await db.ping()
            components["postgres"] = "ok"
        except Exception:  # noqa: BLE001 - health must never throw
            logger.warning("postgres health check failed", exc_info=True)
            components["postgres"] = "error"

    status = "ok" if all(v in ("ok", "disabled") for v in components.values()) else "degraded"
    return HealthResponse(status=status, components=components)


@router.get("/agents", response_model=AgentsResponse)
async def agents(request: Request) -> AgentsResponse:
    """The agent roster (name, role, capabilities, tools, tier, fallback) for the UI.

    Filtered by the agent_visibility hook, so a client only ever sees the agents
    it can actually reach.
    """
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return AgentsResponse(agents=[], fallback_agent=None)
    allowed = await resolve_allowed_agents(request)
    infos = [
        AgentInfo(
            name=defn.name,
            description=defn.description,
            capabilities=list(defn.capabilities),
            tools=list(defn.tools),
            model=defn.model,
            max_tokens=defn.max_tokens,
            eval_rubric=list(defn.eval_rubric),
            judge_sample_rate=defn.judge_sample_rate,
            fallback_agent=defn.fallback_agent,
        )
        for defn in registry.all()
        if allowed is None or defn.name in allowed
    ]
    return AgentsResponse(
        agents=infos, fallback_agent=getattr(request.app.state, "fallback_agent", None)
    )


def _chat_response(outcome: service.TurnOutcome) -> ChatResponse:
    return ChatResponse(
        conversation_id=outcome.conversation_id,
        agent=", ".join(outcome.agents) if outcome.agents else None,
        response=outcome.response,
        eval=outcome.eval,
        current_agents=outcome.agents,
        routing_confidence=outcome.routing_confidence,
        routing_reason=outcome.routing_reason,
        query_category=outcome.query_category,
        tool_calls=outcome.tool_calls,
        agent_runs=[AgentRun(**run) for run in outcome.agent_runs],
        retry_count=outcome.retry_count,
        usage=outcome.usage,
    )


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
        owner_id=await resolve_owner(request, payload.owner_id),
        allowed_agents=await resolve_allowed_agents(request),
    )
    return _chat_response(outcome)


@router.post("/chat/stream")
async def chat_stream(request: Request, payload: ChatRequest) -> StreamingResponse:
    """Server-sent events variant of /chat: emits a `stage` event as each pipeline
    node finishes, then a final `done` event carrying the full ChatResponse."""
    state = request.app.state
    # Resolve identity before the stream starts so auth failures are a clean 401.
    owner_id = await resolve_owner(request, payload.owner_id)
    allowed_agents = await resolve_allowed_agents(request)

    async def events():
        # Once the stream has started, an exception can't become an HTTP 500 anymore —
        # emit a terminal error event so the client gets a reason instead of a dead socket.
        try:
            async for kind, data in service.stream_turn(
                graph=state.graph,
                db=getattr(state, "db", None),
                settings=getattr(state, "settings", None),
                registry=getattr(state, "registry", None),
                message=payload.message,
                conversation_id=payload.conversation_id,
                owner_id=owner_id,
                allowed_agents=allowed_agents,
            ):
                if kind == "token":
                    yield f"data: {json.dumps({'type': 'token', **data})}\n\n"
                elif kind == "stage":
                    yield f"data: {json.dumps({'type': 'stage', **data})}\n\n"
                else:
                    body = _chat_response(data).model_dump()
                    yield f"data: {json.dumps({'type': 'done', 'payload': body})}\n\n"
        except Exception as exc:  # noqa: BLE001 - surface the failure to the client
            logger.warning("chat stream failed", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(request: Request, payload: FeedbackRequest) -> FeedbackResponse:
    """Record a thumbs rating against the conversation's last turn. Never raises.

    With an identity resolver configured you can only rate your own conversations;
    anyone else's id is silently not recorded (feedback is best-effort anyway).
    """
    owner = await resolve_owner(request)
    db = getattr(request.app.state, "db", None)
    if owner is not None and db is not None:
        try:
            conv = await db.conversations.get_conversation(uuid.UUID(payload.conversation_id))
        except Exception:  # noqa: BLE001 - malformed id / storage error -> just not recorded
            conv = None
        if conv is None or conv.owner_id != owner:
            return FeedbackResponse(recorded=False, agent=None)
    outcome = await service.record_feedback(
        db=db,
        conversation_id=payload.conversation_id,
        rating=payload.rating,
    )
    return FeedbackResponse(
        recorded=outcome.recorded,
        agent=", ".join(outcome.agents) if outcome.agents else None,
    )


@router.post("/dream", response_model=DreamResponse)
async def dream(
    request: Request, owner_id: str | None = None, force: bool = False
) -> DreamResponse:
    """Run memory consolidation for an owner, when it's due or force is set.

    With an identity resolver configured, you can only dream for yourself —
    the query parameter is overridden by the verified identity, so it is optional
    (requiring it would 422 the caller for a value that is then discarded).
    """
    owner_id = await resolve_owner(request, owner_id)
    outcome = await service.run_dream(
        db=getattr(request.app.state, "db", None),
        settings=getattr(request.app.state, "settings", None),
        owner_id=owner_id,
        force=force,
    )
    return DreamResponse(ran=outcome.ran, merged=outcome.merged, pruned=outcome.pruned)
