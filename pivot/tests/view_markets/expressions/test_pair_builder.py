"""Focused unit tests for the Phase-3 PAIR expression builder.

``build_pair_expression`` is the flagship non-basket builder (R1 cointegrated
pair / R2 sector-vs-index / R3 factor-ETF / R4 ratio-RS / E2 rate pair). It
delegates the cointegration math to the REAL ``run_pairs_backtest`` engine and
the SHORT leg to ``honest_short.short_leg_for`` — both are mocked here so the
test is self-contained (no network, no instrument master, no live Kite).

The load-bearing contract pinned below:

* the output is a ``config_schema`` envelope carrying every
  ``STRUCTURE_KEYS["pair"]`` key + India-typed instruments,
* the SHORT leg is ALWAYS routed through ``honest_short`` (never a fabricated
  delivery short) — a degraded short flips ``expressability.degraded`` and drops
  the Alignment Score,
* the tier z-bands flow from ``tiers.tier_knobs`` into the structure,
* the half-life-vs-horizon gate + construct-rigor ceiling shape the score
  (R4 ratio < R1 cointegrated, always),
* thin/unavailable data degrades honestly (``PairsError`` → no fabricated β).

Disclosures (the five ViewExpression columns) are populated by ``dispatch``, not
the builder; the builder owns the ``config`` envelope, asserted here.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.view_markets.expressions import commodities, honest_short
from backend.view_markets.expressions.builders import pair_builder
from backend.view_markets.expressions.catalog import get_archetype
from backend.view_markets.expressions.config_schema import (
    CONFIG_SCHEMA_VERSION,
    STRUCTURE_KEYS,
)

R1 = "R1_cointegrated_pair"
R2 = "R2_sector_vs_index"
R3 = "R3_factor_etf_vs_index"
R4 = "R4_ratio_rs"
CM3 = "CM3_commodity_producer_vs_importer_pair"
CM4 = "CM4_gold_silver_ratio_pair"


# ── test doubles ─────────────────────────────────────────────────────────────


def _eg_backtest(
    *, beta: float = 0.85, half_life: float | None = 12.0,
    cointegrated_at: str | None = "5%", adf: float | None = -3.6,
) -> dict[str, Any]:
    """A minimal ``run_pairs_backtest`` payload (only the keys the builder reads)."""
    return {
        "cointegration": {
            "alpha": 0.1,
            "beta": beta,
            "adf_tstat": adf,
            "half_life_days": half_life,
            "cointegrated_at": cointegrated_at,
            "is_cointegrated": cointegrated_at in ("1%", "5%"),
        },
        "metrics": {},
        "series": {},
    }


def _ssf_short(symbol: str = "BAJFINANCE") -> honest_short.ShortLeg:
    """A clean (non-degraded) single-stock-future short."""
    return honest_short.ShortLeg(
        symbol=symbol, mode="ssf_future", instrument=f"{symbol}-FUT",
        tradeable=True, degraded=False, note="SSF short", warnings=[],
    )


def _index_future_short(symbol: str = "NIFTY") -> honest_short.ShortLeg:
    return honest_short.ShortLeg(
        symbol=symbol, mode="index_future", instrument=f"{symbol}-FUT",
        tradeable=True, degraded=False, note="index future short", warnings=[],
    )


def _commodity_future_short(symbol: str = "SILVER") -> honest_short.ShortLeg:
    """A clean (non-degraded) tradeable MCX commodity-future short."""
    return honest_short.ShortLeg(
        symbol=symbol, mode="commodity_future", instrument=f"{symbol} FUT",
        tradeable=True, degraded=False, note="MCX future short", warnings=[],
    )


def _avoid_short(symbol: str = "ZOMATO") -> honest_short.ShortLeg:
    """A degraded AVOID short (no SSF, no liquid option) — must drop the score."""
    return honest_short.ShortLeg(
        symbol=symbol, mode="avoid", instrument=symbol,
        tradeable=False, degraded=True,
        note="no tradeable short — AVOID/underweight only",
        warnings=[honest_short.UNSHORTABLE_ETF_NOTE],
    )


@pytest.fixture
def patch_engine(monkeypatch: pytest.MonkeyPatch):
    """Patch the real engine + honest_short the builder imported."""

    def _apply(*, backtest=None, backtest_exc=None, short=None) -> dict[str, Any]:
        calls: dict[str, Any] = {}

        def fake_run(a, b, **kw):  # noqa: ANN001
            calls["run"] = {"a": a, "b": b, **kw}
            if backtest_exc is not None:
                raise backtest_exc
            return backtest if backtest is not None else _eg_backtest()

        def fake_short(symbol, **kw):  # noqa: ANN001
            calls["short"] = {"symbol": symbol, **kw}
            return short if short is not None else _ssf_short(symbol)

        monkeypatch.setattr(pair_builder, "run_pairs_backtest", fake_run)
        monkeypatch.setattr(pair_builder.honest_short, "short_leg_for", fake_short)
        return calls

    return _apply


# ── core contract: envelope shape + disclosures ──────────────────────────────


def test_r1_envelope_carries_all_pair_structure_keys(relative_view, patch_engine):
    patch_engine()
    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R1), "balanced",
        symbol_a="TCS", symbol_b="INFY",
    )
    assert cfg["schema_version"] == CONFIG_SCHEMA_VERSION
    assert cfg["expression_kind"] == "pair"
    assert cfg["archetype"] == R1
    assert cfg["tier"] == "balanced"
    # every pinned pair structure key present
    for key in STRUCTURE_KEYS["pair"]:
        assert key in cfg["structure"], f"missing structure key {key!r}"
    assert cfg["structure"]["a"] == "TCS"
    assert cfg["structure"]["b"] == "INFY"
    assert cfg["disclaimer"]
    # construction score only — Phase 4 adds Trust
    assert "construction_alignment" in cfg["scores"]
    assert cfg["scores"]["alignment_kind"] == "relative_value"


def test_tier_z_bands_flow_into_structure(relative_view, patch_engine):
    """Balanced uses engine defaults 2.0/0.5/4.0; Aggressive tightens to 1.75/0.4/4.5."""
    calls = patch_engine()
    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R1), "aggressive",
        symbol_a="TCS", symbol_b="INFY",
    )
    assert (cfg["structure"]["z_entry"], cfg["structure"]["z_exit"],
            cfg["structure"]["z_stop"]) == (1.75, 0.4, 4.5)
    # the z-bands were actually handed to the engine
    assert calls["run"]["entry_z"] == 1.75
    assert calls["run"]["exit_z"] == 0.4
    assert calls["run"]["stop_z"] == 4.5


# ── honest short: the central guardrail ──────────────────────────────────────


def test_short_leg_always_routes_through_honest_short(relative_view, patch_engine):
    calls = patch_engine(short=_ssf_short("INFY"))
    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R1), "balanced",
        symbol_a="TCS", symbol_b="INFY", ssf_eligible=True,
    )
    # honest_short was called for the SHORT leg (B), with the SSF hint forwarded
    assert calls["short"]["symbol"] == "INFY"
    assert calls["short"]["ssf_eligible"] is True
    short = cfg["structure"]["short_leg"]
    assert short["mode"] == "ssf_future"
    assert short["tradeable"] is True
    # the short instrument is India-typed as a single-stock future, NFO segment
    short_instr = [i for i in cfg["instruments"] if i["role"] == "short"][0]
    assert short_instr["instrument_type"] == "stock_future"
    assert short_instr["segment"] == "NFO-FUT"


def test_degraded_short_flags_expressability_and_drops_score(relative_view, patch_engine):
    """An AVOID short (un-shortable name) must set degraded + lower the score."""
    patch_engine(short=_avoid_short("ZOMATO"))
    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R1), "balanced",
        symbol_a="PAYTM", symbol_b="ZOMATO",
    )
    assert cfg["expressability"]["degraded"] is True
    assert cfg["expressability"]["symmetric"] is False
    assert cfg["expressability"]["short_mode"] == "avoid"
    short_instr = [i for i in cfg["instruments"] if i["role"] == "short"][0]
    assert short_instr["tradeable"] is False

    # same view, clean short → strictly higher score (degrade_factor applied)
    patch_engine(short=_ssf_short("ZOMATO"))
    clean = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R1), "balanced",
        symbol_a="PAYTM", symbol_b="ZOMATO",
    )
    assert (clean["scores"]["construction_alignment"]
            > cfg["scores"]["construction_alignment"])


def test_index_short_routes_to_index_future(relative_view, patch_engine):
    """R2 sector-vs-index: leg B = NIFTY → short via index future, is_index=True."""
    calls = patch_engine(short=_index_future_short("NIFTY"))
    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R2), "conservative",
        symbol_a="CNXIT",
    )
    # leg B defaults to the archetype's NIFTY; honest_short told it's an index
    assert calls["short"]["symbol"] == "NIFTY"
    assert calls["short"]["is_index"] is True
    short_instr = [i for i in cfg["instruments"] if i["role"] == "short"][0]
    assert short_instr["instrument_type"] == "index_future"


# ── per-leg sizing + residual beta (β-hedge math, not fabricated) ────────────


def test_beta_hedge_sizing_and_zero_residual_beta(relative_view, patch_engine):
    patch_engine(backtest=_eg_backtest(beta=1.0))
    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R1), "balanced",
        symbol_a="TCS", symbol_b="INFY", capital_inr=1_000_000.0,
    )
    leg_a, leg_b = cfg["structure"]["leg_a"], cfg["structure"]["leg_b"]
    # β=1 → equal-weight beta-neutral split, notionals sum to capital
    assert leg_a["weight"] == pytest.approx(0.5)
    assert leg_b["weight"] == pytest.approx(0.5)
    assert leg_a["notional"] + leg_b["notional"] == pytest.approx(1_000_000.0)
    assert leg_a["side"] == "long"
    assert leg_b["side"] == "short"
    # residual market beta ≈ 0 by the β-hedge construction
    assert cfg["structure"]["residual_beta"] == 0.0


# ── rigor ceiling: ratio/RS can never out-score a true cointegrated pair ─────


def test_r4_ratio_scores_below_r1_cointegrated(relative_view, patch_engine):
    patch_engine(backtest=_eg_backtest(cointegrated_at="1%"))
    r1 = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R1), "balanced",
        symbol_a="TCS", symbol_b="INFY",
    )
    patch_engine(backtest=_eg_backtest(cointegrated_at="1%"))
    r4 = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R4), "balanced",
        symbol_a="TCS", symbol_b="INFY",
    )
    assert (r4["scores"]["construction_alignment"]
            < r1["scores"]["construction_alignment"])
    # R4 explicitly flags the lower-rigor ratio/RS degrade in warnings
    assert any("ratio" in w.lower() for w in r4["warnings"])


def test_half_life_gate_warns_when_exceeds_horizon(relative_view, patch_engine):
    """relative_view horizon = 6m (~180d); a 400d half-life can't revert in time."""
    patch_engine(backtest=_eg_backtest(half_life=400.0))
    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R1), "balanced",
        symbol_a="TCS", symbol_b="INFY",
    )
    assert any("half-life" in w.lower() for w in cfg["warnings"])


