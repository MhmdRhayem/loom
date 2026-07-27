from __future__ import annotations

import asyncio
import sys


def configure_async_runtime() -> None:
    """Force the selector event loop on Windows.

    Async psycopg can't run on Windows' default ProactorEventLoop (it raises
    InterfaceError), so switch to the SelectorEventLoop policy. No-op elsewhere.

    This covers code that creates its own loop (scripts/init_db.py, tests). It does
    NOT cover uvicorn, which passes an explicit loop factory to asyncio.run and so
    never consults the policy: use scripts/serve.py, which hands uvicorn the right
    factory. A bare `uvicorn` command on Windows boots with Postgres unreachable.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
