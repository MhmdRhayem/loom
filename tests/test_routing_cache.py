"""The router's decision cache: what it keys on, and that it never breaks a turn.

The key is the whole safety argument. A routing decision is a pure classification of the
user's text against a fixed roster, so sharing it between users is correct — but only if
the key covers everything the decision depended on. These tests pin that.
"""

import json

from backend.agents import router as router_module
from backend.core import routing_cache


class FakeCache:
    """A dict with the two methods the cache uses, plus a failure mode."""

    def __init__(self, raises=False):
        self.data = {}
        self.raises = raises
        self.reads = 0
        self.writes = 0

    async def get(self, key):
        self.reads += 1
        if self.raises:
            raise RuntimeError("redis down")
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.writes += 1
        if self.raises:
            raise RuntimeError("redis down")
        self.data[key] = value


# --- what the key covers ---------------------------------------------------


def test_the_same_question_and_roster_is_the_same_key():
    a = routing_cache.fingerprint("Where is my order?", "menu", None)
    b = routing_cache.fingerprint("  where is MY   order? ", "menu", None)
    assert a == b  # case and whitespace only


def test_a_different_question_is_a_different_key():
    a = routing_cache.fingerprint("Where is my order?", "menu", None)
    b = routing_cache.fingerprint("How do I return this?", "menu", None)
    assert a != b


def test_editing_the_roster_invalidates_the_key():
    # Agent descriptions ARE the routing surface, so a description edit has to change
    # the decision. This is what stops a stale cache surviving a roster repair.
    a = routing_cache.fingerprint("q", "menu listing eight agents", None)
    b = routing_cache.fingerprint("q", "menu listing eight agents, one reworded", None)
    assert a != b


def test_visibility_is_part_of_the_key():
    # Without this a merchant's decision could be served to a client, routing them to
    # an agent they are not allowed to see.
    everyone = routing_cache.fingerprint("q", "menu", None)
    merchant = routing_cache.fingerprint("q", "menu", ["catalog_advisor", "shop_manager"])
    client = routing_cache.fingerprint("q", "menu", ["catalog_advisor"])
    assert len({everyone, merchant, client}) == 3


def test_visibility_order_does_not_matter():
    a = routing_cache.fingerprint("q", "menu", ["b", "a"])
    b = routing_cache.fingerprint("q", "menu", ["a", "b"])
    assert a == b


def test_the_question_is_not_stored_in_the_key():
    # Keys show up in MONITOR output and key dumps; user text should not.
    key = routing_cache.fingerprint("my order ORD-1005 for alice@example.com", "menu", None)
    assert "alice" not in key and "ORD-1005" not in key
    assert key.startswith("route:")


# --- never breaks a turn ---------------------------------------------------


async def test_a_broken_cache_reads_as_a_miss():
    assert await routing_cache.get(FakeCache(raises=True), "k") is None


async def test_a_broken_cache_swallows_writes():
    await routing_cache.put(FakeCache(raises=True), "k", {"agents": []})  # must not raise


async def test_no_cache_configured_is_a_miss():
    assert await routing_cache.get(None, "k") is None
    await routing_cache.put(None, "k", {"agents": []})


async def test_round_trip():
    cache = FakeCache()
    await routing_cache.put(cache, "k", {"agents": ["a"], "confidence": 0.9})
    assert await routing_cache.get(cache, "k") == {"agents": ["a"], "confidence": 0.9}


# --- the router's use of it ------------------------------------------------


class FakeRegistry:
    def __init__(self, names):
        self._names = list(names)

    def __contains__(self, name):
        return name in self._names

    def router_menu(self):
        return [
            {"name": n, "description": f"{n} does things", "capabilities": [n]} for n in self._names
        ]


def settings():
    from types import SimpleNamespace

    return SimpleNamespace(default_provider="openai", model_id_for_tier=lambda t: f"model-{t}")


async def test_a_hit_skips_the_model_entirely(monkeypatch):
    calls = []
    monkeypatch.setattr(
        router_module, "init_chat_model", lambda *a, **k: calls.append(1) or object()
    )
    registry = FakeRegistry(["order_tracking", "catalog_advisor"])
    cache = FakeCache()
    menu = router_module._format_menu(registry, None)
    key = routing_cache.fingerprint("Where is my order?", menu, None)
    cache.data[key] = json.dumps(
        {"agents": ["order_tracking"], "confidence": 0.95, "reason": "cached", "category": "orders"}
    )

    got = await router_module.route_turn(
        [{"role": "user", "content": "Where is my order?"}], registry, settings(), cache=cache
    )
    assert got["agents"] == ["order_tracking"]
    assert got["reason"] == "cached"
    assert calls == []  # the whole point: no model was constructed


async def test_validation_still_runs_on_a_cache_hit(monkeypatch):
    # A cached decision naming an agent this caller cannot see must still be filtered.
    monkeypatch.setattr(router_module, "init_chat_model", lambda *a, **k: object())
    registry = FakeRegistry(["catalog_advisor", "shop_manager"])
    cache = FakeCache()
    menu = router_module._format_menu(registry, ["catalog_advisor"])
    key = routing_cache.fingerprint("stock?", menu, ["catalog_advisor"])
    cache.data[key] = json.dumps(
        {"agents": ["shop_manager"], "confidence": 0.99, "reason": "stale", "category": "shop"}
    )

    got = await router_module.route_turn(
        [{"role": "user", "content": "stock?"}],
        registry,
        settings(),
        allowed_agents=["catalog_advisor"],
        fallback_agent="catalog_advisor",
        cache=cache,
    )
    assert got["agents"] == ["catalog_advisor"]  # the hidden agent did not survive the hit
