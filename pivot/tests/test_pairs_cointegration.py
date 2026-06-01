"""Pairs / cointegration (Phase 2.3) — deterministic, network-free.

The cointegration math is implemented from scratch (no statsmodels), so these
tests pin it down on synthetic series with known answers, prove the backtest
signal is look-ahead-free, and exercise the scanner with injected data.
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.market.yfinance_service import canonical_symbol
from backend.services.backtest.pairs import (
    adf_tstat,
    engle_granger,
    ou_half_life,
    rolling_zscore,
    run_pairs_backtest,
    simulate_pairs,
)
from backend.services.backtest.pairs.engine import PairsError


def _ar1(n, phi, rng, scale=1.0):
    y = np.zeros(n)
    for i in range(1, n):
        y[i] = phi * y[i - 1] + scale * rng.standard_normal()
    return y


# ── cointegration math ───────────────────────────────────────────────

def test_adf_random_walk_is_not_stationary():
    rng = np.random.default_rng(7)
    rw = np.cumsum(rng.standard_normal(600))
    t, _, _ = adf_tstat(rw, regression="c")
    assert t > -2.86          # fails to reject unit root


def test_adf_stationary_ar1_is_stationary():
    rng = np.random.default_rng(7)
    ar = _ar1(600, 0.5, rng)
    t, _, _ = adf_tstat(ar, regression="c")
    assert t < -2.86          # rejects unit root strongly


def test_engle_granger_detects_cointegration():
    rng = np.random.default_rng(11)
    x = np.cumsum(rng.standard_normal(600)) + 50
    spread = _ar1(600, 0.6, rng, scale=0.5)        # stationary
    y = 1.0 + 2.0 * x + spread
    eg = engle_granger(y, x)
    assert eg.is_cointegrated
    assert eg.cointegrated_at in ("1%", "5%")
    assert abs(eg.beta - 2.0) < 0.1                # hedge ratio recovered
    assert eg.half_life is not None and eg.half_life > 0


def test_engle_granger_rejects_independent_walks():
    rng = np.random.default_rng(13)
    x = np.cumsum(rng.standard_normal(600)) + 50
    y = np.cumsum(rng.standard_normal(600)) + 30   # independent
    eg = engle_granger(y, x)
    assert not eg.is_cointegrated
    assert eg.cointegrated_at is None


def test_ou_half_life_recovers_known_speed():
    rng = np.random.default_rng(3)
    ar = _ar1(2000, 0.5, rng)                      # theoretical HL = ln2/0.5 ≈ 1.39
    hl = ou_half_life(ar)
    assert hl is not None and 0.8 < hl < 2.5


def test_ou_half_life_long_or_none_for_random_walk():
    # A random walk has no fast, tradable mean reversion: either b >= 0 (None)
    # or a tiny-negative finite-sample b giving an implausibly long half-life.
    rng = np.random.default_rng(5)
    rw = np.cumsum(rng.standard_normal(500))
    hl = ou_half_life(rw)
    assert hl is None or hl > 30


def test_rolling_zscore_is_causal_and_correct():
    s = np.array([1.0, 2, 3, 4, 5, 4, 3, 2, 1, 2], dtype=float)
    z = rolling_zscore(s, window=4)
    assert np.all(np.isnan(z[:3]))                 # first window-1 are NaN
    win = s[1:5]
    expected = (s[4] - win.mean()) / win.std(ddof=1)
    assert abs(z[4] - expected) < 1e-9


# ── simulation core ──────────────────────────────────────────────────

def _coint_pair(n=400, seed=21):
    rng = np.random.default_rng(seed)
    b = np.cumsum(rng.standard_normal(n)) + 100
    spread = _ar1(n, 0.7, rng, scale=1.5)
    a = 5.0 + 1.5 * b + spread
    return a, b


def test_simulate_generates_trades_on_mean_reverting_spread():
    a, b = _coint_pair()
    sim = simulate_pairs(a, b, lookback=60, entry_z=1.5, exit_z=0.5, stop_z=5.0)
    assert np.any(sim["pos"] != 0)                 # took positions
    assert np.isfinite(sim["equity"][-1])
    assert sim["equity"].size == a.size


def test_simulation_has_no_lookahead():
    """Perturbing prices after day k must not change any position or return at
    or before k — the core no-look-ahead guarantee."""
    a, b = _coint_pair()
    k = 250
    base = simulate_pairs(a, b, lookback=60, entry_z=1.5, exit_z=0.5, stop_z=5.0)
    a2 = a.copy()
    a2[k + 1:] *= 1.7                              # corrupt the future
    perturbed = simulate_pairs(a2, b, lookback=60, entry_z=1.5, exit_z=0.5, stop_z=5.0)
    assert np.array_equal(base["pos"][: k + 1], perturbed["pos"][: k + 1])
    assert np.allclose(base["net_ret"][: k + 1], perturbed["net_ret"][: k + 1],
                       equal_nan=True)


# ── full backtest assembly (data injected, no network) ───────────────

def _records(prices):
    return [{"date": f"2020-01-{i + 1:02d}", "close": float(p)} for i, p in enumerate(prices)]


def test_run_pairs_backtest_structure(monkeypatch):
    a, b = _coint_pair(n=300)
    ca, cb = canonical_symbol("AAA"), canonical_symbol("BBB")
    monkeypatch.setattr(
        "backend.services.backtest.pairs.engine.fetch_multi_symbol",
        lambda syms, period, interval: {ca: _records(a), cb: _records(b)},
    )
    r = run_pairs_backtest("AAA", "BBB", period="2y", lookback=60)
    assert r["pair"] == {"a": ca, "b": cb}
    assert r["cointegration"]["is_cointegrated"] is True       # by construction
    # full rigor battery present
    m = r["metrics"]
    for key in ("forward_stats", "monte_carlo", "sub_periods", "trust_verdict"):
        assert key in m
    assert "verdict" in m["trust_verdict"]
    assert isinstance(m["n_trades"], int)


@pytest.mark.parametrize("kwargs,frag", [
    (dict(entry_z=1.0, exit_z=1.5), "entry_z"),
    (dict(lookback=10), "lookback"),
])
def test_run_pairs_backtest_validation(kwargs, frag):
    with pytest.raises(PairsError) as e:
        run_pairs_backtest("AAA", "BBB", **kwargs)
    assert frag in str(e.value)


def test_same_symbol_rejected():
    with pytest.raises(PairsError):
        run_pairs_backtest("RELIANCE", "RELIANCE")


# ── scanner (injected data) ──────────────────────────────────────────

def test_scanner_finds_cointegrated_pair(monkeypatch):
    a, b = _coint_pair(n=300, seed=21)
    rng = np.random.default_rng(99)
    c = np.cumsum(rng.standard_normal(300)) + 80      # independent of a,b
    store = {
        canonical_symbol("AAA"): a,
        canonical_symbol("BBB"): b,
        canonical_symbol("CCC"): c,
    }

    def fake_fetch(sym, period, interval):
        return _records(store[canonical_symbol(sym)])

    monkeypatch.setattr(
        "backend.services.backtest.pairs.scanner.fetch_price_history", fake_fetch
    )
    from backend.services.backtest.pairs import scan_pairs
    res = scan_pairs(["AAA", "BBB", "CCC"], period="2y", min_level="5%")
    assert res["tested"] == 3
    found = {frozenset((r["dependent"], r["independent"])) for r in res["cointegrated"]}
    assert frozenset((canonical_symbol("AAA"), canonical_symbol("BBB"))) in found
