from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update

from multi_agent_framework.storage.base import Repository
from multi_agent_framework.storage.models import AutoMemory

# Sentinel for "argument not provided", so update_auto_memory can tell
# "leave this field alone" apart from an explicit None (e.g. expires_at=None
# means "clear the expiry", which is a real, intended value).
_UNSET: Any = object()


class MemoryRepository(Repository):
    """Read/write operations for the auto-memory layer."""

    async def save_auto_memory(
        self,
        owner_id: str,
        topic: str,
        content: str,
        confidence: float = 0.5,
        expires_at: datetime | None = None,
    ) -> UUID:
        async with self._session() as session, session.begin():
            mem = AutoMemory(
                owner_id=owner_id,
                topic=topic,
                content=content,
                confidence=confidence,
                expires_at=expires_at,
            )
            session.add(mem)
            await session.flush()
            return mem.id

    async def load_auto_memory(self, owner_id: str, topic: str | None = None, limit: int = 200) -> list[AutoMemory]:
        stmt = select(AutoMemory).where(AutoMemory.owner_id == owner_id)
        stmt = stmt.where((AutoMemory.expires_at.is_(None)) | (AutoMemory.expires_at > func.now()))
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

    async def update_auto_memory(
        self,
        memory_id: UUID,
        *,
        content: str = _UNSET,
        confidence: float = _UNSET,
        expires_at: datetime | None = _UNSET,
    ) -> None:
        """Revise an existing memory in place (used by the consolidation/merge step).

        Only the fields you pass are changed; omitted fields are left untouched.
        ``expires_at=None`` is honoured as an explicit "never expires".
        """
        values: dict[str, Any] = {"updated_at": func.now()}
        if content is not _UNSET:
            values["content"] = content
        if confidence is not _UNSET:
            values["confidence"] = confidence
        if expires_at is not _UNSET:
            values["expires_at"] = expires_at
        if len(values) == 1:  # only updated_at — nothing was actually passed
            return
        async with self._session() as session, session.begin():
            await session.execute(update(AutoMemory).where(AutoMemory.id == memory_id).values(**values))

    async def delete_auto_memory(self, memory_id: UUID) -> None:
        """Permanently remove a memory (used by the consolidation/prune step)."""
        async with self._session() as session, session.begin():
            await session.execute(delete(AutoMemory).where(AutoMemory.id == memory_id))

    async def get_auto_memory(self, memory_id: UUID) -> AutoMemory | None:
        """Fetch a single memory by id, or None if it does not exist."""
        async with self._session() as session:
            return await session.get(AutoMemory, memory_id)

    async def count_auto_memory(self, owner_id: str, include_expired: bool = False) -> int:
        """How many memories an owner has (drives the size-based dream trigger).

        Counts only live (unexpired) memories by default — matching what
        ``load_auto_memory`` returns. Pass ``include_expired=True`` for the raw
        row count.
        """
        stmt = select(func.count()).select_from(AutoMemory).where(AutoMemory.owner_id == owner_id)
        if not include_expired:
            stmt = stmt.where((AutoMemory.expires_at.is_(None)) | (AutoMemory.expires_at > func.now()))
        async with self._session() as session:
            result = await session.execute(stmt)
            return int(result.scalar_one())