# ── honest failure: thin data degrades, never fabricates ─────────────────────


def test_thin_data_degrades_without_fabricating_beta(relative_view, patch_engine):
    from backend.services.backtest.pairs.engine import PairsError

    patch_engine(backtest_exc=PairsError("insufficient aligned data"))
    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(R3), "balanced",
        symbol_a="MOMENTUM30", factor="momentum",
    )
    # no fabricated cointegration numbers
    assert cfg["structure"]["beta"] is None
    assert cfg["structure"]["half_life_days"] is None
    assert cfg["structure"]["cointegrated_at"] is None
    assert cfg["structure"]["residual_beta"] is None  # cannot claim neutrality
    assert any("unavailable" in w.lower() for w in cfg["warnings"])
    # R3 long leg is an India-listed smart-beta ETF
    long_instr = [i for i in cfg["instruments"] if i["role"] == "long"][0]
    assert long_instr["instrument_type"] == "etf"


# ── commodity (MCX) pairs: tradeable MCX short + honest backtest gate ─────────


def test_cm4_direct_bullion_construct_only_with_real_mcx_short(
    relative_view, monkeypatch,
):
    """CM4 on DIRECT MCX gold/silver: construct-only (NO fabricated spread) and the
    SHORT leg is a TRADEABLE MCX future via the REAL honest_short — never AVOID."""

    def _boom(*_a, **_k):  # the engine must NOT be called for direct-MCX legs
        raise AssertionError("run_pairs_backtest called for direct-MCX legs")

    monkeypatch.setattr(pair_builder, "run_pairs_backtest", _boom)

    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(CM4), "balanced",
    )
    # legs resolved from the archetype params (GOLD/SILVER), commodity flagged
    assert cfg["structure"]["a"] == "GOLD"
    assert cfg["structure"]["b"] == "SILVER"
    assert cfg["structure"]["is_commodity"] is True
    # construct-only: backtest unavailable, NO fabricated spread statistics
    assert cfg["structure"]["backtest_available"] is False
    assert cfg["structure"]["beta"] is None
    assert cfg["structure"]["half_life_days"] is None
    assert cfg["structure"]["cointegrated_at"] is None
    assert cfg["structure"]["residual_beta"] is None  # cannot claim neutrality
    assert any("backtest unavailable" in w.lower() for w in cfg["warnings"])
    # the SHORT is a TRADEABLE MCX future (NOT avoid) from the REAL honest_short
    short = cfg["structure"]["short_leg"]
    assert short["mode"] == "commodity_future"
    assert short["tradeable"] is True
    assert short["degraded"] is False
    short_instr = [i for i in cfg["instruments"] if i["role"] == "short"][0]
    assert short_instr["instrument_type"] == "commodity_future"
    assert short_instr["segment"] == "MCX-FUT"
    assert short_instr["exchange"] == "MCX"
    assert short_instr["tradeable"] is True
    # the long leg is a direct MCX commodity future too
    long_instr = [i for i in cfg["instruments"] if i["role"] == "long"][0]
    assert long_instr["instrument_type"] == "commodity_future"
    assert long_instr["segment"] == "MCX-FUT"
    # the leverage-risk note is surfaced; the expression itself is NOT degraded
    assert any(w == commodities.LEVERAGE_NOTE for w in cfg["warnings"])
    assert cfg["expressability"]["commodity"] is True
    assert cfg["expressability"]["degraded"] is False
    assert cfg["disclaimer"]
    # CM4 (ratio rigor) is ceiling-capped below a true cointegrated pair
    assert cfg["scores"]["construction_alignment"] <= 60.0


