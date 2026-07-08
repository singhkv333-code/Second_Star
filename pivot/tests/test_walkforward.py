"""Walk-forward + no-skill permutation test (Phase 1.4) — deterministic.

Pins the permutation p-value math + its discrimination (real serial structure vs
iid noise), the walk-forward fold accounting, and the warmup-aware Engine-2b
adapter running end-to-end on synthetic bars.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from backend.services.backtest.validation.walkforward import (
    deep_validate_engine2b,
    permutation_test,
    walk_forward,
)
from backend.workflows.dsl.backtest.schema import ExitPolicyDeclarative as ExitPolicy

VERDICTS_PERM = {"beats_random", "no_skill"}
VERDICTS_WF = {"consistent_oos", "inconsistent_oos"}


def _mom_return(close):
    """A momentum rule's return: long next bar when the trailing 5-bar return is
    positive. Order-sensitive, so a permutation of returns changes the result."""
    close = np.asarray(close, float)
    r = close[1:] / close[:-1] - 1.0
    n = close.size
    sig = np.zeros(n)
    for t in range(5, n):
        sig[t] = 1.0 if close[t] / close[t - 5] - 1.0 > 0 else 0.0
    return float(np.prod(1.0 + sig[:-1] * r) - 1.0) * 100.0


def _autocorr_close(n=400, phi=0.3, seed=3):
    rng = np.random.default_rng(seed)
    e = rng.standard_normal(n) * 0.01
    r = np.zeros(n)
    for t in range(1, n):
        r[t] = phi * r[t - 1] + e[t]
    return 100.0 * np.cumprod(1.0 + r)


def _random_walk_close(n=400, seed=9):
    rng = np.random.default_rng(seed)
    return 100.0 * np.cumprod(1.0 + rng.standard_normal(n) * 0.01)


# ── permutation math + discrimination ────────────────────────────────

def test_permutation_detects_real_structure():
    close = _autocorr_close()
    obs = _mom_return(close)
    res = permutation_test(_mom_return, close, observed=obs, n_perm=200, seed=5)
    assert res["verdict"] == "beats_random"
    assert res["p_value"] < 0.05
    assert res["observed"] > res["null_mean"]      # real order beats the shuffled null


def test_permutation_discriminates_structure_from_noise():
    ac = _autocorr_close()
    rw = _random_walk_close()
    p_ac = permutation_test(_mom_return, ac, observed=_mom_return(ac), n_perm=200, seed=5)
    p_rw = permutation_test(_mom_return, rw, observed=_mom_return(rw), n_perm=200, seed=5)
    # structure is more significant than noise, and the random walk isn't "skill".
    assert p_ac["p_value"] < p_rw["p_value"]
    assert p_rw["verdict"] == "no_skill"


def test_permutation_is_deterministic_and_bounded():
    close = _autocorr_close()
    obs = _mom_return(close)
    a = permutation_test(_mom_return, close, observed=obs, n_perm=120, seed=42)
    b = permutation_test(_mom_return, close, observed=obs, n_perm=120, seed=42)
    assert a["p_value"] == b["p_value"]            # same seed → same result
    assert 0.0 < a["p_value"] <= 1.0
    assert a["n_perm"] == 120


# ── walk-forward fold accounting ─────────────────────────────────────

def test_walk_forward_consistent_when_all_folds_positive():
    # run_window returns a small positive per-bar return for every fold.
    def run_window(ts, te, warmup):
        return np.full(te - ts, 0.001)
    wf = walk_forward(run_window, n_bars=400, n_folds=4, warmup=40)
    assert wf["n_folds"] == 4
    assert wf["frac_folds_positive"] == 1.0
    assert wf["verdict"] == "consistent_oos"
    assert wf["oos_total_return_pct"] > 0


def test_walk_forward_inconsistent_when_folds_mixed():
    # first half of the folds win, second half lose → not consistent.
    def run_window(ts, te, warmup):
        sign = 1.0 if ts < 220 else -1.0          # folds at ts 40,130 win; 220,310 lose
        return np.full(te - ts, sign * 0.002)
    wf = walk_forward(run_window, n_bars=400, n_folds=4, warmup=40)
    assert wf["verdict"] in VERDICTS_WF
    assert wf["frac_folds_positive"] < 1.0
    assert wf["frac_folds_positive"] == 0.5


def test_walk_forward_returns_none_when_too_short():
    assert walk_forward(lambda a, b, c: [], n_bars=45, n_folds=4, warmup=40) is None


# ── Engine-2b adapter end-to-end (synthetic bars, no network) ────────

def test_deep_validate_engine2b_runs_end_to_end():
    t = np.arange(320)
    close = 100.0 + 15.0 * np.sin(t / 8.0)
    idx = pd.date_range("2022-01-03", periods=320, freq="B").normalize()
    bars = pd.DataFrame({"open": close, "high": close * 1.001, "low": close * 0.999,
                         "close": close, "volume": np.full(320, 1e6)}, index=idx)
    tree = {"type": "comparison", "op": "<",
            "left": {"type": "indicator", "indicator": "rsi", "symbol": "X", "period": 14},
            "right": {"type": "constant", "value": 30}}
    out = deep_validate_engine2b(
        tree=tree, primary_symbol="X", bars=bars,
        exit_policy=ExitPolicy(kind="n_day_hold", bars=5),
        n_perm=40, n_folds=3, warmup=30, seed=11,
    )
    perm, wf = out["permutation"], out["walk_forward"]
    assert perm is not None and 0.0 < perm["p_value"] <= 1.0
    assert perm["verdict"] in VERDICTS_PERM
    assert perm["n_perm"] <= 40
    assert wf is not None and wf["verdict"] in VERDICTS_WF
    assert isinstance(out["observed_return_pct"], float)
