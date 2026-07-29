"""Focused unit tests for ``backend.view_markets.expectations``.

Siblings (``implied_move`` / ``feeds``) and external PM sources are mocked so
the suite is self-contained — it asserts ONLY this module's surprise
aggregation, PROGA hidden-prior handling, and persistence behaviour. Uses the
shared parent ``db`` fixture (in-memory SQLite with the 6 View-Markets tables
already created).
"""
from __future__ import annotations

import asyncio

import pytest

from backend.models import ExpectationSource, MarketView, ViewExpectation, ViewType
from backend.view_markets import expectations as exp
from backend.view_markets.expectations import (
    SurpriseFraming,
    augment_with_prediction_market_prior,
    backfill_resolved_value,
    compute_surprise,
    persist_expectations,
)


class _FakeImpliedMove:
    """Minimal stand-in for ``implied_move.ImpliedMove`` (only the attrs the
    aggregator reads)."""

    def __init__(self, forward: float):
        self.forward = forward
        self.expected_move_abs = forward * 0.02
        self.expected_move_pct = 0.02
        self.source = "iv"


def _seed_view(db) -> str:
    view = MarketView(view_type=ViewType.event, title="NIFTY post-MPC")
    db.add(view)
    db.flush()
    return view.id


# ── compute_surprise ──────────────────────────────────────────────────


def test_compute_surprise_positive_uses_option_implied_forward(db, monkeypatch):
    # Patch the sibling module symbols the function imports locally.
    import backend.view_markets.implied_move as im_mod

    monkeypatch.setattr(im_mod, "implied_move", lambda *a, **k: _FakeImpliedMove(25000.0))
    monkeypatch.setattr(im_mod, "implied_probability", lambda *a, **k: 0.62)

    framing = compute_surprise(
        db,
        underlying="NIFTY",
        user_view_value=27000.0,   # 8% above priced-in -> positive (> 5% tol)
        target_level=27000.0,
    )
    assert framing.source == "model"
    assert framing.expected_value == 25000.0
    assert framing.user_view_value == 27000.0
    assert framing.surprise_sign == "positive"
    assert framing.surprise_magnitude == pytest.approx(2000.0)
    assert framing.implied_probability == pytest.approx(0.62)
    assert framing.hidden_prior is None


def test_compute_surprise_inline_within_tolerance(db, monkeypatch):
    import backend.view_markets.implied_move as im_mod

    monkeypatch.setattr(im_mod, "implied_move", lambda *a, **k: _FakeImpliedMove(25000.0))
    monkeypatch.setattr(im_mod, "implied_probability", lambda *a, **k: None)

    framing = compute_surprise(
        db,
        underlying="NIFTY",
        user_view_value=25100.0,  # 0.4% -> within default 5% tolerance
    )
    assert framing.surprise_sign == "inline"


def test_compute_surprise_degrades_when_no_chain(db, monkeypatch):
    import backend.view_markets.implied_move as im_mod

    monkeypatch.setattr(im_mod, "implied_move", lambda *a, **k: None)
    monkeypatch.setattr(im_mod, "implied_probability", lambda *a, **k: None)

    framing = compute_surprise(db, underlying="ZZZZ", user_view_value=100.0)
    assert framing.expected_value is None
    assert framing.surprise_sign is None  # can't frame surprise without a ref
    assert any("degraded" in n for n in framing.notes)


def test_compute_surprise_consensus_overrides_when_available(db, monkeypatch):
    import backend.view_markets.feeds as feeds_mod
    import backend.view_markets.implied_move as im_mod

    monkeypatch.setattr(im_mod, "implied_move", lambda *a, **k: _FakeImpliedMove(25000.0))
    monkeypatch.setattr(im_mod, "implied_probability", lambda *a, **k: None)
    monkeypatch.setattr(
        feeds_mod,
        "consensus_for_event",
        lambda tag, **k: feeds_mod.ConsensusPoint(
            metric="cpi", expected_value=5.0, source="consensus", available=True,
        ),
    )

    framing = compute_surprise(
        db, underlying="NIFTY", user_view_value=5.4, consensus_tag="india_cpi",
    )
    assert framing.source == "consensus"
    assert framing.expected_value == 5.0
    assert framing.surprise_sign == "positive"


