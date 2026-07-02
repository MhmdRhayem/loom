from __future__ import annotations

import asyncio

from multi_agent_framework._platform import configure_async_runtime
from multi_agent_framework.core.config import Settings
from multi_agent_framework.storage.base import Database

configure_async_runtime()


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
