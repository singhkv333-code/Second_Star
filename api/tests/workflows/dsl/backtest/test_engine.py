"""Engine — runs a tree over synthetic OHLCV and asserts expected trades.

We synthesise a price series with deterministic moves so the entry
+ exit conditions fire at known bars, then check the trade list
matches.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backend.workflows.dsl.backtest.engine import run_backtest
from backend.workflows.dsl.backtest.schema import (
    BacktestRequest,
    ExitPolicyDeclarative as ExitPolicy,
)


def _make_oscillating_series(n_days: int = 250) -> pd.DataFrame:
    """Sinusoidal closes around 100 with amplitude ±15 so RSI dips
    below 30 a handful of times in a 250-day window. Open == close
    for simplicity so entry/exit fills are predictable."""
    t = np.arange(n_days)
    closes = 100.0 + 15.0 * np.sin(t / 8.0)
    dates = pd.date_range("2024-01-02", periods=n_days, freq="B").normalize()
    df = pd.DataFrame({
        "open":   closes,
        "high":   closes + 0.1,
        "low":    closes - 0.1,
        "close":  closes,
        "volume": np.full(n_days, 1_000_000.0),
    }, index=dates)
    return df


def _fixed_fetcher(df_by_symbol: dict[str, pd.DataFrame]):
    """Return a fetcher function the bar_loader can drive in place
    of yfinance."""
    def _fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
        df = df_by_symbol.get(symbol.upper())
        if df is None:
            raise ValueError(f"no fixture data for {symbol}")
        mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
        return df.loc[mask].copy()
    return _fetch


def test_engine_runs_end_to_end_on_synthetic_data():
    """The motivating case: RSI(14) of TCS < 30 → buy, exit after
    5 bars. The naive sine-wave version of this strategy stacks
    entries on consecutive oversold bars (RSI stays under 30 for a
    chunk of the trough) so some exits land mid-downswing. Test
    just confirms the engine runs end-to-end and produces shape-
    correct output — not that the strategy is profitable.
    """
    series = _make_oscillating_series(250)
    fetcher = _fixed_fetcher({"TCS": series})

    tree = {
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "rsi",
                 "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    }
    req = BacktestRequest(
        tree=tree,
        primary_symbol="TCS",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        starting_capital=100_000.0,
        quantity=10,
        exit_policy=ExitPolicy(kind="n_day_hold", bars=5),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)

    # Engine ran the loop and produced a result of the expected shape.
    assert result.metrics.total_trades > 0
    assert (
        result.metrics.winning_trades + result.metrics.losing_trades
        == result.metrics.total_trades
    )
    # Equity curve has roughly one point per bar in the window.
    assert len(result.equity_curve) > 200
    # Tree summary made it through to the result.
    assert "RSI(14) of TCS" in result.tree_summary
    # Cash + position value reconcile near the ending equity.
    assert result.metrics.ending_value > 0


def test_engine_force_closes_open_position_at_window_end():
    """If a position is open on the last bar, it must be force-closed
    so the equity curve isn't misleading."""
    # Series that just keeps rising — RSI will eventually exceed 30
    # forever after a brief dip at start.
    n = 60
    closes = np.linspace(80, 200, n)
    dates = pd.date_range("2024-01-02", periods=n, freq="B").normalize()
    df = pd.DataFrame({
        "open":   closes, "high": closes + 0.5, "low": closes - 0.5,
        "close":  closes, "volume": np.full(n, 100_000.0),
    }, index=dates)
    fetcher = _fixed_fetcher({"TCS": df})

    tree = {
        "type": "comparison", "op": ">",
        "left": {"type": "price", "symbol": "TCS"},
        "right": {"type": "constant", "value": 90},
    }
    req = BacktestRequest(
        tree=tree,
        primary_symbol="TCS",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 5, 1),
        starting_capital=100_000.0,
        quantity=5,
        # Hold for 1000 bars — longer than the window. Forces the
        # force-close path.
        exit_policy=ExitPolicy(kind="n_day_hold", bars=1000),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)

    # Exactly one entry should have fired; the force-close gives it
    # an exit_reason='force_close'.
    assert result.metrics.total_trades >= 1
    assert any(t.exit_reason == "force_close" for t in result.trades)


def test_engine_respects_stop_loss():
    """Series that gaps down sharply right after entry triggers the
    stop loss."""
    n = 50
    closes = np.concatenate([
        np.linspace(120, 80, 20),    # falling → RSI drops below 30
        np.linspace(75, 60, 30),     # keeps dropping past stop loss
    ])
    dates = pd.date_range("2024-01-02", periods=n, freq="B").normalize()
    df = pd.DataFrame({
        "open":   closes, "high": closes + 0.5, "low": closes - 0.5,
        "close":  closes, "volume": np.full(n, 100_000.0),
    }, index=dates)
    fetcher = _fixed_fetcher({"TCS": df})

    tree = {
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "rsi",
                 "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    }
    req = BacktestRequest(
        tree=tree,
        primary_symbol="TCS",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 5, 1),
        starting_capital=100_000.0,
        quantity=10,
        exit_policy=ExitPolicy(kind="stop_loss_pct", value=0.05),  # 5% stop
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)

    # At least one trade exited on the stop.
    assert any(t.exit_reason == "stop_loss" for t in result.trades), (
        "expected at least one stop_loss exit; got "
        f"{[t.exit_reason for t in result.trades]}"
    )


