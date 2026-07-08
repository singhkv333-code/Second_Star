"""View Markets — Phase 2 END-TO-END integration test.

Exercises the whole composition flow with NO network (option chain + event-study
OHLCV are monkeypatched, the verifier is stubbed):

    curation.create_view (EVENT)
      -> transmission.seed_transmission_from_scenario -> attach_transmission
      -> expectations.compute_surprise (implied-move PRIMARY, PM odds HIDDEN)
         -> attach_expectations
      -> event_study.run_event_study (mocked closes)
      -> confidence.two_dial_score (two SEPARATE dials, suppressed < MinTRL)
         -> attach_confidence
      -> curation.validate_for_review -> publish_view
      -> lifecycle.advance_one_view / advance_view_lifecycle (status ladder)

Key invariants asserted:
  * the two confidence dials stay SEPARATE (never averaged into one scalar),
  * the score is SUPPRESSED below MinTRL (a thin analog sample => insufficient_
    data => both dials None),
  * the prediction-market prior is HIDDEN (never persisted as a user-facing
    expectation row),
  * the lifecycle advances open->developing->consensus->resolved->archived and
    REFUSES to resolve without a confirmed outcome (no fabrication).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.orm import Session

from backend.models import (
    ConfidenceDimension,
    ExpectationSource,
    ViewStatus,
)
from backend.schemas import MarketViewCreate, ViewExpressionInput
from backend.view_markets import confidence as _confidence
from backend.view_markets import curation as _curation
from backend.view_markets import expectations as _expectations
from backend.view_markets import feeds as _feeds
from backend.view_markets import lifecycle as _lifecycle
from backend.view_markets import transmission as _transmission
from backend.view_markets.event_study import EventStudyWindows, run_event_study
from backend.view_markets.expectations import (
    SurpriseFraming,
    compute_surprise,
)
from backend.view_markets.feeds import AnalogEvent
from backend.view_markets.implied_move import ImpliedMove

_BANK_INSTRUMENTS = ("HDFCBANK", "ICICIBANK", "BAJFINANCE")
_BENCHMARK = "NIFTY"
_WINDOWS = EventStudyWindows()


# ── synthetic-market fixtures (no network) ─────────────────────────────


def _make_closes(
    *,
    symbols: tuple[str, ...],
    benchmark: str = _BENCHMARK,
    event_pos: int = 200,
    n_days: int = 320,
    event_jump: float = 0.05,
    seed: int = 11,
) -> tuple[dict[str, pd.Series], "date"]:  # noqa: F821 - date via pandas
    """Aligned symbol+benchmark close series with a known abnormal jump at t=0."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n_days)
    mkt_ret = rng.normal(0.0003, 0.01, size=n_days)
    mkt_px = 100.0 * np.cumprod(1.0 + mkt_ret)
    closes: dict[str, pd.Series] = {benchmark: pd.Series(mkt_px, index=idx)}
    for i, sym in enumerate(symbols):
        idio = rng.normal(0.0, 0.006, size=n_days)
        sym_ret = 1.0 * mkt_ret + idio
        sym_ret[event_pos] += event_jump  # the abnormal shock at t=0
        closes[sym] = pd.Series(
            (100.0 + 10 * i) * np.cumprod(1.0 + sym_ret), index=idx
        )
    return closes, idx[event_pos].date()


def _patch_closes(
    monkeypatch: pytest.MonkeyPatch, closes: dict[str, pd.Series]
) -> None:
    monkeypatch.setattr(
        "backend.core.data.historical.get_close_dict",
        lambda symbols, period="1y": {
            s: closes[s] for s in symbols if s in closes
        },
    )


def _fake_implied_move(forward: float = 50000.0) -> ImpliedMove:
    """A deterministic option-implied move (stands in for a live chain read)."""
    return ImpliedMove(
        underlying="BANKNIFTY",
        expiry="2026-07-30",
        forward=forward,
        atm_strike=round(forward / 100) * 100,
        atm_iv=0.16,
        t_years=0.06,
        expected_move_abs=forward * 0.04,
        expected_move_pct=0.04,
        low=forward * 0.96,
        high=forward * 1.04,
        straddle_price=forward * 0.034,
        source="iv",
        asof="2026-06-29T00:00:00Z",
    )


