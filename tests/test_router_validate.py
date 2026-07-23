"""Routing policy (_validate) — pure, so tested without an LLM call."""

from backend.agents.router import RouterDecision, _validate


class FakeRegistry:
    """Only membership matters to _validate."""

    def __init__(self, names: set[str]):
        self._names = names

    def __contains__(self, name: str) -> bool:
        return name in self._names


REGISTRY = FakeRegistry({"order_tracking", "catalog_advisor", "support_concierge"})


def decision(agents: list[str], confidence: float = 0.9) -> RouterDecision:
    return RouterDecision(agents=agents, confidence=confidence, reason="test")


def test_valid_single_pick_passes_through():
    assert _validate(decision(["order_tracking"]), REGISTRY) == ["order_tracking"]


def test_valid_multi_pick_passes_through():
    picks = ["order_tracking", "catalog_advisor"]
    assert _validate(decision(picks), REGISTRY) == picks


def test_unknown_agents_are_dropped():
    got = _validate(decision(["order_tracking", "nonsense"]), REGISTRY)
    assert got == ["order_tracking"]


def test_all_unknown_falls_back_when_fallback_configured():
    got = _validate(decision(["nonsense"]), REGISTRY, fallback_agent="support_concierge")
    assert got == ["support_concierge"]


def test_all_unknown_without_fallback_returns_nothing():
    # Never the raw model output: hallucinated names must not reach execution.
    assert _validate(decision(["nonsense"]), REGISTRY) == []


def test_duplicate_picks_are_deduplicated():
    got = _validate(decision(["order_tracking", "order_tracking", "catalog_advisor"]), REGISTRY)
    assert got == ["order_tracking", "catalog_advisor"]


def test_low_confidence_single_pick_falls_back():
    got = _validate(
        decision(["order_tracking"], confidence=0.2),
        REGISTRY,
        fallback_agent="support_concierge",
    )
    assert got == ["support_concierge"]


def test_low_confidence_multi_pick_is_kept():
    picks = ["order_tracking", "catalog_advisor"]
    got = _validate(decision(picks, confidence=0.2), REGISTRY, fallback_agent="support_concierge")
    assert got == picks


def test_unknown_fallback_name_is_ignored():
    got = _validate(decision(["order_tracking"], confidence=0.2), REGISTRY, fallback_agent="nope")
    assert got == ["order_tracking"]


def test_disallowed_agent_is_dropped():
    got = _validate(
        decision(["order_tracking", "catalog_advisor"]),
        REGISTRY,
        allowed_agents=["catalog_advisor"],
    )
    assert got == ["catalog_advisor"]


def test_only_disallowed_picks_fall_back():
    got = _validate(
        decision(["order_tracking"]),
        REGISTRY,
        fallback_agent="support_concierge",
        allowed_agents=["catalog_advisor"],
    )
    assert got == ["support_concierge"]


def test_fallback_is_exempt_from_visibility():
    got = _validate(
        decision(["support_concierge"]),
        REGISTRY,
        fallback_agent="support_concierge",
        allowed_agents=["catalog_advisor"],
    )
    assert got == ["support_concierge"]


def test_no_restriction_when_allowed_is_none():
    picks = ["order_tracking", "catalog_advisor"]
    assert _validate(decision(picks), REGISTRY, allowed_agents=None) == picks