def test_cm4_etf_proxy_route_backtests_but_short_stays_mcx_future(
    relative_view, patch_engine,
):
    """CM4 with ``use_etf_proxy``: the spread is MEASURED on GOLDBEES/SILVERBEES
    (real, backtestable) while the LIVE short stays a direct MCX future."""
    calls = patch_engine(
        backtest=_eg_backtest(beta=0.9, cointegrated_at="5%"),
        short=_commodity_future_short("SILVER"),
    )
    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(CM4), "balanced",
        use_etf_proxy=True,
    )
    # the backtest ran on the ETF proxies, NOT the direct MCX legs
    assert {calls["run"]["a"], calls["run"]["b"]} == {"GOLDBEES", "SILVERBEES"}
    assert cfg["structure"]["proxy_basis"] is True
    assert cfg["structure"]["backtest_available"] is True
    assert cfg["structure"]["beta"] == 0.9  # measured on the proxy, not fabricated
    # honest_short was still asked for the DIRECT MCX leg with is_commodity=True
    assert calls["short"]["symbol"] == "SILVER"
    assert calls["short"]["is_commodity"] is True
    short_instr = [i for i in cfg["instruments"] if i["role"] == "short"][0]
    assert short_instr["segment"] == "MCX-FUT"
    # a clear note that the stats are proxy-based and the live legs are MCX futures
    assert any(
        "proxies" in w.lower() and "mcx" in w.lower() for w in cfg["warnings"]
    )
    assert any(w == commodities.LEVERAGE_NOTE for w in cfg["warnings"])


