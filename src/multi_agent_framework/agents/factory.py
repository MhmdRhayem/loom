"""Build a runnable agent from a declarative ``AgentDefinition``, and run it.

The registry is the catalog of definitions; this factory turns one definition
into a live ``create_agent`` instance (:func:`build_agent`) and runs it in-process
(:func:`run_agent`). Tier names (fast/standard/deep) resolve to provider model IDs
via ``core/config.py``, so the YAML roster stays provider-agnostic.

Agents can also call each other. With depth to spare, :func:`run_agent` hands an
agent an ``ask_<name>`` tool for every peer; calling one is *just a tool call* that
re-enters :func:`run_agent` one level deeper. There is no shared context, budget, or
approval — the only guard is **depth** (``settings.max_delegation_depth``; 1 disables
peer calls), threaded as a plain argument so nested calls can't recurse without bound.
Everything is fail-soft: a bad run returns a short note, never an exception.
"""

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
from multi_agent_framework.core.prompt_builder import build_messages, build_system_prompt, message_text

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
    """Build a runnable agent for ``defn``. With no tools it is still a
    conversational agent; with tools, ``create_agent`` runs the tool loop."""
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
) -> AgentRun:
    """Run agent ``name`` on ``query`` in-process and return its answer. Guarded by ``depth``, fail-soft.

    With depth to spare, the agent is also handed an ``ask_<peer>`` tool for every other
    agent; calling one is a plain tool call that re-enters ``run_agent`` one level deeper.
    Every router-selected agent and every peer call comes through here.
    """
    if name not in registry:
        return AgentRun(f"(cannot run: unknown agent '{name}')")

    defn = registry.get(name)
    tools = list(tool_provider(defn.tools))
    # Offer peer tools whenever we can still go one level deeper. Any agent can ask any peer.
    if depth + 1 < settings.max_delegation_depth:
        tools = tools + _ask_peer_tools(registry, settings, tool_provider, depth + 1, exclude=name)

    agent = build_agent(defn, settings, tools)
    base = [{"role": "user", "content": query}]
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
    """An ``ask_<agent>`` tool for every agent except ``exclude`` — the agent-to-agent call surface."""
    return [_ask_tool(name, registry, settings, tool_provider, depth) for name in registry.names() if name != exclude]


def _ask_tool(
    name: str,
    registry: AgentRegistry,
    settings: Settings,
    tool_provider: ToolProvider,
    depth: int,
) -> Callable[..., Any]:
    """Build the ``ask_<name>`` tool: calling it runs ``name`` one level deeper and returns its answer."""
    description = registry.get(name).description.strip()

    async def ask(query: str) -> str:
        run = await run_agent(name, query, registry=registry, settings=settings, tool_provider=tool_provider, depth=depth)
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
