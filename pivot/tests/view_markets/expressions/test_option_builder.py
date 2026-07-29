"""Focused unit tests for the Phase-3 OPTION expression builder.

Self-contained: the live option engine (``option_strategies.resolve_strategy`` /
``implied_move`` / ``get_chain`` / ``trading_costs``) is mocked so no chain,
broker, or network is touched. We assert the builder delegates to the real
engine, REJECTS unlimited-loss structures (defined-risk first), India-types every
leg, stamps the single-stock honest-short warning, and emits the pinned config
envelope (structure keys, scores, costs, disclaimer).
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.services import option_strategies as _opt
from backend.view_markets import implied_move as _im
from backend.view_markets.expressions import commodities, config_schema, honest_short
from backend.view_markets.expressions.builders import option_builder
from backend.view_markets.expressions.catalog import get_archetype


# ── Fakes ────────────────────────────────────────────────────────────────────


def _fake_payload(
    underlying: str,
    template: str,
    *,
    max_loss: float | None,
    max_profit: float | None = 8000.0,
    sides: tuple[str, ...] = ("BUY", "SELL"),
) -> dict[str, Any]:
    legs = [
        {
            "option_type": "CE", "side": side, "strike": 50000.0 + 100 * i,
            "mid": 120.0, "iv": 0.16, "delta": 0.4, "iv_status": "ok",
            "tradingsymbol": f"{underlying}{template[:2].upper()}{i}",
            "instrument_token": 1000 + i,
        }
        for i, side in enumerate(sides)
    ]
    return {
        "locked": {
            "underlying": underlying, "segment": "NFO-OPT", "exchange": "NFO",
            "lot_size": 25, "expiry": "2026-07-30",
        },
        "editable": {"template": template, "qty_lots": 1, "legs": legs},
        "computed": {
            "net_premium": -2400.0,
            "max_loss": max_loss,
            "max_profit": max_profit,
            "pop": 0.55,
            "breakevens": [50250.0],
            "net_greeks": {"delta": 10.0, "gamma": 0.1, "theta": -50.0, "vega": 30.0},
            "capital_required": 2400.0,
            "margin_estimate": 2400.0,
        },
        "critique": {"verdict": "ok", "flags": [], "summary": "fine"},
        "validation": {"liquidity_flags": []},
    }


@pytest.fixture
def patch_engine(monkeypatch: pytest.MonkeyPatch):
    """Patch the option engine + implied move + costs with deterministic fakes."""
    calls: dict[str, Any] = {"resolve": [], "implied": []}

    def fake_resolve(db, underlying, template_name, *, expiry=None, qty_lots=1,
                     explicit_legs=None, chain=None):
        calls["resolve"].append((underlying, template_name, bool(explicit_legs)))
        # Unlimited only for an explicit naked-call sentinel.
        ml = None if template_name == "__naked__" else 5000.0
        n_sides = ("BUY", "SELL", "SELL", "BUY") if explicit_legs else ("BUY", "SELL")
        return _fake_payload(underlying, template_name, max_loss=ml, sides=n_sides)

    def fake_implied(db, underlying, *, expiry=None, horizon_days=None, width=10):
        calls["implied"].append(underlying)
        return _im.ImpliedMove(
            underlying=underlying, expiry=expiry, forward=50000.0,
            atm_strike=50000.0, atm_iv=0.16, t_years=0.08,
            expected_move_abs=1200.0, expected_move_pct=2.4,
            low=48800.0, high=51200.0, straddle_price=1400.0, source="iv",
            asof=None,
        )

    monkeypatch.setattr(_opt, "resolve_strategy", fake_resolve)
    monkeypatch.setattr(option_builder._opt, "resolve_strategy", fake_resolve)
    monkeypatch.setattr(option_builder._im, "implied_move", fake_implied)
    # Costs: a tiny module-level shim avoids importing the real bps constants.
    from backend.services import trading_costs
    monkeypatch.setattr(trading_costs, "option_leg_bps", lambda side, **k: 3.0)
    return calls


# ── Tests ────────────────────────────────────────────────────────────────────


def test_e1_index_debit_spread_envelope(patch_engine, event_view):
    arch = get_archetype("E1_rate_debit_spread")
    cfg = option_builder.build_option_expression(
        db=None, view=event_view, archetype=arch, tier="balanced",
    )
    # Envelope identity.
    assert cfg["schema_version"] == config_schema.CONFIG_SCHEMA_VERSION
    assert cfg["archetype"] == "E1_rate_debit_spread"
    assert cfg["expression_kind"] == "option_strategy"
    assert cfg["tier"] == "balanced"
    assert cfg["disclaimer"] == config_schema.DISCLAIMER

    # Structure carries the full pinned key set, defined-risk.
    structure = cfg["structure"]
    for key in config_schema.STRUCTURE_KEYS["option_strategy"]:
        assert key in structure, key
    assert structure["max_loss"] is not None
    assert structure["template"] == "bull_call_spread"  # balanced E1 → ATM debit
    assert structure["implied_move"]["source"] == "iv"

    # Index legs → index_option, no single-stock warning.
    assert cfg["instruments"]
    assert all(i["instrument_type"] == "index_option" for i in cfg["instruments"])
    assert honest_short.SINGLE_STOCK_OPTION_WARNING not in cfg["warnings"]

    # Scores + costs populated (construction-time, event-study).
    assert cfg["scores"]["alignment_kind"] == "event_study"
    assert 0.0 <= cfg["scores"]["construction_alignment"] <= 100.0
    assert cfg["costs"]["round_trip_bps"] == pytest.approx(6.0)


def test_conservative_e1_uses_credit_wing(patch_engine, event_view):
    arch = get_archetype("E1_rate_debit_spread")
    cfg = option_builder.build_option_expression(
        db=None, view=event_view, archetype=arch, tier="conservative",
    )
    # Conservative moneyness knob → OTM credit wing (bull_put_spread).
    assert cfg["structure"]["template"] == "bull_put_spread"
    assert ("BANKNIFTY", "bull_put_spread", False) in patch_engine["resolve"]


def test_single_stock_stamps_honest_short_warning(patch_engine, event_view):
    """A single-name option leg MUST carry the honest-short STT/physical warning."""
    arch = get_archetype("E3_event_straddle")
    cfg = option_builder.build_option_expression(
        db=None, view=event_view, archetype=arch, tier="balanced",
        underlying="RELIANCE",
    )
    assert all(i["instrument_type"] == "stock_option" for i in cfg["instruments"])
    assert honest_short.SINGLE_STOCK_OPTION_WARNING in cfg["warnings"]
    assert any(
        honest_short.SINGLE_STOCK_OPTION_WARNING in i["note"]
        for i in cfg["instruments"]
    )


def test_banknifty_monthly_warning(patch_engine, event_view):
    arch = get_archetype("E4_iv_crush_harvest")
    cfg = option_builder.build_option_expression(
        db=None, view=event_view, archetype=arch, tier="conservative",
        underlying="BANKNIFTY",
    )
    assert cfg["structure"]["template"] == "iron_condor"
    assert any("monthly-only" in w for w in cfg["warnings"])


def test_rejects_unlimited_loss(monkeypatch, event_view):
    """max_loss None (unlimited) must raise — never registered."""
    def naked_resolve(db, underlying, template_name, *, expiry=None, qty_lots=1,
                      explicit_legs=None, chain=None):
        return _fake_payload(underlying, template_name, max_loss=None,
                             max_profit=None, sides=("SELL",))

    monkeypatch.setattr(option_builder._opt, "resolve_strategy", naked_resolve)
    arch = get_archetype("E3_event_straddle")
    with pytest.raises(_opt.StrategyResolutionError):
        option_builder.build_option_expression(
            db=None, view=event_view, archetype=arch, tier="balanced",
            underlying="NIFTY",
        )


def test_broken_wing_composes_explicit_legs(monkeypatch, event_view):
    """E6 (GAP template) composes explicit legs off the live chain."""
    captured: dict[str, Any] = {}

    def fake_get_chain(db, sym, expiry=None, *, width=15, now=None):
        return {
            "underlying": sym, "atm_strike": 50000.0,
            "rows": [{"strike": 49800.0 + 100 * k} for k in range(8)],
        }

    def fake_resolve(db, underlying, template_name, *, expiry=None, qty_lots=1,
                     explicit_legs=None, chain=None):
        captured["explicit_legs"] = explicit_legs
        captured["template"] = template_name
        return _fake_payload(underlying, template_name, max_loss=3000.0,
                             sides=("BUY", "SELL", "SELL", "BUY"))

    monkeypatch.setattr("backend.market.option_chain.get_chain", fake_get_chain)
    monkeypatch.setattr(option_builder._opt, "resolve_strategy", fake_resolve)
    monkeypatch.setattr(option_builder._im, "implied_move", lambda *a, **k: None)
    from backend.services import trading_costs
    monkeypatch.setattr(trading_costs, "option_leg_bps", lambda side, **k: 3.0)

    arch = get_archetype("E6_broken_wing")
    cfg = option_builder.build_option_expression(
        db=None, view=event_view, archetype=arch, tier="aggressive",
        underlying="NIFTY",
    )
    # 4 explicit legs, net-zero calls; GAP → alignment ceiling 70.
    assert captured["template"] == "broken_wing_butterfly"
    assert len(captured["explicit_legs"]) == 4
    assert cfg["structure"]["max_loss"] is not None
    assert cfg["scores"]["construction_alignment"] <= 70.0


def test_r5_two_underlying_aggregation(patch_engine, relative_view):
    """R5 resolves BOTH underlyings and sums greeks; joint POP is a GAP (None)."""
    arch = get_archetype("R5_relative_options")
    cfg = option_builder.build_option_expression(
        db=None, view=relative_view, archetype=arch, tier="balanced",
        symbol_a="INFY", symbol_b="TCS",
    )
    structure = cfg["structure"]
    assert structure["underlyings"] == ["INFY", "TCS"]
    # Greeks summed across the two legs (2× the single-leg fake).
    assert structure["net_greeks"]["delta"] == pytest.approx(20.0)
    assert structure["pop"] is None          # cross-underlying joint POP = GAP
    assert structure["breakevens"] == []
    assert cfg["scores"]["alignment_kind"] == "relative_value"
    assert cfg["scores"]["construction_alignment"] <= 70.0  # GAP ceiling
    resolved_syms = {c[0] for c in patch_engine["resolve"]}
    assert {"INFY", "TCS"} <= resolved_syms


def test_r5_requires_both_underlyings(patch_engine, relative_view):
    arch = get_archetype("R5_relative_options")
    with pytest.raises(_opt.StrategyResolutionError):
        option_builder.build_option_expression(
            db=None, view=relative_view, archetype=arch, tier="balanced",
            symbol_a="INFY",  # symbol_b missing
        )


# ── Commodity (MCX) option archetypes (CM1 / CM2) ────────────────────────────


def _fake_commodity_payload(
    underlying: str, template: str, *, max_loss: float | None = 5000.0,
    sides: tuple[str, ...] = ("BUY", "SELL"),
) -> dict[str, Any]:
    """A resolved payload typed for the MCX commodity segment (what the real
    ``resolve_strategy`` returns for CRUDEOIL/GOLD now research_only is lifted)."""
    payload = _fake_payload(underlying, template, max_loss=max_loss, sides=sides)
    payload["locked"]["segment"] = "MCX-OPT"
    payload["locked"]["exchange"] = "MCX"
    return payload


@pytest.fixture
def patch_commodity_engine(monkeypatch: pytest.MonkeyPatch):
    """Patch the engine with an MCX-typed payload; implied move UNAVAILABLE on MCX
    (the data layer doesn't price it) → the builder must degrade, not fabricate."""
    resolved: list[tuple[str, str]] = []

    def fake_resolve(db, underlying, template_name, *, expiry=None, qty_lots=1,
                     explicit_legs=None, chain=None):
        resolved.append((underlying, template_name))
        ml = None if template_name == "__naked__" else 5000.0
        n_sides = (
            ("BUY", "BUY") if template_name in ("long_straddle", "long_strangle")
            else ("BUY", "SELL")
        )
        return _fake_commodity_payload(underlying, template_name, max_loss=ml,
                                       sides=n_sides)

    monkeypatch.setattr(option_builder._opt, "resolve_strategy", fake_resolve)
    # MCX implied move is unavailable on the NSE-only data layer → None (honest).
    monkeypatch.setattr(option_builder._im, "implied_move", lambda *a, **k: None)
    from backend.services import trading_costs
    monkeypatch.setattr(trading_costs, "option_leg_bps", lambda side, **k: 3.0)
    return resolved


def test_cm1_commodity_directional_defined_risk(patch_commodity_engine, event_view):
    """CM1 on the default crude underlying: MCX-typed, defined-risk, leverage-noted."""
    arch = get_archetype("CM1_commodity_directional_option")
    cfg = option_builder.build_option_expression(
        db=None, view=event_view, archetype=arch, tier="balanced",
    )
    # Default underlying (CRUDEOIL) routed through the real engine, bullish base.
    assert ("CRUDEOIL", "bull_call_spread") in patch_commodity_engine

    # Every leg is India-typed as a COMMODITY option on MCX (never index/stock).
    assert cfg["instruments"]
    assert all(i["instrument_type"] == "commodity_option" for i in cfg["instruments"])
    assert all(i["segment"] == "MCX-OPT" for i in cfg["instruments"])
    assert all(i["exchange"] == "MCX" for i in cfg["instruments"])

    # Leverage note surfaced on warnings + structure disclosure + each leg note.
    assert commodities.LEVERAGE_NOTE in cfg["warnings"]
    assert cfg["structure"]["leverage_note"] == commodities.LEVERAGE_NOTE
    assert all(commodities.LEVERAGE_NOTE in i["note"] for i in cfg["instruments"])
    # NOT a single stock → the single-stock STT warning must not appear.
    assert honest_short.SINGLE_STOCK_OPTION_WARNING not in cfg["warnings"]

    # MCX cost segment + defined-risk preserved + implied move degraded honestly.
    assert cfg["costs"]["segment"] == "MCX-OPT"
    assert "MCX" in cfg["costs"]["note"]
    assert cfg["structure"]["max_loss"] is not None
    assert cfg["structure"]["implied_move"] is None  # no fabricated MCX move


def test_cm1_bearish_view_flips_to_defined_risk_bear_template(
    patch_commodity_engine, event_view,
):
    """A bearish commodity directional view uses the bearish (still defined-risk)
    template — never a naked/unbounded short."""
    arch = get_archetype("CM1_commodity_directional_option")
    cfg = option_builder.build_option_expression(
        db=None, view=event_view, archetype=arch, tier="balanced",
        direction="bearish",
    )
    assert ("CRUDEOIL", "bear_put_spread") in patch_commodity_engine
    assert cfg["structure"]["template"] == "bear_put_spread"
    assert cfg["structure"]["max_loss"] is not None


def test_cm2_event_straddle_conservative_drops_to_strangle(
    patch_commodity_engine, event_view,
):
    """CM2 = long vol (defined-risk debit); Conservative tier → cheaper strangle."""
    arch = get_archetype("CM2_commodity_event_straddle")
    bal = option_builder.build_option_expression(
        db=None, view=event_view, archetype=arch, tier="balanced",
    )
    assert ("CRUDEOIL", "long_straddle") in patch_commodity_engine
    assert bal["structure"]["template"] == "long_straddle"
    assert bal["structure"]["max_loss"] is not None  # long vol = defined risk

    cons = option_builder.build_option_expression(
        db=None, view=event_view, archetype=arch, tier="conservative",
    )
    assert ("CRUDEOIL", "long_strangle") in patch_commodity_engine
    assert cons["structure"]["template"] == "long_strangle"


def test_commodity_mini_routes_to_option_bearing_sibling(
    patch_commodity_engine, event_view,
):
    """A futures-only mini (GOLDM) routes its option chain to GOLD — never a
    fabricated mini-option chain."""
    arch = get_archetype("CM1_commodity_directional_option")
    option_builder.build_option_expression(
        db=None, view=event_view, archetype=arch, tier="balanced",
        underlying="GOLDM",
    )
    assert ("GOLD", "bull_call_spread") in patch_commodity_engine
    assert all(u != "GOLDM" for u, _ in patch_commodity_engine)


def test_commodity_rejects_unlimited_loss(monkeypatch, event_view):
    """Defined-risk first holds for commodities too: max_loss None must raise."""
    def naked_resolve(db, underlying, template_name, *, expiry=None, qty_lots=1,
                      explicit_legs=None, chain=None):
        return _fake_commodity_payload(underlying, template_name, max_loss=None,
                                       sides=("SELL",))

    monkeypatch.setattr(option_builder._opt, "resolve_strategy", naked_resolve)
    monkeypatch.setattr(option_builder._im, "implied_move", lambda *a, **k: None)
    arch = get_archetype("CM1_commodity_directional_option")
    with pytest.raises(_opt.StrategyResolutionError):
        option_builder.build_option_expression(
            db=None, view=event_view, archetype=arch, tier="balanced",
        )


def test_commodity_short_is_a_tradeable_mcx_future_not_avoid():
    """The commodity short the engine relies on is a TRADEABLE MCX future (clean,
    symmetric, non-degraded) — never a fabricated short or an AVOID."""
    fut = honest_short.short_leg_for("CRUDEOIL", is_commodity=True)
    assert fut.mode == "commodity_future"
    assert fut.tradeable is True
    assert fut.degraded is False
    assert "FUT" in fut.instrument
    assert any("LEVERAGED" in w or "register-not-execute" in w for w in fut.warnings)

    # Defined-risk preference → a tradeable long MCX put (still not degraded).
    put = honest_short.short_leg_for("GOLD", is_commodity=True, prefer_defined_risk=True)
    assert put.mode == "commodity_put"
    assert put.tradeable is True
    assert "PE" in put.instrument

    # The ONLY AVOID path: a symbol confirmed off the MCX F&O master.
    avoid = honest_short.short_leg_for("NOTACMDTY", is_commodity=True, fno_eligible=False)
    assert avoid.mode == "avoid"
    assert avoid.tradeable is False
