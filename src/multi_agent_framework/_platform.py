"""Platform setup that must run before any asyncio event loop is created."""

from __future__ import annotations

import asyncio
import sys


def configure_async_runtime() -> None:
    """Force the selector event loop on Windows.

    Async psycopg cannot run on Windows' default ``ProactorEventLoop`` (it
    raises ``InterfaceError``), so switch the policy to ``SelectorEventLoop``.
    No-op on other platforms. Must be called before the loop is created — i.e.
    at module import for the server, or before ``asyncio.run`` in a script.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
