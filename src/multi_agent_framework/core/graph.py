"""The conversation pipeline as a LangGraph.

    load_memory → route → (execute_agent | coordinate) → evaluate → save_memory → END
                               ^                |
                               +----- revise ---+   (failing, retryable single-agent eval)

``route`` picks one agent, or flags the turn multi-part → ``coordinate`` (decompose → run
agents in parallel → synthesize). Any agent can also delegate to a peer mid-task; every
agent run — single, worker, or peer — goes through the guarded ``run_agent``, sharing the
turn's depth/budget/approval limits. ``evaluate`` is a structural check + a sampled LLM
critic; a failing single-agent turn retries once with feedback. Memory load/save wrap it.

Dependency-injected: build_graph(registry, settings, tool_provider, fallback_agent, memory).
"""
from __future__ import annotations

import random
import uuid
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from multi_agent_framework.agents.coordinator import coordinate as run_coordinator
from multi_agent_framework.agents.coordinator import review_action
from multi_agent_framework.agents.delegation import DelegationContext, run_agent
from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.agents.router import route_turn
from multi_agent_framework.core.config import Settings
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

    auto_memory_on = settings.enable_memory and memory is not None

    def _delegation_context() -> DelegationContext:
        """A fresh per-turn context carrying the delegation limits + the approval guardrail."""
        return DelegationContext(
            registry=registry,
            settings=settings,
            tool_provider=tool_provider,
            approver=lambda name, query: review_action(name, query, settings),
        )

    async def load_memory(state: ConversationState) -> dict[str, Any]:
        """Layer 2 (read): surface the owner's stored memories as hints. Fail-silent."""
        owner_id = state.get("owner_id")
        if not auto_memory_on or not owner_id:
            return {"auto_memory_hints": []}
        return {"auto_memory_hints": await load_hints(memory, owner_id)}

    async def route(state: ConversationState) -> dict[str, Any]:
        """Pick the agent via the LLM router; set the query category."""
        decision = await route_turn(state["messages"], registry, settings, fallback_agent=fallback_agent)
        agent = decision["agent"]
        category = decision.get("category") or "general"
        reason = decision["reason"]
        multipart = settings.enable_coordinator and bool(decision.get("multipart"))
        return {
            "current_agent": agent,
            "routing_scores": {agent: decision["confidence"]},
            "routing_reason": reason,
            "coordinator_mode": multipart,
            "query_category": category,
        }

    async def execute_agent(state: ConversationState) -> dict[str, Any]:
        """Run the routed agent (it may delegate to peers). Retries reuse this node with feedback."""
        ctx = _delegation_context()
        query = _last_user_message(state["messages"])
        feedback = state.get("eval_feedback")
        if feedback:
            previous = state.get("agent_response") or ""
            query = (
                f"{query}\n\n[Revision requested] Your previous answer was rejected by review.\n"
                f"Previous answer: {previous}\nReviewer feedback: {feedback}\n"
                "Provide an improved answer to the original request."
            )
        hints = state.get("auto_memory_hints") or []
        run = await run_agent(state["current_agent"], query, ctx, depth=0, hints=hints)
        return {
            "agent_response": run.text,
            "tool_calls": run.tool_calls,
            "spawned_agents": ctx.spawned,
            "approval_queue": ctx.approvals,
        }

    async def coordinate(state: ConversationState) -> dict[str, Any]:
        """Multi-part path: decompose → run agents in parallel → synthesize one answer."""
        ctx = _delegation_context()
        hints = state.get("auto_memory_hints") or []
        result = await run_coordinator(state["messages"], ctx, hints=hints)
        return {
            "agent_response": result["response"],
            "current_agent": "coordinator",
            "spawned_agents": result["spawned"],
            "approval_queue": ctx.approvals,
            "tool_calls": [],
        }

    async def evaluate(state: ConversationState) -> dict[str, Any]:
        """Phase 4: deterministic structural check, then a sampled LLM critic."""
        if not settings.enable_evaluation:
            return {"eval_result": {"pass": True, "score": None, "feedback": "evaluation disabled", "stage": "disabled"}}
        response = state.get("agent_response") or ""
        structural = check_structural(response)
        if not structural["pass"]:
            return {"eval_result": {"pass": False, "score": 0.0, "feedback": structural["reason"], "stage": "structural"}}

        agent = state.get("current_agent")
        defn = registry.get(agent) if agent and agent in registry else None
        if defn is None:
            return {"eval_result": {"pass": True, "score": 1.0, "feedback": "no rubric to judge against", "stage": "skipped"}}
        if state.get("retry_count", 0) == 0 and random.random() >= defn.judge_sample_rate:
            return {"eval_result": {"pass": True, "score": None, "feedback": "not judged (sampled out)", "stage": "skipped"}}

        verdict = await critique(response, defn, settings, _last_user_message(state["messages"]))
        return {"eval_result": {**verdict, "stage": "critic"}}

    async def revise(state: ConversationState) -> dict[str, Any]:
        """Set up one retry: bump the counter and feed the critic's feedback to the agent."""
        result = state.get("eval_result") or {}
        return {"retry_count": state.get("retry_count", 0) + 1, "eval_feedback": result.get("feedback")}

    async def save_memory(state: ConversationState) -> dict[str, Any]:
        """Layer 2 (write): extract durable facts from the turn and upsert them. Fail-silent."""
        owner_id = state.get("owner_id")
        if not auto_memory_on or not owner_id:
            return {"memory_writes": []}
        user_message = _last_user_message(state["messages"])
        written = await extract_and_upsert(memory, settings, owner_id, user_message, state.get("agent_response") or "")
        return {"memory_writes": [{"extracted": written}]}

    def should_coordinate(state: ConversationState) -> str:
        """Branch after routing: multi-part → coordinator, else the single agent."""
        return "coordinate" if state.get("coordinator_mode") else "execute_agent"

    def should_retry(state: ConversationState) -> str:
        """Route a failing, retryable single-agent eval through ``revise``; otherwise finish."""
        if state.get("coordinator_mode"):
            return "save_memory"  # coordinated turns have no single agent to re-run
        result = state.get("eval_result") or {}
        if result.get("pass") is False and state.get("retry_count", 0) < state.get("max_retries", 0):
            return "revise"
        return "save_memory"

    builder = StateGraph(ConversationState)
    builder.add_node("load_memory", load_memory)
    builder.add_node("route", route)
    builder.add_node("execute_agent", execute_agent)
    builder.add_node("coordinate", coordinate)
    builder.add_node("evaluate", evaluate)
    builder.add_node("revise", revise)
    builder.add_node("save_memory", save_memory)

    builder.add_edge(START, "load_memory")
    builder.add_edge("load_memory", "route")
    builder.add_conditional_edges("route", should_coordinate, {"execute_agent": "execute_agent", "coordinate": "coordinate"})
    builder.add_edge("execute_agent", "evaluate")
    builder.add_edge("coordinate", "evaluate")
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
        query_category=None,
        agent_response=None,
        tool_calls=[],
        eval_result=None,
        eval_feedback=None,
        retry_count=0,
        max_retries=2,
        session_summary=None,
        auto_memory_hints=[],
        memory_writes=[],
        coordinator_mode=False,
        spawned_agents=[],
        approval_queue=[],
        metadata={},
    )


def _last_user_message(messages: Sequence[dict[str, Any]]) -> str:
    """The most recent user-authored message text (what the critic + memory read)."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""
