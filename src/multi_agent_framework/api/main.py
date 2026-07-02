from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from multi_agent_framework._platform import configure_async_runtime
from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.api.analytics_routes import router as analytics_router
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
    """Build the FastAPI app for a given agent registry and tool_provider."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = Settings.from_env()
        app.state.settings = settings
        app.state.registry = registry
        app.state.fallback_agent = fallback_agent

        db: Database | None = Database(settings.database_url)
        try:
            await db.open()
            await db.ping()
        except Exception as exc:  # noqa: BLE001 - boot must survive a missing backend
            logger.warning("Postgres unavailable at startup: %s", exc)
            await db.close()
            db = None
        app.state.db = db

        # Build the graph after the DB so the Auto-Memory layer can use it.
        # No DB (or flag off) -> build_graph leaves the memory nodes as no-ops.
        app.state.graph = build_graph(
            registry,
            settings,
            tool_provider,
            fallback_agent=fallback_agent,
            memory=db.memory if db is not None else None,
        )

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

    # CORS is read from the environment at build time: app.state.settings is only
    # populated once the lifespan startup runs, which is after middleware is added.
    # A lone "*" allows any origin (dev convenience) and disables credentials, since
    # the two cannot be combined per the CORS spec.
    origins = list(Settings.from_env().cors_allow_origins)
    allow_all = origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=not allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    app.include_router(analytics_router)
    return app
