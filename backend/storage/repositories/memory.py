from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.storage.base import Repository
from backend.storage.models import AutoMemory


class MemoryRepository(Repository):
    """Read/write operations for the auto-memory layer."""

    async def upsert_auto_memory(
        self,
        owner_id: str,
        topic: str,
        content: str,
        confidence: float = 0.5,
    ) -> None:
        """Insert the (owner, topic) memory or overwrite it in place — one atomic
        statement against uq_auto_memory_owner_topic, so concurrent turns can't
        create duplicate topics."""
        stmt = pg_insert(AutoMemory).values(
            owner_id=owner_id, topic=topic, content=content, confidence=confidence
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["owner_id", "topic"],
            set_={
                "content": stmt.excluded.content,
                "confidence": stmt.excluded.confidence,
                "updated_at": func.now(),
            },
        )
        async with self._session() as session, session.begin():
            await session.execute(stmt)

    async def load_auto_memory(
        self, owner_id: str, topic: str | None = None, limit: int = 200
    ) -> list[AutoMemory]:
        stmt = select(AutoMemory).where(AutoMemory.owner_id == owner_id)
        if topic is not None:
            stmt = stmt.where(AutoMemory.topic == topic)
        stmt = stmt.order_by(AutoMemory.updated_at.desc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_auto_memory(
        self,
        memory_id: UUID,
        *,
        content: str | None = None,
        confidence: float | None = None,
    ) -> None:
        """Revise an existing memory in place (used by the consolidation/merge step).

        Only the fields you pass are changed; omitted ones are left alone.
        """
        values: dict[str, object] = {"updated_at": func.now()}
        if content is not None:
            values["content"] = content
        if confidence is not None:
            values["confidence"] = confidence
        if len(values) == 1:  # only updated_at — nothing was actually passed
            return
        async with self._session() as session, session.begin():
            await session.execute(
                update(AutoMemory).where(AutoMemory.id == memory_id).values(**values)
            )

    async def delete_auto_memory(self, memory_id: UUID) -> None:
        """Permanently remove a memory (used by the consolidation/prune step)."""
        async with self._session() as session, session.begin():
            await session.execute(delete(AutoMemory).where(AutoMemory.id == memory_id))

    async def count_auto_memory(self, owner_id: str) -> int:
        """How many memories an owner has (drives the size-based dream trigger)."""
        stmt = select(func.count()).select_from(AutoMemory).where(AutoMemory.owner_id == owner_id)
        async with self._session() as session:
            result = await session.execute(stmt)
            return int(result.scalar_one())
