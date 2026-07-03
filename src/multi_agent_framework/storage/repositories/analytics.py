from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select

from multi_agent_framework.storage.base import Repository
from multi_agent_framework.storage.models import (
    AgentPerformance,
    AutoMemory,
    Conversation,
    ConversationTurn,
    DreamRun,
    TurnAgent,
)

# Turns whose memory is still live (never expires, or not yet expired).
_LIVE_MEMORY = (AutoMemory.expires_at.is_(None)) | (AutoMemory.expires_at > func.now())


def _f(value: Any) -> float | None:
    """Cast a Decimal/None aggregate to a plain float (or None)."""
    return float(value) if value is not None else None


def _truncate(message: str | None, limit: int = 60) -> str | None:
    """Collapse a message to one display line of at most `limit` characters."""
    if not message:
        return None
    line = " ".join(message.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


class AnalyticsRepository(Repository):
    """Read-only aggregate queries for the dashboard. Never writes."""

    async def overview(self) -> dict[str, Any]:
        """Headline KPIs across all conversations/turns."""
        async with self._session() as s:
            conversations = await s.scalar(select(func.count()).select_from(Conversation))
            turns = await s.scalar(select(func.count()).select_from(ConversationTurn))
            avg_eval = await s.scalar(select(func.avg(ConversationTurn.eval_score)))
            avg_latency = await s.scalar(select(func.avg(ConversationTurn.latency_ms)))
            retries = await s.scalar(
                select(func.count())
                .select_from(ConversationTurn)
                .where(ConversationTurn.retry_count > 0)
            )
            passes = await s.scalar(
                select(func.count()).select_from(TurnAgent).where(TurnAgent.eval_pass.is_(True))
            )
            judged = await s.scalar(
                select(func.count()).select_from(TurnAgent).where(TurnAgent.eval_pass.isnot(None))
            )
            memories = await s.scalar(
                select(func.count()).select_from(AutoMemory).where(_LIVE_MEMORY)
            )
            dreams = await s.scalar(select(func.count()).select_from(DreamRun))

        turns = int(turns or 0)
        judged = int(judged or 0)
        return {
            "conversations": int(conversations or 0),
            "turns": turns,
            "avg_eval_score": _f(avg_eval),
            "avg_latency_ms": _f(avg_latency),
            "eval_pass_rate": (int(passes or 0) / judged) if judged else None,
            "retry_rate": (int(retries or 0) / turns) if turns else None,
            "total_memories": int(memories or 0),
            "dream_runs": int(dreams or 0),
        }

    async def agent_turn_stats(self) -> list[dict[str, Any]]:
        """Per-agent turn counts, avg eval score, pass/fail tallies, avg latency."""
        stmt = (
            select(
                TurnAgent.agent_name.label("agent"),
                func.count().label("turns"),
                func.avg(TurnAgent.eval_score).label("avg_eval_score"),
                func.sum(case((TurnAgent.eval_pass.is_(True), 1), else_=0)).label("passes"),
                func.sum(case((TurnAgent.eval_pass.is_(False), 1), else_=0)).label("fails"),
                func.avg(ConversationTurn.latency_ms).label("avg_latency_ms"),
            )
            .select_from(TurnAgent)
            .join(ConversationTurn, TurnAgent.turn_id == ConversationTurn.id)
            .group_by(TurnAgent.agent_name)
            .order_by(TurnAgent.agent_name)
        )
        async with self._session() as s:
            rows = (await s.execute(stmt)).all()
        return [
            {
                "agent": r.agent,
                "turns": int(r.turns),
                "avg_eval_score": _f(r.avg_eval_score),
                "passes": int(r.passes or 0),
                "fails": int(r.fails or 0),
                "avg_latency_ms": _f(r.avg_latency_ms),
            }
            for r in rows
        ]

    async def agent_performance_rows(self) -> list[dict[str, Any]]:
        """Every agent_performance row (EMA score plus counts per agent/category)."""
        stmt = select(AgentPerformance).order_by(
            AgentPerformance.agent_name, AgentPerformance.query_category
        )
        async with self._session() as s:
            rows = list((await s.execute(stmt)).scalars().all())
        return [
            {
                "agent": r.agent_name,
                "category": r.query_category,
                "score": _f(r.score),
                "successes": int(r.successes),
                "failures": int(r.failures),
            }
            for r in rows
        ]

    async def _distribution(self, column) -> list[dict[str, Any]]:
        """Count rows grouped by a column, most frequent first (for pie/donut charts)."""
        key = func.coalesce(column, "unknown").label("key")
        stmt = select(key, func.count().label("count")).group_by(key).order_by(func.count().desc())
        async with self._session() as s:
            rows = (await s.execute(stmt)).all()
        return [{"key": r.key, "count": int(r.count)} for r in rows]

    async def routing(self) -> dict[str, Any]:
        """Routing distribution by category and by agent + average confidence."""
        category_distribution = await self._distribution(ConversationTurn.query_category)
        agent_distribution = await self._distribution(TurnAgent.agent_name)
        async with self._session() as s:
            avg_conf = await s.scalar(select(func.avg(ConversationTurn.routing_confidence)))
        return {
            "category_distribution": category_distribution,
            "agent_distribution": agent_distribution,
            "avg_routing_confidence": _f(avg_conf),
        }

    async def timeseries(self, bucket: str, limit: int) -> list[dict[str, Any]]:
        """Turn counts, avg eval score, and avg latency bucketed over time (chronological)."""
        trunc = func.date_trunc(bucket, ConversationTurn.created_at).label("bucket")
        stmt = (
            select(
                trunc,
                func.count().label("turns"),
                func.avg(ConversationTurn.eval_score).label("avg_eval_score"),
                func.avg(ConversationTurn.latency_ms).label("avg_latency_ms"),
            )
            .group_by(trunc)
            .order_by(trunc.desc())
            .limit(limit)
        )
        async with self._session() as s:
            rows = (await s.execute(stmt)).all()
        return [
            {
                "bucket": r.bucket.isoformat() if r.bucket is not None else None,
                "turns": int(r.turns),
                "avg_eval_score": _f(r.avg_eval_score),
                "avg_latency_ms": _f(r.avg_latency_ms),
            }
            for r in reversed(rows)
        ]

    async def memory_counts(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        """Live memory count per owner (most memories first)."""
        key = AutoMemory.owner_id.label("owner_id")
        stmt = (
            select(key, func.count().label("count"))
            .where(_LIVE_MEMORY)
            .group_by(key)
            .order_by(func.count().desc())
        )
        if owner_id:
            stmt = stmt.where(AutoMemory.owner_id == owner_id)
        async with self._session() as s:
            rows = (await s.execute(stmt)).all()
        return [{"owner_id": r.owner_id, "count": int(r.count)} for r in rows]

    async def recent_dream_runs(
        self, limit: int = 50, owner_id: str | None = None
    ) -> list[DreamRun]:
        """Recent consolidation runs, newest first (all owners unless one is given)."""
        stmt = select(DreamRun).order_by(DreamRun.started_at.desc()).limit(limit)
        if owner_id:
            stmt = stmt.where(DreamRun.owner_id == owner_id)
        async with self._session() as s:
            return list((await s.execute(stmt)).scalars().all())

    async def recent_conversations(
        self, limit: int = 50, owner_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Recent conversations, newest first (all owners unless one is given).

        Each dict carries a display title: the AI-generated one when set, else the
        first user message truncated — so lists never show a bare UUID.
        """
        stmt = select(Conversation).order_by(Conversation.created_at.desc()).limit(limit)
        if owner_id:
            stmt = stmt.where(Conversation.owner_id == owner_id)
        async with self._session() as s:
            convs = list((await s.execute(stmt)).scalars().all())
            titles: dict[UUID, str] = {}
            if convs:
                first_turns = select(
                    ConversationTurn.conversation_id, ConversationTurn.user_message
                ).where(
                    ConversationTurn.conversation_id.in_([c.id for c in convs]),
                    ConversationTurn.turn_number == 1,
                )
                titles = {
                    r.conversation_id: r.user_message for r in (await s.execute(first_turns)).all()
                }
        return [
            {
                "id": str(c.id),
                "owner_id": c.owner_id,
                "created_at": c.created_at,
                "status": c.status,
                "total_turns": c.total_turns,
                "title": c.title or _truncate(titles.get(c.id)),
            }
            for c in convs
        ]

    async def turn_agents_for_conversation(self, conversation_id: UUID) -> list[dict[str, Any]]:
        """Per-agent verdict rows for a whole conversation, tagged with their turn number.

        One query (joined to conversation_turns) instead of N calls to get_turn_agents,
        avoiding the per-turn round trip in the detail view.
        """
        stmt = (
            select(TurnAgent, ConversationTurn.turn_number.label("turn_number"))
            .join(ConversationTurn, TurnAgent.turn_id == ConversationTurn.id)
            .where(ConversationTurn.conversation_id == conversation_id)
            .order_by(ConversationTurn.turn_number, TurnAgent.id)
        )
        async with self._session() as s:
            rows = (await s.execute(stmt)).all()
        return [
            {
                "turn_number": row.turn_number,
                "agent_name": row[0].agent_name,
                "model_tier": row[0].model_tier,
                "eval_score": _f(row[0].eval_score),
                "eval_pass": row[0].eval_pass,
            }
            for row in rows
        ]
