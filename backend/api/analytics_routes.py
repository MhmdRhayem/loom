from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from backend.api.analytics_models import (
    AgentAnalyticsResponse,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationSummary,
    DeleteConversationResponse,
    MemoryAnalyticsResponse,
    OverviewResponse,
    RoutingAnalyticsResponse,
    TimeseriesResponse,
    TurnDetail,
)
from backend.api.routes import resolve_owner

router = APIRouter(tags=["analytics"])

_VALID_BUCKETS = {"day", "hour"}


def _db(request: Request):
    """The Database on app.state, or None when Postgres was down at boot."""
    return getattr(request.app.state, "db", None)


async def _check_guard(request: Request) -> None:
    """Run the app's analytics_guard hook (see create_app); it raises 401/403 to deny.

    Applied per-endpoint to the aggregate /analytics handlers only — the
    /conversations routes share this router but stay owner-scoped instead.
    """
    guard = getattr(request.app.state, "analytics_guard", None)
    if guard is not None:
        await guard(request)


def _f(value) -> float | None:
    return float(value) if value is not None else None


def _summary(conv) -> ConversationSummary:
    return ConversationSummary(
        id=str(conv.id),
        owner_id=conv.owner_id,
        created_at=conv.created_at,
        status=conv.status,
        total_turns=conv.total_turns,
        title=conv.title,
    )


@router.get("/analytics/overview", response_model=OverviewResponse)
async def analytics_overview(request: Request) -> OverviewResponse:
    await _check_guard(request)
    db = _db(request)
    if db is None:
        return OverviewResponse()
    return OverviewResponse(**await db.analytics.overview())


@router.get("/analytics/agents", response_model=AgentAnalyticsResponse)
async def analytics_agents(request: Request) -> AgentAnalyticsResponse:
    await _check_guard(request)
    db = _db(request)
    if db is None:
        return AgentAnalyticsResponse()
    return AgentAnalyticsResponse(
        agents=await db.analytics.agent_turn_stats(),
        performance=await db.analytics.agent_performance_rows(),
    )


@router.get("/analytics/routing", response_model=RoutingAnalyticsResponse)
async def analytics_routing(request: Request) -> RoutingAnalyticsResponse:
    await _check_guard(request)
    db = _db(request)
    if db is None:
        return RoutingAnalyticsResponse()
    return RoutingAnalyticsResponse(**await db.analytics.routing())


@router.get("/analytics/timeseries", response_model=TimeseriesResponse)
async def analytics_timeseries(
    request: Request, bucket: str = "day", limit: int = 30
) -> TimeseriesResponse:
    await _check_guard(request)
    db = _db(request)
    if db is None:
        return TimeseriesResponse()
    if bucket not in _VALID_BUCKETS:
        bucket = "day"
    limit = max(1, min(limit, 365))
    return TimeseriesResponse(points=await db.analytics.timeseries(bucket, limit))


@router.get("/analytics/memory", response_model=MemoryAnalyticsResponse)
async def analytics_memory(
    request: Request, owner_id: str | None = None
) -> MemoryAnalyticsResponse:
    await _check_guard(request)
    db = _db(request)
    if db is None:
        return MemoryAnalyticsResponse()
    runs = await db.analytics.recent_dream_runs(limit=50, owner_id=owner_id)
    return MemoryAnalyticsResponse(
        memory_counts=await db.analytics.memory_counts(owner_id=owner_id),
        dream_runs=[
            {
                "owner_id": r.owner_id,
                "memories_merged": r.memories_merged,
                "memories_pruned": r.memories_pruned,
                "duration_ms": r.duration_ms,
                "started_at": r.started_at,
            }
            for r in runs
        ],
    )


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    request: Request, owner_id: str | None = None, limit: int = 50
) -> ConversationListResponse:
    db = _db(request)
    if db is None:
        return ConversationListResponse()
    # With an identity resolver configured, you only ever see your own conversations.
    owner_id = await resolve_owner(request, owner_id)
    limit = max(1, min(limit, 200))
    convs = await db.analytics.recent_conversations(limit=limit, owner_id=owner_id)
    return ConversationListResponse(conversations=[ConversationSummary(**c) for c in convs])


@router.delete("/conversations/{conversation_id}", response_model=DeleteConversationResponse)
async def delete_conversation(request: Request, conversation_id: str) -> DeleteConversationResponse:
    """Delete one of your own conversations (turns cascade). 404 for anyone else's."""
    db = _db(request)
    if db is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    # Authenticate before touching storage, so an unauthenticated caller gets the same
    # 401 whether or not the id happens to exist.
    owner = await resolve_owner(request)
    try:
        cid = uuid.UUID(conversation_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="conversation not found") from None
    conv = await db.conversations.get_conversation(cid)
    if conv is None or (owner is not None and conv.owner_id != owner):
        # 404, not 403: don't confirm that someone else's conversation exists.
        raise HTTPException(status_code=404, detail="conversation not found")
    await db.conversations.delete_conversation(cid)
    return DeleteConversationResponse(deleted=True)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def conversation_detail(request: Request, conversation_id: str) -> ConversationDetailResponse:
    db = _db(request)
    if db is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    # Authenticate first, for the same reason as the DELETE above.
    owner = await resolve_owner(request)
    try:
        cid = uuid.UUID(conversation_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="conversation not found") from None
    conv = await db.conversations.get_conversation(cid)
    if conv is None or (owner is not None and conv.owner_id != owner):
        # 404, not 403: don't confirm that someone else's conversation exists.
        raise HTTPException(status_code=404, detail="conversation not found")
    turns = await db.conversations.get_turns(cid)
    turn_agents = await db.analytics.turn_agents_for_conversation(cid)
    return ConversationDetailResponse(
        conversation=_summary(conv),
        turns=[
            TurnDetail(
                turn_number=t.turn_number,
                user_message=t.user_message,
                agent_name=t.agent_name,
                query_category=t.query_category,
                routing_confidence=_f(t.routing_confidence),
                agent_response=t.agent_response,
                eval_score=_f(t.eval_score),
                retry_count=t.retry_count,
                model_tier=t.model_tier,
                latency_ms=t.latency_ms,
                created_at=t.created_at,
            )
            for t in turns
        ],
        turn_agents=turn_agents,
    )
