"""Compatibility shim — signals.py re-exports primitives.py with old names.

The wide-primitive engine lives in primitives.py + composer.py. This shim
keeps the legacy ``signal_*`` / ``combine_signals_*`` names working for
tests and call-sites that haven't migrated.

If you're writing new code, import directly from
``backend.backtester.primitives`` (signals) or
``backend.backtester.exits`` (exits).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

# --- Re-exports: legacy signal_* names → new sig_* primitives -----------
from backend.backtester.primitives import (
    sig_rsi_cross_below as signal_rsi_cross_below,
    sig_rsi_cross_above as signal_rsi_cross_above,
    sig_macd_cross_above_signal as signal_macd_cross_above_signal,
    sig_macd_cross_below_signal as signal_macd_cross_below_signal,
    sig_price_cross_above_sma as signal_price_cross_above_sma,
    sig_price_cross_below_sma as signal_price_cross_below_sma,
    sig_price_above_sma as signal_price_above_sma,
    sig_price_below_sma as signal_price_below_sma,
    sig_bb_lower_touch as signal_bb_lower_touch,
    sig_volume_spike as signal_volume_spike,
    sig_52wk_high_breakout as signal_52wk_high,
    sig_52wk_low_breakdown as signal_52wk_low,
    sig_weekday,
    sig_day_of_month,
    sig_first_trading_day_of_month as signal_first_trading_day_of_month,
    sig_last_trading_day_of_month as signal_last_trading_day_of_month,
    combine_and as combine_signals_and,
    combine_or as combine_signals_or,
    SIGNAL_REGISTRY as PRIMITIVE_REGISTRY,
)

# --- Re-exports: legacy exit names → new exit_* helpers -----------------
from backend.backtester.exits import (
    exit_stop_loss as signal_exit_stop_loss,
    exit_take_profit as signal_exit_take_profit,
    exit_trailing_stop as _exit_trailing_stop,
)


# --- Small wrappers for legacy signatures the new primitives don't match -

def signal_calendar(
    df: pd.DataFrame,
    weekday: Optional[int] = None,
    day_of_month: Optional[int] = None,
) -> pd.Series:
    """Legacy calendar dispatch.

    weekday is 0..4 (Mon..Fri). day_of_month picks the first trading day on
    or after that day-of-month each month. If both are None, returns all
    False. If both are given, takes the AND.
    """
    idx = df.index
    if weekday is None and day_of_month is None:
        return pd.Series([False] * len(idx), index=idx, name="signal_calendar")

    parts: list[pd.Series] = []
    if weekday is not None:
        parts.append(sig_weekday(df, int(weekday)))
    if day_of_month is not None:
        parts.append(sig_day_of_month(df, int(day_of_month)))

    if len(parts) == 1:
        out = parts[0]
    else:
        out = combine_signals_and(*parts)
    out = out.astype(bool)
    out.name = "signal_calendar"
    return out


def signal_price_cross_above(df: pd.DataFrame, level: float) -> pd.Series:
    close = df["close"].astype(float)
    prev = close.shift(1)
    cross = (prev < float(level)) & (close >= float(level))
    out = cross.fillna(False).astype(bool)
    out.name = "signal_price_cross_above"
    return out


def signal_price_cross_below(df: pd.DataFrame, level: float) -> pd.Series:
    close = df["close"].astype(float)
    prev = close.shift(1)
    cross = (prev > float(level)) & (close <= float(level))
    out = cross.fillna(False).astype(bool)
    out.name = "signal_price_cross_below"
    return out


def signal_price_above_pct(df: pd.DataFrame, pct: float) -> pd.Series:
    """True on days the close moved up by more than pct% vs. the previous close."""
    close = df["close"].astype(float)
    prev = close.shift(1)
    move = (close - prev) / prev * 100.0
    out = (move > float(pct)).fillna(False).astype(bool)
    out.name = "signal_price_above_pct"
    return out


def signal_price_below_pct(df: pd.DataFrame, pct: float) -> pd.Series:
    """True on days the close moved down by more than abs(pct)% vs. the previous close."""
    close = df["close"].astype(float)
    prev = close.shift(1)
    move = (close - prev) / prev * 100.0
    out = (move < -abs(float(pct))).fillna(False).astype(bool)
    out.name = "signal_price_below_pct"
    return out


def signal_exit_after_n_days(df: pd.DataFrame, entry_date: date, n_days: int) -> bool:
    """True if today's df has at least n_days remaining after entry_date.

    Mirrors the legacy semantics used by older tests: returns False when
    we're not yet n trading days past entry. The new exit primitive in
    backend.backtester.exits is per-row-aware; this wrapper is the
    convenience signature.
    """
    idx = df.index
    try:
        entry_pos = idx.searchsorted(pd.Timestamp(entry_date))
    except Exception:
        return False
    if entry_pos + int(n_days) >= len(idx):
        return False
    return True


def signal_exit_trailing_stop(
    entry_price: float,
    peak_price: float,
    current_price: float,
    trail_pct: float,
) -> bool:
    """Legacy trailing-stop signature. Delegates to exits.exit_trailing_stop."""
    # exit_trailing_stop only checks peak vs current_low; the legacy signature
    # used current_price as the comparator, so we route that through.
    return _exit_trailing_stop(peak_price, current_price, trail_pct)


# --- Legacy SIGNAL_REGISTRY (entry-side) shape preserved for old callers
SIGNAL_REGISTRY = {
    "rsi_cross_below": signal_rsi_cross_below,
    "rsi_cross_above": signal_rsi_cross_above,
    "macd_cross_above_signal": signal_macd_cross_above_signal,
    "macd_cross_below_signal": signal_macd_cross_below_signal,
    "price_cross_above_sma": signal_price_cross_above_sma,
    "price_cross_below_sma": signal_price_cross_below_sma,
    "price_above_sma": signal_price_above_sma,
    "price_below_sma": signal_price_below_sma,
    "price_52wk_high": signal_52wk_high,
    "price_52wk_low": signal_52wk_low,
    "price_above_pct": signal_price_above_pct,
    "price_below_pct": signal_price_below_pct,
    "bb_lower_touch": signal_bb_lower_touch,
    "calendar": signal_calendar,
    "sip_monthly": signal_first_trading_day_of_month,
    "first_trading_day_of_month": signal_first_trading_day_of_month,
    "last_trading_day_of_month": signal_last_trading_day_of_month,
    "volume_spike": signal_volume_spike,
}


__all__ = [
    "signal_rsi_cross_below",
    "signal_rsi_cross_above",
    "signal_macd_cross_above_signal",
    "signal_macd_cross_below_signal",
    "signal_price_cross_above_sma",
    "signal_price_cross_below_sma",
    "signal_price_above_sma",
    "signal_price_below_sma",
    "signal_bb_lower_touch",
    "signal_volume_spike",
    "signal_52wk_high",
    "signal_52wk_low",
    "signal_calendar",
    "signal_first_trading_day_of_month",
    "signal_last_trading_day_of_month",
    "signal_price_cross_above",
    "signal_price_cross_below",
    "signal_price_above_pct",
    "signal_price_below_pct",
    "signal_exit_after_n_days",
    "signal_exit_stop_loss",
    "signal_exit_take_profit",
    "signal_exit_trailing_stop",
    "combine_signals_and",
    "combine_signals_or",
    "SIGNAL_REGISTRY",
    "PRIMITIVE_REGISTRY",
]
