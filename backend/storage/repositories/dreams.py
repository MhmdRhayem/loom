from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from backend.storage.base import Repository
from backend.storage.models import DreamRun


class DreamRepository(Repository):
    """Read/write operations for memory-consolidation ("dream") runs."""

    async def log_dream_run(
        self,
        owner_id: str,
        memories_merged: int,
        memories_pruned: int,
        duration_ms: int,
        started_at: datetime | None = None,
    ) -> UUID:
        """Record the outcome of one finished consolidation pass.

        Pass started_at (captured when the run began) to record the true start time;
        if omitted, the DB stamps the insert time, which is the finish, not the start.
        """
        values: dict = {
            "owner_id": owner_id,
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
        """Most recent dream run for an owner, or None if it never has.

        Used to decide whether it's time to dream again.
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
