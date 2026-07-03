from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from multi_agent_framework.agents.registry import AgentDefinition, AgentRegistry
from multi_agent_framework.core.config import Settings
from multi_agent_framework.core.prompt_builder import (
    build_messages,
    build_system_prompt,
    message_text,
)

logger = logging.getLogger(__name__)

ToolProvider = Callable[[Sequence[str]], Sequence[Any]]


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
    """Build a runnable agent for defn. With no tools it's just conversational;
    with tools, create_agent runs the tool loop."""
    system_prompt = build_system_prompt(
        {
            "name": defn.name,
            "description": defn.description,
            "capabilities": list(defn.capabilities),
        }
    )
    return create_agent(
        resolve_model(defn, settings),
        tools=list(tools or []),
        system_prompt=system_prompt,
        name=defn.name,
    )


@dataclass
class AgentRun:
    """The result of running one agent: its text answer and the tools it called."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


async def run_agent(
    name: str,
    query: str,
    *,
    registry: AgentRegistry,
    settings: Settings,
    tool_provider: ToolProvider,
    depth: int = 0,
    hints: list[str] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> AgentRun:
    """Run agent `name` on `query` and return its answer. Bounded by depth; never raises.

    history is the conversation before this query (router-picked runs get it; peer calls
    stay self-contained). While there's depth left, the agent also gets an ask_<peer> tool
    for each other agent; calling one re-enters run_agent one level deeper. Every router
    pick and every peer call goes through here.
    """
    if name not in registry:
        return AgentRun(f"(cannot run: unknown agent '{name}')")

    defn = registry.get(name)
    tools = list(tool_provider(defn.tools))
    # Offer peer tools whenever we can still go one level deeper. Any agent can ask any peer.
    if depth + 1 < settings.max_delegation_depth:
        tools = tools + _ask_peer_tools(registry, settings, tool_provider, depth + 1, exclude=name)

    agent = build_agent(defn, settings, tools)
    base = [*(history or []), {"role": "user", "content": query}]
    messages = build_messages(base, {}, hints) if hints else base
    try:
        result = await agent.ainvoke({"messages": messages})
        msgs = result["messages"]
        return AgentRun(message_text(msgs[-1]), _tool_calls(msgs))
    except Exception:  # noqa: BLE001 - a failed sub-run must not crash the turn
        logger.warning("run of agent %s failed", name, exc_info=True)
        return AgentRun(f"({name} could not complete the request)")


def _ask_peer_tools(
    registry: AgentRegistry,
    settings: Settings,
    tool_provider: ToolProvider,
    depth: int,
    exclude: str | None = None,
) -> list[Callable[..., Any]]:
    """One ask_<agent> tool per agent except `exclude`: the agent-to-agent call surface."""
    return [
        _ask_tool(name, registry, settings, tool_provider, depth)
        for name in registry.names()
        if name != exclude
    ]


def _ask_tool(
    name: str,
    registry: AgentRegistry,
    settings: Settings,
    tool_provider: ToolProvider,
    depth: int,
) -> Callable[..., Any]:
    """Build the ask_<name> tool: it runs `name` one level deeper and returns its answer."""
    description = registry.get(name).description.strip()

    async def ask(query: str) -> str:
        run = await run_agent(
            name,
            query,
            registry=registry,
            settings=settings,
            tool_provider=tool_provider,
            depth=depth,
        )
        return run.text

    ask.__name__ = f"ask_{name}"
    ask.__doc__ = f"Ask the {name} agent for help and get its answer. {name} handles: {description} Pass a clear, self-contained question."
    return ask


def _tool_calls(messages: Sequence[Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            calls.append({"name": call.get("name"), "args": call.get("args", {})})
    return calls
