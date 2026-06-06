from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update

from multi_agent_framework.storage.base import Repository
from multi_agent_framework.storage.models import AutoMemory


class MemoryRepository(Repository):
    """Read/write operations for the auto-memory layer."""

    async def save_auto_memory(
        self,
        project_id: str,
        topic: str,
        content: str,
        confidence: float = 0.5,
        expires_at: datetime | None = None,
    ) -> UUID:
        async with self._session() as session, session.begin():
            mem = AutoMemory(
                project_id=project_id,
                topic=topic,
                content=content,
                confidence=confidence,
                expires_at=expires_at,
            )
            session.add(mem)
            await session.flush()
            return mem.id

    async def load_auto_memory(
        self, project_id: str, topic: str | None = None, limit: int = 200
    ) -> list[AutoMemory]:
        stmt = select(AutoMemory).where(AutoMemory.project_id == project_id)
        stmt = stmt.where(
            (AutoMemory.expires_at.is_(None))
            | (AutoMemory.expires_at > func.now())
        )
        if topic is not None:
            stmt = stmt.where(AutoMemory.topic == topic)
        stmt = stmt.order_by(AutoMemory.updated_at.desc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def touch_auto_memory(self, memory_id: UUID) -> None:
        async with self._session() as session, session.begin():
            await session.execute(
                update(AutoMemory)
                .where(AutoMemory.id == memory_id)
                .values(
                    access_count=AutoMemory.access_count + 1,
                    updated_at=func.now(),
                )
            )
