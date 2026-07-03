"""Focused unit tests for the MULTI_ASSET expression builder (Phase 3).

Self-contained: the sibling sub-builders (``basket_builder`` / ``hedge_builder``)
are still mid-build, so we monkeypatch them with fakes that return well-formed
``config_schema`` envelopes. We assert the multi-asset builder:

  * composes equity + gold-ETF (+ hedge) sleeves with weights summing to 1.0,
  * sizes gold to the tier target when no per-sleeve history is given, and runs
    REAL asset-class risk-parity (``weighting.compute_weights``) when it is,
  * uses ONLY a listed gold ETF (degrades SGB/physical/MCX → GOLDBEES, never
    fabricates a non-tradeable instrument),
  * carries the pinned ``multi_asset`` structure keys + scores + costs,
  * is long-only across asset classes — NO fabricated single-name short leg,
  * degrades honestly when the hedge sleeve fails (no crash, a warning instead).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from backend.view_markets.expressions import catalog, commodities, config_schema
from backend.view_markets.expressions.builders import multi_asset_builder
from backend.view_markets.expressions.builders.multi_asset_builder import (
    build_multi_asset_expression,
)

EQUITY_MOD = "backend.view_markets.expressions.builders.basket_builder"
HEDGE_MOD = "backend.view_markets.expressions.builders.hedge_builder"


def _fake_equity_env() -> dict[str, Any]:
    env = config_schema.base_envelope(
        archetype="T1_purity_conviction_basket",
        expression_kind="basket",
        tier="conservative",
        label="Manufacturing purity basket",
    )
    env["instruments"] = [
        {
            "symbol": "LT", "exchange": "NSE", "segment": "EQ",
            "instrument_type": "equity", "role": "long", "tradeable": True,
        },
        {
            "symbol": "SIEMENS", "exchange": "NSE", "segment": "EQ",
            "instrument_type": "equity", "role": "long", "tradeable": True,
        },
    ]
    env["structure"] = {
        "scheme": "risk_parity",
        "weights": {"LT": 0.55, "SIEMENS": 0.45},
        "basket_purity": 78.0,
        "single_name_cap": 0.10,
        "n_names": 2,
    }
    env["scores"] = {"basket_purity": 78.0}
    return env


def _fake_hedge_env() -> dict[str, Any]:
    env = config_schema.base_envelope(
        archetype="T3_optionized_hedged",
        expression_kind="hedge",
        tier="conservative",
        label="Nifty zero-cost collar",
    )
    env["instruments"] = [
        {
            "symbol": "NIFTY", "exchange": "NFO", "segment": "NFO-OPT",
            "instrument_type": "index_option", "role": "hedge", "tradeable": True,
        },
    ]
    env["structure"] = {
        "underlying_index": "NIFTY",
        "hedge_template": "collar",
        "legs": [{"side": "buy", "type": "PE"}, {"side": "sell", "type": "CE"}],
        "max_loss": 12000.0,
        "net_cost": 0.0,
    }
    return env


@pytest.fixture
def patch_sleeves(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the two sub-builders; return a dict so tests can swap behaviour."""
    state: dict[str, Any] = {
        "equity": lambda *a, **k: _fake_equity_env(),
        "hedge": lambda *a, **k: _fake_hedge_env(),
    }
    monkeypatch.setattr(
        EQUITY_MOD + ".build_basket_expression",
        lambda *a, **k: state["equity"](*a, **k),
    )
    monkeypatch.setattr(
        HEDGE_MOD + ".build_hedge_expression",
        lambda *a, **k: state["hedge"](*a, **k),
    )
    return state


def _t4() -> Any:
    return catalog.get_archetype("T4_multi_asset")


