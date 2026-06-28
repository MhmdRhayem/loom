"""Stage 3 — LLM critic. Scores a response against the agent's own rubric.

A separate fast-tier call returns a structured verdict. It is *sampled* per agent via
``judge_sample_rate`` (high-stakes agents judged every turn, read-only agents rarely;
the sampling decision lives in the graph's ``evaluate`` node). Fail-silent: if the critic
errors or times out, the turn passes through flagged rather than blocking the user.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from multi_agent_framework.agents.registry import AgentDefinition
from multi_agent_framework.core.config import Settings

logger = logging.getLogger(__name__)


class Verdict(BaseModel):
    """Structured critic verdict (``passed`` avoids the ``pass`` keyword)."""

    passed: bool = Field(description="True if the response is acceptable to return to the user.")
    score: float = Field(ge=0.0, le=1.0, description="0-1 quality score.")
    feedback: str = Field(description="One sentence: what to fix, or why it passed.")


def _prompt(response: str, definition: AgentDefinition, user_message: str) -> list[dict[str, str]]:
    rubric = ", ".join(definition.eval_rubric) or "helpfulness, accuracy, clarity"
    system = (
        f"You evaluate the '{definition.name}' agent's response against this rubric: {rubric}. "
        "Pass it if it adequately answers the user and is fit to send — it need not be perfect or "
        "include detail the user did not ask for. Fail only genuinely deficient responses (the "
        "answer is missing, wrong, unsafe, evasive, or unhelpful). Give a 0-1 score and one "
        "sentence of feedback."
    )
    user = f"User asked:\n{user_message}\n\nAssistant response:\n{response}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def critique(response: str, definition: AgentDefinition, settings: Settings, user_message: str) -> dict[str, Any]:
    """Return ``{pass, score, feedback}``. Fail-silent: on error, pass through with a warning."""
    try:
        model = init_chat_model(settings.model_id_for_tier("fast"), model_provider=settings.default_provider)
        verdict: Verdict = await model.with_structured_output(Verdict).ainvoke(_prompt(response, definition, user_message))
        return {"pass": verdict.passed, "score": verdict.score, "feedback": verdict.feedback}
    except Exception:  # noqa: BLE001 - the critic must never block the user
        logger.warning("LLM critic failed for agent %s", definition.name, exc_info=True)
        return {"pass": True, "score": None, "feedback": "critic unavailable; passed through", "warning": True}
