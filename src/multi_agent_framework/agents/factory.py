"""Build a runnable agent from a declarative ``AgentDefinition``.

The registry is the catalog of definitions; this factory turns one definition
into a live ``create_agent`` instance. Tier names (fast/standard/deep) resolve to
provider model IDs via ``core/config.py``, so the YAML roster stays
provider-agnostic.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from multi_agent_framework.agents.registry import AgentDefinition
from multi_agent_framework.core.config import Settings
from multi_agent_framework.core.prompt_builder import build_system_prompt


def resolve_model(defn: AgentDefinition, settings: Settings) -> BaseChatModel:
    """Resolve the agent's tier to a chat model for the active provider."""
    return init_chat_model(
        settings.model_id_for_tier(defn.model),
        model_provider=settings.default_provider,
        max_tokens=defn.max_tokens,
    )


def build_agent(
    defn: AgentDefinition,
    settings: Settings,
    tools: Sequence[BaseTool | Callable[..., Any]] | None = None,
):
    """Build a runnable agent for ``defn``. Tools arrive in Task 2.4; an empty
    roster still yields a conversational agent."""
    system_prompt, _ = build_system_prompt(
        {
            "name": defn.name,
            "description": defn.description,
            "capabilities": list(defn.capabilities),
            "tools": list(defn.tools),
        }
    )
    return create_agent(
        resolve_model(defn, settings),
        tools=list(tools or []),
        system_prompt=system_prompt,
        name=defn.name,
    )
