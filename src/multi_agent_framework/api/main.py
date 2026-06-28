"""FastAPI application factory.

``create_app`` is the framework's entry point. It is demo-agnostic: the caller (a
composition root such as ``demo/shopping_assistant/app.py``) passes the agent
``registry`` and a ``tool_provider``, and this module wires them into the graph.

The lifespan handler builds the conversation graph and opens the Postgres and Redis
connections on startup, closing them on shutdown. Storage init is fail-soft: if a
backend is unreachable the app still boots and ``/health`` reports the degraded
component. Run the demo from the repo root with::

    python -m uvicorn demo.shopping_assistant.app:app --reload
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from multi_agent_framework._platform import configure_async_runtime
from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.api.routes import router
from multi_agent_framework.core.config import Settings
from multi_agent_framework.core.graph import build_graph
from multi_agent_framework.storage.base import Database
from multi_agent_framework.storage.redis_store import RedisStore

# Must run before uvicorn creates the event loop (async psycopg needs the
# selector loop on Windows).
configure_async_runtime()

logger = logging.getLogger(__name__)

ToolProvider = Callable[[Sequence[str]], Sequence[Any]]


def create_app(
    registry: AgentRegistry,
    tool_provider: ToolProvider,
    *,
    fallback_agent: str | None = None,
) -> FastAPI:
    """Build the FastAPI app for a given agent ``registry`` and ``tool_provider``."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = Settings.from_env()
        app.state.settings = settings
        app.state.graph = build_graph(
            registry, settings, tool_provider, fallback_agent=fallback_agent
        )

        db: Database | None = Database(settings.database_url)
        try:
            await db.open()
            await db.ping()
        except Exception as exc:  # noqa: BLE001 - boot must survive a missing backend
            logger.warning("Postgres unavailable at startup: %s", exc)
            await db.close()
            db = None
        app.state.db = db

        redis_store: RedisStore | None = RedisStore(settings.redis_url)
        try:
            await redis_store.open()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis unavailable at startup: %s", exc)
            redis_store = None
        app.state.redis = redis_store

        try:
            yield
        finally:
            if db is not None:
                await db.close()
            if redis_store is not None:
                await redis_store.close()

    app = FastAPI(title="Multi-Agent Framework", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app
