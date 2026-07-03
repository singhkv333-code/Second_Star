"""Tests for backend.news_events.specs — the spec lifecycle helpers.

Exercises create / disambiguate / activate / cancel and their guards.
The parser layer is mocked; we feed ParsedSpec instances directly.
"""
from __future__ import annotations

import pytest

from backend.news_events.models import (
    NewsDisambiguationSession,
    NewsEventSpec,
)
from backend.news_events.parsing.event_spec_parser import ParsedSpec
from backend.news_events.schemas import (
    KeywordSet,
    ResolutionCriteria,
    RetractionPolicy,
)
from backend.news_events import specs as specs_mod


def _tier1_parsed() -> ParsedSpec:
    return ParsedSpec(
        description="RBI cuts repo rate",
        tier="tier1",
        keyword_set=KeywordSet(must_have_one=["RBI", "repo"]),
        resolution_criteria=ResolutionCriteria(
            primary_sources=["rbi_press_releases"],
            min_secondary_confirmations=0,
            conflict_policy="fire",
        ),
        retraction_policy=RetractionPolicy(
            safety_window_minutes=60, action="cancel_and_alert"
        ),
        needs_disambiguation=False,
    )


def _tier3_parsed() -> ParsedSpec:
    return ParsedSpec(
        description="Trump wins the 2028 US presidential election",
        tier="tier3",
        keyword_set=KeywordSet(must_have_one=["Trump", "wins"]),
        resolution_criteria=ResolutionCriteria(
            min_secondary_confirmations=1, conflict_policy="hold"
        ),
        retraction_policy=RetractionPolicy(
            safety_window_minutes=240, action="cancel_pending_approvals"
        ),
        needs_disambiguation=True,
    )


def test_tier1_creates_draft_no_session(db):
    spec, session = specs_mod.create_spec_from_parsed(
        db, user_id=1, parsed=_tier1_parsed()
    )
    db.commit()
    assert spec.state == "draft"
    assert session is None
    assert spec.workflow_id is None


def test_tier3_creates_pending_with_session(db):
    spec, session = specs_mod.create_spec_from_parsed(
        db, user_id=1, parsed=_tier3_parsed()
    )
    db.commit()
    assert spec.state == "pending_disambiguation"
    assert session is not None
    assert session.state == "open"
    # The pending payload carries the spec_id for navigation.
    assert (session.pending_event_spec or {}).get("_spec_id") == spec.id


def test_tier1_activate_succeeds(db):
    spec, _ = specs_mod.create_spec_from_parsed(
        db, user_id=1, parsed=_tier1_parsed()
    )
    db.commit()
    activated = specs_mod.activate_spec(db, spec_id=spec.id, user_id=1)
    assert activated.state == "active"


def test_tier3_activate_before_disambiguation_fails(db):
    spec, _ = specs_mod.create_spec_from_parsed(
        db, user_id=1, parsed=_tier3_parsed()
    )
    db.commit()
    with pytest.raises(specs_mod.SpecError) as exc:
        specs_mod.activate_spec(db, spec_id=spec.id, user_id=1)
    assert exc.value.status == 409


def test_disambiguation_answer_flow(db):
    spec, session = specs_mod.create_spec_from_parsed(
        db, user_id=1, parsed=_tier3_parsed()
    )
    db.commit()
    questions = session.questions

    # Answer the first question — spec stays in pending state.
    q0 = questions[0]
    spec1, session1 = specs_mod.record_answer(
        db,
        spec_id=spec.id,
        user_id=1,
        question_id=q0["id"],
        option_id=q0["options"][1]["id"],  # multi_source_consensus
    )
    db.commit()
    assert spec1.state == "pending_disambiguation"
    assert (session1.answers or {}).get(q0["id"]) == q0["options"][1]["id"]

    # Answer the second (final) question — spec flips to draft.
    q1 = questions[1]
    spec2, session2 = specs_mod.record_answer(
        db,
        spec_id=spec.id,
        user_id=1,
        question_id=q1["id"],
        option_id=q1["options"][0]["id"],  # cancel_pending_approvals
    )
    db.commit()
    assert spec2.state == "draft"
    assert session2.state == "completed"
    # The mutation landed.
    assert spec2.resolution_criteria["min_secondary_confirmations"] == 1


def test_disambiguation_cross_user_blocked(db):
    spec, _ = specs_mod.create_spec_from_parsed(
        db, user_id=1, parsed=_tier3_parsed()
    )
    db.commit()
    with pytest.raises(specs_mod.SpecError) as exc:
        specs_mod.record_answer(
            db,
            spec_id=spec.id,
            user_id=99,
            question_id="exact_event",
            option_id="first_major_call",
        )
    assert exc.value.status == 404


def test_cancel_is_idempotent(db):
    spec, _ = specs_mod.create_spec_from_parsed(
        db, user_id=1, parsed=_tier1_parsed()
    )
    db.commit()
    specs_mod.cancel_spec(db, spec_id=spec.id, user_id=1)
    second = specs_mod.cancel_spec(db, spec_id=spec.id, user_id=1)
    assert second.state == "cancelled"
