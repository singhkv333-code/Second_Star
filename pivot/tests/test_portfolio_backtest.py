"""Multi-position portfolio backtest (Phase 2.4) — deterministic, network-free.

Pins the constraint enforcement (max names, gross/net exposure), the compounding
math, and the two no-look-ahead guarantees (the momentum signal and the
simulation both ignore future data).
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.market.yfinance_service import canonical_symbol
from backend.services.backtest.portfolio import (
    momentum_scores,
    run_portfolio_backtest,
    simulate_portfolio,
    target_weights,
)
from backend.services.backtest.portfolio.engine import PortfolioError


# ── constraints ──────────────────────────────────────────────────────

def test_target_weights_enforces_max_names_and_gross():
    sc = np.array([3.0, 1, 2, np.nan, 5])
    w = target_weights(sc, top_n=2, gross=1.0)
    assert (w != 0).sum() == 2                    # max names
    assert abs(abs(w).sum() - 1.0) < 1e-9         # gross budget
    assert w[4] > 0 and w[0] > 0                  # the two highest scores


def test_target_weights_long_short_is_dollar_neutral():
    sc = np.array([3.0, 1, 2, 0.5, 5])
    w = target_weights(sc, top_n=2, gross=1.0, long_short=True, bottom_n=2)
    assert abs(abs(w).sum() - 1.0) < 1e-9         # gross
    assert abs(w.sum()) < 1e-9                     # net ≈ 0
    assert (w > 0).sum() == 2 and (w < 0).sum() == 2


def test_target_weights_excludes_nan_scores():
    sc = np.array([np.nan, np.nan, 1.0, 2.0])
    w = target_weights(sc, top_n=3, gross=1.0)
    assert (w != 0).sum() == 2                    # only 2 eligible


def test_target_weights_respects_sector_cap():
    # 6 names, top_n=4; without a cap the 4 highest are all 'tech'. Cap = 2/sector.
    sc = np.array([6.0, 5, 4, 3, 2, 1])
    sectors = ["tech", "tech", "tech", "tech", "bank", "bank"]
    w = target_weights(sc, top_n=4, gross=1.0, sectors=sectors, max_names_per_sector=2)
    held = [i for i in range(6) if w[i] != 0]
    tech_held = sum(1 for i in held if sectors[i] == "tech")
    assert tech_held <= 2                          # sector cap enforced
    assert len(held) == 4 and abs(abs(w).sum() - 1.0) < 1e-9


# ── simulation ───────────────────────────────────────────────────────

def test_simulate_compounds_and_lags_one_bar():
    R = np.zeros((5, 2)); R[:, 0] = 0.01
    sim = simulate_portfolio(R, {0: np.array([1.0, 0.0])}, cost_rate=0.0, starting_capital=100.0)
    # decision at day 0 applies from day 1 → 100·1.01^4
    assert abs(sim["equity"][-1] - 100 * 1.01 ** 4) < 1e-6
    assert abs(sim["equity"][0] - 100.0) < 1e-9   # flat before first applied


def test_simulate_charges_turnover_cost():
    R = np.zeros((4, 2))
    free = simulate_portfolio(R, {0: np.array([1.0, 0.0])}, cost_rate=0.0, starting_capital=100.0)
    costed = simulate_portfolio(R, {0: np.array([1.0, 0.0])}, cost_rate=0.01, starting_capital=100.0)
    assert costed["equity"][-1] < free["equity"][-1]   # cost dragged equity


def test_simulation_has_no_lookahead():
    rng = np.random.default_rng(5)
    R = 0.01 * rng.standard_normal((200, 4))
    targets = {60: np.array([0.5, 0.5, 0, 0]), 120: np.array([0, 0, 0.5, 0.5])}
    base = simulate_portfolio(R, targets, cost_rate=0.001)
    R2 = R.copy(); R2[130:] *= 2.0                # corrupt the future
    pert = simulate_portfolio(R2, targets, cost_rate=0.001)
    assert np.allclose(base["equity"][:130], pert["equity"][:130])


# ── momentum signal causality ────────────────────────────────────────

def test_momentum_is_causal():
    rng = np.random.default_rng(1)
    P = np.cumprod(1 + 0.001 * rng.standard_normal((300, 3)), axis=0) * 100
    s = momentum_scores(P, 260, lookback=252, skip=21)
    P2 = P.copy(); P2[261:] *= 1.5                # change the future
    s2 = momentum_scores(P2, 260, lookback=252, skip=21)
    assert np.allclose(s, s2, equal_nan=True)
    # not enough history → NaN
    assert np.all(np.isnan(momentum_scores(P, 100, lookback=252, skip=21)))


# ── full backtest assembly (data injected) ───────────────────────────

def _records(prices):
    return [{"date": f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}", "close": float(p)}
            for i, p in enumerate(prices)]


def test_run_portfolio_backtest_structure(monkeypatch):
    rng = np.random.default_rng(3)
    n, T = 6, 220
    # trending random walks so momentum has something to rank
    P = {f"S{i}": np.cumprod(1 + (0.0005 * i + 0.01 * rng.standard_normal(T))) * 100
         for i in range(n)}
    canon = {f"S{i}": canonical_symbol(f"S{i}") for i in range(n)}
    monkeypatch.setattr(
        "backend.services.backtest.portfolio.engine.fetch_multi_symbol",
        lambda syms, period, interval: {canon[f"S{i}"]: _records(P[f"S{i}"]) for i in range(n)},
    )
    r = run_portfolio_backtest(
        [f"S{i}" for i in range(n)], period="2y",
        top_n=3, rebalance="M", lookback=60, skip=5,
    )
    m = r["metrics"]
    for key in ("forward_stats", "monte_carlo", "sub_periods", "trust_verdict"):
        assert key in m
    assert m["n_rebalances"] > 0
    assert m["avg_gross"] <= 1.01                 # gross budget respected
    assert len(r["symbols"]) == n


@pytest.mark.parametrize("kwargs,frag", [
    (dict(signal="rsi"), "signal"),
    (dict(top_n=10), "symbols"),                  # 3 symbols < top_n=10
])
def test_run_portfolio_validation(kwargs, frag):
    with pytest.raises(PortfolioError) as e:
        run_portfolio_backtest(["AAA", "BBB", "CCC"], **kwargs)
    assert frag in str(e.value)