def test_builds_three_sleeves_with_disclosable_structure(
    view_db, theme_view, patch_sleeves
) -> None:
    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "conservative",
        symbols=["LT", "SIEMENS"], theme="manufacturing",
    )

    # Envelope identity + pinned multi_asset structure keys.
    assert cfg["expression_kind"] == "multi_asset"
    assert cfg["archetype"] == "T4_multi_asset"
    for key in config_schema.STRUCTURE_KEYS["multi_asset"]:
        assert key in cfg["structure"], f"missing structure key {key}"

    sleeves = cfg["structure"]["sleeves"]
    kinds = {s["kind"] for s in sleeves}
    assert {"equity_basket", "gold_etf", "hedge"} <= kinds

    # Long sleeves (equity + gold) sum to 1.0; the hedge is a 0-weight overlay.
    long_w = sum(s["weight"] for s in sleeves if s["kind"] != "hedge")
    assert long_w == pytest.approx(1.0, abs=1e-6)
    hedge = next(s for s in sleeves if s["kind"] == "hedge")
    assert hedge["weight"] == 0.0
    assert hedge["detail"]["overlay"] is True


def test_gold_sleeve_sized_to_tier_target_without_history(
    view_db, theme_view, patch_sleeves
) -> None:
    knob_pct = {"conservative": 0.09, "balanced": 0.05, "aggressive": 0.025}
    for tier, pct in knob_pct.items():
        cfg = build_multi_asset_expression(
            view_db, theme_view, _t4(), tier,
            symbols=["LT", "SIEMENS"], theme="manufacturing",
        )
        gold = next(s for s in cfg["structure"]["sleeves"] if s["kind"] == "gold_etf")
        assert gold["weight"] == pytest.approx(pct, abs=1e-6)
        assert cfg["structure"]["asset_class_scheme"] == "tier_target"


def test_asset_class_risk_parity_runs_with_history(
    view_db, theme_view, patch_sleeves
) -> None:
    # Two synthetic return series → real weighting.compute_weights("risk_parity").
    rng = np.random.default_rng(7)
    idx = pd.date_range("2024-01-01", periods=400, freq="B")
    eq = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.012, len(idx)))), index=idx)
    gold = pd.Series(60 * np.exp(np.cumsum(rng.normal(0, 0.006, len(idx)))), index=idx)

    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "balanced",
        symbols=["LT", "SIEMENS"], theme="manufacturing",
        asset_class_price_history={"EQUITY_SLEEVE": eq, "GOLDBEES": gold},
    )
    assert cfg["structure"]["asset_class_scheme"] == "risk_parity"
    gold_w = next(
        s["weight"] for s in cfg["structure"]["sleeves"] if s["kind"] == "gold_etf"
    )
    # Lower-vol gold should earn MORE than its 5% tier target under equal-risk.
    assert 0.0 < gold_w < 1.0
    assert gold_w > 0.05


def test_sgb_degrades_to_listed_etf_no_fabrication(
    view_db, theme_view, patch_sleeves
) -> None:
    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "conservative",
        symbols=["LT"], theme="manufacturing", gold_symbol="SGBNOV32",
    )
    gold_instr = [
        i for i in cfg["instruments"] if i["instrument_type"] == "gold_etf"
    ]
    assert gold_instr and gold_instr[0]["symbol"] == "GOLDBEES"
    assert gold_instr[0]["tradeable"] is True
    assert any("SGB" in w or "not a chat-tradeable" in w for w in cfg["warnings"])


def test_long_only_no_fabricated_short_leg(
    view_db, theme_view, patch_sleeves
) -> None:
    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "aggressive",
        symbols=["LT", "SIEMENS"], theme="manufacturing",
    )
    # No instrument is a single-name delivery short; all are long/hedge/underlying.
    assert all(i["role"] in {"long", "hedge", "underlying"} for i in cfg["instruments"])
    assert cfg["expressability"]["short_mode"] is None
    assert cfg["expressability"]["symmetric"] is True
    # Gold ETF + index hedge are flagged tradeable (no fabricated instrument).
    assert all(i["tradeable"] for i in cfg["instruments"])


