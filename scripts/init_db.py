from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Run as a flat app (no editable install): ensure the repo root is importable when
# this script is launched by path, e.g. `python scripts/init_db.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend._platform import configure_async_runtime  # noqa: E402
from backend.core.config import Settings  # noqa: E402
from backend.storage.base import Database  # noqa: E402

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
