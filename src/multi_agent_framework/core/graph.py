"""The conversation pipeline as a LangGraph.

State flows through one node per stage::

    load_memory -> route -> execute_agent -> evaluate -> save_memory -> END
                                  ^                |
                                  +---- revise ----+   (on a failing, retryable eval)

``route`` + ``execute_agent`` are Phase 2; ``load_memory`` / ``save_memory`` are the
Auto-Memory layer (Phase 3, Layer 2); ``evaluate`` is Phase 4: deterministic structural
checks plus a *sampled* LLM critic (per the agent's ``judge_sample_rate``). A failing,
retryable eval routes through ``revise`` (which feeds the critic's feedback back to the
agent) for one more attempt; otherwise the turn proceeds and is never blocked.

The graph is dependency-injected: :func:`build_graph` takes the agent ``registry``,
``settings``, a ``tool_provider`` (name -> callables), and an optional ``memory``
repository. Auto-memory is active only when a memory repo is present, the feature flag is
on, and the turn carries an ``owner_id`` — otherwise the memory nodes no-op.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from multi_agent_framework.agents.factory import build_agent
from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.agents.router import route_turn
from multi_agent_framework.core.config import Settings
from multi_agent_framework.core.prompt_builder import build_messages
from multi_agent_framework.core.state import ConversationState
from multi_agent_framework.evaluation.critic import critique
from multi_agent_framework.evaluation.structural import check_structural
from multi_agent_framework.memory.auto_memory import extract_and_upsert, load_hints

if TYPE_CHECKING:
    from multi_agent_framework.storage.repositories.memory import MemoryRepository

ToolProvider = Callable[[Sequence[str]], Sequence[Any]]


def build_graph(
    registry: AgentRegistry,
    settings: Settings,
    tool_provider: ToolProvider,
    *,
    fallback_agent: str | None = None,
    memory: "MemoryRepository | None" = None,
):
    """Compile and return the conversation graph wired to ``registry`` + ``settings``."""

    auto_memory_on = settings.enable_auto_memory and memory is not None

    async def load_memory(state: ConversationState) -> dict[str, Any]:
        """Layer 2 (read): surface the owner's stored memories as hints. Fail-silent."""
        owner_id = state.get("owner_id")
        if not auto_memory_on or not owner_id:
            return {"auto_memory_hints": []}
        return {"auto_memory_hints": await load_hints(memory, owner_id)}

    async def route(state: ConversationState) -> dict[str, Any]:
        """Pick the agent for this turn via the LLM router."""
        decision = await route_turn(state["messages"], registry, settings, fallback_agent=fallback_agent)
        return {
            "current_agent": decision["agent"],
            "routing_scores": {decision["agent"]: decision["confidence"]},
            "routing_reason": decision["reason"],
        }

    async def execute_agent(state: ConversationState) -> dict[str, Any]:
        """Build the selected agent from its definition + tools and run it on the conversation.

        Memory hints (if any) are injected ahead of the conversation as a synthetic
        ``<system-reminder>``. On a retry, the critic's feedback is appended so the agent
        revises. Does not mutate ``messages`` — the answer lives in ``agent_response`` — so
        the retry loop re-runs cleanly from the original turn.
        """
        defn = registry.get(state["current_agent"])
        agent = build_agent(defn, settings, tool_provider(defn.tools))

        hints = state.get("auto_memory_hints") or []
        messages = build_messages(state["messages"], {}, hints) if hints else list(state["messages"])

        feedback = state.get("eval_feedback")
        if feedback:
            previous = state.get("agent_response") or ""
            messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "Your previous answer was rejected by review.\n"
                        f"Previous answer: {previous}\n"
                        f"Reviewer feedback: {feedback}\n"
                        "Provide an improved answer to the original request."
                    ),
                }
            ]

        result = await agent.ainvoke({"messages": messages})
        result_messages = result["messages"]
        return {
            "agent_response": _text_of(result_messages[-1]),
            "tool_calls": _collect_tool_calls(result_messages),
        }

    async def evaluate(state: ConversationState) -> dict[str, Any]:
        """Phase 4: deterministic structural check, then a sampled LLM critic."""
        response = state.get("agent_response") or ""

        structural = check_structural(response)
        if not structural["pass"]:
            return {"eval_result": {"pass": False, "score": 0.0, "feedback": structural["reason"], "stage": "structural"}}

        agent = state.get("current_agent")
        defn = registry.get(agent) if agent and agent in registry else None
        if defn is None:
            return {"eval_result": {"pass": True, "score": 1.0, "feedback": "no rubric to judge against", "stage": "skipped"}}

        # Sample per the agent's risk weight; always judge on a retry (we got here by failing).
        if state.get("retry_count", 0) == 0 and random.random() >= defn.judge_sample_rate:
            return {"eval_result": {"pass": True, "score": None, "feedback": "not judged (sampled out)", "stage": "skipped"}}

        verdict = await critique(response, defn, settings, _last_user_message(state["messages"]))
        return {"eval_result": {**verdict, "stage": "critic"}}

    async def revise(state: ConversationState) -> dict[str, Any]:
        """Set up one retry: bump the counter and feed the critic's feedback to the agent."""
        result = state.get("eval_result") or {}
        return {"retry_count": state.get("retry_count", 0) + 1, "eval_feedback": result.get("feedback")}

    def should_retry(state: ConversationState) -> str:
        """Route a failing, retryable eval back through ``revise``; otherwise finish."""
        result = state.get("eval_result") or {}
        if result.get("pass") is False and state.get("retry_count", 0) < state.get("max_retries", 0):
            return "revise"
        return "save_memory"

    async def save_memory(state: ConversationState) -> dict[str, Any]:
        """Layer 2 (write): extract durable facts from the turn and upsert them. Fail-silent."""
        owner_id = state.get("owner_id")
        if not auto_memory_on or not owner_id:
            return {"memory_writes": []}
        user_message = _last_user_message(state["messages"])
        written = await extract_and_upsert(memory, settings, owner_id, user_message, state.get("agent_response") or "")
        return {"memory_writes": [{"extracted": written}]}

    builder = StateGraph(ConversationState)
    builder.add_node("load_memory", load_memory)
    builder.add_node("route", route)
    builder.add_node("execute_agent", execute_agent)
    builder.add_node("evaluate", evaluate)
    builder.add_node("revise", revise)
    builder.add_node("save_memory", save_memory)

    builder.add_edge(START, "load_memory")
    builder.add_edge("load_memory", "route")
    builder.add_edge("route", "execute_agent")
    builder.add_edge("execute_agent", "evaluate")
    builder.add_conditional_edges("evaluate", should_retry, {"revise": "revise", "save_memory": "save_memory"})
    builder.add_edge("revise", "execute_agent")
    builder.add_edge("save_memory", END)

    return builder.compile()


def build_initial_state(message: str, conversation_id: str | None = None, owner_id: str | None = None) -> ConversationState:
    """Build a fresh :class:`ConversationState` for one user message."""
    return ConversationState(
        messages=[{"role": "user", "content": message}],
        conversation_id=conversation_id or str(uuid.uuid4()),
        owner_id=owner_id,
        current_agent=None,
        routing_scores={},
        routing_reason=None,
        agent_response=None,
        tool_calls=[],
        eval_result=None,
        eval_feedback=None,
        retry_count=0,
        max_retries=2,
        session_summary=None,
        auto_memory_hints=[],
        memory_writes=[],
        metadata={},
    )


def _last_user_message(messages: Sequence[dict[str, Any]]) -> str:
    """The most recent user-authored message text (what the critic + memory read)."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


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
