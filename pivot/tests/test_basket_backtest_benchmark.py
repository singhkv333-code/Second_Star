"""Basket backtest "buy & hold" benchmark — regression coverage for the bug
where a basket's benchmark silently compared against ONE arbitrarily-chosen
constituent (alphabetical primary_symbol fallback) instead of the basket's
own target-weight buy-and-hold. See workflow_backtester._basket_buy_hold_weights
and the benchmark block in backtest_workflow().
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import workflow_backtester as wb


# ── Pure unit tests: _basket_buy_hold_weights ───────────────────────────


class _FakeBranch:
    def __init__(self, body):
        self.body = body


def test_basket_weights_from_allocate_basket_legs():
    branches = [_FakeBranch([
        {"step_type": "trigger.schedule", "config": {}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [
                {"symbol": "aaaa", "weight": 60},
                {"symbol": "zzzz", "weight": 40},
            ],
            "total_inr": 100000,
        }},
    ])]
    weights = wb._basket_buy_hold_weights(branches)
    assert weights == pytest.approx({"AAAA": 0.6, "ZZZZ": 0.4})


def test_basket_weights_equal_split_for_multi_place_order():
    branches = [_FakeBranch([
        {"step_type": "action.place_order", "config": {"symbol": "AAAA", "side": "buy"}},
        {"step_type": "action.place_order", "config": {"symbol": "BBBB", "side": "buy"}},
        {"step_type": "action.place_order", "config": {"symbol": "CCCC", "side": "buy"}},
    ])]
    weights = wb._basket_buy_hold_weights(branches)
    assert weights == pytest.approx({"AAAA": 1 / 3, "BBBB": 1 / 3, "CCCC": 1 / 3})


def test_basket_weights_empty_for_single_symbol():
    branches = [_FakeBranch([
        {"step_type": "action.place_order", "config": {"symbol": "AAAA", "side": "buy"}},
    ])]
    assert wb._basket_buy_hold_weights(branches) == {}


# ── Integration: backtest_workflow's benchmark on a real basket ────────


def _flat_or_ramp_bars(n: int, lo: float, hi: float) -> pd.DataFrame:
    closes = np.linspace(lo, hi, n)
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({
        "Open": closes, "High": closes + 1, "Low": closes - 1,
        "Close": closes, "Volume": np.full(n, 1_000_000.0),
    }, index=idx)


def test_basket_benchmark_is_weighted_blend_not_one_leg(monkeypatch):
    """AAAA doubles (+100%), ZZZZ is flat (0%), basket weights 60/40. The
    benchmark must read close to the 60/40 blend (~+60%) — NOT AAAA's raw
    +100% (which is what the old alphabetical-primary_symbol fallback would
    have silently used, since AAAA sorts first and the basket has no
    trigger symbol or place_order leg to anchor on)."""
    n = 40
    bars_by_symbol = {
        "AAAA": _flat_or_ramp_bars(n, 100.0, 200.0),
        "ZZZZ": _flat_or_ramp_bars(n, 100.0, 100.0),
    }
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: bars_by_symbol[sym.upper()],
    )

    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-10T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [
                {"symbol": "AAAA", "weight": 60},
                {"symbol": "ZZZZ", "weight": 40},
            ],
            "total_inr": 100000,
        }},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="Basket")

    assert res.strategy_kind == "basket"
    assert res.benchmark_label == "2-name basket (ideal weights)"
    # A 60/40 blend of (a leg that roughly doubles) and (a flat leg), over
    # the one-time-entry-clipped window (run_at trims the start, so AAAA's
    # captured move is less than its full 100->200 range) — comfortably
    # between the two legs' own returns, nowhere near AAAA's ~100%+ alone.
    # That gap (this used to silently equal AAAA's own return, the exact
    # bug this test guards) is the regression signature.
    assert 25.0 < res.bench_buy_hold_return_pct < 55.0


def test_basket_benchmark_explicit_symbol_overrides_basket_default(monkeypatch):
    """An explicit benchmark_symbol (e.g. an index) always wins over the
    basket-ideal-weights default."""
    n = 40
    bars_by_symbol = {
        "AAAA": _flat_or_ramp_bars(n, 100.0, 200.0),
        "ZZZZ": _flat_or_ramp_bars(n, 100.0, 100.0),
        "NIFTYBEES": _flat_or_ramp_bars(n, 100.0, 110.0),
    }
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: bars_by_symbol[sym.upper()],
    )

    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-10T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [
                {"symbol": "AAAA", "weight": 60},
                {"symbol": "ZZZZ", "weight": 40},
            ],
            "total_inr": 100000,
        }},
    ]
    res = wb.backtest_workflow(
        steps, period="1y", name="Basket", benchmark_symbol="NIFTYBEES",
    )
    assert res.benchmark_label == "NIFTYBEES"
    # ~+10% (NIFTYBEES move), not the ~60% basket blend nor AAAA's +100%.
    assert 0.0 < res.bench_buy_hold_return_pct < 15.0
