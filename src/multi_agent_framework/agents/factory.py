from __future__ import annotations

import logging
from collections.abc import Collection
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

# Tool names -> callables, with the turn's owner already bound by the caller.
# (core.graph.ToolProvider is the owner-aware two-argument version; the graph's
# execute_agents adapts one into the other.)
ScopedToolProvider = Callable[[Sequence[str]], Sequence[Any]]


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
    """The result of running one agent: its answer, the tools it called, tokens spent."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0


async def run_agent(
    name: str,
    query: str,
    *,
    registry: AgentRegistry,
    settings: Settings,
    tool_provider: ScopedToolProvider,
    depth: int = 0,
    hints: list[str] | None = None,
    history: list[dict[str, Any]] | None = None,
    allowed_agents: Collection[str] | None = None,
) -> AgentRun:
    """Run agent `name` on `query` and return its answer. Bounded by depth; never raises.

    history is the conversation before this query (router-picked runs get it; peer calls
    stay self-contained). While there's depth left, the agent also gets an ask_<peer> tool
    for each other agent; calling one re-enters run_agent one level deeper. Every router
    pick and every peer call goes through here. allowed_agents (None = no restriction)
    limits both this run and which peers get an ask_<peer> tool, at every depth.
    """
    if name not in registry:
        return AgentRun(f"(cannot run: unknown agent '{name}')")
    if allowed_agents is not None and name not in allowed_agents:
        return AgentRun(f"(cannot run: agent '{name}' is not available)")

    defn = registry.get(name)
    tools = list(tool_provider(defn.tools))
    # Offer peer tools whenever we can still go one level deeper. Any allowed agent
    # can ask any allowed peer; hidden agents never get an ask_<peer> tool at all.
    # Peer runs report back through sub_runs so their tokens/tool calls are counted.
    sub_runs: list[AgentRun] = []
    if depth + 1 < settings.max_delegation_depth:
        tools = tools + _ask_peer_tools(
            registry,
            settings,
            tool_provider,
            depth + 1,
            sub_runs,
            exclude=name,
            allowed_agents=allowed_agents,
        )

    agent = build_agent(defn, settings, tools)
    base = [*(history or []), {"role": "user", "content": query}]
    messages = build_messages(base, hints) if hints else base
    try:
        result = await agent.ainvoke({"messages": messages})
        msgs = result["messages"]
        tool_calls = _tool_calls(msgs) + [c for r in sub_runs for c in r.tool_calls]
        tokens = _tokens_used(msgs) + sum(r.tokens for r in sub_runs)
        return AgentRun(message_text(msgs[-1]), tool_calls, tokens)
    except Exception:  # noqa: BLE001 - a failed sub-run must not crash the turn
        logger.warning("run of agent %s failed", name, exc_info=True)
        return AgentRun(f"({name} could not complete the request)")


def _ask_peer_tools(
    registry: AgentRegistry,
    settings: Settings,
    tool_provider: ScopedToolProvider,
    depth: int,
    sub_runs: list[AgentRun],
    exclude: str | None = None,
    allowed_agents: Collection[str] | None = None,
) -> list[Callable[..., Any]]:
    """One ask_<agent> tool per allowed agent except `exclude`: the agent-to-agent call surface."""
    return [
        _ask_tool(name, registry, settings, tool_provider, depth, sub_runs, allowed_agents)
        for name in registry.names()
        if name != exclude and (allowed_agents is None or name in allowed_agents)
    ]


def _ask_tool(
    name: str,
    registry: AgentRegistry,
    settings: Settings,
    tool_provider: ScopedToolProvider,
    depth: int,
    sub_runs: list[AgentRun],
    allowed_agents: Collection[str] | None = None,
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
            allowed_agents=allowed_agents,
        )
        # Surface the nested run to the caller so its cost isn't silently dropped.
        sub_runs.append(run)
        return run.text

    ask.__name__ = f"ask_{name}"
    ask.__doc__ = f"Ask the {name} agent for help and get its answer. {name} handles: {description} Pass a clear, self-contained question."
    return ask


def _tokens_used(messages: Sequence[Any]) -> int:
    """Total tokens across every model call in the run (LangChain usage_metadata)."""
    total = 0
    for message in messages:
        usage = getattr(message, "usage_metadata", None) or {}
        total += int(usage.get("total_tokens", 0) or 0)
    return total


def _tool_calls(messages: Sequence[Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            calls.append({"name": call.get("name"), "args": call.get("args", {})})
    return calls
