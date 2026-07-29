"""P1.3 trial counter — Deflated-Sharpe selection-bias guard.

The kept strategy's DSR must fall as MORE distinct variants are tried in the
same session (multiple-testing deflation), while re-running the identical
strategy must NOT inflate the trial count.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.services.backtest.validation.trials import (
    record_and_deflate,
    reset_group,
    strategy_fingerprint,
)
from backend.services.forward_stats import (
    deflated_sharpe_ratio,
    psr,
)


def _block(sharpe: float, n: int = 250, skew: float = 0.0, kurt: float = 3.0) -> dict:
    """A minimal forward_stats block with real PSR/DSR for the given moments."""
    return {
        "observed_sharpe": sharpe,
        "skew": skew,
        "kurtosis": kurt,
        "n_obs": n,
        "num_trials": 1,
        "psr": psr(sharpe, n, skew, kurt, sr_threshold=0.0),
        "min_trl": None,
        "deflated_sharpe": deflated_sharpe_ratio(sharpe, n, skew, kurt, 1),
    }


def test_no_group_is_noop():
    blk = _block(0.15)
    assert record_and_deflate(blk, None, "fp") == blk
    assert record_and_deflate(blk, "", "fp") == blk


def test_first_trial_num_trials_one_dsr_equals_psr():
    reset_group("G_first")
    out = record_and_deflate(_block(0.15), "G_first", "A")
    assert out["num_trials"] == 1
    # N=1 ⇒ SR0=0 ⇒ DSR == PSR(0).
    assert out["deflated_sharpe"] == pytest.approx(
        psr(0.15, 250, 0.0, 3.0, sr_threshold=0.0), abs=1e-4
    )


def test_more_variants_deflate_the_kept_strategy():
    reset_group("G_deflate")
    d1 = record_and_deflate(_block(0.15), "G_deflate", "A")["deflated_sharpe"]
    # A second DISTINCT (weaker) variant joins the session...
    record_and_deflate(_block(0.05), "G_deflate", "B")
    # ...so re-evaluating the strong strategy now deflates for N=2.
    strong2 = record_and_deflate(_block(0.15), "G_deflate", "A")
    assert strong2["num_trials"] == 2
    assert strong2["deflated_sharpe"] is not None and d1 is not None
    assert strong2["deflated_sharpe"] < d1  # trying more ⇒ less confidence


def test_identical_fingerprint_does_not_inflate_n():
    reset_group("G_dedup")
    record_and_deflate(_block(0.15), "G_dedup", "A")
    out = record_and_deflate(_block(0.15), "G_dedup", "A")
    assert out["num_trials"] == 1  # same strategy, re-run — not a new trial


def test_fingerprint_stability_and_sensitivity():
    a = strategy_fingerprint([{"step_type": "x"}], "RELIANCE", "2y", None, None)
    b = strategy_fingerprint([{"step_type": "x"}], "RELIANCE", "2y", None, None)
    c = strategy_fingerprint([{"step_type": "x"}], "TCS", "2y", None, None)
    assert a == b and a != c


def test_reset_group_forgets_trials():
    reset_group("G_reset")
    record_and_deflate(_block(0.1), "G_reset", "A")
    record_and_deflate(_block(0.1), "G_reset", "B")
    reset_group("G_reset")
    out = record_and_deflate(_block(0.1), "G_reset", "C")
    assert out["num_trials"] == 1  # cleared


def test_engine_deflates_across_distinct_variants(monkeypatch):
    """End-to-end through Engine 2: two distinct strategies in one trial_group →
    the second backtest reports num_trials == 2 in its forward_stats."""
    from backend.services import workflow_backtester as wb

    idx = pd.bdate_range("2024-01-01", periods=40)
    bars = pd.DataFrame(
        {
            "Open": [100 + i for i in range(40)],
            "High": [100.6 + i for i in range(40)],
            "Low": [99.4 + i for i in range(40)],
            "Close": [100.3 + i for i in range(40)],
            "Volume": [1_000_000] * 40,
        },
        index=idx,
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
    reset_group("uTEST")

    steps_a = [
        {"step_type": "trigger.schedule", "config": {"cron": "0 9 * * *"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 1}},
    ]
    steps_b = [
        {"step_type": "trigger.schedule", "config": {"cron": "0 9 * * *"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 2}},
    ]
    r1 = wb.backtest_workflow(steps_a, period="1y", name="A", trial_group="uTEST")
    assert r1.metrics["forward_stats"]["num_trials"] == 1
    r2 = wb.backtest_workflow(steps_b, period="1y", name="B", trial_group="uTEST")
    assert r2.metrics["forward_stats"]["num_trials"] == 2
    # Re-running A (same fingerprint) must NOT push the count to 3.
    r1b = wb.backtest_workflow(steps_a, period="1y", name="A", trial_group="uTEST")
    assert r1b.metrics["forward_stats"]["num_trials"] == 2
