from __future__ import annotations

from pathlib import Path

from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.api.main import create_app

from .shop_routes import shop_router
from .tools import get_tools

# Agent definitions live next to this file; resolve regardless of the working dir.
DEFINITIONS_DIR = Path(__file__).parent / "definitions"

# Where the router sends low-confidence / no-fit turns. The concierge is the roster's
# designated catch-all + clarifier; the framework itself stays unaware of this name.
FALLBACK_AGENT = "support_concierge"

registry = AgentRegistry.from_directory(DEFINITIONS_DIR)
app = create_app(registry, get_tools, fallback_agent=FALLBACK_AGENT)

# The storefront read endpoints (/shop/*) are demo-specific, so they're mounted here
# rather than in the framework. CORS is applied inside create_app and wraps the whole
# app, so routers added afterward are covered too.
app.include_router(shop_router)
