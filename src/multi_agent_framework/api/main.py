"""FastAPI application entry point.

The lifespan handler builds the conversation graph and opens the Postgres and
Redis connections on startup, closing them on shutdown. Storage init is
fail-soft: if a backend is unreachable the app still boots (the graph nodes are
placeholders that don't need it yet), and ``/health`` reports the degraded
component. Run locally with::

    uvicorn multi_agent_framework.api.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from multi_agent_framework._platform import configure_async_runtime
from multi_agent_framework.api.routes import router
from multi_agent_framework.core.config import Settings
from multi_agent_framework.core.graph import build_graph
from multi_agent_framework.storage.base import Database
from multi_agent_framework.storage.redis_store import RedisStore

# Must run before uvicorn creates the event loop (async psycopg needs the
# selector loop on Windows).
configure_async_runtime()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.settings = settings
    app.state.graph = build_graph()

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


def create_app() -> FastAPI:
    app = FastAPI(title="Multi-Agent Framework", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
