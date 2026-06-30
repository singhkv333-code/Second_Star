"""Focused unit tests for the Phase-3 BASKET expression builder.

The builder delegates to sibling screens / weighting / sector_universe /
thematic_map / merger_arb / honest_short. Those siblings are mid-build (stubs
raising ``NotImplementedError``), so we mock them at the seams the builder calls
and exercise the REAL ``backend.services.weighting`` engine for the weight math.

Assertions cover the contract: a screened, purity-scaled, capped basket (NOT a
flat equal-weight), the documented ``config.structure`` keys, the construction
disclosures the builder owns (disclaimer / scores / expressability), the
min-names refusal → ETF-proxy degrade, the loser leg routed through
``honest_short`` (never a fabricated short), and the E7 merger-arb economics.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.services import thematic_map
from backend.services.thematic_map import ThematicScenario
from backend.view_markets.expressions import (
    cross_sectional,
    honest_short,
    merger_arb,
    screens,
)
from backend.view_markets.expressions.builders import basket_builder
from backend.view_markets.expressions.catalog import get_archetype
from backend.view_markets.expressions.config_schema import (
    DISCLAIMER,
    STRUCTURE_KEYS,
)
from backend.view_markets.expressions.honest_short import ShortLeg
from backend.view_markets.expressions.merger_arb import MergerArbMetrics
from backend.view_markets.expressions.screens import (
    LiquidityResult,
    MinNamesResult,
    PurityResult,
)

_UNIVERSE = [
    "TATASTEEL", "JSWSTEEL", "JINDALSTEL", "SAIL", "NMDC",
    "HINDALCO", "VEDL", "COALINDIA", "NATIONALUM", "HINDCOPPER",
]


# ════════════════════════════════════════════════════════════════════════════
# Stub seams (siblings are mid-build → patched here)
# ════════════════════════════════════════════════════════════════════════════


def _iterative_cap(weights, cap):
    """A correct iterative single-name cap (stands in for the screens stub)."""
    w = {k: float(v) for k, v in weights.items()}
    for _ in range(100):
        total = sum(w.values()) or 1.0
        w = {k: v / total for k, v in w.items()}
        over = {k: v for k, v in w.items() if v > cap + 1e-12}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for k in over:
            w[k] = cap
        free = [k for k in w if k not in over]
        free_sum = sum(w[k] for k in free) or 1.0
        for k in free:
            w[k] += excess * (w[k] / free_sum)
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def _weighted_purity(purities, weights):
    if not weights:
        return 0.0
    by_sym = {p.symbol: p.score for p in purities}
    return sum(by_sym.get(s, 0.0) * w for s, w in weights.items())


@pytest.fixture
def patch_screens(monkeypatch):
    """Wire the screens / data seams to deterministic, passing stubs."""

    def _purity(db, symbol, *, theme, fundamentals=None):
        # A real conviction gradient: first names purer than the tail.
        score = 90.0 if symbol in _UNIVERSE[:3] else 55.0 if symbol in _UNIVERSE[:7] else 30.0
        return PurityResult(
            symbol=symbol, score=score, layer="curated",
            estimated=False, rationale="seed",
        )

    def _liquidity(db, symbols):
        return [
            LiquidityResult(
                symbol=s, adv_cr=25.0, passes=True, watch=False,
                impact_cost_bps=5.0, options_available=True, note="ok",
            )
            for s in symbols
        ]

    def _min_names(symbols, *, theme, min_names=10):
        ok = len(symbols) >= min_names
        return MinNamesResult(
            ok=ok, n_names=len(symbols), min_required=min_names,
            etf_proxy=None if ok else "MAKEINDIA",
            note="ok" if ok else "only a handful of names — offering the ETF proxy",
        )

    monkeypatch.setattr(screens, "purity_score", _purity)
    monkeypatch.setattr(screens, "liquidity_screen", _liquidity)
    monkeypatch.setattr(screens, "min_names_floor", _min_names)
    monkeypatch.setattr(screens, "apply_single_name_cap", _iterative_cap)
    monkeypatch.setattr(screens, "basket_purity", _weighted_purity)
    # mcap map: real query_screener already returns these names with mcap_cr, so
    # leave sector_universe.query_screener real. No price history needed for mcap.
    monkeypatch.setattr(basket_builder.historical, "get_close_dict", lambda syms, period="2y": {})
    # No scenario unless a test opts in.
    monkeypatch.setattr(thematic_map, "detect_thematic_scenario", lambda msg: None)
    yield


# ════════════════════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════════════════════


def test_happy_path_is_screened_capped_and_not_flat(theme_view, view_db, patch_screens):
    arch = get_archetype("T1_purity_conviction_basket")
    cfg = basket_builder.build_basket_expression(
        view_db, theme_view, arch, "balanced", symbols=list(_UNIVERSE),
    )

    struct = cfg["structure"]
    # Every documented basket structure key is present.
    for key in STRUCTURE_KEYS["basket"]:
        assert key in struct, key

    weights = struct["weights"]
    assert weights, "expected a non-empty basket"
    assert abs(sum(weights.values()) - 1.0) < 1e-4
    # Single-name cap (balanced = 0.15) is respected.
    assert max(weights.values()) <= 0.15 + 1e-6
    # NOT a flat equal-weight basket (purity-scaled mcap → a conviction gradient).
    assert len(set(round(w, 4) for w in weights.values())) > 1

    # Builder-owned construction disclosures.
    assert cfg["disclaimer"] == DISCLAIMER
    assert cfg["scores"]["alignment_kind"] == "basket_purity"
    assert 0.0 <= cfg["scores"]["construction_alignment"] <= 100.0
    assert struct["basket_purity"] is not None
    assert struct["n_names"] == len(weights)
    assert struct["single_name_cap"] == 0.15

    # All holdings are India-typed cash/delivery longs (no short leg).
    assert cfg["instruments"]
    for ins in cfg["instruments"]:
        assert ins["role"] == "long"
        assert ins["instrument_type"] == "equity"
        assert ins["tradeable"] is True
    assert cfg["expressability"]["degraded"] is False


def test_min_names_refusal_degrades_to_etf_proxy(theme_view, view_db, patch_screens, monkeypatch):
    # Too few names → min_names_floor refuses → ETF proxy degrade.
    cfg = basket_builder.build_basket_expression(
        view_db, theme_view, get_archetype("T1_purity_conviction_basket"),
        "conservative", symbols=["TATASTEEL", "JSWSTEEL", "SAIL"],
    )
    struct = cfg["structure"]
    assert struct["scheme"] == "etf_proxy"
    assert struct["etf_proxy"] == "MAKEINDIA"
    assert struct["fallback_reason"]
    assert cfg["expressability"]["degraded"] is True
    # The single holding is the listed ETF, honestly typed — no fake breadth.
    assert len(cfg["instruments"]) == 1
    assert cfg["instruments"][0]["instrument_type"] == "etf"
    assert cfg["scores"]["construction_alignment"] == 0.0


def test_losers_routed_through_honest_short_as_avoid(theme_view, view_db, patch_screens, monkeypatch):
    scenario = ThematicScenario(
        key="test_theme", label="t", thesis="x",
        winners=tuple((s, "w") for s in _UNIVERSE),
        losers=(("HINDUNILVR", "rural drag"), ("DABUR", "volume drag")),
        confirm="c", invalidate="i",
    )
    monkeypatch.setattr(thematic_map, "detect_thematic_scenario", lambda msg: scenario)

    calls: list[str] = []

    def _avoid(symbol, *, reason, suggested_underweight=0.0):
        calls.append(symbol)
        return ShortLeg(
            symbol=symbol, mode="avoid", instrument=symbol,
            tradeable=False, degraded=True, note=f"AVOID: {reason}",
        )

    monkeypatch.setattr(honest_short, "avoid_annotation", _avoid)

    cfg = basket_builder.build_basket_expression(
        view_db, theme_view, get_archetype("T1_purity_conviction_basket"),
        "balanced",  # symbols=None → universe comes from the scenario winners
    )

    # honest_short was used for the loser leg — never a fabricated short.
    assert set(calls) == {"HINDUNILVR", "DABUR"}
    avoid = cfg["structure"]["avoid"]
    assert {a["symbol"] for a in avoid} == {"HINDUNILVR", "DABUR"}
    assert all(a["mode"] == "avoid" and a["tradeable"] is False for a in avoid)
    # No short instrument leaked into the holdings.
    assert all(ins["role"] == "long" for ins in cfg["instruments"])


def test_e7_merger_arb_economics_attached(event_view, view_db, patch_screens, monkeypatch):
    captured = {}

    def _metrics(*, target_price, offer_price, days_to_close, broken_price=None, acceptance_ratio=None):
        captured.update(dict(target=target_price, offer=offer_price, days=days_to_close))
        return MergerArbMetrics(
            target_price=target_price, offer_price=offer_price, days_to_close=days_to_close,
            spread_abs=offer_price - target_price, spread_pct=5.0, gross_return_pct=5.0,
            annualized_return_pct=18.0, implied_break_prob=0.2, broken_price=broken_price,
            acceptance_ratio=acceptance_ratio, prorated_return_pct=None, note="ok",
        )

    monkeypatch.setattr(merger_arb, "merger_arb_metrics", _metrics)

    cfg = basket_builder.build_basket_expression(
        view_db, event_view, get_archetype("E7_merger_arb"), "balanced",
        symbols=list(_UNIVERSE),
        target_price=100.0, offer_price=105.0, days_to_close=100,
    )
    block = cfg["structure"]["merger_arb"]
    assert captured == {"target": 100.0, "offer": 105.0, "days": 100}
    assert block["spread_abs"] == 5.0
    assert block["annualized_return_pct"] == 18.0
    assert block["implied_break_prob"] == 0.2


def test_e7_without_deal_terms_does_not_fabricate(event_view, view_db, patch_screens):
    cfg = basket_builder.build_basket_expression(
        view_db, event_view, get_archetype("E7_merger_arb"), "balanced",
        symbols=list(_UNIVERSE),
    )
    assert "merger_arb" not in cfg["structure"]
    assert any("Merger-arb economics need" in w for w in cfg["warnings"])


def test_aggressive_factor_tilt_uses_composite_scores(theme_view, view_db, patch_screens, monkeypatch):
    # Aggressive theme tier → factor scheme → composite_factor_scores is called.
    def _composite(db, symbols, *, factors, fundamentals=None):
        return {s: float(i) for i, s in enumerate(symbols)}

    monkeypatch.setattr(cross_sectional, "composite_factor_scores", _composite)
    # factor scheme needs no covariance; momentum derived from price history, but
    # the caller-supplied views (composite) carry the signal even with no prices.
    monkeypatch.setattr(
        basket_builder.historical, "get_close_dict",
        lambda syms, period="2y": {s: pd.Series([100.0, 101.0, 102.0]) for s in syms},
    )

    cfg = basket_builder.build_basket_expression(
        view_db, theme_view, get_archetype("T2_factor_tilt"), "aggressive",
        symbols=list(_UNIVERSE),
    )
    tilt = cfg["structure"]["factor_tilt"]
    assert tilt["factors"] == ["value", "momentum", "quality"]
    assert set(tilt["scores"]) == set(_UNIVERSE)
    assert cfg["structure"]["single_name_cap"] == 0.20  # aggressive cap
    assert abs(sum(cfg["structure"]["weights"].values()) - 1.0) < 1e-4


def test_real_weighting_fallback_reason_surfaced(theme_view, view_db, patch_screens, monkeypatch):
    # Conservative → risk_parity, but no price history → weighting falls back to
    # equal-weight and the honest reason is surfaced (not swallowed).
    cfg = basket_builder.build_basket_expression(
        view_db, theme_view, get_archetype("T1_purity_conviction_basket"),
        "conservative", symbols=list(_UNIVERSE),
    )
    struct = cfg["structure"]
    assert struct["requested_scheme"] == "risk_parity"
    assert struct["scheme"] == "equal"  # honest degrade
    assert "fallback_reason" in struct
    assert any("equal-weight" in w for w in cfg["warnings"])


# ════════════════════════════════════════════════════════════════════════════
# COMMODITY (MCX) leg — crude-shock-hedged basket + producer basket
# ════════════════════════════════════════════════════════════════════════════


def _commodity_leg_for(direction=None, tier="balanced", **ctx):
    """Helper-free call into the CM6 crude-shock-hedged-basket builder."""
    if direction is not None:
        ctx["commodity_direction"] = direction
    return get_archetype("CM6_crude_shock_hedged_basket"), tier, ctx


def test_crude_shock_basket_adds_direct_mcx_future_leg(theme_view, view_db, patch_screens):
    # CM6: the screened equity defensive sleeve PLUS a DIRECT MCX crude future.
    arch, tier, ctx = _commodity_leg_for(tier="balanced")
    cfg = basket_builder.build_basket_expression(
        view_db, theme_view, arch, tier, symbols=list(_UNIVERSE), **ctx,
    )

    # The equity sleeve is still a real screened, capped, non-flat basket.
    weights = cfg["structure"]["weights"]
    assert weights and abs(sum(weights.values()) - 1.0) < 1e-4
    assert len(set(round(w, 4) for w in weights.values())) > 1

    # The DIRECT MCX commodity leg is attached, India-typed + leveraged.
    leg = cfg["structure"]["commodity_leg"]
    assert leg["symbol"] == "CRUDEOIL"
    assert leg["direction"] == "long"
    assert leg["vehicle"] == "future"
    assert leg["instrument_type"] == "commodity_future"
    assert leg["segment"] == "MCX-FUT"
    assert leg["defined_risk"] is False
    # Honest backtest gate: a direct-MCX leg has no aligned OHLCV → flagged.
    assert leg["backtest_available"] is False
    # Lot size is the master's value or None — never fabricated.
    assert leg["lot_size"] is None or isinstance(leg["lot_size"], int)

    # The instrument list carries the MCX leg with the leverage note.
    mcx = [i for i in cfg["instruments"] if i["exchange"] == "MCX"]
    assert len(mcx) == 1
    assert mcx[0]["instrument_type"] == "commodity_future"
    assert "LEVERAGED" in mcx[0]["note"]

    # Disclosures: the LEVERAGE_NOTE + backtest-unavailable degrade are surfaced.
    from backend.view_markets.expressions import commodities
    assert commodities.LEVERAGE_NOTE in cfg["warnings"]
    assert any("backtest-unavailable" in w for w in cfg["warnings"])
    # Cost note mentions the leveraged MCX leg (real trading_costs path).
    assert "MCX" in cfg["costs"]["note"]


def test_crude_short_uses_tradeable_mcx_future_not_avoid(theme_view, view_db, patch_screens):
    # A commodity SHORT must be a TRADEABLE MCX future via honest_short — NEVER
    # a fabricated short or an AVOID.
    arch, tier, ctx = _commodity_leg_for(direction="short", tier="balanced")
    cfg = basket_builder.build_basket_expression(
        view_db, theme_view, arch, tier, symbols=list(_UNIVERSE), **ctx,
    )
    leg = cfg["structure"]["commodity_leg"]
    assert leg["direction"] == "short"
    assert leg["mode"] == "commodity_future"      # the clean symmetric short
    assert leg["vehicle"] == "future"
    assert leg["tradeable"] is True               # NOT an AVOID
    assert leg["instrument"].endswith("FUT")

    expr = cfg["expressability"]
    assert expr["short_mode"] == "commodity_future"
    assert any("TRADEABLE MCX" in n for n in expr["notes"])
    # No AVOID leaked: the MCX leg is a real tradeable short.
    mcx = [i for i in cfg["instruments"] if i["exchange"] == "MCX"]
    assert mcx and mcx[0]["role"] == "short" and mcx[0]["tradeable"] is True


def test_conservative_commodity_leg_is_defined_risk_option(theme_view, view_db, patch_screens):
    # Conservative tier → the commodity vehicle is a DEFINED-RISK long MCX option.
    arch, tier, ctx = _commodity_leg_for(tier="conservative")
    cfg = basket_builder.build_basket_expression(
        view_db, theme_view, arch, tier, symbols=list(_UNIVERSE), **ctx,
    )
    leg = cfg["structure"]["commodity_leg"]
    assert leg["instrument_type"] == "commodity_option"
    assert leg["segment"] == "MCX-OPT"
    assert leg["defined_risk"] is True
    assert leg["instrument"].endswith("CE")
    # Defined-risk option cost routed through the MCX exchange rate.
    assert "premium per leg" in cfg["costs"]["note"]


def test_commodity_producer_basket_resolves_crude_beneficiaries(
    theme_view, view_db, patch_screens, monkeypatch,
):
    # A commodity-producer basket: no explicit symbols → the equity sleeve is
    # resolved from the crude beneficiary universe (sector_universe), screened
    # and weighted like any theme basket (reuse, not fabricate).
    monkeypatch.setattr(
        basket_builder.sector_universe, "crude_up_beneficiaries",
        lambda: list(_UNIVERSE),
    )
    arch = get_archetype("CM3_commodity_producer_vs_importer_pair")
    cfg = basket_builder.build_basket_expression(
        view_db, theme_view, arch, "balanced", crude_intent="crude_up",
    )
    weights = cfg["structure"]["weights"]
    assert set(weights) == set(_UNIVERSE)
    assert any("crude beneficiary universe" in w for w in cfg["warnings"])
