from __future__ import annotations

from pathlib import Path

from backend.agents.registry import AgentRegistry
from backend.api.main import create_app

from .auth import auth_router, require_admin, resolve_identity
from .shop_routes import shop_router
from .tools import get_tools

# Ablation control: the same application, wired to a one-agent roster instead of the
# eight specialists. Routing still runs (the router simply has one choice), so the
# pipeline, tools, memory, evaluation and persistence are all identical and the only
# variable is the roster. That is what makes the comparison against app.py meaningful.
#
# Run it the same way as the real app:
#   uvicorn --env-file .env demo.shopping_assistant.app_monolith:app
#
# No agent_visibility hook: there is no merchant agent to hide, and the merchant
# routers are left off for the same reason.
DEFINITIONS_DIR = Path(__file__).parent / "definitions_monolith"
FALLBACK_AGENT = "shop_assistant"

registry = AgentRegistry.from_directory(DEFINITIONS_DIR)
app = create_app(
    registry,
    get_tools,
    fallback_agent=FALLBACK_AGENT,
    identity_resolver=resolve_identity,
    analytics_guard=require_admin,
)

app.include_router(auth_router)
app.include_router(shop_router)
