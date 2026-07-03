"""End-to-end integration tests for the Phase-3 expression *dispatch*.

Exercises the ONE public entry — ``dispatch.suggest_expressions(db, view, tier?)``
— from a curated ``MarketView`` all the way to persisted ``ViewExpression`` rows,
asserting the contract the rest of the engine relies on:

  * a curated view yields a clean **3-tier ladder** (Conservative / Balanced /
    Aggressive), one persisted row each;
  * every row carries the **five disclosures** non-blank (same gate as
    ``curation._missing_disclosures``) and the pinned **config envelope**;
  * the engine is **NOT "always a basket"** — at least one tier is a NON-basket
    structure (pair / option) for the rate-event view;
  * a RELATIVE view's short leg is routed through **honest_short** — never a
    fabricated cash-delivery short — and an un-tradeable short **degrades honestly**
    to a defined-risk put proxy;
  * **register-not-execute** is preserved: dispatch only describes an *armed*
    workflow (``config.timing`` SPEC), creates no workflow and arms no order
    (``workflow_id`` / ``backtest_run_id`` stay ``None``, left to Phase 4), and
    never emits a prediction-market (PROGA) trigger.

All market/engine access is mocked at the builders' seams — no Kite, no broker,
no network. The pairs engine is faked everywhere; the option engine is faked for
the event view; the RELATIVE honest-short tests run the REAL ``honest_short``
decision rule on the resolved short symbol.
"""
from __future__ import annotations

from typing import Any, Callable

import pytest
from sqlalchemy.orm import Session

from backend.models import ExpressionKind, ExpressionTier, MarketView, ViewExpression
from backend.services import trading_costs
from backend.view_markets import implied_move as _im
from backend.view_markets.expressions import dispatch, honest_short
from backend.view_markets.expressions.builders import option_builder, pair_builder

# Valid honest-short vehicle modes (the ShortMode literal set).
_SHORT_MODES = {
    "ssf_future", "put", "put_spread", "index_future", "index_put",
    "commodity_future", "commodity_put", "avoid",
}


# ── Engine fakes (no network) ────────────────────────────────────────────────


def _fake_pairs_backtest(a: str, b: str, **_kw: Any) -> dict[str, Any]:
    """A representative cointegration payload (only keys the builder reads)."""
    return {
        "cointegration": {
            "alpha": 0.12, "beta": 0.85, "adf_tstat": -3.9,
            "half_life_days": 9.0, "cointegrated_at": "1%",
        },
        "metrics": {}, "series": {},
    }


def _fake_resolve_strategy(
    db: Any, underlying: str, template_name: str, *,
    expiry: Any = None, qty_lots: int = 1,
    explicit_legs: Any = None, chain: Any = None,
) -> dict[str, Any]:
    """A deterministic defined-risk option payload (bounded max loss)."""
    sides = ("BUY", "SELL", "SELL", "BUY") if explicit_legs else ("BUY", "SELL")
    legs = [
        {
            "option_type": "CE", "side": s, "strike": 50000.0 + 100 * i,
            "mid": 120.0, "iv": 0.16, "delta": 0.4, "iv_status": "ok",
            "tradingsymbol": f"{underlying}OPT{i}", "instrument_token": 1000 + i,
        }
        for i, s in enumerate(sides)
    ]
    return {
        "locked": {
            "underlying": underlying, "segment": "NFO-OPT", "exchange": "NFO",
            "lot_size": 25, "expiry": "2026-07-30",
        },
        "editable": {"template": template_name, "qty_lots": qty_lots, "legs": legs},
        "computed": {
            "net_premium": -2400.0, "max_loss": 5000.0, "max_profit": 8000.0,
            "pop": 0.55, "breakevens": [50250.0],
            "net_greeks": {"delta": 10.0, "gamma": 0.1, "theta": -50.0, "vega": 30.0},
            "capital_required": 2400.0, "margin_estimate": 2400.0,
        },
        "critique": {"verdict": "ok", "flags": [], "summary": "fine"},
        "validation": {"liquidity_flags": []},
    }


def _fake_implied_move(
    db: Any, underlying: str, *, expiry: Any = None,
    horizon_days: Any = None, width: int = 10,
) -> _im.ImpliedMove:
    return _im.ImpliedMove(
        underlying=underlying, expiry=expiry, forward=50000.0, atm_strike=50000.0,
        atm_iv=0.16, t_years=0.08, expected_move_abs=1200.0, expected_move_pct=2.4,
        low=48800.0, high=51200.0, straddle_price=1400.0, source="iv", asof=None,
    )


