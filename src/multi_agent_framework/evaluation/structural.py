"""Stage 1 — deterministic structural validation. Cheap, runs on every turn, no LLM.

Catches obviously broken output (empty / whitespace / too short) before the costly,
sampled LLM critic. Intentionally minimal: the fuzzy "repeated text / markdown artifact"
heuristics from the original plan were dropped as over-specified for the demo.
"""
from __future__ import annotations

_MIN_CHARS = 2


def check_structural(response: str) -> dict:
    """Return ``{"pass": bool, "reason": str}`` for a response's basic well-formedness."""
    text = (response or "").strip()
    if not text:
        return {"pass": False, "reason": "empty response"}
    if len(text) < _MIN_CHARS:
        return {"pass": False, "reason": "response too short to be useful"}
    return {"pass": True, "reason": "ok"}