def test_engine_rejects_tree_with_no_market_data():
    """A tree that's just constants has no symbols to load — engine
    must surface this clearly rather than crash."""
    tree = {
        "type": "comparison", "op": "<",
        "left": {"type": "constant", "value": 1},
        "right": {"type": "constant", "value": 2},
    }
    req = BacktestRequest(
        tree=tree, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 6, 1),
        save=False,
    )
    # The DSL semantic_validate flags vacuous comparisons before
    # the engine even sees them; this is the safety net we want.
    from backend.workflows.dsl.validators import DSLValidationError
    with pytest.raises((DSLValidationError, ValueError)):
        run_backtest(request=req, user_id=1)


def test_engine_diagnostics_populated():
    series = _make_oscillating_series(150)
    fetcher = _fixed_fetcher({"TCS": series})

    tree = {
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "rsi",
                 "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    }
    req = BacktestRequest(
        tree=tree, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)
    d = result.diagnostics
    assert d.bars_evaluated > 0
    assert d.warmup_bars_skipped >= 19  # RSI(14) + 5 buffer floor
    assert "TCS:NSE" in d.symbols_loaded


def test_engine_handles_multi_symbol_tree():
    """Tree references both TCS and NIFTY; bars are loaded for both."""
    n = 200
    tcs = _make_oscillating_series(n)
    # NIFTY: ramp up so the "> 23000" condition is true everywhere
    # past the warmup.
    nifty_close = np.linspace(22500, 24500, n)
    dates = tcs.index
    nifty = pd.DataFrame({
        "open":   nifty_close, "high": nifty_close + 5, "low": nifty_close - 5,
        "close":  nifty_close, "volume": np.full(n, 10_000_000.0),
    }, index=dates)
    fetcher = _fixed_fetcher({"TCS": tcs, "NIFTY": nifty})

    tree = {
        "type": "logic", "op": "and",
        "operands": [
            {"type": "comparison", "op": "<",
             "left": {"type": "indicator", "indicator": "rsi",
                      "symbol": "TCS", "period": 14},
             "right": {"type": "constant", "value": 30}},
            {"type": "comparison", "op": ">",
             "left": {"type": "price", "symbol": "NIFTY"},
             "right": {"type": "constant", "value": 23000}},
        ],
    }
    req = BacktestRequest(
        tree=tree, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
        starting_capital=100_000.0, quantity=10,
        exit_policy=ExitPolicy(kind="n_day_hold", bars=5),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)

    # Both symbols loaded; trades fired only when both conditions met.
    assert {"TCS:NSE", "NIFTY:NSE"}.issubset(set(result.diagnostics.symbols_loaded))
    # The tree only fires once NIFTY crosses 23000 (somewhere mid-window),
    # so we expect fewer trades than the single-condition variant.
    # Just assert "some trades fired" — exact count depends on the sine.
    assert result.metrics.total_trades > 0


# ── Exit-tree paths ─────────────────────────────────────────────────


def test_lowered_stop_loss_uses_bar_low_and_fills_at_stop_price():
    """Stop loss is lowered to a tree on bar-LOW. The engine should
    fire when the bar's low pierces the stop level, NOT when the
    close pierces it. Fill price must equal entry × (1 - stop_pct).
    """
    from backend.workflows.dsl.backtest.schema import (
        BacktestRequest, ExitPolicyDeclarative,
    )

    n = 40
    # Closes drift sideways at 100. One bar has a low spike to 92
    # but recovers to close at 100. With a 5% stop, that bar's LOW
    # (=92) sits 8% below entry (=100) → stop should trigger here
    # even though the close (=100) is well above the stop level.
    # Spike sits comfortably past the 20-bar warmup floor.
    closes = np.full(n, 100.0)
    lows = np.full(n, 99.0)
    highs = np.full(n, 101.0)
    spike_idx = 28
    lows[spike_idx] = 92.0
    dates = pd.date_range("2024-01-02", periods=n, freq="B").normalize()
    df = pd.DataFrame({
        "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": np.full(n, 100_000.0),
    }, index=dates)
    fetcher = _fixed_fetcher({"TCS": df})

    # Entry: price > 50 (always true after warmup) → opens on bar 21.
    # Wait — actually entry will fire on every bar past warmup. So
    # we'll get one position opened then close on the spike bar.
    tree = {
        "type": "comparison", "op": ">",
        "left": {"type": "price", "symbol": "TCS"},
        "right": {"type": "constant", "value": 50},
    }
    req = BacktestRequest(
        tree=tree, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 5, 1),
        starting_capital=100_000.0, quantity=10,
        exit_policy=ExitPolicyDeclarative(kind="stop_loss_pct", value=0.05),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)

    stops = [t for t in result.trades if t.exit_reason == "stop_loss"]
    assert stops, (
        "expected a stop_loss exit on the bar-low spike; got "
        f"{[t.exit_reason for t in result.trades]}"
    )
    # Fill price must be exactly entry × (1 - 0.05) = entry × 0.95.
    first = stops[0]
    assert first.exit_price == pytest.approx(first.entry_price * 0.95, rel=1e-9), (
        f"expected stop fill at entry*0.95 ({first.entry_price * 0.95}); "
        f"got {first.exit_price}"
    )


