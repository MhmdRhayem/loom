"""Composition root for the shopping-assistant demo.

This is where the e-commerce demo is assembled: the YAML agent roster and the mock
tools are wired into the provider-agnostic framework, producing a FastAPI ``app``.
Nothing under ``src/multi_agent_framework`` knows about e-commerce — that knowledge
lives here, so the framework stays demo-agnostic and reusable.

Run from the repository root (``multi-agent-framework/``)::

    python -m uvicorn demo.shopping_assistant.app:app --reload
"""

from __future__ import annotations

from pathlib import Path

from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.api.main import create_app

from .tools import get_tools

# Agent definitions live next to this file; resolve regardless of the working dir.
DEFINITIONS_DIR = Path(__file__).parent / "definitions"

# Where the router sends low-confidence / no-fit turns. The concierge is the roster's
# designated catch-all + clarifier; the framework itself stays unaware of this name.
FALLBACK_AGENT = "support_concierge"

registry = AgentRegistry.from_directory(DEFINITIONS_DIR)
app = create_app(registry, get_tools, fallback_agent=FALLBACK_AGENT)
