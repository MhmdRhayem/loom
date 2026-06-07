"""Create the database schema from the SQLAlchemy models.

Replaces Alembic: the models in ``storage/models.py`` are the single source of
truth. Run after Postgres is up (``docker compose up``):

    python scripts/init_db.py

Idempotent — safe to run repeatedly (CREATE TABLE IF NOT EXISTS).
"""
from __future__ import annotations

import asyncio

from multi_agent_framework.core.config import Settings
from multi_agent_framework.storage.base import Database


async def main() -> None:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    await db.open()
    try:
        await db.create_all()
        print(f"Schema created at {settings.database_url}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