def _patch_implied_move(
    monkeypatch: pytest.MonkeyPatch,
    *,
    move: Optional[ImpliedMove] = None,
    prob: Optional[float] = 0.55,
) -> None:
    mv = move if move is not None else _fake_implied_move()
    monkeypatch.setattr(
        "backend.view_markets.implied_move.implied_move",
        lambda db, underlying, **kw: mv,
    )
    monkeypatch.setattr(
        "backend.view_markets.implied_move.implied_probability",
        lambda db, underlying, **kw: prob,
    )


def _analog_events(event_date) -> list[AnalogEvent]:
    """Three analog RBI events clustered around the synthetic shock day."""
    return [
        AnalogEvent(
            tag="rbi_mpc",
            event_date=event_date - timedelta(days=5 * k),
            label=f"RBI MPC analog #{k}",
            meta={"source": "test"},
        )
        for k in range(3)
    ]


def _expressions() -> list[ViewExpressionInput]:
    disclosures = dict(
        rationale="banks gain on credit growth + treasury gains in an easing cycle",
        risk_profile="large-cap, liquid, no leverage",
        capital_intensity="fully-funded long basket",
        historical_strength="banks led past easing cycles (regime-dependent)",
        time_horizon="3-6 months",
    )
    return [
        ViewExpressionInput(
            tier="conservative",
            expression_kind="basket",
            config={"weights": [{"symbol": "HDFCBANK", "weight": 100}]},
            **disclosures,
        ),
    ]


def _author_event_view(db: Session, *, resolution_date: datetime):
    """create_view + attach transmission/expressions for an RBI rate-cut view."""
    view = _curation.create_view(
        db,
        MarketViewCreate(
            view_type="event",
            title="RBI cuts the repo rate at the next MPC meeting",
            thesis="A rate-cut cycle lowers funding costs; banks/NBFCs benefit.",
            category="rbi_mpc",
            time_horizon="3-6 months",
            resolution_date=resolution_date,
        ),
    )
    edges = _transmission.seed_transmission_from_scenario(
        "rate_cut", include_losers=True
    )
    _curation.attach_transmission(db, view.id, edges, replace=True)
    _curation.attach_expressions(db, view.id, _expressions(), replace=True)
    return view


# ── 1) full pipeline: author -> score -> publish (suppressed < MinTRL) ──


