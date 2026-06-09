"""Minimal LangGraph wiring for the conversation pipeline.

This is the Task 1.5 skeleton: a linear ``StateGraph`` over
:class:`ConversationState` with one placeholder node per stage::

    load_memory -> router -> execute_agent -> evaluate -> save_memory -> END

Each node here is intentionally a stub that only annotates the state so the
end-to-end flow (and the FastAPI ``/chat`` endpoint) can be exercised before
the real router, agents, evaluation, and memory layers land in later phases.
Replace the node bodies task by task; the graph topology stays the same.
"""
from __future__ import annotations

import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from multi_agent_framework.core.state import ConversationState


def _last_user_message(state: ConversationState) -> str:
    for message in reversed(state["messages"]):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def load_memory(state: ConversationState) -> dict[str, Any]:
    """Layer 1/2 memory load (Phase 3). Stub: no hints yet."""
    return {"auto_memory_hints": []}


def router(state: ConversationState) -> dict[str, Any]:
    """Dynamic routing (Phase 2). Stub: always pick the echo agent."""
    return {
        "current_agent": "echo_agent",
        "routing_scores": {"echo_agent": 1.0},
        "routing_reason": "placeholder router: single echo agent until Phase 2",
    }


def execute_agent(state: ConversationState) -> dict[str, Any]:
    """Agent execution (Phase 2). Stub: echo the last user message."""
    user_text = _last_user_message(state)
    response = f"[echo] {user_text}" if user_text else "[echo] (no user message)"
    return {
        "agent_response": response,
        "messages": state["messages"] + [{"role": "assistant", "content": response}],
    }


def evaluate(state: ConversationState) -> dict[str, Any]:
    """Self-evaluation (Phase 4). Stub: always passes."""
    return {
        "eval_result": {
            "pass": True,
            "score": 1.0,
            "feedback": "placeholder evaluator: always passes until Phase 4",
        }
    }


def save_memory(state: ConversationState) -> dict[str, Any]:
    """Memory write-back / persistence (Phase 3). Stub: no-op."""
    return {"memory_writes": []}


def build_graph():
    """Compile and return the conversation graph."""
    builder = StateGraph(ConversationState)

    builder.add_node("load_memory", load_memory)
    builder.add_node("router", router)
    builder.add_node("execute_agent", execute_agent)
    builder.add_node("evaluate", evaluate)
    builder.add_node("save_memory", save_memory)

    builder.add_edge(START, "load_memory")
    builder.add_edge("load_memory", "router")
    builder.add_edge("router", "execute_agent")
    builder.add_edge("execute_agent", "evaluate")
    builder.add_edge("evaluate", "save_memory")
    builder.add_edge("save_memory", END)

    return builder.compile()


def make_initial_state(message: str, conversation_id: str | None = None) -> ConversationState:
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
        execution_model="fork",
        spawned_agents=[],
        coordinator_mode=False,
        approval_queue=[],
        metadata={},
    )
