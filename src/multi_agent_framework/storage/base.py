from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from multi_agent_framework.storage.models import Base


def _normalize_dsn(url: str) -> str:
    """Force the async psycopg driver on a plain ``postgresql://`` DSN."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


class Repository:
    """Base class for domain repositories.

    Each repository owns the read/write functions for one area of the schema
    (conversations, memory, performance, dreams). They all share a single
    connection pool, handed in as a sessionmaker.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    def _session(self) -> AsyncSession:
        return self._sessionmaker()


class Database:
    """Owns the Postgres connection pool and exposes the domain repositories.

    Usage::

        db = Database(settings.database_url)
        await db.open()
        conv_id = await db.conversations.create_conversation()
        await db.memory.save_auto_memory(...)
        await db.close()
    """

    def __init__(self, dsn: str, pool_size: int = 5, max_overflow: int = 5) -> None:
        self._dsn = _normalize_dsn(dsn)
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

        # Set in open(); one repository per area of the schema.
        self.conversations: "ConversationRepository" = None  # type: ignore[assignment]
        self.memory: "MemoryRepository" = None  # type: ignore[assignment]
        self.performance: "PerformanceRepository" = None  # type: ignore[assignment]
        self.dreams: "DreamRepository" = None  # type: ignore[assignment]

    async def open(self) -> None:
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            self._dsn,
            pool_size=self._pool_size,
            max_overflow=self._max_overflow,
            pool_pre_ping=True,
        )
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

        self.conversations = ConversationRepository(self._sessionmaker)
        self.memory = MemoryRepository(self._sessionmaker)
        self.performance = PerformanceRepository(self._sessionmaker)
        self.dreams = DreamRepository(self._sessionmaker)

    async def create_all(self) -> None:
        """Create every table defined on the models if missing (idempotent).

        Replaces Alembic: ``models.py`` is the single source of truth for the
        schema. Suitable for a single-developer project with no production
        data to preserve.
        """
        if self._engine is None:
            raise RuntimeError("Database is not open; call await db.open() first")
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        if self._engine is None:
            return
        await self._engine.dispose()
        self._engine = None
        self._sessionmaker = None


# Imported for the type hints above; placed at the bottom to avoid the
# circular import at module load time.
from multi_agent_framework.storage.repositories.conversations import ConversationRepository  # noqa: E402
from multi_agent_framework.storage.repositories.dreams import DreamRepository  # noqa: E402
from multi_agent_framework.storage.repositories.memory import MemoryRepository  # noqa: E402
from multi_agent_framework.storage.repositories.performance import PerformanceRepository  # noqa: E402
