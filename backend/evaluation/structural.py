from __future__ import annotations

_MIN_CHARS = 2


def check_structural(response: str) -> dict:
    """Check a response's basic well-formedness. Returns {"pass": bool, "reason": str}."""
    text = (response or "").strip()
    if not text:
        return {"pass": False, "reason": "empty response"}
    if len(text) < _MIN_CHARS:
        return {"pass": False, "reason": "response too short to be useful"}
    return {"pass": True, "reason": "ok"}
