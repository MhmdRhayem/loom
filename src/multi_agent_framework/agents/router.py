"""LLM router: pick the single best agent for a turn.

The router shows the model the registry's compact agent menu (name, description,
capabilities) and the recent conversation, then asks for a structured decision:
which agent, how confident, and why. Two post-checks keep it safe:

* if the model names an agent that isn't in the registry, fall back; and
* if confidence is below ``min_confidence`` and a ``fallback_agent`` is configured,
  route there instead (the demo points this at its catch-all/clarifier agent).

Fan-out, cascades, and learned routing policies are deliberately out of scope here
(Phase 6). This is the simplest router that routes correctly.
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.core.config import Settings

# How many trailing messages to show the router. Routing only needs the recent ask,
# not the whole history — keeps the call cheap and on the fast tier.
_RECENT_WINDOW = 6
_DEFAULT_MIN_CONFIDENCE = 0.5

_INSTRUCTIONS = (
    "You are the router for a multi-agent assistant. Choose the SINGLE best agent to "
    "handle the user's latest message, based on each agent's description and capabilities.\n"
    "- Use the agent name exactly as written in the menu.\n"
    "- Give a confidence in [0, 1]: high when one agent clearly fits, low when the request "
    "is ambiguous or no agent fits well.\n"
    "- Keep the reason to one sentence."
)


class RouterDecision(BaseModel):
    """Structured routing decision returned by the model."""

    agent: str = Field(description="Exact name of the single best agent from the menu.")
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence in the choice.")
    reason: str = Field(description="One-sentence justification.")


def _format_menu(registry: AgentRegistry) -> str:
    lines = ["Available agents:"]
    for entry in registry.router_menu():
        caps = ", ".join(entry["capabilities"])
        lines.append(f"- {entry['name']}: {entry['description'].strip()} | capabilities: {caps}")
    return "\n".join(lines)


def _build_prompt(registry: AgentRegistry, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    system = f"{_INSTRUCTIONS}\n\n{_format_menu(registry)}"
    recent = [{"role": str(m.get("role", "user")), "content": str(m.get("content", ""))} for m in messages[-_RECENT_WINDOW:]]
    return [{"role": "system", "content": system}, *recent]


async def route_turn(
    messages: list[dict[str, Any]],
    registry: AgentRegistry,
    settings: Settings,
    *,
    fallback_agent: str | None = None,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Pick an agent for this turn. Returns ``{agent, confidence, reason}``."""
    model = init_chat_model(
        settings.model_id_for_tier("fast"),
        model_provider=settings.default_provider,
    )
    decision: RouterDecision = await model.with_structured_output(RouterDecision).ainvoke(_build_prompt(registry, messages))

    return _resolve(decision, registry, fallback_agent=fallback_agent, min_confidence=min_confidence)


def _resolve(
    decision: RouterDecision,
    registry: AgentRegistry,
    *,
    fallback_agent: str | None = None,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Apply the safety post-checks to a raw model decision.

    Pure (no I/O), so the routing *policy* is unit-testable without an LLM call:
    unknown agent -> fallback (if usable); confidence below ``min_confidence`` ->
    fallback; otherwise keep the model's choice.
    """
    agent, confidence, reason = decision.agent, decision.confidence, decision.reason
    has_fallback = bool(fallback_agent) and fallback_agent in registry

    if agent not in registry:
        if has_fallback:
            return {"agent": fallback_agent, "confidence": confidence, "reason": f"router named unknown agent '{agent}'; using fallback. ({reason})"}
        # No usable fallback: surface the raw decision so the failure is visible.
        return {"agent": agent, "confidence": confidence, "reason": reason}

    if has_fallback and confidence < min_confidence:
        return {"agent": fallback_agent, "confidence": confidence, "reason": f"low confidence {confidence:.2f} < {min_confidence:.2f} for '{agent}'; routing to fallback. ({reason})"}

    return {"agent": agent, "confidence": confidence, "reason": reason}
