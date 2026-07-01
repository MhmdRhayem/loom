from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from multi_agent_framework.storage.base import Repository
from multi_agent_framework.storage.models import AgentPerformance


class PerformanceRepository(Repository):
    """Read/write operations for per-agent performance scores."""

    async def upsert_agent_performance(
        self,
        agent_name: str,
        query_category: str,
        score: float,
        success_delta: int = 0,
        failure_delta: int = 0,
    ) -> None:
        stmt = pg_insert(AgentPerformance).values(
            agent_name=agent_name,
            query_category=query_category,
            score=score,
            successes=success_delta,
            failures=failure_delta,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="pk_agent_performance",
            set_={
                "score": stmt.excluded.score,
                "successes": AgentPerformance.successes + stmt.excluded.successes,
                "failures": AgentPerformance.failures + stmt.excluded.failures,
                "updated_at": func.now(),
            },
        )
        async with self._session() as session, session.begin():
            await session.execute(stmt)

    async def get_agent_performance(
        self, agent_name: str, query_category: str
    ) -> AgentPerformance | None:
        async with self._session() as session:
            return await session.get(AgentPerformance, (agent_name, query_category))

    async def get_category_performance(
        self, query_category: str, limit: int = 50
    ) -> list[AgentPerformance]:
        """All agents scored for a query category, best score first (for routing)."""
        stmt = (
            select(AgentPerformance)
            .where(AgentPerformance.query_category == query_category)
            .order_by(AgentPerformance.score.desc())
            .limit(limit)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_best_agent(self, query_category: str) -> AgentPerformance | None:
        """The single highest-scoring agent for a category, or None if untrained."""
        stmt = (
            select(AgentPerformance)
            .where(AgentPerformance.query_category == query_category)
            .order_by(AgentPerformance.score.desc())
            .limit(1)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return result.scalars().first()
