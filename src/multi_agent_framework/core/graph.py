from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from langchain.chat_models import init_chat_model
from langgraph.graph import END, START, StateGraph

from multi_agent_framework.agents.factory import run_agent
from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.agents.router import route_turn
from multi_agent_framework.core.config import Settings
from multi_agent_framework.core.prompt_builder import message_text
from multi_agent_framework.core.state import ConversationState
from multi_agent_framework.evaluation.critic import critique
from multi_agent_framework.evaluation.structural import check_structural
from multi_agent_framework.memory.auto_memory import extract_and_upsert, load_hints

if TYPE_CHECKING:
    from multi_agent_framework.storage.repositories.memory import MemoryRepository

# Maps an agent's declared tool names (+ the turn's owner, when known) to callables,
# so a consumer can hand out user-scoped tools.
ToolProvider = Callable[[Sequence[str], str | None], Sequence[Any]]


def build_graph(
    registry: AgentRegistry,
    settings: Settings,
    tool_provider: ToolProvider,
    *,
    fallback_agent: str | None = None,
    memory: "MemoryRepository | None" = None,
):
    """Compile and return the conversation graph wired to the registry and settings."""

    auto_memory_on = settings.enable_memory and memory is not None

    async def load_memory(state: ConversationState) -> dict[str, Any]:
        """Surface the owner's stored memories as hints. Never raises."""
        owner_id = state.get("owner_id")
        if not auto_memory_on or not owner_id:
            return {"auto_memory_hints": []}
        return {"auto_memory_hints": await load_hints(memory, owner_id)}

    async def route(state: ConversationState) -> dict[str, Any]:
        """Pick one or more agents via the LLM router; set the query category."""
        decision = await route_turn(
            state["messages"], registry, settings, fallback_agent=fallback_agent
        )
        agents = decision["agents"]
        return {
            "current_agents": agents,
            "routing_confidence": decision["confidence"],
            "routing_reason": decision["reason"],
            "query_category": decision.get("category") or "general",
        }

    async def execute_agents(state: ConversationState) -> dict[str, Any]:
        """Run all selected agents in parallel. Synthesize if more than one."""
        agents = state.get("current_agents") or []
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
        owner_id = state.get("owner_id")
        # Everything before the current user message — the loaded conversation so far.
        history = list(state["messages"][:-1])

        def scoped_tools(names: Sequence[str]) -> Sequence[Any]:
            """Bind this turn's owner into the tool provider (peer calls included)."""
            return tool_provider(names, owner_id)

        runs = await asyncio.gather(
            *(
                run_agent(
                    a,
                    query,
                    registry=registry,
                    settings=settings,
                    tool_provider=scoped_tools,
                    depth=0,
                    hints=hints,
                    history=history,
                )
                for a in agents
            )
        )
        agent_runs = [
            {"agent": a, "text": r.text, "tool_calls": r.tool_calls} for a, r in zip(agents, runs)
        ]
        tool_calls = [call for r in runs for call in r.tool_calls]
        if len(runs) == 1:
            answer = runs[0].text
        else:
            answer = await _synthesize(
                state["messages"], list(zip(agents, [r.text for r in runs])), settings
            )
        return {"agent_response": answer, "tool_calls": tool_calls, "agent_runs": agent_runs}

    async def evaluate(state: ConversationState) -> dict[str, Any]:
        """Structural check on the reply, then judge each agent's own answer.

        The structural gate runs once on the synthesized reply. The critic then judges each
        agent's raw answer against that agent's rubric, sampled per agent, and the verdicts
        are aggregated so the turn fails if any judged agent fails. For a single-agent turn
        the raw answer is the reply, so that's the retryable path; multi-agent verdicts are
        recorded but advisory (should_retry only retries single-agent turns).
        """
        if not settings.enable_evaluation:
            return {
                "eval_result": {
                    "pass": True,
                    "score": None,
                    "feedback": "evaluation disabled",
                    "stage": "disabled",
                }
            }
        response = state.get("agent_response") or ""
        structural = check_structural(response)
        if not structural["pass"]:
            return {
                "eval_result": {
                    "pass": False,
                    "score": 0.0,
                    "feedback": structural["reason"],
                    "stage": "structural",
                }
            }

        runs = state.get("agent_runs") or []
        if not runs:
            return {
                "eval_result": {
                    "pass": True,
                    "score": 1.0,
                    "feedback": "no rubric to judge against",
                    "stage": "skipped",
                }
            }

        force = state.get("retry_count", 0) > 0
        user_message = _last_user_message(state["messages"])

        async def judge(run: dict[str, Any]) -> dict[str, Any]:
            agent = run.get("agent")
            defn = registry.get(agent) if agent and agent in registry else None
            if defn is None:
                return {
                    "agent": agent,
                    "pass": True,
                    "score": 1.0,
                    "feedback": "no rubric to judge against",
                    "judged": False,
                }
            if not force and random.random() >= defn.judge_sample_rate:
                return {
                    "agent": agent,
                    "pass": True,
                    "score": None,
                    "feedback": "not judged (sampled out)",
                    "judged": False,
                }
            verdict = await critique(run.get("text") or "", defn, settings, user_message)
            return {"agent": agent, "judged": True, **verdict}

        agent_evals = await asyncio.gather(*(judge(run) for run in runs))
        return {"eval_result": _aggregate_evals(list(agent_evals))}

    async def revise(state: ConversationState) -> dict[str, Any]:
        """Set up one retry: bump the counter and feed the critic's feedback to the agent."""
        result = state.get("eval_result") or {}
        return {
            "retry_count": state.get("retry_count", 0) + 1,
            "eval_feedback": result.get("feedback"),
        }

    async def save_memory(state: ConversationState) -> dict[str, Any]:
        """Extract durable facts from the turn and upsert them. Never raises."""
        owner_id = state.get("owner_id")
        if not auto_memory_on or not owner_id:
            return {"memory_writes": []}
        user_message = _last_user_message(state["messages"])
        written = await extract_and_upsert(
            memory, settings, owner_id, user_message, state.get("agent_response") or ""
        )
        return {"memory_writes": [{"extracted": written}]}

    def should_retry(state: ConversationState) -> str:
        """Send a failing single-agent eval to revise; multi-agent turns skip retry."""
        if len(state.get("current_agents") or []) > 1:
            return "save_memory"
        result = state.get("eval_result") or {}
        if result.get("pass") is False and state.get("retry_count", 0) < state.get(
            "max_retries", 0
        ):
            return "revise"
        return "save_memory"

    builder = StateGraph(ConversationState)
    builder.add_node("load_memory", load_memory)
    builder.add_node("route", route)
    builder.add_node("execute_agents", execute_agents)
    builder.add_node("evaluate", evaluate)
    builder.add_node("revise", revise)
    builder.add_node("save_memory", save_memory)

    builder.add_edge(START, "load_memory")
    builder.add_edge("load_memory", "route")
    builder.add_edge("route", "execute_agents")
    builder.add_edge("execute_agents", "evaluate")
    builder.add_conditional_edges(
        "evaluate", should_retry, {"revise": "revise", "save_memory": "save_memory"}
    )
    builder.add_edge("revise", "execute_agents")
    builder.add_edge("save_memory", END)

    return builder.compile()


