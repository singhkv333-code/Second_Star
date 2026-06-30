"""Unit tests for the Phase-3 theme screens (``expressions/screens.py``).

Self-contained: the only external dependency (Kite/yfinance OHLCV via
``core.data.historical.get_ohlcv``) is monkeypatched, so the ADV/liquidity path
is exercised without a network call. The curated/sector layers run against the
real static ``thematic_map`` / ``sector_universe`` tables (no fabrication).
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.core.data.historical import DataUnavailableError
from backend.view_markets.expressions import screens


# ── purity_score: the layered, disclosed score ──────────────────────────────


def test_purity_curated_winner_is_pure_play(view_db):
    """A curated thematic winner → highest-confidence pure-play, not estimated."""
    res = screens.purity_score(view_db, "ONGC", theme="crude")
    assert res.layer == "curated"
    assert res.estimated is False
    assert res.score == screens._PURITY_CURATED_WINNER
    assert "ONGC" == res.symbol


def test_purity_curated_avoid_is_excluded(view_db):
    """A curated AVOID-list name scores low and is flagged excluded."""
    res = screens.purity_score(view_db, "IOC", theme="crude")
    assert res.layer == "curated"
    assert res.estimated is False
    assert res.score == screens._PURITY_CURATED_AVOID


def test_purity_fundamentals_segment_band(view_db):
    """Fundamentals segment-revenue share wins layer-2 with the real %, no fab."""
    res = screens.purity_score(
        view_db,
        "SOMECO",
        theme="manufacturing",
        fundamentals={"manufacturing_revenue_pct": 65.0},
    )
    assert res.layer == "fundamentals_segment"
    assert res.estimated is False
    assert res.score == pytest.approx(65.0)
    assert "pure-play" in res.rationale


def test_purity_segment_accepts_fraction(view_db):
    """A 0..1 fraction is read as a percentage (0.30 → 30%)."""
    res = screens.purity_score(
        view_db,
        "SOMECO",
        theme="manufacturing",
        fundamentals={"segment_revenue": {"manufacturing": 0.30}},
    )
    assert res.layer == "fundamentals_segment"
    assert res.score == pytest.approx(30.0)


def test_purity_sector_proximity_is_estimated(view_db):
    """No curated tag + no segment → sector-proximity heuristic, flagged estimated."""
    res = screens.purity_score(view_db, "HDFCBANK", theme="fintech")
    assert res.layer == "llm_estimated"
    assert res.estimated is True
    assert 0.0 <= res.score <= 100.0


def test_purity_no_signal_low_confidence(view_db):
    """Unknown theme + unknown symbol → low-confidence estimate, never fabricated."""
    res = screens.purity_score(view_db, "ZZZUNKNOWN", theme="quantum_widgets")
    assert res.layer == "llm_estimated"
    assert res.estimated is True
    assert res.score == screens._PURITY_NO_SIGNAL


# ── liquidity_screen: ADV floor / options-availability / honest gaps ─────────


def _fake_df(close: float, volume: float) -> pd.DataFrame:
    return pd.DataFrame({"Close": [close] * 25, "Volume": [volume] * 25})


def test_liquidity_screen_pass_watch_fail_and_unavailable(view_db, monkeypatch):
    # cr = (Close*Volume)/1e7  → tune Volume for each band.
    fakes = {
        "TCS": _fake_df(100.0, 1_000_000),    # 1e8 traded value → 10 cr (pass)
        "WATCHCO": _fake_df(100.0, 700_000),  # 7e7 → 7 cr (watch band)
        "THINCO": _fake_df(100.0, 200_000),   # 2e7 → 2 cr (below floor)
    }

    def fake_get_ohlcv(symbol, period="1y", interval="1d"):
        if symbol == "DEADCO":
            raise DataUnavailableError(symbol, "rate-limited")
        return fakes[symbol]

    monkeypatch.setattr(screens, "get_ohlcv", fake_get_ohlcv)

    out = {r.symbol: r for r in screens.liquidity_screen(view_db, ["TCS", "WATCHCO", "THINCO", "DEADCO"])}

    assert out["TCS"].passes is True
    assert out["TCS"].adv_cr == pytest.approx(10.0, rel=1e-3)
    # TCS is a large cap in the static universe → options estimated available.
    assert out["TCS"].options_available is True

    assert out["WATCHCO"].passes is True
    assert out["WATCHCO"].watch is True
    # Not in the static universe → no mcap → options estimated unavailable.
    assert out["WATCHCO"].options_available is False

    assert out["THINCO"].passes is False
    assert out["THINCO"].watch is False

    # Unavailable data → honest None, never a fabricated number.
    assert out["DEADCO"].adv_cr is None
    assert out["DEADCO"].passes is False
    assert out["DEADCO"].watch is True

    # Impact cost is never fabricated in this layer.
    for r in out.values():
        assert r.impact_cost_bps is None


# ── apply_single_name_cap: iterative redistribution ─────────────────────────


def test_single_name_cap_redistributes_and_normalises():
    capped = screens.apply_single_name_cap(
        {"A": 0.6, "B": 0.2, "C": 0.1, "D": 0.1}, 0.4
    )
    assert sum(capped.values()) == pytest.approx(1.0)
    assert all(w <= 0.4 + 1e-9 for w in capped.values())
    assert capped["A"] == pytest.approx(0.4)
    # Excess flows pro-rata to the uncapped names (B kept its 2:1 edge over C/D).
    assert capped["B"] > capped["C"]


def test_single_name_cap_infeasible_falls_back_to_equal():
    # cap*n < 1 is infeasible → effective cap lifts to 1/n (equal weight).
    capped = screens.apply_single_name_cap({"A": 0.9, "B": 0.1}, 0.1)
    assert capped["A"] == pytest.approx(0.5)
    assert capped["B"] == pytest.approx(0.5)


def test_single_name_cap_drops_nonpositive():
    capped = screens.apply_single_name_cap({"A": 0.5, "B": 0.5, "C": 0.0}, 0.5)
    assert "C" not in capped
    assert sum(capped.values()) == pytest.approx(1.0)


# ── min_names_floor: refuse → ETF proxy ─────────────────────────────────────


def test_min_names_floor_refuses_thin_theme_with_proxy():
    res = screens.min_names_floor(["ONLYONE", "TWO", "THREE"], theme="manufacturing")
    assert res.ok is False
    assert res.n_names == 3
    assert res.etf_proxy == "MAKEINDIA"
    assert "ETF proxy" in res.note


def test_min_names_floor_passes_with_breadth():
    syms = [f"SYM{i}" for i in range(12)]
    res = screens.min_names_floor(syms, theme="manufacturing")
    assert res.ok is True
    assert res.etf_proxy is None


def test_min_names_floor_unknown_theme_no_proxy():
    res = screens.min_names_floor(["A", "B"], theme="quantum_widgets")
    assert res.ok is False
    assert res.etf_proxy is None


# ── basket_purity: purity-weighted headline ─────────────────────────────────


def test_basket_purity_weighted_average():
    purities = [
        screens.PurityResult("A", 90.0, "curated", False, ""),
        screens.PurityResult("B", 30.0, "llm_estimated", True, ""),
    ]
    assert screens.basket_purity(purities, {"A": 0.5, "B": 0.5}) == pytest.approx(60.0)
    # Concentrating weight on the pure name lifts the headline.
    assert screens.basket_purity(purities, {"A": 0.75, "B": 0.25}) == pytest.approx(75.0)


def test_basket_purity_equal_weight_fallback():
    purities = [
        screens.PurityResult("A", 80.0, "curated", False, ""),
        screens.PurityResult("B", 40.0, "curated", False, ""),
    ]
    # No usable weights → equal-weight average, not 0.
    assert screens.basket_purity(purities, {}) == pytest.approx(60.0)
