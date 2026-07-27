"""Run the API on an event loop async psycopg can actually use.

Why this exists rather than a bare `uvicorn ...` command: uvicorn chooses
ProactorEventLoop on Windows unless it is supervising a subprocess, and async psycopg
raises InterfaceError on Proactor. So `uvicorn --reload` reaches Postgres (--reload sets
use_subprocess, which flips uvicorn's own factory to the selector loop) while a plain
`uvicorn` silently does not: the app boots, logs one warning, and serves every request
with persistence, memory, learning and analytics disabled.

Setting the loop *policy* does not fix it either, because uvicorn passes an explicit
loop factory to asyncio.run and never consults the policy. The factory has to be handed
to uvicorn directly, which is what this does.

    python scripts/serve.py                                  # the assistant
    python scripts/serve.py --app demo.shopping_assistant.app_monolith:app
    python scripts/serve.py --port 8001 --env-file .env.other
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn

# Running a file puts scripts/ on sys.path, not the repo root, and this is a flat app:
# `backend` and `demo` are imported from the root, so put the root back on the path.
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_APP = "demo.shopping_assistant.app:app"


def _loop_factory() -> asyncio.AbstractEventLoop:
    """A selector loop on Windows, the platform default everywhere else."""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Loom API.")
    parser.add_argument("--app", default=DEFAULT_APP, help=f"ASGI target (default {DEFAULT_APP})")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    server = uvicorn.Server(
        uvicorn.Config(
            args.app,
            host=args.host,
            port=args.port,
            env_file=args.env_file,
            log_level=args.log_level,
            loop=_loop_factory,
        )
    )
    server.run()


if __name__ == "__main__":
    main()