# ── augment_with_prediction_market_prior (PROGA) ──────────────────────


def _base_framing() -> SurpriseFraming:
    return SurpriseFraming(
        underlying="NIFTY",
        expected_value=25000.0,
        user_view_value=26000.0,
        surprise_sign="positive",
        surprise_magnitude=1000.0,
        implied_probability=0.6,
        source="model",
    )


def test_pm_prior_noop_when_flag_off(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "polymarket_ws_enabled", False, raising=False)
    monkeypatch.setattr(settings, "kalshi_rest_enabled", False, raising=False)

    out = asyncio.run(
        augment_with_prediction_market_prior(_base_framing(), pm_query="nifty up")
    )
    assert out.hidden_prior is None
    assert out.hidden_prior_source is None


def test_pm_prior_set_as_hidden_when_flag_on(monkeypatch):
    from backend.config import settings
    import backend.news_events.sources.polymarket as pm_mod

    monkeypatch.setattr(settings, "polymarket_ws_enabled", True, raising=False)

    class _Snap:
        market_id = "0xabc"
        yes_price = 0.71
        closed = False

    async def _fake_search(query, *, limit=5):
        return [_Snap()]

    monkeypatch.setattr(pm_mod, "search_markets", _fake_search)

    out = asyncio.run(
        augment_with_prediction_market_prior(_base_framing(), pm_query="nifty up")
    )
    assert out.hidden_prior == pytest.approx(0.71)
    assert out.hidden_prior_source == "polymarket"
    # PROGA: user-facing fields are untouched.
    assert out.expected_value == 25000.0
    assert out.source == "model"


def test_pm_prior_failsafe_on_read_error(monkeypatch):
    from backend.config import settings
    import backend.news_events.sources.polymarket as pm_mod

    monkeypatch.setattr(settings, "polymarket_ws_enabled", True, raising=False)

    async def _boom(query, *, limit=5):
        raise RuntimeError("network down")

    monkeypatch.setattr(pm_mod, "search_markets", _boom)

    out = asyncio.run(
        augment_with_prediction_market_prior(_base_framing(), pm_query="x")
    )
    assert out.hidden_prior is None  # never raises out


# ── persist_expectations / backfill ───────────────────────────────────


def test_persist_writes_model_row_only(db):
    view_id = _seed_view(db)
    framing = _base_framing()
    # Hidden prior present — must NOT be persisted (PROGA).
    framing = exp.replace(framing, hidden_prior=0.71, hidden_prior_source="polymarket")

    rows = persist_expectations(db, view_id, framing)
    assert len(rows) == 1
    assert rows[0].source == ExpectationSource.model
    assert rows[0].expected_value == 25000.0
    assert rows[0].user_view_value == 26000.0
    assert rows[0].surprise_sign == "positive"
    assert rows[0].market_id is None

    # No polymarket/kalshi row was written.
    all_rows = db.query(ViewExpectation).filter_by(view_id=view_id).all()
    assert {r.source for r in all_rows} == {ExpectationSource.model}


def test_persist_replace_clears_prior_rows(db):
    view_id = _seed_view(db)
    persist_expectations(db, view_id, _base_framing())
    persist_expectations(db, view_id, _base_framing())  # replace=True default
    rows = db.query(ViewExpectation).filter_by(view_id=view_id).all()
    assert len(rows) == 1


def test_persist_consensus_source_row(db):
    view_id = _seed_view(db)
    framing = exp.replace(_base_framing(), source="consensus", expected_value=5.0)
    rows = persist_expectations(db, view_id, framing)
    assert rows[0].source == ExpectationSource.consensus


def test_backfill_resolved_value(db):
    view_id = _seed_view(db)
    persist_expectations(db, view_id, _base_framing())
    rows = backfill_resolved_value(db, view_id, resolved_value=25750.0)
    assert rows
    assert all(r.resolved_value == 25750.0 for r in rows)
