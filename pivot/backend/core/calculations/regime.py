"""Regime / pre-post-window comparison.

Splits a price history at a pivot date and computes a small set of
risk + performance metrics for each window separately. Used by the
chat layer when a user asks "compare X before and after <date>" /
"how did X behave in 2022 vs 2023" / "INFY pre-Covid vs post-Covid".

The pivot date is interpreted as the LAST DAY of the "before" window
(inclusive). The "after" window starts the next trading day.

Returns a dict shaped for easy consumption by the LLM:

  {
    "symbol": "INFY",
    "pivot_date": "2022-01-01",
    "before": {
      "n_days": 731,
      "start_date": "2020-01-02",
      "end_date":   "2021-12-31",
      "total_return_pct": 92.3,
      "annualised_return_pct": ...,
      "volatility_pct": ...,
      "max_drawdown_pct": ...,
      "sharpe_ratio": ...,
      "sortino_ratio": ...,
    },
    "after":  { ... same shape ... },
    "delta":  {
      "total_return_pct": -68.4,
      "max_drawdown_pct": +12.1,
      "sharpe_ratio":     -1.42,
      ...
    },
    "interpretation": "Sharper drawdowns and lower Sharpe post-pivot suggests a regime shift.",
  }
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd


logger = logging.getLogger(__name__)


_REQUESTED_METRICS = (
    "total_return_pct",
    "annualised_return_pct",
    "volatility_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "sortino_ratio",
)


def _parse_pivot(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    if not s:
        return None
    # Allow common short forms.
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # "2022" → Jan 1 of that year (a common "before/after 2022" pivot).
    if s.isdigit() and len(s) == 4:
        try:
            return date(int(s), 1, 1)
        except ValueError:
            return None
    return None


def _window_metrics(prices: pd.Series) -> dict[str, Any]:
    """Compute the small bundle of risk + return metrics over a window
    of close prices. Falls back to None when the window is too short."""
    from backend.core.calculations.risk_metrics import (
        volatility, max_drawdown,
    )
    from backend.core.calculations.performance_metrics import (
        sharpe_ratio, sortino_ratio,
    )

    out: dict[str, Any] = {
        "n_days": int(len(prices)),
        "start_date": str(prices.index[0].date()) if len(prices) else None,
        "end_date":   str(prices.index[-1].date()) if len(prices) else None,
    }
    # 12 is the minimum for any meaningful trend read; yfinance often
    # downsamples 5y+ requests to monthly bars, so 12 = 1 year of data.
    if len(prices) < 12:
        for k in _REQUESTED_METRICS:
            out[k] = None
        out["note"] = "fewer than 12 bars — metrics suppressed"
        return out

    returns = prices.pct_change().dropna()
    if returns.empty:
        for k in _REQUESTED_METRICS:
            out[k] = None
        return out

    total_return = float(prices.iloc[-1]) / float(prices.iloc[0]) - 1.0
    years = max((prices.index[-1] - prices.index[0]).days / 365.25, 0.001)
    ann_return = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1 else None

    vol_d = volatility(returns)
    dd_d = max_drawdown(prices)
    sharpe_d = sharpe_ratio(returns)
    sortino_d = sortino_ratio(returns)

    def _pull(d: dict, key: str = "value") -> Optional[float]:
        if not isinstance(d, dict):
            return None
        v = d.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    out["total_return_pct"] = round(total_return * 100, 2)
    out["annualised_return_pct"] = (
        round(ann_return * 100, 2) if ann_return is not None else None
    )
    out["volatility_pct"] = (
        round(_pull(vol_d) * 100, 2) if _pull(vol_d) is not None else None
    )
    # max_drawdown returns a positive magnitude in [0,1] range
    out["max_drawdown_pct"] = (
        round(_pull(dd_d) * 100, 2) if _pull(dd_d) is not None else None
    )
    out["sharpe_ratio"] = (
        round(_pull(sharpe_d), 3) if _pull(sharpe_d) is not None else None
    )
    out["sortino_ratio"] = (
        round(_pull(sortino_d), 3) if _pull(sortino_d) is not None else None
    )
    return out


def regime_compare_metrics(
    symbol: str,
    pivot_date: Any,
    period: str = "5y",
) -> dict[str, Any]:
    """Public entry point. Fetches `period` of daily OHLCV for `symbol`,
    splits at `pivot_date`, returns metrics for each window plus a
    delta block.

    Raises:
      ValueError on missing args / bad pivot_date.
      DataUnavailableError on yfinance failure.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        raise ValueError("regime_compare_metrics needs a 'symbol'.")
    pivot = _parse_pivot(pivot_date)
    if pivot is None:
        raise ValueError(
            "regime_compare_metrics needs 'pivot_date' as ISO YYYY-MM-DD "
            "or a 4-digit year (e.g. 2022)."
        )
    period = (period or "5y").strip().lower()

    from backend.core.data.historical import get_ohlcv

    df = get_ohlcv(sym, period=period)
    if df is None or df.empty:
        raise RuntimeError(f"no price history for {sym}")

    prices = df["Close"]
    # Index may be tz-aware; convert pivot to a comparable form.
    pivot_ts = pd.Timestamp(pivot)
    if prices.index.tz is not None:
        pivot_ts = pivot_ts.tz_localize(prices.index.tz)

    before = prices[prices.index <= pivot_ts]
    after = prices[prices.index > pivot_ts]

    before_m = _window_metrics(before)
    after_m = _window_metrics(after)

    delta: dict[str, Optional[float]] = {}
    for k in _REQUESTED_METRICS:
        b = before_m.get(k)
        a = after_m.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            delta[k] = round(a - b, 3)
        else:
            delta[k] = None

    # Lightweight one-line interpretation.
    bits: list[str] = []
    if delta.get("sharpe_ratio") is not None:
        if delta["sharpe_ratio"] >= 0.3:
            bits.append("Sharpe improved meaningfully after the pivot")
        elif delta["sharpe_ratio"] <= -0.3:
            bits.append("Sharpe deteriorated after the pivot")
    if delta.get("max_drawdown_pct") is not None:
        if delta["max_drawdown_pct"] >= 5:
            bits.append("max drawdown widened")
        elif delta["max_drawdown_pct"] <= -5:
            bits.append("max drawdown tightened")
    interp = "; ".join(bits) if bits else (
        "no large shift in the metrics between the two windows"
    )

    return {
        "symbol": sym,
        "pivot_date": str(pivot),
        "period": period,
        "before": before_m,
        "after": after_m,
        "delta": delta,
        "interpretation": interp,
    }
