"""Run an agent in-process, and let agents call each other as tools.

Two multi-agent patterns share one runner, :func:`run_agent`:
- the agents the router selects for a turn (one or more, run in parallel), and
- peer "agent-as-tool" calls — an agent asks another for help mid-task via an
  auto-generated ``ask_<name>`` tool (e.g. ``ask_catalog_advisor``).

A peer call is *just a tool call*: ``ask_<name>`` re-enters :func:`run_agent` for that
agent and returns its answer. There is no delegation context, budget, or shared state —
the only guard is **depth** (``settings.max_delegation_depth``; 1 disables peer calls),
threaded as a plain argument so nested calls can't recurse without bound.

Everything is fail-soft: a bad run returns a short note, never an exception.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from multi_agent_framework.agents.factory import build_agent
from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.core.config import Settings
from multi_agent_framework.core.prompt_builder import build_messages, message_text

logger = logging.getLogger(__name__)

ToolProvider = Callable[[Sequence[str]], Sequence[Any]]


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
        return AgentRun(f"(cannot delegate: unknown agent '{name}')")

    defn = registry.get(name)
    tools = list(tool_provider(defn.tools))
    # Offer peer tools whenever we can still go one level deeper. Any agent can ask any peer.
    if depth + 1 < settings.max_delegation_depth:
        tools = tools + _peer_tools(registry, settings, tool_provider, depth + 1, exclude=name)

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


def _peer_tools(
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
