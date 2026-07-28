from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Cache for the router's decision.
#
# The router is one fast-tier call on every single turn, and the measured accounting
# (core/usage.py) puts the plumbing calls at roughly 40% of a turn's tokens. A storefront
# sees the same handful of intents endlessly, "where is my order", "how do I return this",
# so the same question is classified from scratch thousands of times. That is the case for
# caching it, and it is a measurement rather than a guess.
#
# What makes this safe to cache when an LLM call generally is not: the router's output is
# a small, pure classification of the user's text against a fixed roster. It reads no
# per-user data and writes nothing. Two shoppers asking the same question should get the
# same routing, so a shared cache is correct rather than a leak.
#
# The key covers everything the decision depends on:
#   - the question itself (normalised for case and whitespace only)
#   - the roster the model was shown, so editing an agent's description invalidates it
#   - the caller's visibility, so a merchant's answer is never served to a client
#
# Deliberately NOT cached: anything downstream of the decision. _validate still runs on
# every turn, so the fallback and visibility rules are applied fresh even on a hit.

_TTL_SECONDS = 3600


class CacheClient(Protocol):
    """The slice of redis.asyncio.Redis this needs. A Protocol so tests pass a dict."""

    async def get(self, key: str) -> Any: ...

    async def set(self, key: str, value: str, ex: int | None = None) -> Any: ...


def fingerprint(question: str, roster: str, allowed: Any) -> str:
    """A stable cache key for one routing decision.

    sha256 over the three inputs rather than the raw question: keys stay short and
    bounded, and a user's text never sits in a Redis key where it would show up in
    MONITOR output or a key dump.
    """
    visibility = "all" if allowed is None else ",".join(sorted(allowed))
    blob = "\x1f".join((" ".join(question.split()).lower(), roster, visibility))
    return "route:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


async def get(client: CacheClient | None, key: str) -> dict[str, Any] | None:
    """A previously cached decision, or None. Never raises: a cache is an optimisation."""
    if client is None:
        return None
    try:
        raw = await client.get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - a cache miss and a broken cache are the same thing
        logger.warning("routing cache read failed", exc_info=True)
        return None


async def put(client: CacheClient | None, key: str, decision: dict[str, Any]) -> None:
    """Store a decision under the key. Never raises."""
    if client is None:
        return
    try:
        await client.set(key, json.dumps(decision), ex=_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - failing to cache must never fail the turn
        logger.warning("routing cache write failed", exc_info=True)
