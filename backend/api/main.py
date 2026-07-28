from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend._platform import configure_async_runtime
from backend.agents.registry import AgentRegistry
from backend.api.analytics_routes import router as analytics_router
from backend.api.routes import router
from backend.core.config import Settings
from backend.core.graph import ToolProvider, build_graph
from backend.storage.base import Database
from backend.storage.redis_store import RedisStore

# Must run before uvicorn creates the event loop (async psycopg needs the
# selector loop on Windows).
configure_async_runtime()

logger = logging.getLogger(__name__)

# Turns an incoming request into an owner id (e.g. by validating a Bearer token).
# Raise fastapi.HTTPException(401) to reject the request. The framework never sees
# the auth mechanism itself — the composition root owns it.
IdentityResolver = Callable[[Request], Awaitable[str]]

# The agent names this request may route to / delegate to, or None for no
# restriction. Same ownership rule as the identity resolver: the composition root
# decides who sees which agents; the framework only enforces the set.
AgentVisibility = Callable[[Request], Awaitable[Collection[str] | None]]

# Guards the aggregate /analytics endpoints. Raise fastapi.HTTPException (401/403)
# to reject — e.g. only an operator/admin account may read cross-user analytics.
AnalyticsGuard = Callable[[Request], Awaitable[None]]


def create_app(
    registry: AgentRegistry,
    tool_provider: ToolProvider,
    *,
    fallback_agent: str | None = None,
    identity_resolver: IdentityResolver | None = None,
    agent_visibility: AgentVisibility | None = None,
    analytics_guard: AnalyticsGuard | None = None,
) -> FastAPI:
    """Build the FastAPI app for a given agent registry and tool_provider.

    With an identity_resolver, chat and conversation reads are scoped to the
    resolved owner instead of trusting client-supplied owner ids. agent_visibility
    limits which agents a request can see and use; analytics_guard protects the
    /analytics endpoints. All three default to None (open).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = Settings.from_env()
        app.state.settings = settings
        app.state.registry = registry
        app.state.fallback_agent = fallback_agent
        app.state.identity_resolver = identity_resolver
        app.state.agent_visibility = agent_visibility
        app.state.analytics_guard = analytics_guard

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
        except Exception as exc:  # noqa: BLE001 - Redis is optional; /health reports it
            logger.warning("Redis unavailable at startup: %s", exc)
            redis_store = None
        app.state.redis = redis_store

        # Built after both stores so the memory layer and the routing cache can use
        # them; either being absent just leaves that part of the pipeline a no-op.
        app.state.graph = build_graph(
            registry,
            settings,
            tool_provider,
            fallback_agent=fallback_agent,
            memory=db.memory if db is not None else None,
            cache=redis_store.client if redis_store is not None else None,
        )

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