def test_hedge_failure_degrades_honestly(
    view_db, theme_view, patch_sleeves
) -> None:
    def _boom(*a: Any, **k: Any) -> dict[str, Any]:
        raise RuntimeError("NIFTY chain stale")

    patch_sleeves["hedge"] = _boom
    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "conservative",
        symbols=["LT", "SIEMENS"], theme="manufacturing",
    )
    kinds = {s["kind"] for s in cfg["structure"]["sleeves"]}
    assert "hedge" not in kinds  # no fabricated hedge
    assert any("hedge sleeve unavailable" in w.lower() for w in cfg["warnings"])
    # Long sleeves still sum to 1.0 and the expression did not crash.
    long_w = sum(
        s["weight"] for s in cfg["structure"]["sleeves"] if s["kind"] != "hedge"
    )
    assert long_w == pytest.approx(1.0, abs=1e-6)


def test_costs_and_scores_present(view_db, theme_view, patch_sleeves) -> None:
    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "balanced",
        symbols=["LT", "SIEMENS"], theme="manufacturing",
    )
    assert cfg["costs"]["round_trip_bps"] > 0
    assert cfg["scores"]["alignment_kind"] == "basket_purity"
    assert cfg["scores"]["basket_purity"] == 78.0
    assert 0 < cfg["scores"]["construction_alignment"] <= 100
    assert cfg["disclaimer"] == config_schema.DISCLAIMER
    # The builder must not crash the module-level import surface.
    assert multi_asset_builder.build_multi_asset_expression is build_multi_asset_expression


# ════════════════════════════════════════════════════════════════════════════
# DIRECT MCX commodity sleeve (CM5/CM6 — the leveraged alternative to the ETF)
# ════════════════════════════════════════════════════════════════════════════


def _commodity_sleeve(cfg: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (s for s in cfg["structure"]["sleeves"] if s["kind"] == "direct_mcx_sleeve"),
        None,
    )


def test_direct_mcx_sleeve_long_keeps_etf_route_with_leverage_note(
    view_db, theme_view, patch_sleeves
) -> None:
    # Aggressive tier → the FUTURE vehicle for the direct MCX gold leg.
    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "aggressive",
        symbols=["LT", "SIEMENS"], theme="bullion",
        commodity_symbol="GOLD",
    )

    # The listed-ETF route is KEPT alongside the new direct MCX sleeve.
    kinds = {s["kind"] for s in cfg["structure"]["sleeves"]}
    assert {"equity_basket", "gold_etf", "direct_mcx_sleeve"} <= kinds

    sleeve = _commodity_sleeve(cfg)
    assert sleeve is not None
    detail = sleeve["detail"]
    # Leveraged margin leg → 0 capital weight, but it inherits the bullion risk
    # budget (the aggressive 2.5% gold target) as its notional target.
    assert sleeve["weight"] == 0.0
    assert detail["notional_target_weight"] == pytest.approx(0.025, abs=1e-6)
    assert detail["route"] == "direct_mcx"
    assert detail["alternative_to"] == "gold_etf"
    assert detail["vehicle"] == "future"
    assert detail["instrument_type"] == "commodity_future"
    assert detail["segment"] == "MCX-FUT"
    assert detail["auto_sized"] is False
    assert detail["leverage_note"] == commodities.LEVERAGE_NOTE

    # The direct MCX instrument is MCX-typed + tradeable (no fabricated instrument).
    mcx_instr = [i for i in cfg["instruments"] if i["instrument_type"] == "commodity_future"]
    assert mcx_instr and mcx_instr[0]["exchange"] == "MCX"
    assert mcx_instr[0]["segment"] == "MCX-FUT"
    assert mcx_instr[0]["role"] == "long"
    assert mcx_instr[0]["tradeable"] is True

    # Disclosures present: the leverage note rides on config.warnings + the costs
    # block flags the MCX margin leg.
    assert commodities.LEVERAGE_NOTE in cfg["warnings"]
    assert "commodity_note" in cfg["costs"]
    assert cfg["disclaimer"] == config_schema.DISCLAIMER


def test_direct_mcx_sleeve_marks_backtest_unavailable_no_fabrication(
    view_db, theme_view, patch_sleeves
) -> None:
    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "balanced",
        symbols=["LT"], theme="bullion", commodity_symbol="GOLD",
    )
    detail = _commodity_sleeve(cfg)["detail"]
    # Direct MCX futures have NO aligned daily OHLCV → backtest-unavailable, and
    # the ETF proxy route is offered as the backtestable alternative. No series
    # is fabricated.
    assert detail["backtest_available"] is False
    assert detail["etf_proxy_route"] == "GOLDBEES"
    assert any("backtest-unavailable" in w for w in cfg["warnings"])
    # Lot size is sourced from the instrument master only (None on a miss — never
    # fabricated). In the test DB there is no MCX master, so it degrades to None.
    assert detail["lot_size"] is None or isinstance(detail["lot_size"], int)