@pytest.fixture
def patch_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake ONLY the pairs engine — the real ``honest_short`` rule still runs."""
    monkeypatch.setattr(pair_builder, "run_pairs_backtest", _fake_pairs_backtest)


@pytest.fixture
def patch_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake the pairs + option engines + option costs (no chain, no network)."""
    monkeypatch.setattr(pair_builder, "run_pairs_backtest", _fake_pairs_backtest)
    monkeypatch.setattr(option_builder._opt, "resolve_strategy", _fake_resolve_strategy)
    monkeypatch.setattr(option_builder._im, "implied_move", _fake_implied_move)
    monkeypatch.setattr(trading_costs, "option_leg_bps", lambda side, **k: 3.0)


# ── helpers ──────────────────────────────────────────────────────────────────


def _persisted(db: Session, view: MarketView) -> list[ViewExpression]:
    """The view's expression rows read back from the table (not the rel cache)."""
    return (
        db.query(ViewExpression)
        .filter(ViewExpression.view_id == view.id)
        .all()
    )


def _assert_full_disclosures(row: ViewExpression) -> None:
    for field_ in (
        "rationale", "risk_profile", "capital_intensity",
        "historical_strength", "time_horizon",
    ):
        value = getattr(row, field_)
        assert isinstance(value, str) and value.strip(), (
            f"{row.tier.value} expression has a blank {field_}"
        )


def _assert_envelope_shape(row: ViewExpression) -> None:
    cfg = row.config
    assert cfg["schema_version"] == 1
    assert cfg["expression_kind"] == row.expression_kind.value
    assert cfg["tier"] == row.tier.value
    assert cfg["archetype"]
    assert isinstance(cfg["instruments"], list) and cfg["instruments"]
    assert isinstance(cfg["structure"], dict) and cfg["structure"]
    assert cfg["disclaimer"] and "not financial advice" in cfg["disclaimer"].lower()


def _assert_register_not_execute(row: ViewExpression) -> None:
    """Dispatch arms nothing: no workflow/backtest ids, only a trigger SPEC, and
    never a prediction-market (PROGA) trigger."""
    assert row.workflow_id is None
    assert row.backtest_run_id is None
    timing = row.config["timing"]
    assert timing["tranches"], "timing SPEC carries no tranches"
    note = (timing.get("note") or "").lower()
    assert "not executed" in note or "armed" in note
    pct_total = 0
    for tranche in timing["tranches"]:
        trig = tranche["trigger"]
        step = trig["step_type"]
        assert step.startswith("trigger."), step
        assert step not in ("trigger.polymarket", "trigger.kalshi"), step
        pct_total += int(tranche["pct"])
    assert pct_total == 100


def _no_fabricated_delivery_short(row: ViewExpression) -> None:
    """No instrument is a tradeable cash-delivery single-stock/ETF short."""
    for inst in row.config["instruments"]:
        if inst.get("role") == "short":
            fabricated = (
                inst.get("instrument_type") in ("equity", "etf")
                and inst.get("tradeable") is True
            )
            assert not fabricated, f"fabricated delivery short: {inst}"


# ── EVENT view: 3-tier ladder, non-basket present, register-not-execute ──────


def test_event_view_persists_three_full_tiers(
    view_db: Session, event_view: MarketView, patch_engines: None,
) -> None:
    rows = dispatch.suggest_expressions(view_db, event_view)

    # Exactly one expression per tier, in card order, all persisted + queryable.
    assert len(rows) == 3
    assert [r.tier for r in rows] == [
        ExpressionTier.conservative, ExpressionTier.balanced, ExpressionTier.aggressive,
    ]
    assert len(_persisted(view_db, event_view)) == 3

    for row in rows:
        assert isinstance(row.id, str) and row.id  # flushed
        _assert_full_disclosures(row)
        _assert_envelope_shape(row)
        _assert_register_not_execute(row)


def test_event_view_is_not_always_a_basket(
    view_db: Session, event_view: MarketView, patch_engines: None,
) -> None:
    rows = dispatch.suggest_expressions(view_db, event_view)
    kinds = {r.expression_kind for r in rows}
    # The rate event resolves to defined-risk options + an NBFC-vs-bank pair —
    # explicitly NOT three baskets.
    assert ExpressionKind.basket not in kinds
    assert ExpressionKind.pair in kinds  # the balanced NBFC-vs-bank pair
    assert ExpressionKind.option_strategy in kinds


def test_event_balanced_is_the_nbfc_bank_pair(
    view_db: Session, event_view: MarketView, patch_engines: None,
) -> None:
    rows = dispatch.suggest_expressions(view_db, event_view)
    balanced = next(r for r in rows if r.tier == ExpressionTier.balanced)
    assert balanced.expression_kind == ExpressionKind.pair
    assert balanced.config["archetype"] == "E2_nbfc_bank_pair"
    # Its short leg goes through honest_short (no fabricated delivery short).
    short = balanced.config["structure"]["short_leg"]
    assert short["mode"] in _SHORT_MODES
    _no_fabricated_delivery_short(balanced)


