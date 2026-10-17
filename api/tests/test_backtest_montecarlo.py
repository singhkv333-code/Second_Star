"""Monte-Carlo robustness (circular block bootstrap) — services.backtest.validation."""
from __future__ import annotations

import numpy as np

from backend.services.backtest.validation import monte_carlo_robustness


def test_too_short_returns_none():
    assert monte_carlo_robustness([0.01] * 5) is None
    assert monte_carlo_robustness([]) is None


def test_deterministic_for_seed():
    rng = np.random.default_rng(0)
    rets = list(rng.normal(0.001, 0.02, size=200))
    a = monte_carlo_robustness(rets, seed=42)
    b = monte_carlo_robustness(rets, seed=42)
    assert a == b
    # A different seed should (almost surely) move at least one statistic.
    c = monte_carlo_robustness(rets, seed=43)
    assert a is not None and c is not None
    assert a != c


def test_constant_positive_returns_have_no_drawdown_no_loss():
    # Every bar +1% → any resampling is still all +1% → monotonic equity.
    res = monte_carlo_robustness([0.01] * 60)
    assert res is not None
    assert res["dd_worst_pct"] == 0.0
    assert res["dd_p95_severity_pct"] == 0.0
    assert res["prob_loss"] == 0.0
    assert res["prob_dd_worse_than_tol"] == 0.0
    assert res["terminal_median_pct"] > 0.0


def test_volatile_series_produces_negative_drawdowns_and_bounded_probs():
    rng = np.random.default_rng(7)
    # Zero-drift, fat daily vol → frequent, real drawdowns.
    rets = list(rng.normal(0.0, 0.03, size=300))
    res = monte_carlo_robustness(rets, n_sims=2000)
    assert res is not None
    # Severity tail must be at least as deep as the median drawdown.
    assert res["dd_p95_severity_pct"] <= res["dd_median_pct"] <= 0.0
    assert res["dd_worst_pct"] <= res["dd_p95_severity_pct"]
    for k in ("prob_loss", "prob_dd_worse_than_tol"):
        assert 0.0 <= res[k] <= 1.0
    # Zero ARITHMETIC drift + real vol → volatility drag (geometric mean ≈
    # -σ²/2) makes losing paths the norm over a long horizon.
    assert res["prob_loss"] > 0.5
    # A clearly positive-drift series must end in loss far less often — the
    # bootstrap discriminates skill, it isn't just noise.
    rng2 = np.random.default_rng(7)
    up = list(rng2.normal(0.004, 0.03, size=300))
    res_up = monte_carlo_robustness(up, n_sims=2000)
    assert res_up is not None
    assert res_up["prob_loss"] < res["prob_loss"]


def test_tolerance_threshold_monotone():
    rng = np.random.default_rng(11)
    rets = list(rng.normal(0.0005, 0.02, size=250))
    lenient = monte_carlo_robustness(rets, drawdown_tolerance_pct=-40.0)
    strict = monte_carlo_robustness(rets, drawdown_tolerance_pct=-5.0)
    assert lenient is not None and strict is not None
    # Breaching a shallow tolerance (-5%) must be at least as likely as a deep one.
    assert strict["prob_dd_worse_than_tol"] >= lenient["prob_dd_worse_than_tol"]


def test_block_size_defaults_and_clamps():
    res = monte_carlo_robustness([0.01, -0.02] * 30)  # n=60 → block ~ 60**(1/3) ≈ 4
    assert res is not None
    assert res["block_size"] >= 2
    assert res["block_size"] <= 60
