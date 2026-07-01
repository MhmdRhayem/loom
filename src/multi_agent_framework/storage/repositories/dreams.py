from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from multi_agent_framework.storage.base import Repository
from multi_agent_framework.storage.models import DreamRun


class DreamRepository(Repository):
    """Read/write operations for memory-consolidation ("dream") runs."""

    async def log_dream_run(
        self,
        owner_id: str,
        sessions_consolidated: int,
        memories_merged: int,
        memories_pruned: int,
        duration_ms: int,
        started_at: datetime | None = None,
    ) -> UUID:
        """Record the outcome of one completed consolidation pass.

        Called when the run finishes, so pass ``started_at`` (captured when the
        run began) to record the true start time. If omitted, the DB stamps the
        insert time instead — which is the finish moment, not the start.
        """
        values: dict = {
            "owner_id": owner_id,
            "sessions_consolidated": sessions_consolidated,
            "memories_merged": memories_merged,
            "memories_pruned": memories_pruned,
            "duration_ms": duration_ms,
        }
        if started_at is not None:
            values["started_at"] = started_at
        async with self._session() as session, session.begin():
            run = DreamRun(**values)
            session.add(run)
            await session.flush()
            return run.id

    async def get_last_dream_run(self, owner_id: str) -> DreamRun | None:
        """Most recent dream run for an owner, or None if it has never dreamed.

        Used by the scheduler to decide whether it is time to dream again.
        """
        stmt = (
            select(DreamRun)
            .where(DreamRun.owner_id == owner_id)
            .order_by(DreamRun.started_at.desc())
            .limit(1)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return result.scalars().first()

    async def list_dream_runs(self, owner_id: str, limit: int = 50) -> list[DreamRun]:
        """Recent dream runs for an owner, newest first (for history/dashboard)."""
        stmt = (
            select(DreamRun)
            .where(DreamRun.owner_id == owner_id)
            .order_by(DreamRun.started_at.desc())
            .limit(limit)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())