def test_full_pipeline_author_score_publish(
    view_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_implied_move(monkeypatch)
    closes, event_date = _make_closes(symbols=_BANK_INSTRUMENTS)
    _patch_closes(monkeypatch, closes)

    resolution_date = datetime.now(timezone.utc) + timedelta(days=20)
    view = _author_event_view(view_db, resolution_date=resolution_date)

    # Expectations: implied-move PRIMARY (source=model), user view below priced.
    framing = compute_surprise(
        view_db,
        underlying="BANKNIFTY",
        user_view_value=48000.0,
        target_level=51000.0,
        direction="above",
    )
    assert framing.source == "model"
    assert framing.expected_value == pytest.approx(50000.0)
    assert framing.implied_probability == pytest.approx(0.55)
    assert framing.hidden_prior is None  # no PM read in the sync path
    _curation.attach_expectations(view_db, view.id, framing, replace=True)

    # Event study on the bank instruments (thin analog sample).
    es_result = run_event_study(
        view_db,
        instruments=_BANK_INSTRUMENTS,
        analog_events=_analog_events(event_date),
        benchmark=_BENCHMARK,
        windows=_WINDOWS,
    )
    # A few-event window is below the Trust battery floor -> insufficient_data.
    assert es_result.verdict.get("verdict") == "insufficient_data"

    # Two-dial confidence: SEPARATE dials, both SUPPRESSED below MinTRL.
    two_dial = _confidence.two_dial_score(
        event_study=es_result, surprise=framing
    )
    assert two_dial.outcome is not two_dial.expression
    assert two_dial.outcome.dimension == "outcome"
    assert two_dial.expression.dimension == "expression"
    assert two_dial.outcome.suppressed and two_dial.outcome.score is None
    assert two_dial.expression.suppressed and two_dial.expression.score is None
    # No combined/averaged scalar exists on the Alignment Score.
    assert set(vars(two_dial)) == {"outcome", "expression", "flags"}

    _curation.attach_confidence(view_db, view.id, two_dial)

    # Review gate passes (suppressed dials still count as scored-or-suppressed).
    gate = _curation.validate_for_review(view_db, view)
    assert gate.ok, gate.failures
    published = _curation.publish_view(view_db, view.id)
    assert published.published_at is not None
    assert published.status == ViewStatus.developing

    # Persisted shape: exactly one user-facing expectation (model), two dials.
    from backend.models import ViewConfidence, ViewExpectation

    exp_rows = (
        view_db.query(ViewExpectation)
        .filter(ViewExpectation.view_id == view.id)
        .all()
    )
    assert len(exp_rows) == 1
    assert exp_rows[0].source == ExpectationSource.model
    conf_dims = {
        c.dimension
        for c in view_db.query(ViewConfidence)
        .filter(ViewConfidence.view_id == view.id)
        .all()
    }
    assert conf_dims == {
        ConfidenceDimension.outcome,
        ConfidenceDimension.expression,
    }


# ── 2) PROGA: the prediction-market prior stays HIDDEN ─────────────────


@pytest.mark.asyncio
async def test_prediction_market_prior_hidden_not_persisted(
    view_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.models import ViewExpectation

    _patch_implied_move(monkeypatch)
    resolution_date = datetime.now(timezone.utc) + timedelta(days=20)
    view = _author_event_view(view_db, resolution_date=resolution_date)

    framing = compute_surprise(view_db, underlying="BANKNIFTY")

    # Flag ON + a Polymarket snapshot -> the odds populate the HIDDEN prior only.
    monkeypatch.setattr(
        "backend.config.settings.polymarket_ws_enabled", True, raising=False
    )

    async def _fake_search(query, *, limit=5):
        return [SimpleNamespace(yes_price=0.62, closed=False)]

    monkeypatch.setattr(
        "backend.news_events.sources.polymarket.search_markets", _fake_search
    )

    augmented = await _expectations.augment_with_prediction_market_prior(
        framing, pm_query="rbi rate cut"
    )
    assert augmented.hidden_prior == pytest.approx(0.62)
    assert augmented.hidden_prior_source == "polymarket"

    # Persist: the PM prior must NOT become a user-facing expectation row.
    _curation.attach_expectations(view_db, view.id, augmented, replace=True)
    rows = (
        view_db.query(ViewExpectation)
        .filter(ViewExpectation.view_id == view.id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].source == ExpectationSource.model  # never polymarket/kalshi
    # The hidden prior is not stored anywhere in the row.
    assert rows[0].expected_value == pytest.approx(50000.0)


# ── 3) two dials produce SEPARATE non-suppressed scores when warranted ──


def test_two_dials_separate_and_distinct(view_db: Session) -> None:
    framing = SurpriseFraming(
        underlying="BANKNIFTY",
        expected_value=50000.0,
        user_view_value=48000.0,
        surprise_sign="negative",
        surprise_magnitude=2000.0,
        implied_probability=0.50,
        source="model",
    )
    two_dial = _confidence.two_dial_score(
        surprise=framing,
        outcome_overrides={
            "hit_rate": 0.70,
            "edge_vs_priced": 0.10,
            "sample_n": 12,
            "verdict": "promising",
        },
        expression_overrides={
            "caar_bhar_alignment": 0.40,
            "significance_p": 0.09,
            "cost_survival": 0.45,
            "payoff_pop": 0.40,
            "verdict": "promising",
        },
    )
    assert two_dial.outcome.score is not None
    assert two_dial.expression.score is not None
    # The two dials answer different questions -> different numbers here.
    assert two_dial.outcome.score != two_dial.expression.score
    # Still never combined.
    assert not hasattr(two_dial, "combined")
    assert not hasattr(two_dial, "overall")


# ── 4) lifecycle ladder (sync, deterministic) ──────────────────────────


def test_lifecycle_developing_consensus_resolved_archived(
    view_db: Session,
) -> None:
    res = datetime.now(timezone.utc) + timedelta(days=10)
    view = _author_event_view(view_db, resolution_date=res)
    # Minimal expectations row so the resolve step has something to backfill.
    _curation.attach_expectations(
        view_db,
        view.id,
        SurpriseFraming(
            underlying="BANKNIFTY",
            expected_value=50000.0,
            user_view_value=48000.0,
            surprise_sign="negative",
            surprise_magnitude=2000.0,
            implied_probability=0.5,
            source="model",
        ),
        replace=True,
    )
    # Publish (force — we're testing the lifecycle, not the gate) -> developing.
    _curation.publish_view(view_db, view.id, force=True)
    assert view.status == ViewStatus.developing

    # Far from resolution: developing holds.
    assert _lifecycle.advance_one_view(view_db, view, now=res - timedelta(days=8)) is None
    assert view.status == ViewStatus.developing

    # Inside the consensus window (but before resolution): -> consensus.
    new = _lifecycle.advance_one_view(view_db, view, now=res - timedelta(days=2))
    assert new == ViewStatus.consensus.value
    assert view.status == ViewStatus.consensus

    # Past resolution but NO confirmed outcome: refuse to resolve (honesty).
    assert _lifecycle.advance_one_view(view_db, view, now=res + timedelta(days=1)) is None
    assert view.status == ViewStatus.consensus

    # Past resolution WITH a confirmed outcome: -> resolved + backfill.
    outcome = SimpleNamespace(matched=True)
    new = _lifecycle.advance_one_view(
        view_db, view, now=res + timedelta(days=1), outcome=outcome
    )
    assert new == ViewStatus.resolved.value
    assert view.status == ViewStatus.resolved
    from backend.models import ViewExpectation

    rows = (
        view_db.query(ViewExpectation)
        .filter(ViewExpectation.view_id == view.id)
        .all()
    )
    assert rows and all(r.resolved_value == pytest.approx(1.0) for r in rows)

    # After the grace period: resolved -> archived.
    new = _lifecycle.advance_one_view(
        view_db, view, now=res + timedelta(days=40)
    )
    assert new == ViewStatus.archived.value
    assert view.status == ViewStatus.archived

    # Archived is terminal.
    assert _lifecycle.advance_one_view(
        view_db, view, now=res + timedelta(days=200)
    ) is None


def test_lifecycle_unpublished_draft_does_not_advance(
    view_db: Session,
) -> None:
    res = datetime.now(timezone.utc) + timedelta(days=2)
    view = _author_event_view(view_db, resolution_date=res)
    assert view.published_at is None
    # A draft (never published) must not move even inside the consensus window.
    assert _lifecycle.advance_one_view(
        view_db, view, now=res - timedelta(days=1)
    ) is None
    assert view.status == ViewStatus.open


# ── 5) async sweep job (module-level, own SessionLocal) ────────────────


@pytest.mark.asyncio
async def test_advance_view_lifecycle_sweep(
    view_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scheduler job sweeps published views and resolves a due EVENT view
    via the (stubbed) verifier read, committing through its own SessionLocal."""
    # Repoint the job's SessionLocal at the test session (shared identity map),
    # mirroring tests/workflows/test_scheduler.py's _scheduler_uses_test_db.
    class _Shared:
        def __init__(self, real: Session) -> None:
            self._real = real

        def __getattr__(self, name: str) -> object:
            if name in ("close", "commit", "rollback"):
                return lambda: None
            return getattr(self._real, name)

    monkeypatch.setattr(
        _lifecycle, "SessionLocal", lambda: _Shared(view_db)
    )

    # A confirmed verifier outcome (no network).
    async def _fake_outcome(kind, expected, **kw):
        return SimpleNamespace(matched=True)

    monkeypatch.setattr(_feeds, "read_event_outcome", _fake_outcome)

    # A published EVENT view already past its resolution date.
    res = datetime.now(timezone.utc) - timedelta(days=1)
    view = _author_event_view(view_db, resolution_date=res)
    _curation.attach_expectations(
        view_db,
        view.id,
        SurpriseFraming(
            underlying="BANKNIFTY",
            expected_value=50000.0,
            user_view_value=48000.0,
            surprise_sign="negative",
            surprise_magnitude=2000.0,
            implied_probability=0.5,
            source="model",
        ),
        replace=True,
    )
    _curation.publish_view(view_db, view.id, force=True)  # -> developing
    view_db.flush()

    summary = await _lifecycle.advance_view_lifecycle()

    assert summary["scanned"] >= 1
    assert summary["errors"] == 0
    assert summary["transitions"].get(ViewStatus.resolved.value, 0) >= 1

    view_db.expire_all()
    refreshed = view_db.get(type(view), view.id)
    assert refreshed is not None
    assert refreshed.status == ViewStatus.resolved
