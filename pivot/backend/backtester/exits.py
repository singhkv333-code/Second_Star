"""Section L — Exit Primitives.

Each exit function receives only what it needs from the engine. Stop-style
exits use intraday low so the test fires when the bar's low touched the stop
price (realistic). Profit targets use intraday high. The composer routes the
right kwargs to each function.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional, Union

import pandas as pd

DateLike = Union[date, datetime, pd.Timestamp]


def _to_timestamp(d: Any) -> pd.Timestamp:
    return pd.Timestamp(d)


def exit_after_n_days(
    entry_date: DateLike,
    current_date: DateLike,
    n_days: int,
    df: Optional[pd.DataFrame] = None,
    current_idx: Optional[int] = None,
) -> bool:
    """Exit after n_days have elapsed since entry. Counts trading days when
    df + current_idx are provided (preferred), else calendar days."""
    if entry_date is None or current_date is None:
        return False
    if df is not None and current_idx is not None:
        try:
            entry_ts = _to_timestamp(entry_date)
            entry_loc = df.index.get_indexer([entry_ts], method="pad")[0]
            if entry_loc < 0:
                return False
            return (int(current_idx) - int(entry_loc)) >= int(n_days)
        except Exception:
            pass
    delta = (_to_timestamp(current_date) - _to_timestamp(entry_date)).days
    return delta >= int(n_days)


def exit_stop_loss(entry_price: float, current_low: float, stop_pct: float) -> bool:
    """True when the bar's low touched/breached entry_price * (1 - stop_pct%)."""
    if entry_price is None or current_low is None or entry_price <= 0:
        return False
    stop_price = entry_price * (1.0 - float(stop_pct) / 100.0)
    return float(current_low) <= stop_price


def exit_trailing_stop(peak_price: float, current_low: float, trail_pct: float) -> bool:
    """True when bar's low fell trail_pct% below the peak price seen since entry."""
    if peak_price is None or current_low is None or peak_price <= 0:
        return False
    trigger = peak_price * (1.0 - float(trail_pct) / 100.0)
    return float(current_low) <= trigger


def exit_take_profit(entry_price: float, current_high: float, target_pct: float) -> bool:
    """True when bar's high reached entry_price * (1 + target_pct%)."""
    if entry_price is None or current_high is None or entry_price <= 0:
        return False
    target_price = entry_price * (1.0 + float(target_pct) / 100.0)
    return float(current_high) >= target_price


def exit_stop_and_target(
    entry_price: float,
    peak_price: Optional[float],
    current_high: float,
    current_low: float,
    stop_pct: float,
    target_pct: float,
) -> Optional[str]:
    """Returns "stop", "target", or None. Conservative tie-break: stop wins."""
    if entry_price is None or entry_price <= 0:
        return None
    stop_price = entry_price * (1.0 - float(stop_pct) / 100.0)
    target_price = entry_price * (1.0 + float(target_pct) / 100.0)

    hit_stop = current_low is not None and float(current_low) <= stop_price
    hit_target = current_high is not None and float(current_high) >= target_price

    if hit_stop:
        return "stop"
    if hit_target:
        return "target"
    return None


def exit_indicator_signal(
    signal_series: pd.Series,
    current_idx: Optional[int] = None,
    current_date: Optional[DateLike] = None,
) -> bool:
    """True when the lookup row of signal_series is True."""
    if signal_series is None or len(signal_series) == 0:
        return False
    try:
        if current_idx is not None and 0 <= int(current_idx) < len(signal_series):
            value = signal_series.iloc[int(current_idx)]
        elif current_date is not None:
            ts = _to_timestamp(current_date)
            if ts not in signal_series.index:
                return False
            value = signal_series.loc[ts]
        else:
            return False
        if isinstance(value, pd.Series):
            value = value.iloc[-1]
        return bool(value)
    except Exception:
        return False


def exit_end_of_period(is_last_day: bool) -> bool:
    return bool(is_last_day)


EXIT_REGISTRY = {
    "after_n_days": exit_after_n_days,
    "stop_loss": exit_stop_loss,
    "trailing_stop": exit_trailing_stop,
    "take_profit": exit_take_profit,
    "stop_and_target": exit_stop_and_target,
    "indicator_signal": exit_indicator_signal,
    "end_of_period": exit_end_of_period,
}