# ── tier selection (single tier) ─────────────────────────────────────────────


def test_single_tier_builds_one_row(
    view_db: Session, event_view: MarketView, patch_engines: None,
) -> None:
    rows = dispatch.suggest_expressions(
        view_db, event_view, tier=ExpressionTier.conservative.value,
    )
    assert len(rows) == 1
    assert rows[0].tier == ExpressionTier.conservative
    assert len(_persisted(view_db, event_view)) == 1


def test_unknown_tier_raises(
    view_db: Session, event_view: MarketView, patch_engines: None,
) -> None:
    with pytest.raises(dispatch.ExpressionDispatchError):
        dispatch.suggest_expressions(view_db, event_view, tier="reckless")


# ── RELATIVE view: honest short on every short leg ───────────────────────────


def test_relative_view_short_leg_uses_honest_short(
    view_db: Session, relative_view: MarketView, patch_pairs: None,
) -> None:
    rows = dispatch.suggest_expressions(view_db, relative_view)

    assert len(rows) == 3
    for row in rows:
        # A relative view expresses as a market-neutral PAIR (a non-basket).
        assert row.expression_kind == ExpressionKind.pair
        _assert_full_disclosures(row)
        _assert_register_not_execute(row)

        short = row.config["structure"]["short_leg"]
        assert short["mode"] in _SHORT_MODES
        # honest_short is the ONLY producer of the short leg (no fabricated short).
        assert short["instrument"]
        _no_fabricated_delivery_short(row)

        # expressability mirrors the short leg's honesty.
        expr = row.config["expressability"]
        assert expr["short_mode"] == short["mode"]
        assert expr["degraded"] == short["degraded"]


def test_relative_index_short_is_a_clean_index_future(
    view_db: Session, relative_view: MarketView, patch_pairs: None,
) -> None:
    """"IT outperforms the Nifty" → short the index via its NFO future (the legal
    index short), tradeable + not degraded — never an ETF/cash delivery short."""
    rows = dispatch.suggest_expressions(view_db, relative_view)
    index_shorts = [
        r for r in rows
        if r.config["structure"]["short_leg"]["mode"] == "index_future"
    ]
    assert index_shorts, "expected at least one index-future short leg"
    for row in index_shorts:
        short = row.config["structure"]["short_leg"]
        assert short["tradeable"] is True
        assert short["degraded"] is False


def test_relative_single_stock_short_degrades_to_honest_put(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
    patch_pairs: None,
) -> None:
    """A relative view whose short leg is a single stock (eligibility unknown)
    degrades to a DEFINED-RISK long put proxy — flagged ``degraded``, carrying the
    single-stock-option microstructure warning — never a fabricated delivery short."""
    view = make_curated_view(
        view_type="relative",
        title="Defensives beat cyclicals into a growth slowdown",
        thesis=(
            "A growth slowdown rewards staples/pharma and punishes high-beta "
            "cyclicals like autos and metals."
        ),
        category="relative_value",
        time_horizon="6m",
    )
    rows = dispatch.suggest_expressions(view_db, view)

    degraded = [
        r for r in rows
        if r.config["structure"]["short_leg"]["degraded"]
    ]
    assert degraded, "expected the single-stock short to degrade honestly"
    for row in degraded:
        short = row.config["structure"]["short_leg"]
        assert short["mode"] == "put"  # the deliverable-safe defined-risk proxy
        assert short["tradeable"] is True
        assert honest_short.SINGLE_STOCK_OPTION_WARNING in short["warnings"]
        _no_fabricated_delivery_short(row)


# ── disclosure gate parity with curation ─────────────────────────────────────


def test_dispatched_rows_pass_the_curation_disclosure_gate(
    view_db: Session, event_view: MarketView, patch_engines: None,
) -> None:
    """Every dispatched expression satisfies the SAME blank-rule the curation
    publish gate enforces (``_is_blank``)."""
    from backend.view_markets.curation import _DISCLOSURE_FIELDS, _is_blank

    rows = dispatch.suggest_expressions(view_db, event_view)
    for row in rows:
        for field_ in _DISCLOSURE_FIELDS:
            assert not _is_blank(getattr(row, field_))


def test_view_with_no_tier_plan_raises(view_db: Session) -> None:
    """A view whose type has no tier plan is refused honestly (no silent empty)."""

    class _StubViewType:
        value = "macro_unknown"

    class _StubView:
        view_type = _StubViewType()
        id = "stub-view-id"

    with pytest.raises(dispatch.ExpressionDispatchError):
        dispatch.suggest_expressions(view_db, _StubView())  # type: ignore[arg-type]
