from __future__ import annotations

from collections.abc import Collection
from typing import Any

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from backend.agents.registry import AgentRegistry
from backend.core import routing_cache, usage
from backend.core.config import Settings

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


def _format_menu(registry: AgentRegistry, allowed_agents: Collection[str] | None = None) -> str:
    lines = ["Available agents:"]
    for entry in registry.router_menu():
        # A hidden agent never even appears in the menu the router model sees.
        if allowed_agents is not None and entry["name"] not in allowed_agents:
            continue
        caps = ", ".join(entry["capabilities"])
        lines.append(f"- {entry['name']}: {entry['description'].strip()} | capabilities: {caps}")
    return "\n".join(lines)


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    """The message being routed. Only this drives the decision, so only this is keyed."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _build_prompt(
    registry: AgentRegistry,
    messages: list[dict[str, Any]],
    allowed_agents: Collection[str] | None = None,
) -> list[dict[str, str]]:
    system = f"{_INSTRUCTIONS}\n\n{_format_menu(registry, allowed_agents)}"
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
    allowed_agents: Collection[str] | None = None,
    cache: routing_cache.CacheClient | None = None,
) -> dict[str, Any]:
    """Pick agents for this turn. Returns ``{agents, confidence, reason, category}``.

    allowed_agents (None = no restriction) limits both the menu the model sees and
    the picks accepted back; the fallback agent is always exempt.

    With a cache, an identical question against an identical roster and visibility skips
    the model call. Only the model's raw decision is cached; _validate still runs on
    every turn, so the fallback and visibility rules apply on a hit as well as a miss.
    """
    menu = _format_menu(registry, allowed_agents)
    key = routing_cache.fingerprint(_last_user_text(messages), menu, allowed_agents)
    cached = await routing_cache.get(cache, key)
    if cached is not None:
        decision = RouterDecision(**cached)
    else:
        model = init_chat_model(
            settings.model_id_for_tier("fast"),
            model_provider=settings.default_provider,
        )
        decision = await usage.structured(
            model, RouterDecision, _build_prompt(registry, messages, allowed_agents)
        )
        if decision is None:
            # with_structured_output returns None when the model answers without calling
            # the schema tool — treat it like any other unusable decision.
            decision = RouterDecision(
                agents=[], confidence=0.0, reason="router returned no decision"
            )
        else:
            await routing_cache.put(cache, key, decision.model_dump())
    agents = _validate(
        decision,
        registry,
        fallback_agent=fallback_agent,
        min_confidence=min_confidence,
        allowed_agents=allowed_agents,
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
    allowed_agents: Collection[str] | None = None,
) -> list[str]:
    """Validate the model's picks and apply the fallback rules.

    Pure and I/O-free, so the routing policy can be tested without an LLM call.
    """
    has_fallback = bool(fallback_agent) and fallback_agent in registry
    # dict.fromkeys: drop duplicate picks while keeping the model's relevance order.
    valid = [
        a
        for a in dict.fromkeys(decision.agents)
        if a in registry and (allowed_agents is None or a in allowed_agents or a == fallback_agent)
    ]

    if not valid:
        # No usable pick: the fallback if there is one, else nothing — never the raw,
        # unvalidated model output (hallucinated or hidden names must not run).
        return [fallback_agent] if has_fallback else []

    if has_fallback and decision.confidence < min_confidence and len(valid) == 1:
        return [fallback_agent]

    return valid