def test_cm3_producer_vs_importer_equity_legs_backtest_with_leverage_note(
    relative_view, patch_engine,
):
    """CM3 producer-vs-importer: EQUITY legs (upstream long / OMC short) DO
    backtest; the crude-driven expression still carries the leverage note."""
    calls = patch_engine(
        backtest=_eg_backtest(beta=0.8, cointegrated_at="5%"),
        short=_ssf_short("IOC"),
    )
    cfg = pair_builder.build_pair_expression(
        None, relative_view, get_archetype(CM3), "balanced",
        symbol_a="ONGC", symbol_b="IOC", ssf_eligible=True,
    )
    # equity producer/importer legs backtest with real spread stats
    assert cfg["structure"]["backtest_available"] is True
    assert cfg["structure"]["beta"] == 0.8
    assert calls["run"]["a"] == "ONGC"
    assert calls["run"]["b"] == "IOC"
    # the OMC short is the equity importer's SSF (NOT a commodity)
    assert calls["short"]["is_commodity"] is False
    short_instr = [i for i in cfg["instruments"] if i["role"] == "short"][0]
    assert short_instr["instrument_type"] == "stock_future"
    # but the crude-driven CM archetype still flags commodity + carries the note
    assert cfg["structure"]["is_commodity"] is True
    assert any(w == commodities.LEVERAGE_NOTE for w in cfg["warnings"])


def test_unresolved_long_leg_raises_not_fabricates():
    """No symbol_a / factor → honest ValueError, not an invented leg."""
    class _V:
        view_type = "relative"
        time_horizon = "6m"

    with pytest.raises(ValueError):
        pair_builder.build_pair_expression(
            None, _V(), get_archetype(R1), "balanced",
        )
