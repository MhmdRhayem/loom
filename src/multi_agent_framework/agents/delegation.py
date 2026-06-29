"""Agent-to-agent delegation: let one agent call another, in-process, with guards.

This powers both multi-agent patterns:
- the coordinator's workers (top-down), and
- peer "agent-as-tool" calls (an agent asks another for help mid-task).

Both go through :func:`run_agent`, which shares one :class:`DelegationContext` per turn.
The context enforces the limits so delegation can never loop or fan out without bound:

- **depth** — how deep calls may nest (``settings.max_delegation_depth``; 1 disables delegation).
- **budget** — the max total agent runs in a turn (``settings.delegation_budget``); a hard stop.
- **approval** — high-risk agents (``requires_approval``) are reviewed before they run.

Everything is fail-soft: a bad delegated run returns a short note, never an exception.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from multi_agent_framework.agents.factory import build_agent
from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.core.config import Settings
from multi_agent_framework.core.prompt_builder import build_messages, message_text

logger = logging.getLogger(__name__)

ToolProvider = Callable[[Sequence[str]], Sequence[Any]]
# Reviews a high-risk action: async (agent_name, query) -> {"approved": bool, "reason": str}.
Approver = Callable[[str, str], Awaitable[dict[str, Any]]]


@dataclass
class AgentRun:
    """The result of running one agent: its text answer and the tools it called."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DelegationContext:
    """Shared per-turn state and limits for a chain of agent-to-agent calls."""

    registry: AgentRegistry
    settings: Settings
    tool_provider: ToolProvider
    approver: Approver | None = None
    spawned: list[str] = field(default_factory=list)        # agents run this turn (telemetry)
    approvals: list[dict[str, Any]] = field(default_factory=list)  # approval decisions made
    calls: int = 0                                          # runs used (against the budget)

    def budget_left(self) -> int:
        return self.settings.delegation_budget - self.calls


async def run_agent(name: str, query: str, ctx: DelegationContext, depth: int = 0, hints: list[str] | None = None) -> AgentRun:
    """Run agent ``name`` on ``query`` in-process and return its answer. Guarded + fail-soft."""
    if name not in ctx.registry:
        return AgentRun(f"(cannot delegate: unknown agent '{name}')")
    if ctx.budget_left() <= 0:
        return AgentRun(f"(delegation budget reached; '{name}' was not run)")

    ctx.calls += 1
    ctx.spawned.append(name)
    defn = ctx.registry.get(name)

    if defn.requires_approval and ctx.approver is not None:
        verdict = await ctx.approver(name, query)
        ctx.approvals.append({"agent": name, **verdict})
        if not verdict.get("approved", False):
            return AgentRun(f"({name} needs approval and it was declined: {verdict.get('reason', '')})")

    tools = list(ctx.tool_provider(defn.tools))
    # Hand it peer-delegation tools only if we can still go deeper and have budget to spend.
    if depth + 1 < ctx.settings.max_delegation_depth and ctx.budget_left() > 0:
        tools = tools + delegation_tools(ctx, depth + 1, exclude=name)

    agent = build_agent(defn, ctx.settings, tools)
    base = [{"role": "user", "content": query}]
    messages = build_messages(base, {}, hints) if hints else base
    try:
        result = await agent.ainvoke({"messages": messages})
        msgs = result["messages"]
        return AgentRun(message_text(msgs[-1]), _tool_calls(msgs))
    except Exception:  # noqa: BLE001 - a failed sub-run must not crash the turn
        logger.warning("delegated run of %s failed", name, exc_info=True)
        return AgentRun(f"({name} could not complete the request)")


def delegation_tools(ctx: DelegationContext, depth: int, exclude: str | None = None) -> list[Callable[..., Any]]:
    """Build an ``ask_<agent>`` tool for every agent except ``exclude``, each guarded by ``ctx``."""
    return [_ask_tool(name, ctx, depth) for name in ctx.registry.names() if name != exclude]


def _ask_tool(name: str, ctx: DelegationContext, depth: int) -> Callable[..., Any]:
    description = ctx.registry.get(name).description.strip()

    async def ask(query: str) -> str:
        return (await run_agent(name, query, ctx, depth=depth)).text

    ask.__name__ = f"ask_{name}"
    ask.__doc__ = f"Ask the {name} agent for help and get its answer. {name} handles: {description} Pass a clear, self-contained question."
    return ask


def _tool_calls(messages: Sequence[Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            calls.append({"name": call.get("name"), "args": call.get("args", {})})
    return calls
