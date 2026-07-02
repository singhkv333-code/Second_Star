"""Tests for the View Markets quality engines added for the revamp:
affordability (min ₹ entries), the forward scenario model (no-precedent
events), the ETF catalog reader, and the option-model extensions."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backend.view_markets import affordability as aff
from backend.view_markets import etf_catalog
from backend.view_markets import forward_model as fwd
from backend.view_markets import option_model


# ── affordability ─────────────────────────────────────────────────────────────
def test_lite_allocation_integer_shares_and_drops():
    weights = {"CHEAP": 0.4, "MID": 0.4, "COSTLY": 0.2}
    prices = {"CHEAP": 50.0, "MID": 200.0, "COSTLY": 5000.0}
    out = aff.lite_allocation(weights, prices, budget=1000.0)
    # COSTLY (1 share > budget) must be dropped with a stated reason
    assert any(d["symbol"] == "COSTLY" for d in out.dropped)
    assert all(isinstance(l["shares"], int) and l["shares"] >= 1 for l in out.legs)
    assert out.total_cost <= 1000.0
    assert out.deployed_frac >= aff._MIN_DEPLOYED_FRAC
    # renormalised targets: CHEAP and MID become 50/50
    tgt = {l["symbol"]: l["weight_target"] for l in out.legs}
    assert tgt["CHEAP"] == pytest.approx(0.5)
    assert tgt["MID"] == pytest.approx(0.5)


def test_lite_allocation_no_price_dropped_honestly():
    out = aff.lite_allocation({"A": 1.0}, {}, budget=1000.0)
    assert out.legs == []
    assert out.dropped[0]["reason"] == "no live price"


def test_etf_route_clears_floor():
    leg = aff.etf_route({"symbol": "ITBEES", "last_price": 29.94,
                         "tracks": "Nifty IT", "as_of": "2026-07-02"}, floor=800.0)
    assert leg["units"] == math.ceil(800.0 / 29.94)
    assert leg["cost"] >= 800.0
    assert aff.etf_route({"symbol": "X", "last_price": None}) is None


def test_option_entry_is_premium_times_lot():
    e = aff.option_entry(spot=25000.0, premium_pct_of_spot=1.6, lot_size=75)
    assert e["premium_per_lot_inr"] == pytest.approx(25000 * 0.016 * 75, abs=1)
    assert aff.option_entry(spot=25000.0, premium_pct_of_spot=1.6, lot_size=None) is None


def test_entry_block_prefers_faithful_lite_basket():
    weights = {"A": 0.5, "B": 0.3, "C": 0.2}
    prices = {"A": 90.0, "B": 60.0, "C": 45.0}
    block = aff.entry_block(kind="basket", weights=weights, prices=prices,
                            as_of="2026-07-02")
    assert block["basis"] == "lite_basket"
    assert block["min_entry_inr"] is not None and block["min_entry_inr"] <= 1000
    assert len(block["legs"]) == 3


def test_entry_block_falls_back_to_etf_when_unfaithful():
    weights = {"A": 0.6, "B": 0.4}
    prices = {"A": 4000.0, "B": 6000.0}         # nothing affordable
    etf = {"symbol": "BANKBEES", "last_price": 600.07, "tracks": "Nifty Bank",
           "as_of": "2026-07-02"}
    block = aff.entry_block(kind="basket", weights=weights, prices=prices, etf=etf)
    assert block["basis"] == "etf_substitute"
    assert block["min_entry_inr"] == pytest.approx(2 * 600.07, abs=1)


def test_entry_block_pair_states_boundary():
    block = aff.entry_block(kind="pair")
    assert block["basis"] == "margin_required"
    assert block["min_entry_inr"] is None


# ── forward model ─────────────────────────────────────────────────────────────
def _beta_fixture(beta=1.5, n_days=800, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n_days)
    drv = pd.Series(rng.normal(0, 0.01, n_days), index=idx)
    port = beta * drv + pd.Series(rng.normal(0, 0.002, n_days), index=idx)
    return port, drv


def test_driver_beta_recovers_known_beta():
    port, drv = _beta_fixture(beta=1.5)
    b = fwd.driver_beta(port, drv)
    assert b is not None
    assert b["beta"] == pytest.approx(1.5, abs=0.15)
    assert b["t_stat"] > 5
    assert b["n_weeks"] >= 52


def test_driver_beta_insufficient_history_returns_none():
    port, drv = _beta_fixture(n_days=60)
    assert fwd.driver_beta(port, drv) is None


def test_scenario_forward_arithmetic_and_honesty():
    beta_block = {"beta": 0.9, "t_stat": 8.0, "r2": 0.5, "n_weeks": 200}
    out = fwd.scenario_forward(
        p_yes=0.6, p_source="assumed even odds (stated)", driver="GOLD",
        driver_move_yes_pct=12.0, driver_move_no_pct=-5.0,
        beta_block=beta_block, sigma_annual=0.14, horizon_days=126,
        round_trip_bps=30.0,
    )
    r_yes, r_no = 0.9 * 12.0, 0.9 * -5.0
    gross = 0.6 * r_yes + 0.4 * r_no
    net = 0.5 * gross - 0.30
    assert out["expected_gross_pct"] == pytest.approx(gross, abs=0.05)
    assert out["expected_net_pct"] == pytest.approx(net, abs=0.05)
    assert out["no_history"] is True
    assert out["band_pct"]["p25"] < out["band_pct"]["p50"] < out["band_pct"]["p75"]
    assert out["band_pct"]["p05"] < out["band_pct"]["p25"]
    assert any("not a track record" in a for a in out["assumptions"])


def test_scenario_forward_degenerate_returns_none():
    assert fwd.scenario_forward(
        p_yes=0.5, p_source="x", driver="GOLD",
        driver_move_yes_pct=10, driver_move_no_pct=-5,
        beta_block=None, sigma_annual=0.2, horizon_days=126) is None


# ── option model extensions ───────────────────────────────────────────────────
def test_width_for_vol_scales_and_clamps():
    low = option_model.width_for_vol(0.12, 63)     # calm index, 3 months
    high = option_model.width_for_vol(0.45, 126)   # wild single stock, 6 months
    assert 3.0 <= low < high <= 15.0
    assert option_model.width_for_vol(0.0, 126) == 3.0


def test_long_straddle_shape():
    om = option_model.model_long_straddle(sigma_annual=0.2, horizon_days=63,
                                          underlying_label="NIFTY")
    assert om["structure"] == "long_straddle"
    assert om["direction"] == "two_sided"
    assert om["net_premium_pct"] > 0
    assert om["max_loss_pct"] == -100.0
    assert om["max_profit_uncapped"] is True
    assert 0 < om["pop_pct"] < 100
    # payoff floor is exactly the premium (−100% of capital), at the strike
    assert min(p["pnl_pct"] for p in om["payoff"]) == -100.0
    assert om["legs"][0]["option_type"] == "CE" and om["legs"][1]["option_type"] == "PE"


def test_vertical_spread_bearish_still_works():
    om = option_model.model_vertical_spread(
        bullish=False, sigma_annual=0.25, horizon_days=63,
        width_pct=option_model.width_for_vol(0.25, 63), underlying_label="X")
    assert om["structure"] == "bear_put_spread"
    assert om["breakeven_move_pct"] < 0


# ── etf catalog ───────────────────────────────────────────────────────────────
def test_etf_catalog_lookup_real_file():
    cats = etf_catalog.categories()
    if not cats:                                  # catalog not built in CI
        pytest.skip("etf_catalog.json not built")
    gold = etf_catalog.etf_for("gold")
    assert gold and gold["symbol"] == "GOLDBEES"
    it = etf_catalog.etf_for("Information Technology")
    assert it and it["last_price"] > 0
    assert etf_catalog.etf_for("nonexistent-theme-xyz") is None
