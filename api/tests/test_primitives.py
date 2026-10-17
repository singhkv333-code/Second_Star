"""Focused test suite for the wide-primitive backtester.

Covers signal correctness, no-look-ahead invariants, combinator logic, exit
primitives, end-to-end backtest runs, metrics shape, and Indian cost
calculations. End-to-end tests monkeypatch ``_fetch_ohlcv`` so no network
traffic occurs.
"""
from __future__ import annotations

import asyncio
import math

import numpy as np
import pandas as pd
import pytest

from backend.backtester import engine as engine_mod
from backend.backtester.engine import buy_cost, run_backtest, sell_cost
from backend.backtester.exits import (
    exit_stop_loss,
    exit_take_profit,
    exit_trailing_stop,
)
from backend.backtester.primitives import (
    SIGNAL_REGISTRY,
    add_cooldown,
    combine_and,
    combine_or,
    sig_52wk_high_breakout,
    sig_bb_squeeze,
    sig_rsi_cross_below,
    sig_sma_cross_above_sma,
    sig_supertrend_flip_bearish,
    sig_volume_spike,
    sig_weekday,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_df(n: int = 400, seed: int = 0, trend_slope: float = 0.0) -> pd.DataFrame:
    """Synthetic OHLCV with a configurable drift + Gaussian noise."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    close = 100 + trend_slope * np.arange(n) + rng.normal(0, 1, n).cumsum() * 0.5
    high = close + rng.uniform(0.5, 2, n)
    low = close - rng.uniform(0.5, 2, n)
    open_ = close + rng.normal(0, 0.3, n)
    vol = rng.integers(1000, 10000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def _ohlcv_from_close(closes: np.ndarray, vol: np.ndarray | None = None) -> pd.DataFrame:
    n = len(closes)
    if vol is None:
        vol = np.full(n, 1000, dtype=np.int64)
    idx = pd.date_range("2018-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": vol,
        },
        index=idx,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. RSI crossover
# ═══════════════════════════════════════════════════════════════════════════
def test_rsi_crossover_fires_exactly_once_at_transition():
    """RSI series goes 50.5 → 28.3 between day 49 and day 50 → cross_below 30
    must fire on day 50, NOT 49 or 51."""
    n = 100
    trend = np.linspace(100, 130, 40)
    mid = np.linspace(130, 130, 9) + np.array(
        [0, 0.5, -0.5, 0.3, -0.3, 0.4, -0.4, 0.2, -0.2]
    )
    drop = np.array([125.0, 115.0])  # day 49, day 50
    rest = np.linspace(115, 100, n - 40 - 9 - 2)
    closes = np.concatenate([trend, mid, drop, rest])
    df = _ohlcv_from_close(closes)

    sig = sig_rsi_cross_below(df, period=14, threshold=30.0)

    fire_indices = np.where(sig.values)[0]
    assert list(fire_indices) == [50], (
        f"Expected RSI cross below 30 only on day 50, got days {list(fire_indices)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2. SMA golden cross
# ═══════════════════════════════════════════════════════════════════════════
def test_golden_cross_fires_at_sma_intersection():
    """Long downtrend then strong rally — SMA(50) crosses above SMA(200) once."""
    declining = np.linspace(200, 100, 250)
    rally = np.linspace(100, 250, 350)
    closes = np.concatenate([declining, rally])
    df = _ohlcv_from_close(closes)

    sig = sig_sma_cross_above_sma(df, fast_period=50, slow_period=200)

    fire_indices = np.where(sig.values)[0]
    assert len(fire_indices) == 1, (
        f"Expected exactly one golden cross, got {len(fire_indices)}: {fire_indices}"
    )
    assert 250 < fire_indices[0] < 600


# ═══════════════════════════════════════════════════════════════════════════
# 3. Bollinger Band squeeze
# ═══════════════════════════════════════════════════════════════════════════
def test_bb_squeeze_fires_during_narrow_band_period():
    """Volatile period (300 days, populates 252-day rolling baseline), then
    flat period — squeeze must fire heavily during flat, rarely in volatile."""
    rng = np.random.default_rng(42)
    volatile = 100 + rng.normal(0, 3, 300).cumsum() * 0.3
    flat = np.full(300, 100.0) + rng.normal(0, 0.05, 300)
    closes = np.concatenate([volatile, flat])
    df = _ohlcv_from_close(closes)

    sq = sig_bb_squeeze(df)

    volatile_fires = sq.iloc[20:300].sum()
    flat_fires = sq.iloc[350:].sum()
    assert flat_fires > 50, f"Expected many flat-period fires, got {flat_fires}"
    assert flat_fires > volatile_fires * 5, (
        f"Flat fires ({flat_fires}) should dominate volatile fires "
        f"({volatile_fires})"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Supertrend flip
# ═══════════════════════════════════════════════════════════════════════════
def test_supertrend_flip_fires_at_trend_change():
    """Strong uptrend → strong downtrend; supertrend bearish flip fires once
    near the trend reversal."""
    n = 200
    up = np.linspace(100, 200, 100)
    down = np.linspace(200, 100, 100)
    closes = np.concatenate([up, down])
    df = _ohlcv_from_close(closes)

    flip = sig_supertrend_flip_bearish(df)

    fire_indices = np.where(flip.values)[0]
    assert len(fire_indices) >= 1, "Expected at least one bearish supertrend flip"
    # Reversal happens at day 100; flip should occur shortly after
    assert any(95 <= i <= 130 for i in fire_indices), (
        f"Expected flip near day 100, got fires at {list(fire_indices)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 5. Calendar — Monday filter
# ═══════════════════════════════════════════════════════════════════════════
def test_calendar_monday_fires_only_on_mondays():
    """sig_weekday(0) should fire on every Monday and no other day."""
    df = make_df(n=60)

    monday_sig = sig_weekday(df, 0)

    # Verify every True position is actually a Monday
    fire_days = df.index[monday_sig].weekday
    assert all(d == 0 for d in fire_days), f"Non-Monday fires found: {set(fire_days)}"
    # Verify every Monday in the index fires
    expected_mondays = df.index[df.index.weekday == 0]
    actual_mondays = df.index[monday_sig]
    assert list(expected_mondays) == list(actual_mondays)


# ═══════════════════════════════════════════════════════════════════════════
# 6. 52wk high — no look-ahead, transition-only firing
# ═══════════════════════════════════════════════════════════════════════════
def test_52wk_high_uses_shifted_data():
    """Two scenarios:
      A) yesterday wasn't a new high → today is → fires today
      B) yesterday WAS a new high → today still highest → must NOT fire today"""
    # Scenario A: linspace 100→200 over 252 days, then 180 (day 252), 250 (day 253)
    n = 300
    closes_a = np.concatenate(
        [np.linspace(100, 200, 252), [180.0, 250.0], np.full(n - 254, 240.0)]
    )
    df_a = _ohlcv_from_close(closes_a)
    sig_a = sig_52wk_high_breakout(df_a)
    assert not sig_a.iloc[252], "Day 252 (close 180, below prior max) shouldn't fire"
    assert sig_a.iloc[253], "Day 253 (close 250, prev 252-day max ~200) should fire"
    # Day 254 close 240 < 250, so today not new high — should not fire
    assert not sig_a.iloc[254]

    # Scenario B: yesterday already broke out
    closes_b = np.concatenate(
        [np.linspace(100, 200, 252), [205.0, 210.0], np.full(n - 254, 209.0)]
    )
    df_b = _ohlcv_from_close(closes_b)
    sig_b = sig_52wk_high_breakout(df_b)
    assert sig_b.iloc[252], "Day 252 (first day above 200) should fire"
    assert not sig_b.iloc[253], (
        "Day 253 must NOT fire — yesterday was already a 52wk-high break"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 7. Volume spike
# ═══════════════════════════════════════════════════════════════════════════
def test_volume_spike_correct_ratio():
    """Constant volume except a 3x spike on day 30; sig_volume_spike(mult=2.0)
    fires exactly on day 30."""
    n = 50
    closes = np.full(n, 100.0)
    vol = np.full(n, 1000, dtype=np.int64)
    vol[30] = 3500  # 3.5x the average
    df = _ohlcv_from_close(closes, vol=vol)

    sig = sig_volume_spike(df, period=20, mult=2.0)

    fire_indices = np.where(sig.values)[0]
    assert list(fire_indices) == [30], (
        f"Expected spike on day 30 only, got {list(fire_indices)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 8. combine_and
# ═══════════════════════════════════════════════════════════════════════════
def test_combine_and_requires_all_conditions():
    idx = pd.date_range("2020-01-01", periods=10, freq="B")
    a = pd.Series([True, False, True, False, True, False, False, False, True, False], index=idx)
    b = pd.Series([False, False, True, True, False, False, True, False, False, False], index=idx)

    out = combine_and(a, b)

    expected = pd.Series(
        [False, False, True, False, False, False, False, False, False, False], index=idx
    )
    assert (out.values == expected.values).all()


# ═══════════════════════════════════════════════════════════════════════════
# 9. combine_or
# ═══════════════════════════════════════════════════════════════════════════
def test_combine_or_requires_any_condition():
    idx = pd.date_range("2020-01-01", periods=10, freq="B")
    a = pd.Series([True, False, True, False, True, False, False, False, True, False], index=idx)
    b = pd.Series([False, False, True, True, False, False, True, False, False, False], index=idx)

    out = combine_or(a, b)

    expected = pd.Series(
        [True, False, True, True, True, False, True, False, True, False], index=idx
    )
    assert (out.values == expected.values).all()


# ═══════════════════════════════════════════════════════════════════════════
# 10. add_cooldown
# ═══════════════════════════════════════════════════════════════════════════
def test_add_cooldown_suppresses_repeated_signals():
    """Series True on days 0, 2, 5, 8 with cooldown=4:
       day 0 keep, block 1-4, day 5 keep, block 6-9, day 8 suppressed."""
    idx = pd.date_range("2020-01-01", periods=10, freq="B")
    raw = pd.Series([False] * 10, index=idx)
    raw.iloc[[0, 2, 5, 8]] = True

    out = add_cooldown(raw, cooldown_days=4)

    fired = list(np.where(out.values)[0])
    assert fired == [0, 5], f"Expected fires on days [0, 5], got {fired}"


# ═══════════════════════════════════════════════════════════════════════════
# 11. Stop-loss exit
# ═══════════════════════════════════════════════════════════════════════════
def test_exit_stop_loss_triggers_at_correct_price():
    assert exit_stop_loss(entry_price=100.0, current_low=94.0, stop_pct=5.0) is True
    assert exit_stop_loss(entry_price=100.0, current_low=96.0, stop_pct=5.0) is False
    # Boundary: low exactly at stop price → True
    assert exit_stop_loss(entry_price=100.0, current_low=95.0, stop_pct=5.0) is True


# ═══════════════════════════════════════════════════════════════════════════
# 12. Trailing stop
# ═══════════════════════════════════════════════════════════════════════════
def test_exit_trailing_stop_tracks_peak_price():
    # peak=120, trail=5% → trigger 114; low=113 → True
    assert exit_trailing_stop(peak_price=120.0, current_low=113.0, trail_pct=5.0) is True
    # low=115 above trigger → False
    assert exit_trailing_stop(peak_price=120.0, current_low=115.0, trail_pct=5.0) is False


# ═══════════════════════════════════════════════════════════════════════════
# Bonus sanity: take-profit
# ═══════════════════════════════════════════════════════════════════════════
def test_exit_take_profit_basic():
    assert exit_take_profit(entry_price=100.0, current_high=110.0, target_pct=5.0) is True
    assert exit_take_profit(entry_price=100.0, current_high=104.0, target_pct=5.0) is False


# ═══════════════════════════════════════════════════════════════════════════
# End-to-end fixture: fake OHLCV provider
# ═══════════════════════════════════════════════════════════════════════════
def _build_long_synth_df(n: int = 1200, seed: int = 7) -> pd.DataFrame:
    """Long enough so the engine's 300-day default warmup leaves room to trade."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-01", periods=n, freq="B")
    # Mix of mean-reversion and trend so RSI / SMA / etc. all fire periodically
    base = 100 + 0.05 * np.arange(n)
    noise = rng.normal(0, 1, n).cumsum() * 0.6
    close = base + noise + 5 * np.sin(np.arange(n) / 25.0)
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    open_ = close + rng.normal(0, 0.3, n)
    vol = rng.integers(1000, 10000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


@pytest.fixture
def patched_fetch(monkeypatch):
    """Patch _fetch_ohlcv with a deterministic synthetic DataFrame."""
    df = _build_long_synth_df()

    def fake_fetch(symbol, start, end):
        # Slice to the requested window so the engine's warmup logic still
        # works the same way as with real data.
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        sliced = df.loc[(df.index >= start_ts) & (df.index <= end_ts)].copy()
        if sliced.empty:
            # Fall back to entire range (shouldn't happen but defensive)
            sliced = df.copy()
        return sliced

    monkeypatch.setattr(engine_mod, "_fetch_ohlcv", fake_fetch)
    return df


# ═══════════════════════════════════════════════════════════════════════════
# 13. End-to-end RSI strategy
# ═══════════════════════════════════════════════════════════════════════════
def test_full_backtest_rsi_strategy_valid_output(patched_fetch):
    strategy_def = {
        "symbol": "TEST",
        "starting_capital": 500_000,
        "start_date": "2019-01-01",
        "end_date": "2022-06-01",
        "entry": {
            "operator": "single",
            "conditions": [
                {"signal": "rsi_cross_below", "params": {"period": 14, "threshold": 30.0}},
            ],
        },
        "exit": {
            "operator": "first_of",
            "conditions": [
                {
                    "exit_type": "indicator_signal",
                    "params": {
                        "signal": "rsi_cross_above",
                        "params": {"period": 14, "threshold": 70.0},
                    },
                },
                {"exit_type": "end_of_period"},
            ],
        },
    }

    result = asyncio.run(run_backtest(strategy_def))

    assert "metrics" in result
    assert "trades" in result
    assert "equity_curve" in result
    assert isinstance(result["trades"], list)
    assert isinstance(result["equity_curve"], list)
    assert len(result["equity_curve"]) > 0
    assert "total_return_pct" in result["metrics"]


# ═══════════════════════════════════════════════════════════════════════════
# 14. End-to-end golden cross
# ═══════════════════════════════════════════════════════════════════════════
def test_full_backtest_golden_cross_strategy(monkeypatch):
    """Strong upward trend → SMA(50) crosses above SMA(200) at least once."""
    n = 1500
    rng = np.random.default_rng(3)
    idx = pd.date_range("2017-01-01", periods=n, freq="B")
    # Engineered so the 50/200 cross happens inside the test window
    seg1 = np.linspace(150, 100, 700)  # 700 days down-drift
    seg2 = np.linspace(100, 250, 800)  # 800 days strong rally
    base = np.concatenate([seg1, seg2])
    close = base + rng.normal(0, 0.6, n)
    high = close + rng.uniform(0.5, 2, n)
    low = close - rng.uniform(0.5, 2, n)
    open_ = close + rng.normal(0, 0.3, n)
    vol = rng.integers(1000, 10000, n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )

    def fake_fetch(symbol, start, end):
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        sub = df.loc[(df.index >= start_ts) & (df.index <= end_ts)].copy()
        return sub if not sub.empty else df.copy()

    monkeypatch.setattr(engine_mod, "_fetch_ohlcv", fake_fetch)

    strategy_def = {
        "symbol": "TEST",
        "starting_capital": 500_000,
        "start_date": "2018-06-01",
        "end_date": "2022-12-01",
        "entry": {
            "operator": "single",
            "conditions": [
                {
                    "signal": "golden_cross_sma",
                    "params": {"fast_period": 50, "slow_period": 200},
                }
            ],
        },
        "exit": {
            "operator": "first_of",
            "conditions": [{"exit_type": "end_of_period"}],
        },
    }

    result = asyncio.run(run_backtest(strategy_def))
    completed_trades = [t for t in result["trades"] if not t.get("skipped")]
    assert len(completed_trades) >= 1, (
        f"Expected at least 1 completed trade from golden cross, "
        f"got {len(completed_trades)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 15. End-to-end combined signal
# ═══════════════════════════════════════════════════════════════════════════
def test_full_backtest_combined_signal(patched_fetch):
    strategy_def = {
        "symbol": "TEST",
        "starting_capital": 500_000,
        "start_date": "2019-01-01",
        "end_date": "2022-06-01",
        "entry": {
            "operator": "and",
            "conditions": [
                {"signal": "rsi_cross_below", "params": {"period": 14, "threshold": 40.0}},
                {"signal": "price_above_sma", "params": {"period": 50}},
            ],
        },
        "exit": {
            "operator": "first_of",
            "conditions": [{"exit_type": "end_of_period"}],
        },
    }

    result = asyncio.run(run_backtest(strategy_def))

    assert "metrics" in result and isinstance(result["metrics"], dict)
    metrics = result["metrics"]
    for key in ("total_return_pct", "max_drawdown_pct", "total_trades"):
        assert key in metrics, f"missing metrics key: {key}"


# ═══════════════════════════════════════════════════════════════════════════
# 16. All metrics fields present
# ═══════════════════════════════════════════════════════════════════════════
REQUIRED_METRIC_FIELDS = [
    "total_return_pct",
    "cagr_pct",
    "benchmark_return_pct",
    "alpha_pct",
    "max_drawdown_pct",
    "max_drawdown_duration_days",
    "annualised_volatility_pct",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "value_at_risk_95",
    "total_trades",
    "winning_trades",
    "win_rate_pct",
    "avg_winning_return_pct",
    "avg_losing_return_pct",
    "profit_factor",
    "avg_holding_days",
    "largest_win_pct",
    "largest_loss_pct",
    "skipped_trades",
    "total_brokerage_paid",
    "total_stt_paid",
    "drawdown_series",
    "benchmark_max_drawdown_pct",
]


def test_metrics_all_fields_present(patched_fetch):
    strategy_def = {
        "symbol": "TEST",
        "starting_capital": 500_000,
        "start_date": "2019-01-01",
        "end_date": "2022-06-01",
        "entry": {
            "operator": "single",
            "conditions": [
                {"signal": "rsi_cross_below", "params": {"period": 14, "threshold": 30.0}},
            ],
        },
        "exit": {
            "operator": "first_of",
            "conditions": [{"exit_type": "end_of_period"}],
        },
    }

    result = asyncio.run(run_backtest(strategy_def))
    metrics = result["metrics"]
    missing = [f for f in REQUIRED_METRIC_FIELDS if f not in metrics]
    assert not missing, f"Missing metric fields: {missing}"


# ═══════════════════════════════════════════════════════════════════════════
# 17. Critical metrics have no NaN/None
# ═══════════════════════════════════════════════════════════════════════════
def test_metrics_no_nan_values(patched_fetch):
    strategy_def = {
        "symbol": "TEST",
        "starting_capital": 500_000,
        "start_date": "2019-01-01",
        "end_date": "2022-06-01",
        "entry": {
            "operator": "single",
            "conditions": [
                {"signal": "rsi_cross_below", "params": {"period": 14, "threshold": 30.0}},
            ],
        },
        "exit": {
            "operator": "first_of",
            "conditions": [{"exit_type": "end_of_period"}],
        },
    }
    result = asyncio.run(run_backtest(strategy_def))
    metrics = result["metrics"]

    critical_numeric = [
        "total_return_pct",
        "cagr_pct",
        "max_drawdown_pct",
        "annualised_volatility_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "calmar_ratio",
        "value_at_risk_95",
        "win_rate_pct",
        "profit_factor",
        "avg_holding_days",
        "total_brokerage_paid",
        "total_stt_paid",
        "benchmark_return_pct",
        "alpha_pct",
        "benchmark_max_drawdown_pct",
    ]

    for k in critical_numeric:
        v = metrics[k]
        assert v is not None, f"{k} is None"
        assert isinstance(v, (int, float)), f"{k} is not numeric: {type(v)}"
        assert not (isinstance(v, float) and math.isnan(v)), f"{k} is NaN"


# ═══════════════════════════════════════════════════════════════════════════
# 18. Indian costs deducted correctly
# ═══════════════════════════════════════════════════════════════════════════
def test_costs_deducted_correctly():
    # Converged India delivery model (services/trading_costs.py): CNC delivery
    # brokerage is ₹0 (Zerodha), STT on BOTH legs, GST 18% on
    # (brokerage+exchange+sebi).
    # buy_cost(100, 100) — notional 10,000:
    #   brokerage 0, slippage 5.0, STT 10.0, exchange 0.297, sebi 0.01,
    #   gst (0+0.297+0.01)*0.18=0.055, stamp 1.5  → total ≈ 16.86
    net_debit, total_costs = buy_cost(100.0, 100)
    assert abs(total_costs - 16.86) < 0.05, (
        f"buy_cost total expected ~16.86, got {total_costs}"
    )
    assert abs(net_debit - 10016.86) < 0.05, (
        f"buy_cost net_debit expected ~10016.86, got {net_debit}"
    )

    # sell_cost(100, 100): same minus stamp (buy-side only) → total ≈ 15.36
    net_credit, total_sell_costs = sell_cost(100.0, 100)
    assert abs(total_sell_costs - 15.36) < 0.05, (
        f"sell_cost total expected ~15.36, got {total_sell_costs}"
    )
    assert abs(net_credit - 9984.64) < 0.05, (
        f"sell_cost net_credit expected ~9984.64, got {net_credit}"
    )
    # Structural invariants: buy carries stamp (sell doesn't) so buy > sell;
    # both now include STT.
    assert total_costs > total_sell_costs


# ═══════════════════════════════════════════════════════════════════════════
# Sanity: registry exposes the expected signal count
# ═══════════════════════════════════════════════════════════════════════════
def test_signal_registry_size():
    assert len(SIGNAL_REGISTRY) >= 100
