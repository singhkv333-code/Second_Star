"""Tests for backend.news_events.parsing.disambiguation."""
from __future__ import annotations

from backend.news_events.parsing.disambiguation import (
    apply_answers,
    apply_option,
    parsed_spec_to_pending_dict,
    questions_for,
)
from backend.news_events.parsing.event_spec_parser import ParsedSpec
from backend.news_events.schemas import (
    KeywordSet,
    ResolutionCriteria,
    RetractionPolicy,
)


def _tier3_pending():
    return {
        "description": "Trump wins the 2028 US presidential election",
        "tier": "tier3",
        "keyword_set": {
            "must_have_one": ["Trump", "wins"],
            "must_have_one_of": [],
            "must_not_have": ["rumour"],
        },
        "resolution_criteria": {
            "primary_sources": [],
            "min_secondary_confirmations": 1,
            "min_confidence": 0.85,
            "prediction_market_threshold": None,
            "conflict_policy": "hold",
        },
        "retraction_policy": {
            "safety_window_minutes": 240,
            "action": "cancel_pending_approvals",
        },
    }


def test_questions_for_tier3_returns_at_most_three():
    qs = questions_for("tier3")
    assert 1 <= len(qs) <= 3
    assert all(q.options for q in qs)


def test_questions_for_tier1_and_tier2_are_empty():
    assert questions_for("tier1") == []
    assert questions_for("tier2") == []


def test_apply_option_modifies_resolution_criteria():
    pending = _tier3_pending()
    qs = questions_for("tier3")
    q_event = qs[0]
    out = apply_option(pending, question=q_event, option_id="official_certification")
    assert out["resolution_criteria"]["min_secondary_confirmations"] == 2
    assert out["resolution_criteria"]["conflict_policy"] == "hold"
    # Original is untouched (deepcopy semantics).
    assert pending["resolution_criteria"]["min_secondary_confirmations"] == 1
    # Description got the appended suffix.
    assert "official certification" in out["description"]


def test_apply_option_modifies_retraction_policy():
    pending = _tier3_pending()
    qs = questions_for("tier3")
    q_retract = next(q for q in qs if q.id == "retraction_policy")
    out = apply_option(pending, question=q_retract, option_id="ignore")
    assert out["retraction_policy"]["action"] == "ignore"
    assert out["retraction_policy"]["safety_window_minutes"] == 0


def test_apply_option_unknown_id_raises():
    pending = _tier3_pending()
    qs = questions_for("tier3")
    try:
        apply_option(pending, question=qs[0], option_id="nope")
    except ValueError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_apply_answers_skips_unanswered_questions():
    pending = _tier3_pending()
    qs = questions_for("tier3")
    # Answer only the first question.
    out = apply_answers(
        pending,
        answers={qs[0].id: "first_major_call"},
        questions=qs,
    )
    assert out["resolution_criteria"]["min_secondary_confirmations"] == 0
    # The second question's defaults stay in effect.
    assert (
        out["retraction_policy"]["action"]
        == pending["retraction_policy"]["action"]
    )


def test_parsed_spec_to_pending_dict_shape():
    parsed = ParsedSpec(
        description="x",
        tier="tier1",
        keyword_set=KeywordSet(must_have_one=["a"]),
        resolution_criteria=ResolutionCriteria(),
        retraction_policy=RetractionPolicy(),
    )
    out = parsed_spec_to_pending_dict(parsed)
    assert out["description"] == "x"
    assert out["tier"] == "tier1"
    assert "must_have_one" in out["keyword_set"]
    assert "primary_sources" in out["resolution_criteria"]
    assert "action" in out["retraction_policy"]