def test_direct_mcx_short_uses_tradeable_mcx_future_via_honest_short(
    view_db, theme_view, patch_sleeves
) -> None:
    # Aggressive + bearish → the clean SYMMETRIC commodity short = an MCX future.
    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "aggressive",
        symbols=["LT"], theme="crude shock", commodity_symbol="CRUDEOIL",
        commodity_direction="short",
    )
    sleeve = _commodity_sleeve(cfg)
    assert sleeve is not None
    detail = sleeve["detail"]
    short_leg = detail["short_leg"]
    # The short is a TRADEABLE MCX future (honest_short.commodity_future), never a
    # fabricated short or an AVOID.
    assert short_leg is not None
    assert short_leg["mode"] == "commodity_future"
    assert short_leg["tradeable"] is True
    assert short_leg["degraded"] is False
    assert "FUT" in short_leg["instrument"]

    mcx_instr = [i for i in cfg["instruments"] if i["instrument_type"] == "commodity_future"]
    assert mcx_instr and mcx_instr[0]["role"] == "short"
    assert mcx_instr[0]["segment"] == "MCX-FUT"
    assert mcx_instr[0]["tradeable"] is True

    # Expressability reflects the tradeable symmetric short — not None, not degraded.
    assert cfg["expressability"]["short_mode"] == "commodity_future"
    assert cfg["expressability"]["symmetric"] is True
    assert cfg["expressability"]["degraded"] is False


def test_direct_mcx_short_defined_risk_uses_mcx_put(
    view_db, theme_view, patch_sleeves
) -> None:
    # Conservative + bearish → the DEFINED-RISK vehicle = a long MCX put.
    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "conservative",
        symbols=["LT"], theme="bullion", commodity_symbol="GOLD",
        commodity_direction="short",
    )
    detail = _commodity_sleeve(cfg)["detail"]
    assert detail["short_leg"]["mode"] == "commodity_put"
    assert detail["instrument_type"] == "commodity_option"
    assert detail["segment"] == "MCX-OPT"
    mcx_instr = [i for i in cfg["instruments"] if i["instrument_type"] == "commodity_option"]
    assert mcx_instr and mcx_instr[0]["role"] == "short"
    assert mcx_instr[0]["tradeable"] is True
    assert cfg["expressability"]["short_mode"] == "commodity_put"


def test_cm5_archetype_params_drive_the_sleeve(view_db, theme_view, patch_sleeves) -> None:
    # No explicit commodity_symbol kwarg — the CM5 archetype's params supply it.
    cm5 = catalog.get_archetype("CM5_commodity_multi_asset")
    cfg = build_multi_asset_expression(
        view_db, theme_view, cm5, "balanced", symbols=["LT"], theme="bullion",
    )
    sleeve = _commodity_sleeve(cfg)
    assert sleeve is not None
    assert sleeve["detail"]["mcx_symbol"] == "GOLD"
    assert commodities.LEVERAGE_NOTE in cfg["warnings"]


def test_non_commodity_symbol_degrades_honestly_no_sleeve(
    view_db, theme_view, patch_sleeves
) -> None:
    # An ETF passed as a "commodity" is not a recognised MCX commodity → no direct
    # sleeve fabricated; we keep the ETF route and say so.
    cfg = build_multi_asset_expression(
        view_db, theme_view, _t4(), "balanced",
        symbols=["LT"], theme="bullion", commodity_symbol="GOLDBEES",
    )
    assert _commodity_sleeve(cfg) is None
    assert any("not a recognised MCX commodity" in w for w in cfg["warnings"])
    # Behaviour falls back to the long-only multi-asset shape (short_mode None).
    assert cfg["expressability"]["short_mode"] is None
