"""The conversation pipeline as a linear LangGraph.

State flows through one node per stage::

    load_memory -> route -> execute_agent -> evaluate -> save_memory -> END

``route`` and ``execute_agent`` are real as of Phase 2: the router picks an agent
from the registry, and the factory builds + runs that agent with its tools. The
surrounding ``load_memory`` / ``evaluate`` / ``save_memory`` nodes are still stubs
(Phases 3-4); the topology stays the same as their bodies land.

The graph is dependency-injected: :func:`build_graph` takes the agent ``registry``,
``settings``, and a ``tool_provider`` (name -> callables), so the framework never
imports any specific demo. The composition root wires those in.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Any

from langgraph.graph import END, START, StateGraph

from multi_agent_framework.agents.factory import build_agent
from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.agents.router import route_turn
from multi_agent_framework.core.config import Settings
from multi_agent_framework.core.state import ConversationState

ToolProvider = Callable[[Sequence[str]], Sequence[Any]]


def build_graph(
    registry: AgentRegistry,
    settings: Settings,
    tool_provider: ToolProvider,
    *,
    fallback_agent: str | None = None,
):
    """Compile and return the conversation graph wired to ``registry`` + ``settings``."""

    async def load_memory(state: ConversationState) -> dict[str, Any]:
        """Load relevant memory for this turn (Phase 3). Stub: no hints yet."""
        return {"auto_memory_hints": []}

    async def route(state: ConversationState) -> dict[str, Any]:
        """Pick the agent for this turn via the LLM router."""
        decision = await route_turn(state["messages"], registry, settings, fallback_agent=fallback_agent)
        return {
            "current_agent": decision["agent"],
            "routing_scores": {decision["agent"]: decision["confidence"]},
            "routing_reason": decision["reason"],
        }

    async def execute_agent(state: ConversationState) -> dict[str, Any]:
        """Build the selected agent from its definition + tools and run it on the conversation."""
        defn = registry.get(state["current_agent"])
        tools = tool_provider(defn.tools)
        agent = build_agent(defn, settings, tools)
        result = await agent.ainvoke({"messages": state["messages"]})

        result_messages = result["messages"]
        response = _text_of(result_messages[-1])
        return {
            "agent_response": response,
            "messages": state["messages"] + [{"role": "assistant", "content": response}],
            "tool_calls": _collect_tool_calls(result_messages),
        }

    async def evaluate(state: ConversationState) -> dict[str, Any]:
        """Score the response (Phase 4). Stub: always passes."""
        return {"eval_result": {"pass": True, "score": 1.0, "feedback": "stub evaluator until Phase 4"}}

    async def save_memory(state: ConversationState) -> dict[str, Any]:
        """Persist memory write-backs (Phase 3). Stub: no-op."""
        return {"memory_writes": []}

    builder = StateGraph(ConversationState)
    builder.add_node("load_memory", load_memory)
    builder.add_node("route", route)
    builder.add_node("execute_agent", execute_agent)
    builder.add_node("evaluate", evaluate)
    builder.add_node("save_memory", save_memory)

    builder.add_edge(START, "load_memory")
    builder.add_edge("load_memory", "route")
    builder.add_edge("route", "execute_agent")
    builder.add_edge("execute_agent", "evaluate")
    builder.add_edge("evaluate", "save_memory")
    builder.add_edge("save_memory", END)

    return builder.compile()


def build_initial_state(message: str, conversation_id: str | None = None) -> ConversationState:
    """Build a fresh :class:`ConversationState` for one user message."""
    return ConversationState(
        messages=[{"role": "user", "content": message}],
        conversation_id=conversation_id or str(uuid.uuid4()),
        current_agent=None,
        routing_scores={},
        routing_reason=None,
        agent_response=None,
        tool_calls=[],
        eval_result=None,
        retry_count=0,
        max_retries=2,
        session_summary=None,
        auto_memory_hints=[],
        memory_writes=[],
        metadata={},
    )


def _text_of(message: Any) -> str:
    """Coerce a model message's content to plain text (handles string or content-block list)."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts).strip()
    return str(content)


def _collect_tool_calls(messages: Sequence[Any]) -> list[dict[str, Any]]:
    """Pull the tool calls the agent made (for telemetry); empty if it answered directly."""
    calls: list[dict[str, Any]] = []
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            calls.append({"name": call.get("name"), "args": call.get("args", {})})
    return calls
