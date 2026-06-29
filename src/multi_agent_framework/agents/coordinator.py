"""Coordinator: handle a multi-part request top-down.

Decompose the request into independent subtasks, run the right agent for each **in
parallel**, then synthesize one answer. Workers run through the same guarded
:func:`run_agent`, so they share the turn's depth/budget/approval limits and can
themselves delegate to peers. The coordinator is pure orchestration — it never calls a
domain tool directly.

``review_action`` is the automated approval guardrail used for high-risk agents (in both
the coordinator and peer paths). It is best-effort and fail-soft: if the review itself
errors, the action is allowed with a note rather than blocking the user.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from multi_agent_framework.agents.delegation import DelegationContext, run_agent
from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.core.config import Settings
from multi_agent_framework.core.prompt_builder import message_text

logger = logging.getLogger(__name__)


class Subtask(BaseModel):
    agent: str = Field(description="Exact name of the agent that should handle this part.")
    query: str = Field(description="The self-contained sub-question for that agent.")


class Plan(BaseModel):
    subtasks: list[Subtask] = Field(default_factory=list, description="One entry per independent part of the request.")


class Approval(BaseModel):
    approved: bool = Field(description="True only if the high-risk action is legitimate and in scope.")
    reason: str = Field(description="One sentence explaining the decision.")


def _decompose_prompt(registry: AgentRegistry, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    menu = "\n".join(f"- {e['name']}: {e['description'].strip()}" for e in registry.router_menu())
    system = (
        "Split the user's request into independent parts, each handled by the single best agent. "
        "Use exact agent names from the list. If the request really only needs one agent, return a "
        "single subtask.\n\n"
        f"Agents:\n{menu}"
    )
    recent = [{"role": str(m.get("role", "user")), "content": str(m.get("content", ""))} for m in messages[-6:]]
    return [{"role": "system", "content": system}, *recent]


async def decompose(messages: list[dict[str, Any]], registry: AgentRegistry, settings: Settings) -> list[Subtask]:
    """Break a request into subtasks, keeping only those that name a real agent."""
    model = init_chat_model(settings.model_id_for_tier("standard"), model_provider=settings.default_provider)
    plan: Plan = await model.with_structured_output(Plan).ainvoke(_decompose_prompt(registry, messages))
    return [s for s in plan.subtasks if s.agent in registry]


async def coordinate(messages: list[dict[str, Any]], ctx: DelegationContext, hints: list[str] | None = None) -> dict[str, Any]:
    """Decompose → run the workers in parallel → synthesize. Returns ``{response, spawned}``."""
    subtasks = await decompose(messages, ctx.registry, ctx.settings)
    if not subtasks:
        return {"response": "", "spawned": []}

    runs = await asyncio.gather(*(run_agent(s.agent, s.query, ctx, depth=0, hints=hints) for s in subtasks))
    answer = await synthesize(messages, [(s, r.text) for s, r in zip(subtasks, runs)], ctx.settings)
    return {"response": answer, "spawned": list(ctx.spawned)}


async def synthesize(messages: list[dict[str, Any]], answers: list[tuple[Subtask, str]], settings: Settings) -> str:
    """Combine the workers' answers into one reply to the user."""
    user_question = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    parts = "\n\n".join(f"[{s.agent}] {text}" for s, text in answers)
    system = "Combine the specialists' answers into one clear, coherent reply to the user. Do not mention the agents by name."
    model = init_chat_model(settings.model_id_for_tier("standard"), model_provider=settings.default_provider)
    out = await model.ainvoke(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"User asked:\n{user_question}\n\nSpecialist answers:\n{parts}"},
        ]
    )
    return message_text(out)


async def review_action(agent_name: str, query: str, settings: Settings) -> dict[str, Any]:
    """Automated approval guardrail before a high-risk agent acts. Fail-soft (allows on error)."""
    try:
        model = init_chat_model(settings.model_id_for_tier("standard"), model_provider=settings.default_provider)
        system = (
            f"You are a safety reviewer. The '{agent_name}' agent is about to take a high-risk "
            "(money or account) action. Approve only if the request is legitimate and clearly in "
            "scope; decline if it looks unsafe, fraudulent, or out of scope."
        )
        verdict: Approval = await model.with_structured_output(Approval).ainvoke(
            [{"role": "system", "content": system}, {"role": "user", "content": query}]
        )
        return {"approved": verdict.approved, "reason": verdict.reason}
    except Exception:  # noqa: BLE001 - the guardrail must not block the user on its own failure
        logger.warning("approval review failed for %s; allowing", agent_name, exc_info=True)
        return {"approved": True, "reason": "review unavailable; allowed"}
