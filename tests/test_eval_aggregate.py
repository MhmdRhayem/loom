"""Verdict aggregation (_aggregate_evals): the function that decides whether a turn
passed, what score gets stored, and therefore what every agent's EMA learns from.
Pure dict-in, dict-out, so no fakes and no monkeypatching."""

from backend.core.graph import _aggregate_evals


def judged(agent, passed=True, score=0.9, feedback="fine"):
    return {"agent": agent, "judged": True, "pass": passed, "score": score, "feedback": feedback}


def sampled_out(agent):
    return {
        "agent": agent,
        "judged": False,
        "pass": True,
        "score": None,
        "feedback": "not judged (sampled out)",
    }


def test_nothing_judged_is_skipped_not_a_pass():
    # stage matters as much as pass: reward_from_eval only learns from
    # "structural"/"critic", so "skipped" is what keeps an unjudged turn out of the EMA.
    result = _aggregate_evals([sampled_out("a"), sampled_out("b")])
    assert result["stage"] == "skipped"
    assert result["pass"] is True
    assert result["score"] is None


def test_empty_input_is_skipped():
    assert _aggregate_evals([])["stage"] == "skipped"


def test_single_judged_agent_keeps_its_feedback_verbatim():
    result = _aggregate_evals([judged("catalog_advisor", feedback="missed the price")])
    assert result["stage"] == "critic"
    assert result["feedback"] == "missed the price"
    assert result["score"] == 0.9


def test_one_failure_fails_the_whole_turn():
    result = _aggregate_evals([judged("a", passed=True), judged("b", passed=False, score=0.2)])
    assert result["pass"] is False


def test_score_is_the_minimum_not_the_mean():
    # The weakest specialist sets the turn's score; averaging would let a good answer
    # hide a bad one.
    result = _aggregate_evals([judged("a", score=0.9), judged("b", score=0.3)])
    assert result["score"] == 0.3


def test_sampled_out_agents_do_not_dilute_the_verdict():
    result = _aggregate_evals([judged("a", passed=False, score=0.1), sampled_out("b")])
    assert result["pass"] is False
    assert result["score"] == 0.1


def test_multi_agent_failure_feedback_names_only_the_failures():
    result = _aggregate_evals(
        [judged("a", passed=True, feedback="good"), judged("b", passed=False, feedback="wrong")]
    )
    assert result["feedback"] == "[b] wrong"


def test_all_passing_multi_agent_gets_a_summary_feedback():
    result = _aggregate_evals([judged("a"), judged("b")])
    assert result["feedback"] == "all specialists passed"
    assert result["pass"] is True


def test_judged_without_a_score_yields_no_score():
    # The critic errored and passed the response through. It must not look like a 1.0,
    # or an outage would train the agent upward.
    result = _aggregate_evals([judged("a", score=None)])
    assert result["score"] is None
    assert result["pass"] is True


def test_per_agent_verdicts_are_preserved_for_attribution():
    evals = [judged("a"), sampled_out("b")]
    assert _aggregate_evals(evals)["agent_evals"] == evals
