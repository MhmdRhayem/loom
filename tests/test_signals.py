"""Reward signals (Phase 6) — pure functions, no LLM or I/O."""

from backend.learning.signals import (
    reward_from_agent_eval,
    reward_from_eval,
    reward_from_feedback,
)


def test_eval_score_is_clamped():
    assert reward_from_eval({"stage": "critic", "score": 1.7}) == 1.0
    assert reward_from_eval({"stage": "critic", "score": -0.3}) == 0.0
    assert reward_from_eval({"stage": "critic", "score": 0.6}) == 0.6


def test_eval_without_score_gives_no_signal():
    # A missing score means the critic never actually ran (its error path passes
    # through with score=None) — an outage must not be recorded as a success.
    assert reward_from_eval({"stage": "critic", "pass": True}) is None
    assert reward_from_eval({"stage": "structural", "pass": False, "score": 0.0}) == 0.0


def test_sampled_out_eval_gives_no_signal():
    assert reward_from_eval({"stage": "skipped"}) is None
    assert reward_from_eval(None) is None


def test_agent_eval_requires_judged():
    assert reward_from_agent_eval({"judged": False, "score": 0.9}) is None
    assert reward_from_agent_eval({"judged": True, "score": 0.9}) == 0.9
    assert reward_from_agent_eval(None) is None


def test_agent_eval_critic_outage_gives_no_signal():
    assert reward_from_agent_eval({"judged": True, "pass": True, "score": None}) is None


def test_feedback_thumbs():
    assert reward_from_feedback("up") == 1.0
    assert reward_from_feedback("DOWN") == 0.0
    assert reward_from_feedback("meh") is None
    assert reward_from_feedback("") is None
