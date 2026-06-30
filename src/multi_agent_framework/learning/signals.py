"""Phase 6 — turn a turn's outcome into a single reward in [0, 1].

Two sources: the evaluator's verdict (automatic, every judged turn) and explicit user
feedback (thumbs up/down). Pure and tiny — no LLM, no I/O — so scoring stays cheap and
testable. ``None`` means "no usable signal" (e.g. the critic sampled the turn out).
"""
from __future__ import annotations

from typing import Any

_UP = {"up", "good", "1", "true", "positive", "thumbs_up", "yes"}
_DOWN = {"down", "bad", "0", "false", "negative", "thumbs_down", "no"}


def reward_from_eval(eval_result: dict[str, Any] | None) -> float | None:
    """Reward from the evaluator verdict, or None if the turn carried no real signal."""
    result = eval_result or {}
    if result.get("stage") not in ("structural", "critic"):
        return None  # sampled out / no rubric -> nothing to learn from
    score = result.get("score")
    if isinstance(score, (int, float)):
        return max(0.0, min(1.0, float(score)))
    return 1.0 if result.get("pass", True) else 0.0


def reward_from_agent_eval(agent_eval: dict[str, Any] | None) -> float | None:
    """Reward from one agent's own verdict in a multi-agent turn, or None if it carried no signal.

    The graph judges each agent against *its own* rubric and stores the per-agent verdicts in
    ``eval_result['agent_evals']``. An agent that was sampled out (``judged`` False) yields no
    signal; a judged agent yields its 0-1 score, falling back to its pass/fail when the critic
    returned no numeric score. Mirrors :func:`reward_from_eval` for the per-agent case.
    """
    ev = agent_eval or {}
    if not ev.get("judged"):
        return None  # sampled out / no rubric -> nothing to learn from
    score = ev.get("score")
    if isinstance(score, (int, float)):
        return max(0.0, min(1.0, float(score)))
    return 1.0 if ev.get("pass", True) else 0.0


def reward_from_feedback(rating: str) -> float | None:
    """Reward from an explicit thumbs rating: up -> 1.0, down -> 0.0, else None."""
    value = (rating or "").strip().lower()
    if value in _UP:
        return 1.0
    if value in _DOWN:
        return 0.0
    return None