def _aggregate_evals(agent_evals: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine per-agent verdicts into one eval_result; fail if any judged agent fails."""
    judged = [e for e in agent_evals if e.get("judged")]
    if not judged:
        return {
            "pass": True,
            "score": None,
            "feedback": "not judged (sampled out)",
            "stage": "skipped",
            "agent_evals": agent_evals,
        }
    overall_pass = all(e.get("pass", True) for e in judged)
    scores = [e["score"] for e in judged if e.get("score") is not None]
    failed = [e for e in judged if e.get("pass") is False]
    if len(judged) == 1:
        feedback = judged[0]["feedback"]
    elif failed:
        feedback = " | ".join(f"[{e['agent']}] {e['feedback']}" for e in failed)
    else:
        feedback = "all specialists passed"
    return {
        "pass": overall_pass,
        "score": min(scores) if scores else None,
        "feedback": feedback,
        "stage": "critic",
        "agent_evals": agent_evals,
    }


async def _synthesize(
    messages: list[dict[str, Any]], agent_answers: list[tuple[str, str]], settings: Settings
) -> str:
    """Combine multiple agents' answers into one coherent reply."""
    user_question = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""
    )
    parts = "\n\n".join(f"[{agent}] {text}" for agent, text in agent_answers)
    model = init_chat_model(
        settings.model_id_for_tier("standard"), model_provider=settings.default_provider
    )
    out = await model.ainvoke(
        [
            {
                "role": "system",
                "content": "Combine the specialists' answers into one clear, coherent reply. Do not mention agent names.",
            },
            {
                "role": "user",
                "content": f"User asked:\n{user_question}\n\nSpecialist answers:\n{parts}",
            },
        ]
    )
    return message_text(out)


def build_initial_state(
    message: str,
    conversation_id: str | None = None,
    owner_id: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> ConversationState:
    """Build a fresh ConversationState for one user message.

    history is the conversation so far (alternating user/assistant messages, loaded
    from storage), so the router and agents see prior turns; the new message goes last.
    """
    return ConversationState(
        messages=[*(history or []), {"role": "user", "content": message}],
        conversation_id=conversation_id or str(uuid.uuid4()),
        owner_id=owner_id,
        current_agents=[],
        routing_confidence=None,
        routing_reason=None,
        query_category=None,
        agent_response=None,
        tool_calls=[],
        agent_runs=[],
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
    """The most recent user message text (what the critic and memory read)."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""
