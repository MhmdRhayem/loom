from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.storage.base import Repository
from backend.storage.models import AgentPerformance


class PerformanceRepository(Repository):
    """Read/write operations for per-agent performance scores."""

    async def record_reward(
        self,
        agent_name: str,
        query_category: str,
        reward: float,
        *,
        alpha: float,
        prior: float = 0.5,
        success_delta: int = 0,
        failure_delta: int = 0,
    ) -> None:
        """Fold one reward into the (agent, category) EMA score, atomically.

        The EMA update runs inside the upsert (score := score*(1-alpha) + alpha*reward),
        so concurrent rewards compose instead of the last writer erasing the others;
        the first-ever reward starts from `prior`.
        """
        initial = (1.0 - alpha) * prior + alpha * reward
        stmt = pg_insert(AgentPerformance).values(
            agent_name=agent_name,
            query_category=query_category,
            score=initial,
            successes=success_delta,
            failures=failure_delta,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="pk_agent_performance",
            set_={
                "score": AgentPerformance.score * (1.0 - alpha) + alpha * reward,
                "successes": AgentPerformance.successes + stmt.excluded.successes,
                "failures": AgentPerformance.failures + stmt.excluded.failures,
                "updated_at": func.now(),
            },
        )
        async with self._session() as session, session.begin():
            await session.execute(stmt)
