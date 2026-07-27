from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.storage.models import Base


def _normalize_dsn(url: str) -> str:
    """Force the async psycopg driver on a plain postgresql:// DSN."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


# Idempotent catch-up statements for a DB created by an older models.py (see
# create_all): drop retired columns/indexes, then dedupe auto_memory (keep the
# newest row per owner+topic) so its unique index can be enforced.
_SCHEMA_PATCHES = (
    "ALTER TABLE conversations DROP COLUMN IF EXISTS total_cost",
    "ALTER TABLE conversation_turns DROP COLUMN IF EXISTS execution_model",
    "ALTER TABLE conversation_turns DROP COLUMN IF EXISTS cost",
    "ALTER TABLE conversation_turns DROP COLUMN IF EXISTS cache_hit",
    "ALTER TABLE auto_memory DROP COLUMN IF EXISTS access_count",
    "ALTER TABLE dream_runs DROP COLUMN IF EXISTS sessions_consolidated",
    "DROP INDEX IF EXISTS ix_turns_conversation_id",
    "DROP INDEX IF EXISTS ix_auto_memory_owner",
    "DELETE FROM auto_memory a USING auto_memory b"
    " WHERE a.owner_id = b.owner_id AND a.topic = b.topic"
    " AND (a.updated_at, a.id) < (b.updated_at, b.id)",
    "DROP INDEX IF EXISTS ix_auto_memory_owner_topic",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_auto_memory_owner_topic ON auto_memory (owner_id, topic)",
    # Memory expiry was never wired to a writer: every row had expires_at NULL, so the
    # column and its three "still live" predicates only ever cost query planning.
    "ALTER TABLE auto_memory DROP COLUMN IF EXISTS expires_at",
    # No query filters or groups on conversation_turns.agent_name (per-agent aggregates
    # all read turn_agents), and the owner index is superseded by the composite that
    # also covers the ORDER BY created_at DESC on the conversation list.
    "DROP INDEX IF EXISTS ix_turns_agent_name",
    "DROP INDEX IF EXISTS ix_conversations_owner",
    "CREATE INDEX IF NOT EXISTS ix_conversations_owner_created"
    " ON conversations (owner_id, created_at)",
)


class Repository:
    """Base class for domain repositories.

    Each repository owns the reads and writes for one area of the schema
    (conversations, memory, performance, dreams), sharing one connection pool
    handed in as a sessionmaker.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    def _session(self) -> AsyncSession:
        return self._sessionmaker()


class Database:
    """Owns the Postgres connection pool and exposes the domain repositories.

    Usage:
        db = Database(settings.database_url)
        await db.open()
        turns = await db.conversations.get_turns(conversation_id)
        await db.memory.upsert_auto_memory(owner_id="shopper:42", topic=..., content=...)
        await db.close()
    """

    # One repository per area of the schema, bound in open(). Declared rather than
    # pre-assigned None so the annotations stay true: reaching one before open() is an
    # AttributeError naming the attribute, instead of a None sneaking downstream.
    conversations: ConversationRepository
    memory: MemoryRepository
    performance: PerformanceRepository
    dreams: DreamRepository
    analytics: AnalyticsRepository

    def __init__(self, dsn: str, pool_size: int = 5, max_overflow: int = 5) -> None:
        self._dsn = _normalize_dsn(dsn)
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

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
        self.analytics = AnalyticsRepository(self._sessionmaker)

    async def ping(self) -> None:
        """Run SELECT 1 to confirm the pool can reach Postgres.

        open() builds the engine lazily and never connects, so this is the cheapest
        way to check the backend is actually reachable (used at startup and by the
        health check).
        """
        if self._engine is None:
            raise RuntimeError("Database is not open; call await db.open() first")
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def create_all(self) -> None:
        """Create any missing tables defined on the models, then patch an existing
        schema up to date (idempotent).

        We use this instead of Alembic: models.py is the single source of truth
        for the schema, which is fine for a single-developer project with no
        production data to preserve. create_all never ALTERs an existing table,
        so changes to already-created tables are applied as explicit idempotent
        statements below — rerunning scripts/init_db.py migrates a live dev DB.
        """
        if self._engine is None:
            raise RuntimeError("Database is not open; call await db.open() first")
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for statement in _SCHEMA_PATCHES:
                await conn.execute(text(statement))

    async def close(self) -> None:
        if self._engine is None:
            return
        await self._engine.dispose()
        self._engine = None
        self._sessionmaker = None


# Needed at runtime by the constructor calls in open(), not just by the annotations
# above — do not delete these under `from __future__ import annotations`. They sit at
# the bottom because each repository module imports Repository back from this one, so a
# top-of-file import would be circular.
from backend.storage.repositories.analytics import AnalyticsRepository  # noqa: E402
from backend.storage.repositories.conversations import ConversationRepository  # noqa: E402
from backend.storage.repositories.dreams import DreamRepository  # noqa: E402
from backend.storage.repositories.memory import MemoryRepository  # noqa: E402
from backend.storage.repositories.performance import PerformanceRepository  # noqa: E402
