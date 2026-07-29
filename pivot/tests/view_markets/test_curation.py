"""Focused unit tests for ``backend.view_markets.curation``.

Exercises the manual authoring/publish path end-to-end against the in-memory
SQLite test DB (the six View-Markets tables are created by the parent
conftest). ``transmission.persist_transmission`` is the real (DB-free-of-
siblings) dependency and is used as-is; the two delegating attach helpers
(``attach_confidence`` / ``attach_expectations``) are tested by monkeypatching
their sibling persist functions, which are still being built in parallel.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    ConfidenceDimension,
    MarketView,
    ViewConfidence,
    ViewExpectation,
    ViewExpression,
    ViewStatus,
    ViewTransmission,
)
from backend.schemas import (
    MarketViewCreate,
    MarketViewPatch,
    ViewConfidenceInput,
    ViewExpectationInput,
    ViewExpressionInput,
    ViewTransmissionInput,
)
from backend.view_markets import curation


# ── builders ──────────────────────────────────────────────────────────


def _full_expression(tier: str = "balanced") -> ViewExpressionInput:
    return ViewExpressionInput(
        tier=tier,
        expression_kind="basket",
        config={"weights": {"INFY": 50, "TCS": 50}},
        rationale="exporters gain on a weaker rupee",
        risk_profile="moderate; sector-concentrated",
        capital_intensity="~1L for a 2-name basket",
        historical_strength=" rho 0.6 to USDINR over 1y",
        time_horizon="3 months",
    )


def _full_payload(view_type: str = "event") -> MarketViewCreate:
    return MarketViewCreate(
        view_type=view_type,
        title="INR weakens past 86 by Sept expiry",
        thesis="A widening trade deficit pressures the rupee vs NIFTY exporters.",
        category="macro",
        time_horizon="3 months",
        resolution_date=datetime(2026, 9, 25, tzinfo=timezone.utc),
        expressions=[_full_expression()],
        transmission=[
            ViewTransmissionInput(
                seq=0,
                from_node="a falling rupee",
                to_node="INFY",
                edge_label="FX tailwind to USD revenue",
                evidence="exporter margins expand",
            )
        ],
        confidence=[
            ViewConfidenceInput(
                dimension="outcome", score=0.6, evidence="analog hit-rate 0.6"
            ),
            ViewConfidenceInput(
                dimension="expression", score=0.55, evidence="CAAR aligned"
            ),
        ],
        expectations=[
            ViewExpectationInput(
                source="model",
                expected_value=85.4,
                user_view_value=86.0,
                surprise_sign="positive",
            )
        ],
    )


# ── create_view ───────────────────────────────────────────────────────


def test_create_view_minimal(view_db: Session) -> None:
    payload = MarketViewCreate(view_type="theme", title="India capex upcycle")
    view = curation.create_view(view_db, payload)

    assert view.id
    assert view.user_id is None  # curated views stay NULL (PLAN §7.4)
    assert view.status == ViewStatus.open
    assert view.published_at is None


def test_create_view_with_children(view_db: Session) -> None:
    view = curation.create_view(view_db, _full_payload())

    assert (
        view_db.query(ViewExpression).filter_by(view_id=view.id).count() == 1
    )
    assert (
        view_db.query(ViewTransmission).filter_by(view_id=view.id).count() == 1
    )
    assert (
        view_db.query(ViewConfidence).filter_by(view_id=view.id).count() == 2
    )
    assert (
        view_db.query(ViewExpectation).filter_by(view_id=view.id).count() == 1
    )


def test_create_view_accepts_user_id_passthrough(view_db: Session) -> None:
    payload = MarketViewCreate(view_type="theme", title="t")
    view = curation.create_view(view_db, payload, user_id=42)
    assert view.user_id == 42  # forward-compat path is honoured, not forced


# ── update_view ───────────────────────────────────────────────────────


def test_update_view_patches_set_fields_only(view_db: Session) -> None:
    view = curation.create_view(
        view_db, MarketViewCreate(view_type="theme", title="old", thesis="keep")
    )
    updated = curation.update_view(
        view_db, view.id, MarketViewPatch(title="new", status="consensus")
    )
    assert updated.title == "new"
    assert updated.status == ViewStatus.consensus
    assert updated.thesis == "keep"  # untouched (exclude_unset)


def test_update_view_missing_raises(view_db: Session) -> None:
    with pytest.raises(curation.CurationError):
        curation.update_view(view_db, "no-such-id", MarketViewPatch(title="x"))


# ── attach_* ──────────────────────────────────────────────────────────


def test_attach_transmission_replaces(view_db: Session) -> None:
    view = curation.create_view(
        view_db, MarketViewCreate(view_type="theme", title="t")
    )
    curation.attach_transmission(
        view_db,
        view.id,
        [ViewTransmissionInput(from_node="a", to_node="b")],
    )
    rows = curation.attach_transmission(
        view_db,
        view.id,
        [ViewTransmissionInput(from_node="c", to_node="d")],
        replace=True,
    )
    assert len(rows) == 1
    assert rows[0].from_node == "c"
    assert (
        view_db.query(ViewTransmission).filter_by(view_id=view.id).count() == 1
    )


def test_attach_expressions_appends(view_db: Session) -> None:
    view = curation.create_view(
        view_db, MarketViewCreate(view_type="theme", title="t")
    )
    curation.attach_expressions(view_db, view.id, [_full_expression("conservative")])
    curation.attach_expressions(view_db, view.id, [_full_expression("aggressive")])
    assert (
        view_db.query(ViewExpression).filter_by(view_id=view.id).count() == 2
    )


def test_attach_expressions_missing_disclosure_raises(view_db: Session) -> None:
    view = curation.create_view(
        view_db, MarketViewCreate(view_type="theme", title="t")
    )
    bad = ViewExpressionInput(
        tier="balanced", expression_kind="basket", rationale="why"
    )  # risk_profile/capital_intensity/... missing
    with pytest.raises(curation.CurationError) as exc:
        curation.attach_expressions(view_db, view.id, [bad])
    assert "risk_profile" in str(exc.value)


def test_attach_confidence_delegates(
    view_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.view_markets import confidence as confidence_mod

    view = curation.create_view(
        view_db, MarketViewCreate(view_type="theme", title="t")
    )
    sentinel = [object()]
    captured: dict = {}

    def _fake_persist(db, view_id, two_dial):  # noqa: ANN001
        captured["view_id"] = view_id
        captured["two_dial"] = two_dial
        return sentinel

    monkeypatch.setattr(confidence_mod, "persist_confidence", _fake_persist)
    out = curation.attach_confidence(view_db, view.id, two_dial="DIAL")
    assert out is sentinel
    assert captured == {"view_id": view.id, "two_dial": "DIAL"}


def test_attach_expectations_delegates(
    view_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.view_markets import expectations as expectations_mod

    view = curation.create_view(
        view_db, MarketViewCreate(view_type="theme", title="t")
    )
    sentinel = [object()]
    captured: dict = {}

    def _fake_persist(db, view_id, framing, *, replace=True):  # noqa: ANN001
        captured["view_id"] = view_id
        captured["replace"] = replace
        return sentinel

    monkeypatch.setattr(
        expectations_mod, "persist_expectations", _fake_persist
    )
    out = curation.attach_expectations(
        view_db, view.id, framing="FRAMING", replace=False
    )
    assert out is sentinel
    assert captured == {"view_id": view.id, "replace": False}


# ── validate_for_review ───────────────────────────────────────────────


def test_validate_passes_for_complete_event_view(view_db: Session) -> None:
    view = curation.create_view(view_db, _full_payload("event"))
    result = curation.validate_for_review(view_db, view)
    assert result.ok, result.failures
    assert result.failures == ()
    assert all(result.checks.values())


def test_validate_collects_failures(view_db: Session) -> None:
    # Bare event view: missing thesis, horizon, resolution_date, transmission,
    # expression, both confidence dims, and expectations.
    view = curation.create_view(
        view_db, MarketViewCreate(view_type="event", title="bare")
    )
    result = curation.validate_for_review(view_db, view)
    assert not result.ok
    assert result.checks["measurable_title"] is True
    assert result.checks["thesis"] is False
    assert result.checks["resolution_date"] is False
    assert result.checks["transmission_edge"] is False
    assert result.checks["expression_with_disclosures"] is False
    assert result.checks["confidence_both_dims"] is False
    assert result.checks["expectations_row"] is False


def test_validate_theme_skips_objective_only_checks(view_db: Session) -> None:
    # Theme view needs no resolution_date / expectations row.
    payload = _full_payload("theme")
    payload = payload.model_copy(
        update={"resolution_date": None, "expectations": []}
    )
    view = curation.create_view(view_db, payload)
    result = curation.validate_for_review(view_db, view)
    assert result.ok, result.failures
    assert "resolution_date" not in result.checks
    assert "expectations_row" not in result.checks


def test_validate_requires_both_confidence_dims(view_db: Session) -> None:
    payload = _full_payload("event").model_copy(
        update={
            "confidence": [
                ViewConfidenceInput(dimension="outcome", score=0.5)
            ]
        }
    )
    view = curation.create_view(view_db, payload)
    result = curation.validate_for_review(view_db, view)
    assert result.checks["confidence_both_dims"] is False


# ── publish / unpublish ───────────────────────────────────────────────


def test_publish_gate_blocks_incomplete(view_db: Session) -> None:
    view = curation.create_view(
        view_db, MarketViewCreate(view_type="event", title="bare")
    )
    with pytest.raises(curation.CurationGateError) as exc:
        curation.publish_view(view_db, view.id)
    assert exc.value.failures  # carries the failing checks
    refreshed = view_db.get(MarketView, view.id)
    assert refreshed.published_at is None
    assert refreshed.status == ViewStatus.open


def test_publish_force_bypasses_gate(view_db: Session) -> None:
    view = curation.create_view(
        view_db, MarketViewCreate(view_type="event", title="bare")
    )
    published = curation.publish_view(view_db, view.id, force=True)
    assert published.published_at is not None
    assert published.status == ViewStatus.developing


def test_publish_complete_view(view_db: Session) -> None:
    view = curation.create_view(view_db, _full_payload("event"))
    published = curation.publish_view(view_db, view.id, reviewer_id=7)
    assert published.published_at is not None
    assert published.status == ViewStatus.developing


def test_publish_is_idempotent_on_status_and_timestamp(view_db: Session) -> None:
    view = curation.create_view(view_db, _full_payload("event"))
    first = curation.publish_view(view_db, view.id)
    stamp = first.published_at
    # Move past developing, then re-publish: timestamp + advanced status kept.
    curation.update_view(view_db, view.id, MarketViewPatch(status="consensus"))
    again = curation.publish_view(view_db, view.id)
    assert again.published_at == stamp
    assert again.status == ViewStatus.consensus


def test_unpublish_resets(view_db: Session) -> None:
    view = curation.create_view(view_db, _full_payload("event"))
    curation.publish_view(view_db, view.id)
    out = curation.unpublish_view(view_db, view.id)
    assert out.published_at is None
    assert out.status == ViewStatus.open


# ── list_curation_queue ───────────────────────────────────────────────


def test_list_curation_queue_filters(view_db: Session) -> None:
    draft = curation.create_view(view_db, _full_payload("event"))
    live = curation.create_view(view_db, _full_payload("event"))
    curation.publish_view(view_db, live.id)
    # A user-authored view must NOT appear in the curated queue.
    curation.create_view(
        view_db, MarketViewCreate(view_type="theme", title="u"), user_id=99
    )

    all_curated = curation.list_curation_queue(view_db)
    assert {v.id for v in all_curated} == {draft.id, live.id}

    published = curation.list_curation_queue(view_db, published=True)
    assert [v.id for v in published] == [live.id]

    unpublished = curation.list_curation_queue(view_db, published=False)
    assert [v.id for v in unpublished] == [draft.id]


def test_dimension_enum_roundtrip(view_db: Session) -> None:
    # Guard: confidence rows persist as the enum, so the dim-set membership
    # check in validate compares like-for-like.
    view = curation.create_view(view_db, _full_payload("event"))
    rows = view_db.query(ViewConfidence).filter_by(view_id=view.id).all()
    assert {r.dimension for r in rows} == {
        ConfidenceDimension.outcome,
        ConfidenceDimension.expression,
    }
