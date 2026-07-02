from __future__ import annotations

import asyncio
import sys


def configure_async_runtime() -> None:
    """Force the selector event loop on Windows.

    Async psycopg can't run on Windows' default ProactorEventLoop (it raises
    InterfaceError), so switch to the SelectorEventLoop policy. No-op elsewhere.
    Call this before the loop is created: at import for the server, or before
    asyncio.run in a script.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
