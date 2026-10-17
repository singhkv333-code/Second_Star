"""Comprehensive tests for the backtester subsystem.

These tests deliberately avoid the live yfinance network. The engine test
mocks _fetch_ohlcv so the simulation runs against synthetic OHLCV.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from backend.backtester import signals as sig
from backend.backtester import engine as engine_mod
from backend.backtester.metrics import calculate_metrics
from backend.backtester.parser import parse_strategy
from backend.backtester.portfolio import (
    BROKERAGE_PER_ORDER,
    PortfolioSimulator,
    PortfolioSnapshot,
    SLIPPAGE_PCT,
    Trade,
)


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _ohlcv_from_close(closes: list[float], start="2023-01-02") -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=len(closes))
    closes_arr = np.array(closes, dtype=float)
    return pd.DataFrame({
        "open": closes_arr,
        "high": closes_arr * 1.005,
        "low": closes_arr * 0.995,
        "close": closes_arr,
        "volume": [1_000_000] * len(closes),
    }, index=idx)


# ---------------------------------------------------------------------------
# Signal generators
# ---------------------------------------------------------------------------

class TestSignals:
    def test_signal_52wk_high_no_lookahead(self):
        # 252 days flat at 100, then day 252 jumps to 110
        closes = [100.0] * 252 + [110.0]
        df = _ohlcv_from_close(closes)
        s = sig.signal_52wk_high(df)
        # On day 252 (index 252), close=110 > prev 252-day max (100) → True
        assert bool(s.iloc[252]) is True
        # On day 251 (index 251), close=100 == prev max=100, not >
        assert bool(s.iloc[251]) is False

    def test_rsi_crossover_detects_transition(self):
        # Up-trend then a sharp down-trend — RSI rises above 30 first
        # (so prev_rsi >= 30) then crosses below.
        up = list(np.linspace(100, 200, 50))
        down = list(np.linspace(200, 80, 30))
        df = _ohlcv_from_close(up + down)
        s = sig.signal_rsi_cross_below(df, period=14, threshold=30.0)
        assert bool(s.any())
        first_true = int(np.argmax(s.values))
        assert s.iloc[first_true] == True
        # The bar before the crossover must be False (it's a one-day event)
        if first_true > 0:
            assert s.iloc[first_true - 1] == False

    def test_calendar_signal_monday_only(self):
        df = _ohlcv_from_close([100.0] * 30, start="2024-01-01")  # Mon
        s = sig.signal_calendar(df, weekday=0)
        for ts, val in zip(df.index, s.values):
            if ts.weekday() == 0:
                assert bool(val) is True
            else:
                assert bool(val) is False

    def test_calendar_price_combined(self):
        # 60 days. Price below 50 for first 25, above for the rest.
        closes = [100.0] * 25 + [200.0] * 60
        df = _ohlcv_from_close(closes, start="2024-01-01")
        cal = sig.signal_calendar(df, weekday=0)
        price_above = sig.signal_price_above_sma(df, period=10)
        combined = sig.combine_signals_and(cal, price_above)
        # Mondays in the elevated price region should fire; Mondays in the
        # depressed region should not (because price < SMA there).
        assert bool(combined.any())
        for ts, c, val in zip(df.index, combined.values, df["close"].values):
            if not ts.weekday() == 0:
                assert bool(c) is False

    def test_macd_crossover_correct(self):
        # Construct a price series that produces at least one MACD crossover
        closes = list(np.linspace(100, 80, 40)) + list(np.linspace(80, 130, 40))
        df = _ohlcv_from_close(closes)
        s = sig.signal_macd_cross_above_signal(df)
        assert bool(s.any())


# ---------------------------------------------------------------------------
# Portfolio simulator
# ---------------------------------------------------------------------------

class TestPortfolioSimulator:
    def test_entry_executes_at_next_day_open(self):
        sim = PortfolioSimulator(starting_capital=100_000,
                                  symbol="TEST",
                                  position_size_inr=10_000)
        # Day 1: signal fires
        sim.process_day(date(2024, 1, 1), open_price=98.0, high_price=99.0,
                         low_price=97.0, close_price=98.5,
                         entry_signal_today=True, exit_signal_today=False)
        # Day 2: should fill at this day's open = 100
        sim.process_day(date(2024, 1, 2), open_price=100.0, high_price=101.0,
                         low_price=99.0, close_price=100.5,
                         entry_signal_today=False, exit_signal_today=False)
        trades = sim.get_trades()
        non_skipped = [t for t in trades if not t.skipped]
        assert len(non_skipped) == 1
        t = non_skipped[0]
        assert t.entry_price == pytest.approx(100.0)
        assert t.entry_date == date(2024, 1, 2)

    def test_brokerage_deducted(self):
        sim = PortfolioSimulator(starting_capital=15_000,
                                  symbol="TEST",
                                  position_size_inr=10_000)
        sim.process_day(date(2024, 1, 1), open_price=100, high_price=100,
                         low_price=100, close_price=100,
                         entry_signal_today=True, exit_signal_today=False)
        sim.process_day(date(2024, 1, 2), open_price=100, high_price=100,
                         low_price=100, close_price=100,
                         entry_signal_today=False, exit_signal_today=False)
        # qty = floor(10000 / 100) = 100. cost = 100*100 + 20 + 100*100*0.0005 = 10025
        # cash after = 15000 - 10025 = 4975
        assert sim.cash == pytest.approx(15000 - 10000 - 20 - 5, rel=1e-6)

    def test_stop_loss_triggers_intraday(self):
        sim = PortfolioSimulator(starting_capital=200_000,
                                  symbol="TEST",
                                  position_size_inr=100_000,
                                  stop_loss_pct=5.0)
        # Entry signal day
        sim.process_day(date(2024, 1, 1), open_price=1000, high_price=1010,
                         low_price=990, close_price=1000,
                         entry_signal_today=True, exit_signal_today=False)
        # Fill day at 1000
        sim.process_day(date(2024, 1, 2), open_price=1000, high_price=1005,
                         low_price=995, close_price=1000,
                         entry_signal_today=False, exit_signal_today=False)
        # Stop hit intraday at 950, low=940
        sim.process_day(date(2024, 1, 3), open_price=970, high_price=975,
                         low_price=940, close_price=945,
                         entry_signal_today=False, exit_signal_today=False)
        trades = [t for t in sim.get_trades() if not t.skipped]
        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_loss"
        assert trades[0].exit_price == pytest.approx(950.0)

    def test_insufficient_cash_skips_trade(self):
        # Cash too low even for one share at the open price + brokerage
        sim = PortfolioSimulator(starting_capital=50.0,
                                  symbol="TEST",
                                  position_size_inr=60_000)
        sim.process_day(date(2024, 1, 1), open_price=1000, high_price=1001,
                         low_price=999, close_price=1000,
                         entry_signal_today=True, exit_signal_today=False)
        sim.process_day(date(2024, 1, 2), open_price=1000, high_price=1001,
                         low_price=999, close_price=1000,
                         entry_signal_today=False, exit_signal_today=False)
        skipped = [t for t in sim.get_trades() if t.skipped]
        assert len(skipped) >= 1
        assert any(t.skip_reason == "insufficient_cash" for t in skipped)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _curve(values: list[float], start="2024-01-01") -> list[PortfolioSnapshot]:
    idx = pd.bdate_range(start=start, periods=len(values))
    return [
        PortfolioSnapshot(date=ts.date(), cash=v, holdings_value=0.0,
                           total_value=v, open_positions=0)
        for ts, v in zip(idx, values)
    ]


class TestMetrics:
    def test_total_return_calculation(self):
        curve = _curve([500_000, 520_000, 510_000, 580_000])
        metrics = calculate_metrics(curve, [], curve, starting_capital=500_000)
        assert metrics["total_return_pct"] == pytest.approx(16.0, abs=0.01)

    def test_max_drawdown_calculation(self):
        curve = _curve([100, 120, 90, 110, 130])
        metrics = calculate_metrics(curve, [], curve, starting_capital=100)
        assert metrics["max_drawdown_pct"] == pytest.approx(-25.0, abs=0.01)

    def test_sharpe_ratio_positive_for_good_strategy(self):
        # Steady growth, low volatility — Sharpe should be high
        values = [500_000 * (1.0008 ** i) for i in range(252)]
        curve = _curve(values)
        metrics = calculate_metrics(curve, [], curve, starting_capital=500_000)
        assert metrics["sharpe_ratio"] > 1.0

    def test_profit_factor_calculation(self):
        # Build trades manually
        def t(gross_pnl, net_pnl):
            tr = Trade(trade_id=0, symbol="X", entry_date=date(2024, 1, 1),
                        entry_price=100, quantity=10, position_size_inr=1000,
                        exit_date=date(2024, 1, 5), exit_price=110,
                        exit_reason="exit_signal", gross_pnl=gross_pnl,
                        net_pnl=net_pnl, return_pct=net_pnl / 1000 * 100,
                        holding_days=4)
            return tr
        trades = [t(1000, 950), t(2000, 1900), t(500, 450),
                  t(-800, -850), t(-400, -450)]
        curve = _curve([100_000, 100_000])
        metrics = calculate_metrics(curve, trades, curve, starting_capital=100_000)
        assert metrics["profit_factor"] == pytest.approx(3500 / 1200, abs=0.01)
        assert metrics["total_trades"] == 5
        assert metrics["winning_trades"] == 3
        assert metrics["losing_trades"] == 2


# ---------------------------------------------------------------------------
# Engine — yfinance mocked
# ---------------------------------------------------------------------------

class TestEngine:
    def _synthetic_ohlcv(self, n_days: int = 600) -> pd.DataFrame:
        # Random walk that ends higher than it starts (so benchmark is positive too)
        np.random.seed(42)
        rets = np.random.normal(loc=0.0006, scale=0.012, size=n_days)
        prices = 100 * np.cumprod(1 + rets)
        idx = pd.bdate_range(end=date.today(), periods=n_days)
        return pd.DataFrame({
            "open": prices,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": [1_000_000] * n_days,
        }, index=idx)

    def test_full_backtest_rsi_strategy(self):
        df = self._synthetic_ohlcv()
        with patch.object(engine_mod, "_fetch_ohlcv", return_value=df):
            import asyncio
            result = asyncio.run(engine_mod.run_backtest({
                "symbol": "NIFTYBEES",
                "entry_signal": "rsi_cross_below",
                "entry_params": {"period": 14, "threshold": 30.0},
                "exit_signal": "rsi_cross_above",
                "exit_params": {"period": 14, "threshold": 70.0},
                "position_size_inr": 50_000,
                "starting_capital": 500_000,
                "period": "1y",
                "max_positions": 5,
            }))

        assert "equity_curve" in result and len(result["equity_curve"]) > 0
        assert "benchmark_curve" in result
        assert len(result["equity_curve"]) == len(result["benchmark_curve"])
        m = result["metrics"]
        for key in ("total_return_pct", "max_drawdown_pct", "win_rate_pct",
                    "sharpe_ratio", "cagr_pct"):
            assert key in m
            v = m[key]
            assert v is None or (isinstance(v, (int, float))
                                  and math.isfinite(float(v))), f"{key}={v}"

    def test_backtest_fewer_than_5_trades_adds_warning(self):
        # Build a price series that will rarely (if ever) trigger RSI < 30 by
        # using a long uptrend
        n = 300
        prices = np.linspace(100, 200, n)
        idx = pd.bdate_range(end=date.today(), periods=n)
        df = pd.DataFrame({
            "open": prices, "high": prices * 1.001, "low": prices * 0.999,
            "close": prices, "volume": [1_000_000] * n,
        }, index=idx)
        with patch.object(engine_mod, "_fetch_ohlcv", return_value=df):
            import asyncio
            result = asyncio.run(engine_mod.run_backtest({
                "symbol": "NIFTYBEES",
                "entry_signal": "rsi_cross_below",
                "entry_params": {"period": 14, "threshold": 30.0},
                "exit_signal": "rsi_cross_above",
                "exit_params": {"period": 14, "threshold": 70.0},
                "position_size_inr": 50_000,
                "starting_capital": 500_000,
                "period": "3mo",
            }))
        assert any("fewer than 5 trades" in w.lower() or "inactive" in w.lower()
                   for w in result["warnings"])

    def test_benchmark_buy_and_hold(self):
        df = self._synthetic_ohlcv(400)
        with patch.object(engine_mod, "_fetch_ohlcv", return_value=df):
            import asyncio
            result = asyncio.run(engine_mod.run_backtest({
                "symbol": "NIFTYBEES",
                "entry_signal": "price_52wk_high",
                "entry_params": {},
                "exit_signal": "hold",
                "exit_params": {},
                "position_size_inr": 50_000,
                "starting_capital": 500_000,
                "period": "1y",
            }))
        bench = result["benchmark_curve"]
        assert len(bench) > 0
        assert bench[0]["value"] == pytest.approx(500_000, rel=0.01)
        assert bench[-1]["value"] > 0


# ---------------------------------------------------------------------------
# Parser (falls back to rule-based when the LLM is unavailable in tests)
# ---------------------------------------------------------------------------

class TestParser:
    def test_parser_rsi_strategy(self):
        import asyncio
        result = asyncio.run(parse_strategy(
            "backtest buying INFY every time RSI drops below 30 for 2 years with 50000 per trade"
        ))
        assert result["status"] == "ready"
        s = result["strategy"]
        assert s["symbol"] == "INFY"
        cond = s["entry"]["conditions"][0]
        assert cond["signal"] == "rsi_cross_below"
        assert cond["params"]["threshold"] == 30.0
        assert s["position_size_inr"] == 50_000
        assert s["period"] == "2y"

    def test_parser_calendar_strategy(self):
        import asyncio
        result = asyncio.run(parse_strategy(
            "what if I bought RELIANCE every Monday with 25000"
        ))
        assert result["status"] == "ready"
        s = result["strategy"]
        signals = [c["signal"] for c in s["entry"]["conditions"]]
        assert "monday" in signals or any(
            c["signal"] == "weekday" and c["params"].get("weekday") == 0
            for c in s["entry"]["conditions"]
        )
        assert s["position_size_inr"] == 25_000

    def test_parser_not_backtest_returns_none(self):
        import asyncio
        result = asyncio.run(parse_strategy("buy 10 INFY at market"))
        assert result is None

    def test_parser_missing_position_size(self):
        import asyncio
        result = asyncio.run(parse_strategy(
            "backtest RSI strategy on TCS for 1 year"
        ))
        assert result["status"] == "needs_clarification"
        assert "position_size_inr" in result["missing"]
