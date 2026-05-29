"""
Returns calculations for Pivot.

Pure functions for computing various return metrics on price series.
All functions return dicts with standardised schema including metric_name,
value, params, and interpretation fields.

Indian market defaults:
- periods_per_year = 252 (Indian trading days)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Union


def _validate_series(series: pd.Series, min_length: int = 2, metric_name: str = "unknown") -> dict | None:
    """
    Validate input series. Returns error dict if invalid, None if valid.
    """
    if series is None:
        return {"error": "Input series is None", "metric_name": metric_name}

    if not isinstance(series, pd.Series):
        return {"error": "Input must be a pandas Series", "metric_name": metric_name}

    if len(series) < min_length:
        return {"error": f"Series must have at least {min_length} data points, got {len(series)}", "metric_name": metric_name}

    # Drop NaN and check again
    valid_series = series.dropna()
    if len(valid_series) < min_length:
        return {"error": f"Series has fewer than {min_length} non-NaN values", "metric_name": metric_name}

    return None


def simple_return(start_price: float, end_price: float) -> dict:
    """
    Calculate the simple (arithmetic) return between two prices.

    Use this for calculating point-to-point returns when you have just
    the starting and ending values. Returns (end - start) / start as a decimal.

    Args:
        start_price: The initial price
        end_price: The final price

    Returns:
        Dict with value (decimal return), params, and interpretation.
    """
    try:
        if start_price is None or end_price is None:
            return {"error": "Prices cannot be None", "metric_name": "Simple Return"}

        if not np.isfinite(start_price) or not np.isfinite(end_price):
            return {"error": "Prices must be finite numbers", "metric_name": "Simple Return"}

        if start_price == 0:
            return {"error": "Start price cannot be zero", "metric_name": "Simple Return"}

        ret = (end_price - start_price) / start_price
        pct = ret * 100

        if ret > 0:
            interpretation = f"Gain of {pct:.2f}%"
        elif ret < 0:
            interpretation = f"Loss of {abs(pct):.2f}%"
        else:
            interpretation = "No change"

        return {
            "metric_name": "Simple Return",
            "value": float(ret),
            "value_pct": float(pct),
            "params": {"start_price": start_price, "end_price": end_price},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Simple Return"}


def log_return(start_price: float, end_price: float) -> dict:
    """
    Calculate the logarithmic (continuously compounded) return between two prices.

    Use this when you need returns that are additive over time (useful for
    multi-period analysis). Returns ln(end / start).

    Args:
        start_price: The initial price
        end_price: The final price

    Returns:
        Dict with value (log return), params, and interpretation.
    """
    try:
        if start_price is None or end_price is None:
            return {"error": "Prices cannot be None", "metric_name": "Log Return"}

        if not np.isfinite(start_price) or not np.isfinite(end_price):
            return {"error": "Prices must be finite numbers", "metric_name": "Log Return"}

        if start_price <= 0 or end_price <= 0:
            return {"error": "Prices must be positive for log return", "metric_name": "Log Return"}

        ret = np.log(end_price / start_price)
        pct = ret * 100

        if ret > 0:
            interpretation = f"Log return of {pct:.2f}% (gain)"
        elif ret < 0:
            interpretation = f"Log return of {pct:.2f}% (loss)"
        else:
            interpretation = "No change"

        return {
            "metric_name": "Log Return",
            "value": float(ret),
            "value_pct": float(pct),
            "params": {"start_price": start_price, "end_price": end_price},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Log Return"}


def cumulative_returns(price_series: pd.Series) -> dict:
    """
    Calculate the cumulative return curve from a price series.

    Use this to see how returns have accumulated over time. Each point shows
    the total return from the start up to that point. The final value equals
    the simple return over the entire period.

    Args:
        price_series: A pandas Series of prices indexed by date/time

    Returns:
        Dict with values (list of cumulative returns), current (final value),
        params, and interpretation.
    """
    try:
        err = _validate_series(price_series, min_length=2, metric_name="Cumulative Returns")
        if err:
            return err

        # Drop NaN values
        prices = price_series.dropna()
        start_price = prices.iloc[0]

        if start_price == 0:
            return {"error": "Starting price cannot be zero", "metric_name": "Cumulative Returns"}

        cum_returns = (prices / start_price) - 1
        final_return = float(cum_returns.iloc[-1])
        final_pct = final_return * 100

        if final_return > 0:
            interpretation = f"Total cumulative return of {final_pct:.2f}% over {len(prices)} periods"
        elif final_return < 0:
            interpretation = f"Total cumulative loss of {abs(final_pct):.2f}% over {len(prices)} periods"
        else:
            interpretation = f"No net change over {len(prices)} periods"

        return {
            "metric_name": "Cumulative Returns",
            "values": cum_returns.tolist(),
            "current": final_return,
            "current_pct": final_pct,
            "params": {"num_periods": len(prices)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Cumulative Returns"}


def annualised_return(price_series: pd.Series, periods_per_year: int = 252) -> dict:
    """
    Calculate the annualised (CAGR) return from a price series.

    Use this to compare returns across different time horizons on an equal
    footing. Assumes 252 trading days per year (Indian market standard).

    Args:
        price_series: A pandas Series of prices indexed by date/time
        periods_per_year: Number of periods in a year (default 252 for daily)

    Returns:
        Dict with value (annualised return as decimal), params, and interpretation.
    """
    try:
        err = _validate_series(price_series, min_length=2, metric_name="Annualised Return")
        if err:
            return err

        prices = price_series.dropna()
        start_price = prices.iloc[0]
        end_price = prices.iloc[-1]
        n_periods = len(prices) - 1

        if start_price <= 0:
            return {"error": "Starting price must be positive", "metric_name": "Annualised Return"}

        if n_periods == 0:
            return {"error": "Need at least 2 periods to annualise", "metric_name": "Annualised Return"}

        # Calculate CAGR: (end/start)^(1/years) - 1.
        # Derive `years` from the ACTUAL calendar span of the index when it's
        # a DatetimeIndex — NOT from n_periods/periods_per_year. The latter
        # fabricates the CAGR when the series is sampled at a non-daily
        # frequency: a 2-year window returned as ~105 weekly bars gave
        # years = 104/252 = 0.41, turning a true +13% into a reported
        # "+34.62% per year" (2026-05-29 audit). Calendar span is immune to
        # sampling frequency. Fall back to the period count only when the
        # index carries no usable dates.
        total_return = end_price / start_price
        years = None
        idx = prices.index
        try:
            if isinstance(idx, pd.DatetimeIndex) and len(idx) >= 2:
                span_days = (idx[-1] - idx[0]).days
                if span_days > 0:
                    years = span_days / 365.25
        except Exception:  # noqa: BLE001 — defensive; fall back below
            years = None
        if not years or years <= 0:
            years = n_periods / periods_per_year

        if total_return <= 0:
            return {"error": "Cannot annualise negative total return (price went to zero)", "metric_name": "Annualised Return"}

        annualised = (total_return ** (1 / years)) - 1
        pct = annualised * 100

        if annualised > 0:
            interpretation = f"Annualised return of {pct:.2f}% per year over {years:.2f} years"
        else:
            interpretation = f"Annualised loss of {abs(pct):.2f}% per year over {years:.2f} years"

        return {
            "metric_name": "Annualised Return",
            "value": float(annualised),
            "value_pct": float(pct),
            "total_return": float(total_return - 1),
            "years": float(years),
            "params": {"periods_per_year": periods_per_year, "num_periods": n_periods},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Annualised Return"}


def rolling_returns(price_series: pd.Series, window: int) -> dict:
    """
    Calculate rolling returns over a specified window.

    Use this to see how returns have varied over time using a moving window.
    Useful for identifying trends and regime changes in performance.

    Args:
        price_series: A pandas Series of prices indexed by date/time
        window: The rolling window size in periods

    Returns:
        Dict with values (list of rolling returns), summary stats, and interpretation.
    """
    try:
        if window < 1:
            return {"error": "Window must be at least 1", "metric_name": "Rolling Returns"}

        err = _validate_series(price_series, min_length=window + 1, metric_name="Rolling Returns")
        if err:
            return err

        prices = price_series.dropna()

        # Calculate rolling returns: (P_t / P_{t-window}) - 1
        rolled = (prices / prices.shift(window)) - 1
        rolled = rolled.dropna()

        if len(rolled) == 0:
            return {"error": "Not enough data for rolling calculation", "metric_name": "Rolling Returns"}

        mean_ret = float(rolled.mean())
        std_ret = float(rolled.std())
        min_ret = float(rolled.min())
        max_ret = float(rolled.max())

        interpretation = f"{window}-period rolling returns: mean {mean_ret*100:.2f}%, ranging from {min_ret*100:.2f}% to {max_ret*100:.2f}%"

        return {
            "metric_name": "Rolling Returns",
            "values": rolled.tolist(),
            "summary": {
                "mean": mean_ret,
                "std": std_ret,
                "min": min_ret,
                "max": max_ret,
                "count": len(rolled),
            },
            "params": {"window": window},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Rolling Returns"}


def period_returns(price_series: pd.Series, period: str) -> dict:
    """
    Resample price series to a given frequency and compute period returns.

    Use this to get daily, weekly, monthly, quarterly, or yearly returns
    from a higher-frequency price series. Returns the last price of each
    period and the return for that period.

    Args:
        price_series: A pandas Series of prices with a DatetimeIndex
        period: One of "daily", "weekly", "monthly", "quarterly", "yearly"

    Returns:
        Dict with values (list of period returns), summary stats, and interpretation.
    """
    try:
        period_map = {
            "daily": "D",
            "weekly": "W",
            "monthly": "ME",
            "quarterly": "QE",
            "yearly": "YE",
        }

        if period not in period_map:
            return {"error": f"Period must be one of {list(period_map.keys())}", "metric_name": "Period Returns"}

        err = _validate_series(price_series, min_length=2, metric_name="Period Returns")
        if err:
            return err

        prices = price_series.dropna()

        # Ensure datetime index
        if not isinstance(prices.index, pd.DatetimeIndex):
            try:
                prices.index = pd.to_datetime(prices.index)
            except Exception:
                return {"error": "Series index must be convertible to datetime", "metric_name": "Period Returns"}

        # Resample to end-of-period prices
        freq = period_map[period]
        resampled = prices.resample(freq).last().dropna()

        if len(resampled) < 2:
            return {"error": f"Not enough data for {period} resampling", "metric_name": "Period Returns"}

        # Calculate period returns
        returns = resampled.pct_change().dropna()

        if len(returns) == 0:
            return {"error": "Could not compute period returns", "metric_name": "Period Returns"}

        mean_ret = float(returns.mean())
        std_ret = float(returns.std())

        interpretation = f"{period.capitalize()} returns: mean {mean_ret*100:.2f}%, std {std_ret*100:.2f}% over {len(returns)} periods"

        return {
            "metric_name": "Period Returns",
            "values": returns.tolist(),
            "summary": {
                "mean": mean_ret,
                "std": std_ret,
                "min": float(returns.min()),
                "max": float(returns.max()),
                "count": len(returns),
            },
            "params": {"period": period},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Period Returns"}
