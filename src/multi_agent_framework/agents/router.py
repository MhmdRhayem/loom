from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.core.config import Settings

_RECENT_WINDOW = 6
_DEFAULT_MIN_CONFIDENCE = 0.5

_INSTRUCTIONS = (
    "You are the router for a multi-agent assistant. Choose one or more agents to "
    "handle the user's latest message, based on each agent's description and capabilities.\n"
    "- Use exact agent names from the menu.\n"
    "- Choose multiple agents only when the request genuinely spans different domains "
    "(e.g. product info + order status in one message). Otherwise pick one.\n"
    "- Give a confidence in [0, 1]: high when the choice is clear, low when ambiguous.\n"
    "- Set category to a short, reusable lowercase label for the request's intent "
    "(e.g. 'order status', 'product search', 'returns').\n"
    "- Keep the reason to one sentence."
)


class RouterDecision(BaseModel):
    """The routing decision we ask the model to return."""

    agents: list[str] = Field(
        description="One or more exact agent names from the menu, ordered by relevance."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence in the choice.")
    reason: str = Field(description="One-sentence justification.")
    category: str = Field(default="general", description="Short lowercase intent label.")


def _format_menu(registry: AgentRegistry) -> str:
    lines = ["Available agents:"]
    for entry in registry.router_menu():
        caps = ", ".join(entry["capabilities"])
        lines.append(f"- {entry['name']}: {entry['description'].strip()} | capabilities: {caps}")
    return "\n".join(lines)


def _build_prompt(registry: AgentRegistry, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    system = f"{_INSTRUCTIONS}\n\n{_format_menu(registry)}"
    recent = [
        {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
        for m in messages[-_RECENT_WINDOW:]
    ]
    return [{"role": "system", "content": system}, *recent]


async def route_turn(
    messages: list[dict[str, Any]],
    registry: AgentRegistry,
    settings: Settings,
    *,
    fallback_agent: str | None = None,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Pick agents for this turn. Returns ``{agents, confidence, reason, category}``."""
    model = init_chat_model(
        settings.model_id_for_tier("fast"),
        model_provider=settings.default_provider,
    )
    decision: RouterDecision = await model.with_structured_output(RouterDecision).ainvoke(
        _build_prompt(registry, messages)
    )
    agents = _validate(
        decision, registry, fallback_agent=fallback_agent, min_confidence=min_confidence
    )
    return {
        "agents": agents,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "category": (decision.category or "general").strip().lower() or "general",
    }


def _validate(
    decision: RouterDecision,
    registry: AgentRegistry,
    *,
    fallback_agent: str | None = None,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> list[str]:
    """Validate the model's picks and apply the fallback rules.

    Pure and I/O-free, so the routing policy can be tested without an LLM call.
    """
    has_fallback = bool(fallback_agent) and fallback_agent in registry
    valid = [a for a in decision.agents if a in registry]

    if not valid:
        return [fallback_agent] if has_fallback else list(decision.agents)

    if has_fallback and decision.confidence < min_confidence and len(valid) == 1:
        return [fallback_agent]

    return valid
