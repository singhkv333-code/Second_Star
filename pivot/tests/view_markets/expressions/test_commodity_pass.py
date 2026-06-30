"""End-to-end test for the COMMODITY (MCX) pass through ``suggest_expressions``.

Commodities became tradeable via register-not-execute on 2026-06-29. This drives
the ONE public Phase-3 entry — ``dispatch.suggest_expressions(db, view)`` — from a
curated COMMODITY ``MarketView`` to persisted ``ViewExpression`` rows and asserts
the commodity contract the lead's spec pins:

  (a) the engine surfaces the COMMODITY archetypes (CM1–CM6) for a commodity view;
  (b) a commodity SHORT leg is a TRADEABLE MCX future/put (``honest_short``) —
      never an AVOID, never a fabricated cash-delivery short;
  (c) commodity OPTIONS are DEFINED-RISK only (``max_loss`` ``None`` → rejected);
  (d) every commodity expression carries the five disclosures + the LEVERAGE note
      (in ``config.warnings`` AND folded into the ``risk_profile`` column);
  (e) a direct-MCX bullion pair DEGRADES HONESTLY when the data layer has no
      aligned OHLCV (``backtest_available=False``, no fabricated β / half-life / z);
  (f) register-not-execute: dispatch arms a trigger SPEC only — no workflow/order.

All market/engine access is mocked at the builders' seams (no Kite, no broker, no
network). The gold/silver pair runs the REAL ``honest_short`` decision rule.
"""
from __future__ import annotations

from typing import Any, Callable

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    ExpressionKind,
    ExpressionTier,
    MarketView,
    ViewExpression,
)
from backend.services.option_strategies import StrategyResolutionError
from backend.view_markets.expressions import (
    catalog,
    commodities,
    config_schema,
    dispatch,
)
from backend.view_markets.expressions.builders import option_builder, pair_builder

# The tradeable commodity short vehicles (the MCX future / defined-risk put).
_COMMODITY_SHORT_MODES = {"commodity_future", "commodity_put"}


# ── Engine fakes (no network) ────────────────────────────────────────────────


def _fake_pairs_backtest(a: str, b: str, **_kw: Any) -> dict[str, Any]:
    """Representative cointegration payload (only keys the pair builder reads).

    Only equity legs (CM3 producer/importer) ever reach this — the direct-MCX
    bullion legs (CM4) are construct-only and the engine is never called for them.
    """
    return {
        "cointegration": {
            "alpha": 0.10, "beta": 0.85, "adf_tstat": -3.8,
            "half_life_days": 11.0, "cointegrated_at": "5%",
        },
        "metrics": {}, "series": {},
    }


def _fake_resolve_commodity_option(
    db: Any, underlying: str, template_name: str, *,
    expiry: Any = None, qty_lots: int = 1,
    explicit_legs: Any = None, chain: Any = None,
) -> dict[str, Any]:
    """A DEFINED-RISK MCX option payload (the real MCX engine locks MCX values)."""
    sides = ("BUY", "SELL", "SELL", "BUY") if explicit_legs else ("BUY", "SELL")
    legs = [
        {
            "option_type": "CE", "side": s, "strike": 6000.0 + 50 * i,
            "mid": 90.0, "iv": 0.30, "delta": 0.45, "iv_status": "ok",
            "tradingsymbol": f"{underlying}OPT{i}", "instrument_token": 2000 + i,
        }
        for i, s in enumerate(sides)
    ]
    return {
        # The MCX engine locks an MCX segment/exchange (research_only lifted).
        "locked": {
            "underlying": underlying, "segment": "MCX-OPT", "exchange": "MCX",
            "lot_size": 100, "expiry": "2026-07-21",
        },
        "editable": {"template": template_name, "qty_lots": qty_lots, "legs": legs},
        "computed": {
            "net_premium": -2000.0, "max_loss": 4000.0, "max_profit": 6000.0,
            "pop": 0.5, "breakevens": [6050.0],
            "net_greeks": {"delta": 8.0, "gamma": 0.05, "theta": -40.0, "vega": 25.0},
            "capital_required": 2000.0, "margin_estimate": 2000.0,
        },
        "critique": {"verdict": "ok", "flags": [], "summary": "fine"},
        "validation": {"liquidity_flags": []},
    }


