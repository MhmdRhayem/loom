from __future__ import annotations

from uuid import UUID

from multi_agent_framework.storage.base import Repository
from multi_agent_framework.storage.models import DreamRun


class DreamRepository(Repository):
    """Read/write operations for memory-consolidation ("dream") runs."""

    async def log_dream_run(
        self,
        project_id: str,
        sessions_consolidated: int,
        memories_merged: int,
        memories_pruned: int,
        duration_ms: int,
    ) -> UUID:
        async with self._session() as session, session.begin():
            run = DreamRun(
                project_id=project_id,
                sessions_consolidated=sessions_consolidated,
                memories_merged=memories_merged,
                memories_pruned=memories_pruned,
                duration_ms=duration_ms,
            )
            session.add(run)
            await session.flush()
            return run.id