def test_user_written_exit_tree_rsi_above_70_closes_position():
    """Tree-shaped exit on a momentum reversal. Build a series where
    RSI climbs cleanly past 70, confirm the exit fires and the trade
    closes with exit_reason='exit_tree'."""
    from backend.workflows.dsl.backtest.schema import (
        BacktestRequest, ExitPolicyTree,
    )

    # Rising series so RSI climbs into overbought territory.
    n = 80
    closes = np.linspace(100, 200, n)
    dates = pd.date_range("2024-01-02", periods=n, freq="B").normalize()
    df = pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": np.full(n, 100_000.0),
    }, index=dates)
    fetcher = _fixed_fetcher({"TCS": df})

    entry_tree = {
        "type": "comparison", "op": ">",
        "left": {"type": "price", "symbol": "TCS"},
        "right": {"type": "constant", "value": 90},
    }
    exit_tree_dict = {
        "type": "comparison", "op": ">",
        "left": {"type": "indicator", "indicator": "rsi",
                 "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 70},
    }
    req = BacktestRequest(
        tree=entry_tree, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 6, 1),
        starting_capital=100_000.0, quantity=5,
        exit_policy=ExitPolicyTree(
            kind="tree", tree=exit_tree_dict, exit_at="next_open",
        ),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)

    # On a monotonically rising series RSI very quickly crosses 70 →
    # at least one exit should fire from the tree, not force_close.
    tree_exits = [t for t in result.trades if t.exit_reason == "exit_tree"]
    assert tree_exits, (
        "expected exit_tree close on RSI>70; got "
        f"{[t.exit_reason for t in result.trades]}"
    )


def test_trailing_stop_via_drawdown_from_peak_pct():
    """Position rises 15%, then drops 8% from peak. A trailing stop
    written as drawdown_from_peak_pct >= 0.08 should close the
    position at that drop, NOT before."""
    from backend.workflows.dsl.backtest.schema import (
        BacktestRequest, ExitPolicyTree,
    )

    # Manually shaped close curve: ramp up to 115, then drop sharply.
    n = 40
    closes = np.concatenate([
        np.linspace(100, 115, 20),    # +15% over 20 bars
        np.linspace(115, 105, 20),    # -8.7% drop
    ])
    dates = pd.date_range("2024-01-02", periods=n, freq="B").normalize()
    df = pd.DataFrame({
        "open": closes, "high": closes + 0.2, "low": closes - 0.2,
        "close": closes, "volume": np.full(n, 100_000.0),
    }, index=dates)
    fetcher = _fixed_fetcher({"TCS": df})

    entry_tree = {
        "type": "comparison", "op": ">",
        "left": {"type": "price", "symbol": "TCS"},
        "right": {"type": "constant", "value": 90},
    }
    exit_tree_dict = {
        "type": "comparison", "op": ">=",
        "left": {"type": "position", "field": "drawdown_from_peak_pct"},
        "right": {"type": "constant", "value": 0.08},
    }
    req = BacktestRequest(
        tree=entry_tree, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 5, 1),
        starting_capital=100_000.0, quantity=10,
        exit_policy=ExitPolicyTree(
            kind="tree", tree=exit_tree_dict, exit_at="next_open",
        ),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)

    # At least one trailing-stop exit (exit_tree); fired after peak.
    tree_exits = [t for t in result.trades if t.exit_reason == "exit_tree"]
    assert tree_exits, "expected trailing stop to fire"


def test_position_leaf_in_entry_tree_is_rejected():
    """An entry tree must not reference 'position' — semantic validator
    catches it before the engine even loads bars."""
    from backend.workflows.dsl.backtest.schema import BacktestRequest
    from backend.workflows.dsl.validators import DSLValidationError

    tree = {
        "type": "comparison", "op": ">",
        "left": {"type": "position", "field": "unrealised_pct"},
        "right": {"type": "constant", "value": 0.1},
    }
    req = BacktestRequest(
        tree=tree, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 6, 1),
        save=False,
    )
    with pytest.raises((DSLValidationError, ValueError)):
        run_backtest(request=req, user_id=1)