def _fake_resolve_unlimited(
    db: Any, underlying: str, template_name: str, **kw: Any,
) -> dict[str, Any]:
    """An UNLIMITED-loss commodity payload — the defined-risk guard MUST reject it."""
    payload = _fake_resolve_commodity_option(db, underlying, template_name, **kw)
    payload["computed"]["max_loss"] = None  # unbounded → reject
    return payload


@pytest.fixture
def patch_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake ONLY the pairs engine — the real ``honest_short`` rule still runs."""
    monkeypatch.setattr(pair_builder, "run_pairs_backtest", _fake_pairs_backtest)


@pytest.fixture
def patch_commodity_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake the pairs + MCX-option engines; implied move honestly ``None`` on MCX."""
    monkeypatch.setattr(pair_builder, "run_pairs_backtest", _fake_pairs_backtest)
    monkeypatch.setattr(
        option_builder._opt, "resolve_strategy", _fake_resolve_commodity_option
    )
    monkeypatch.setattr(
        option_builder._im, "implied_move", lambda *a, **k: None
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _gold_silver_view(make: Callable[..., MarketView]) -> MarketView:
    return make(
        view_type="relative",
        title="Gold outperforms silver as the gold/silver ratio mean-reverts",
        thesis=(
            "The gold/silver ratio is stretched; gold's bullion bid beats silver's "
            "industrial leg over the window — a leveraged MCX long-gold/short-silver "
            "ratio."
        ),
        category="commodities",
        time_horizon="6m",
    )


def _crude_event_view(make: Callable[..., MarketView]) -> MarketView:
    from datetime import datetime, timedelta, timezone

    return make(
        view_type="event",
        title="Crude oil spikes on an OPEC supply cut",
        thesis=(
            "An OPEC+ supply cut pushes crude higher into the next print; upstream "
            "producers gain vs OMC importers."
        ),
        category="commodities",
        time_horizon="1m",
        resolution_date=datetime.now(timezone.utc) + timedelta(days=21),
    )


def _by_archetype(rows: list[ViewExpression]) -> dict[str, ViewExpression]:
    return {r.config["archetype"]: r for r in rows}


def _short_leg(row: ViewExpression) -> dict[str, Any]:
    return row.config["structure"].get("short_leg", {}) or {}


def _assert_full_disclosures(row: ViewExpression) -> None:
    for field_ in (
        "rationale", "risk_profile", "capital_intensity",
        "historical_strength", "time_horizon",
    ):
        value = getattr(row, field_)
        assert isinstance(value, str) and value.strip(), (
            f"{row.tier.value} expression has a blank {field_}"
        )


def _assert_leverage_note(row: ViewExpression) -> None:
    """(d) the LEVERAGE note rides BOTH the warnings AND the risk_profile column."""
    warnings = row.config.get("warnings", []) or []
    assert any(
        "LEVERAGED" in w or w == commodities.LEVERAGE_NOTE for w in warnings
    ), f"{row.config['archetype']}: leverage note missing from config.warnings"
    assert "LEVERAGED" in (row.risk_profile or ""), (
        f"{row.config['archetype']}: leverage note missing from risk_profile column"
    )


def _assert_register_not_execute(row: ViewExpression) -> None:
    assert row.workflow_id is None
    assert row.backtest_run_id is None
    timing = row.config["timing"]
    assert timing["tranches"], "timing SPEC carries no tranches"
    note = (timing.get("note") or "").lower()
    assert "not executed" in note or "armed" in note
    pct_total = 0
    for tranche in timing["tranches"]:
        step = tranche["trigger"]["step_type"]
        assert step.startswith("trigger."), step
        assert step not in ("trigger.polymarket", "trigger.kalshi"), step
        pct_total += int(tranche["pct"])
    assert pct_total == 100


def _no_fabricated_delivery_short(row: ViewExpression) -> None:
    for inst in row.config["instruments"]:
        if inst.get("role") == "short":
            fabricated = (
                inst.get("instrument_type") in ("equity", "etf")
                and inst.get("tradeable") is True
            )
            assert not fabricated, f"fabricated delivery short: {inst}"


def _is_commodity_envelope(row: ViewExpression) -> bool:
    if row.config["structure"].get("is_commodity"):
        return True
    return any(
        config_schema.is_commodity_segment(inst.get("segment"))
        for inst in row.config["instruments"]
    )


# ── (a) commodity archetypes surfaced + 3-tier ladder ────────────────────────


def test_commodity_view_surfaces_commodity_archetypes(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
    patch_pairs: None,
) -> None:
    view = _gold_silver_view(make_curated_view)
    rows = dispatch.suggest_expressions(view_db, view)

    # A clean 3-tier ladder, every one a COMMODITY (CM) archetype — never the
    # equity RELATIVE plan (R1/R2/R3) for a view that names a tradeable commodity.
    assert [r.tier for r in rows] == [
        ExpressionTier.conservative, ExpressionTier.balanced, ExpressionTier.aggressive,
    ]
    for row in rows:
        assert row.config["archetype"].startswith("CM"), row.config["archetype"]
        assert _is_commodity_envelope(row)
        _assert_full_disclosures(row)
        _assert_leverage_note(row)
        _assert_register_not_execute(row)

    # The gold/silver MCX ratio pair (CM4) is the NON-basket the spec asks for.
    by = _by_archetype(rows)
    assert "CM4_gold_silver_ratio_pair" in by
    assert by["CM4_gold_silver_ratio_pair"].expression_kind == ExpressionKind.pair


# ── (b) commodity short = TRADEABLE MCX future (never AVOID / fabricated) ─────


def test_commodity_short_is_a_tradeable_mcx_future(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
    patch_pairs: None,
) -> None:
    view = _gold_silver_view(make_curated_view)
    rows = dispatch.suggest_expressions(view_db, view)
    cm4 = _by_archetype(rows)["CM4_gold_silver_ratio_pair"]

    short = _short_leg(cm4)
    assert short["mode"] in _COMMODITY_SHORT_MODES  # MCX future / put, not AVOID
    assert short["mode"] != "avoid"
    assert short["tradeable"] is True
    assert short["degraded"] is False           # a CLEAN symmetric commodity short
    assert short["instrument"]                  # a real "SILVER FUT", never blank

    # The persisted short instrument is MCX-typed + tradeable; no fabricated short.
    short_insts = [i for i in cm4.config["instruments"] if i.get("role") == "short"]
    assert short_insts
    for inst in short_insts:
        assert inst["instrument_type"] in (
            "commodity_future", "commodity_option"
        )
        assert config_schema.is_commodity_segment(inst["segment"])
        assert inst["exchange"] == "MCX"
        assert inst["tradeable"] is True
    _no_fabricated_delivery_short(cm4)


# ── (c) commodity options are DEFINED-RISK only ──────────────────────────────


def test_commodity_options_are_defined_risk_mcx_typed(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
    patch_commodity_engines: None,
) -> None:
    view = _crude_event_view(make_curated_view)
    rows = dispatch.suggest_expressions(view_db, view)

    option_rows = [
        r for r in rows if r.expression_kind == ExpressionKind.option_strategy
    ]
    assert option_rows, "expected at least one commodity option tier (CM1/CM2)"
    for row in option_rows:
        assert row.config["archetype"].startswith("CM")
        struct = row.config["structure"]
        # Defined-risk first: a real, bounded max loss (None == UNLIMITED → rejected).
        assert struct["max_loss"] is not None
        assert isinstance(struct["max_loss"], (int, float))
        # Every leg is MCX commodity-typed (never index/stock option), tradeable.
        legs = row.config["instruments"]
        assert legs
        for inst in legs:
            assert inst["instrument_type"] == "commodity_option"
            assert config_schema.is_commodity_segment(inst["segment"])
            assert inst["exchange"] == "MCX"
        # MCX cost segment + leverage note on the structure.
        assert row.config["costs"]["segment"] == "MCX-OPT"
        assert struct.get("leverage_note") == commodities.LEVERAGE_NOTE
        _assert_leverage_note(row)
        _assert_register_not_execute(row)


def test_unlimited_commodity_option_is_rejected(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defined-risk first: an unbounded commodity option REJECTS (no card shipped)."""
    monkeypatch.setattr(
        option_builder._opt, "resolve_strategy", _fake_resolve_unlimited
    )
    monkeypatch.setattr(option_builder._im, "implied_move", lambda *a, **k: None)
    view = _crude_event_view(make_curated_view)
    cm1 = catalog.get_archetype("CM1_commodity_directional_option")
    assert cm1 is not None
    with pytest.raises(StrategyResolutionError):
        option_builder.build_option_expression(
            view_db, view, cm1, "balanced", underlying="CRUDEOIL", direction="long",
        )


# ── (e) honest degrade: direct-MCX bullion pair is construct-only ────────────


def test_direct_mcx_bullion_pair_degrades_honestly(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A direct-MCX gold/silver pair is CONSTRUCT-ONLY (no aligned OHLCV): the
    spread stats stay ``None`` (no fabricated cointegration) and the engine is
    never called for it — proving the honest data degrade."""
    # Guard: the pairs engine must NOT be invoked for the direct-MCX legs.
    calls: list[tuple[str, str]] = []

    def _tripwire(a: str, b: str, **kw: Any) -> dict[str, Any]:
        calls.append((a, b))
        return _fake_pairs_backtest(a, b, **kw)

    monkeypatch.setattr(pair_builder, "run_pairs_backtest", _tripwire)

    view = _gold_silver_view(make_curated_view)
    rows = dispatch.suggest_expressions(view_db, view)
    cm4 = _by_archetype(rows)["CM4_gold_silver_ratio_pair"]
    struct = cm4.config["structure"]

    assert struct["backtest_available"] is False
    # No fabricated numbers — every spread statistic is honestly None.
    for key in ("beta", "alpha", "half_life_days", "adf_tstat", "cointegrated_at"):
        assert struct[key] is None, f"fabricated {key}={struct[key]!r} on a direct-MCX pair"
    # The legs are the DIRECT MCX bullion futures (GOLD vs SILVER), not the proxies.
    assert struct["a"] == "GOLD"
    assert struct["b"] == "SILVER"
    assert struct["proxy_basis"] is False
    # A "backtest unavailable / construct-only" warning is surfaced honestly.
    assert any(
        "backtest unavailable" in w.lower() or "construct-only" in w.lower()
        for w in cm4.config["warnings"]
    )
    # The engine was NOT called for the GOLD/SILVER legs (no aligned OHLCV).
    assert ("GOLD", "SILVER") not in calls


# ── (f) register-not-execute end-to-end ──────────────────────────────────────


def test_commodity_pass_is_register_not_execute(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
    patch_commodity_engines: None,
) -> None:
    view = _crude_event_view(make_curated_view)
    rows = dispatch.suggest_expressions(view_db, view)
    assert len(rows) == 3
    for row in rows:
        _assert_register_not_execute(row)
        _no_fabricated_delivery_short(row)
        # No PROGA prediction-market trigger ever leaks into the SPEC.
        for tranche in row.config["timing"]["tranches"]:
            assert tranche["trigger"]["step_type"] not in (
                "trigger.polymarket", "trigger.kalshi",
            )
