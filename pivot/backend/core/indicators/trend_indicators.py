"""
Trend Indicators for Pivot.

These indicators help identify the direction and strength of market trends.
Use them to determine whether a stock is in an uptrend, downtrend, or ranging.
"""

from typing import Any
import pandas as pd
import numpy as np
import ta


def _get_current_value(series: pd.Series) -> float | None:
    """Extract last non-null value from a series."""
    valid = series.dropna()
    if len(valid) == 0:
        return None
    return float(valid.iloc[-1])


def _series_to_list(series: pd.Series) -> list[float]:
    """Convert pandas Series to list, replacing NaN with None."""
    return [None if pd.isna(v) else float(v) for v in series]


def sma(df: pd.DataFrame, period: int = 20) -> dict[str, Any]:
    """
    Simple Moving Average — the arithmetic mean of closing prices over a period.
    Use SMA to identify trend direction: price above SMA suggests uptrend, below suggests downtrend.
    Longer periods (50, 200) for major trends; shorter periods (10, 20) for short-term moves.
    SMA crossovers (e.g., 50 crossing 200) are classic trend-change signals.
    """
    try:
        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"SMA({period})"}

        indicator = ta.trend.SMAIndicator(close=df["Close"], window=period)
        values = indicator.sma_indicator()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"SMA({period})"}

        close_price = float(df["Close"].iloc[-1])
        if close_price > current * 1.01:
            signal = "bullish"
            interpretation = f"SMA({period}) = {current:.2f} — price above SMA, uptrend indication"
        elif close_price < current * 0.99:
            signal = "bearish"
            interpretation = f"SMA({period}) = {current:.2f} — price below SMA, downtrend indication"
        else:
            signal = "neutral"
            interpretation = f"SMA({period}) = {current:.2f} — price near SMA, no clear trend"

        return {
            "indicator_name": f"SMA({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"SMA({period})"}


def ema(df: pd.DataFrame, period: int = 20) -> dict[str, Any]:
    """
    Exponential Moving Average — weighted average giving more importance to recent prices.
    EMA reacts faster to price changes than SMA, useful for catching trends earlier.
    Common periods: 9, 12, 26 for short-term; 50, 200 for long-term trend identification.
    Price crossing EMA or EMA crossovers signal potential trend changes.
    """
    try:
        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"EMA({period})"}

        indicator = ta.trend.EMAIndicator(close=df["Close"], window=period)
        values = indicator.ema_indicator()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"EMA({period})"}

        close_price = float(df["Close"].iloc[-1])
        if close_price > current * 1.01:
            signal = "bullish"
            interpretation = f"EMA({period}) = {current:.2f} — price above EMA, bullish momentum"
        elif close_price < current * 0.99:
            signal = "bearish"
            interpretation = f"EMA({period}) = {current:.2f} — price below EMA, bearish momentum"
        else:
            signal = "neutral"
            interpretation = f"EMA({period}) = {current:.2f} — price near EMA, consolidation"

        return {
            "indicator_name": f"EMA({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"EMA({period})"}


def wma(df: pd.DataFrame, period: int = 20) -> dict[str, Any]:
    """
    Weighted Moving Average — linear weights where recent prices have higher weight.
    WMA is between SMA and EMA in responsiveness. Use when you want smooth trends
    with moderate sensitivity to recent price action.
    """
    try:
        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"WMA({period})"}

        indicator = ta.trend.WMAIndicator(close=df["Close"], window=period)
        values = indicator.wma()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"WMA({period})"}

        close_price = float(df["Close"].iloc[-1])
        if close_price > current * 1.01:
            signal = "bullish"
            interpretation = f"WMA({period}) = {current:.2f} — price above WMA, upward bias"
        elif close_price < current * 0.99:
            signal = "bearish"
            interpretation = f"WMA({period}) = {current:.2f} — price below WMA, downward bias"
        else:
            signal = "neutral"
            interpretation = f"WMA({period}) = {current:.2f} — price near WMA"

        return {
            "indicator_name": f"WMA({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"WMA({period})"}


def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal_period: int = 9) -> dict[str, Any]:
    """
    MACD (Moving Average Convergence Divergence) — trend-following momentum indicator.
    Shows relationship between two EMAs. MACD line crossing above signal line = bullish.
    Histogram shows momentum strength. Zero-line crossovers indicate trend changes.
    Classic parameters: 12, 26, 9. Use for trend direction and momentum confirmation.
    """
    try:
        if len(df) < slow + signal_period:
            return {"error": f"insufficient data: need >= {slow + signal_period} rows", "indicator_name": f"MACD({fast},{slow},{signal_period})"}

        indicator = ta.trend.MACD(close=df["Close"], window_fast=fast, window_slow=slow, window_sign=signal_period)
        macd_line = indicator.macd()
        signal_line = indicator.macd_signal()
        histogram = indicator.macd_diff()

        macd_current = _get_current_value(macd_line)
        signal_current = _get_current_value(signal_line)
        hist_current = _get_current_value(histogram)

        if macd_current is None or signal_current is None:
            return {"error": "all values are NaN", "indicator_name": f"MACD({fast},{slow},{signal_period})"}

        if hist_current > 0 and macd_current > signal_current:
            signal = "bullish"
            interpretation = f"MACD histogram positive ({hist_current:.2f}), line above signal — bullish momentum"
        elif hist_current < 0 and macd_current < signal_current:
            signal = "bearish"
            interpretation = f"MACD histogram negative ({hist_current:.2f}), line below signal — bearish momentum"
        else:
            signal = "neutral"
            interpretation = f"MACD = {macd_current:.2f}, Signal = {signal_current:.2f} — mixed signals"

        return {
            "indicator_name": f"MACD({fast},{slow},{signal_period})",
            "values": {
                "macd": _series_to_list(macd_line),
                "signal": _series_to_list(signal_line),
                "histogram": _series_to_list(histogram),
            },
            "current_value": {"macd": macd_current, "signal": signal_current, "histogram": hist_current},
            "signal": signal,
            "interpretation": interpretation,
            "params": {"fast": fast, "slow": slow, "signal": signal_period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"MACD({fast},{slow},{signal_period})"}


def adx(df: pd.DataFrame, period: int = 14) -> dict[str, Any]:
    """
    ADX (Average Directional Index) — measures trend strength, not direction.
    ADX < 20 = weak/no trend (ranging market), 20-40 = developing trend,
    > 40 = strong trend, > 60 = very strong trend.
    Use with +DI/-DI for direction: +DI > -DI = uptrend, -DI > +DI = downtrend.
    """
    try:
        if len(df) < period * 2:
            return {"error": f"insufficient data: need >= {period * 2} rows", "indicator_name": f"ADX({period})"}

        indicator = ta.trend.ADXIndicator(high=df["High"], low=df["Low"], close=df["Close"], window=period)
        adx_values = indicator.adx()
        plus_di = indicator.adx_pos()
        minus_di = indicator.adx_neg()

        adx_current = _get_current_value(adx_values)
        plus_di_current = _get_current_value(plus_di)
        minus_di_current = _get_current_value(minus_di)

        if adx_current is None:
            return {"error": "all values are NaN", "indicator_name": f"ADX({period})"}

        if adx_current >= 25:
            if plus_di_current and minus_di_current:
                if plus_di_current > minus_di_current:
                    signal = "bullish"
                    interpretation = f"ADX = {adx_current:.1f} (strong trend), +DI > -DI — strong uptrend"
                else:
                    signal = "bearish"
                    interpretation = f"ADX = {adx_current:.1f} (strong trend), -DI > +DI — strong downtrend"
            else:
                signal = "neutral"
                interpretation = f"ADX = {adx_current:.1f} — strong trend present"
        else:
            signal = "neutral"
            interpretation = f"ADX = {adx_current:.1f} — weak/no trend, ranging market"

        return {
            "indicator_name": f"ADX({period})",
            "values": {
                "adx": _series_to_list(adx_values),
                "plus_di": _series_to_list(plus_di),
                "minus_di": _series_to_list(minus_di),
            },
            "current_value": {"adx": adx_current, "plus_di": plus_di_current, "minus_di": minus_di_current},
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"ADX({period})"}


def psar(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> dict[str, Any]:
    """
    Parabolic SAR — trend-following indicator that provides entry/exit points.
    When price is above SAR dots = uptrend, below = downtrend.
    SAR flipping from below to above price = potential sell signal, vice versa.
    Useful for trailing stops: SAR acts as a dynamic stop-loss level.
    """
    try:
        if len(df) < 5:
            return {"error": "insufficient data: need >= 5 rows", "indicator_name": f"PSAR({step},{max_step})"}

        indicator = ta.trend.PSARIndicator(high=df["High"], low=df["Low"], close=df["Close"], step=step, max_step=max_step)
        psar_values = indicator.psar()

        current = _get_current_value(psar_values)
        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"PSAR({step},{max_step})"}

        close_price = float(df["Close"].iloc[-1])
        if close_price > current:
            signal = "bullish"
            interpretation = f"PSAR = {current:.2f}, price above SAR — uptrend, SAR acts as support"
        else:
            signal = "bearish"
            interpretation = f"PSAR = {current:.2f}, price below SAR — downtrend, SAR acts as resistance"

        return {
            "indicator_name": f"PSAR({step},{max_step})",
            "values": _series_to_list(psar_values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"step": step, "max_step": max_step},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"PSAR({step},{max_step})"}


def ichimoku(df: pd.DataFrame) -> dict[str, Any]:
    """
    Ichimoku Cloud — comprehensive indicator showing support/resistance, trend, and momentum.
    Components: Tenkan-sen (conversion), Kijun-sen (base), Senkou Span A/B (cloud), Chikou Span.
    Price above cloud = bullish, below = bearish, inside = consolidation.
    Cloud color (A>B green, B>A red) shows future trend expectation.
    Tenkan crossing Kijun = signal; above cloud = strong, below = weak.
    """
    try:
        if len(df) < 52:
            return {"error": "insufficient data: need >= 52 rows", "indicator_name": "Ichimoku"}

        indicator = ta.trend.IchimokuIndicator(high=df["High"], low=df["Low"])

        tenkan = indicator.ichimoku_conversion_line()
        kijun = indicator.ichimoku_base_line()
        senkou_a = indicator.ichimoku_a()
        senkou_b = indicator.ichimoku_b()

        tenkan_current = _get_current_value(tenkan)
        kijun_current = _get_current_value(kijun)
        senkou_a_current = _get_current_value(senkou_a)
        senkou_b_current = _get_current_value(senkou_b)

        if tenkan_current is None or kijun_current is None:
            return {"error": "all values are NaN", "indicator_name": "Ichimoku"}

        close_price = float(df["Close"].iloc[-1])
        cloud_top = max(senkou_a_current or 0, senkou_b_current or 0)
        cloud_bottom = min(senkou_a_current or float('inf'), senkou_b_current or float('inf'))

        if close_price > cloud_top:
            signal = "bullish"
            interpretation = f"Price above Ichimoku cloud — bullish trend, cloud acts as support"
        elif close_price < cloud_bottom:
            signal = "bearish"
            interpretation = f"Price below Ichimoku cloud — bearish trend, cloud acts as resistance"
        else:
            signal = "neutral"
            interpretation = f"Price inside Ichimoku cloud — consolidation zone, wait for breakout"

        return {
            "indicator_name": "Ichimoku",
            "values": {
                "tenkan_sen": _series_to_list(tenkan),
                "kijun_sen": _series_to_list(kijun),
                "senkou_span_a": _series_to_list(senkou_a),
                "senkou_span_b": _series_to_list(senkou_b),
            },
            "current_value": {
                "tenkan_sen": tenkan_current,
                "kijun_sen": kijun_current,
                "senkou_span_a": senkou_a_current,
                "senkou_span_b": senkou_b_current,
            },
            "signal": signal,
            "interpretation": interpretation,
            "params": {"tenkan": 9, "kijun": 26, "senkou": 52},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": "Ichimoku"}


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> dict[str, Any]:
    """
    Supertrend — trend-following indicator based on ATR that provides clear buy/sell signals.
    Green line below price = uptrend (buy zone), red line above price = downtrend (sell zone).
    Color flips indicate trend reversals. Excellent for trailing stops and trend confirmation.
    Works best in trending markets; gives false signals in ranging markets.
    """
    try:
        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"Supertrend({period},{multiplier})"}

        # Calculate ATR
        atr_indicator = ta.volatility.AverageTrueRange(high=df["High"], low=df["Low"], close=df["Close"], window=period)
        atr_values = atr_indicator.average_true_range()

        # Calculate Supertrend
        hl2 = (df["High"] + df["Low"]) / 2
        upper_band = hl2 + (multiplier * atr_values)
        lower_band = hl2 - (multiplier * atr_values)

        supertrend_vals = pd.Series(index=df.index, dtype=float)
        direction = pd.Series(index=df.index, dtype=int)

        for i in range(period, len(df)):
            if i == period:
                supertrend_vals.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
            else:
                if df["Close"].iloc[i] > supertrend_vals.iloc[i-1]:
                    supertrend_vals.iloc[i] = lower_band.iloc[i]
                    direction.iloc[i] = 1
                elif df["Close"].iloc[i] < supertrend_vals.iloc[i-1]:
                    supertrend_vals.iloc[i] = upper_band.iloc[i]
                    direction.iloc[i] = -1
                else:
                    supertrend_vals.iloc[i] = supertrend_vals.iloc[i-1]
                    direction.iloc[i] = direction.iloc[i-1]

                # Adjust bands
                if direction.iloc[i] == 1 and lower_band.iloc[i] < supertrend_vals.iloc[i-1]:
                    supertrend_vals.iloc[i] = supertrend_vals.iloc[i-1]
                if direction.iloc[i] == -1 and upper_band.iloc[i] > supertrend_vals.iloc[i-1]:
                    supertrend_vals.iloc[i] = supertrend_vals.iloc[i-1]

        current = _get_current_value(supertrend_vals)
        current_dir = direction.dropna().iloc[-1] if len(direction.dropna()) > 0 else 0

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"Supertrend({period},{multiplier})"}

        close_price = float(df["Close"].iloc[-1])
        if close_price > current:
            signal = "bullish"
            interpretation = f"Supertrend = {current:.2f}, price above — uptrend active"
        else:
            signal = "bearish"
            interpretation = f"Supertrend = {current:.2f}, price below — downtrend active"

        return {
            "indicator_name": f"Supertrend({period},{multiplier})",
            "values": {
                "supertrend": _series_to_list(supertrend_vals),
                "direction": _series_to_list(direction),
            },
            "current_value": {"supertrend": current, "direction": int(current_dir)},
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period, "multiplier": multiplier},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"Supertrend({period},{multiplier})"}


def aroon(df: pd.DataFrame, period: int = 25) -> dict[str, Any]:
    """
    Aroon Indicator — identifies trend changes and trend strength using time since highs/lows.
    Aroon Up > 70 and Aroon Down < 30 = strong uptrend.
    Aroon Down > 70 and Aroon Up < 30 = strong downtrend.
    Both near 50 = consolidation. Crossovers signal trend changes.
    """
    try:
        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"Aroon({period})"}

        indicator = ta.trend.AroonIndicator(high=df["High"], low=df["Low"], window=period)
        aroon_up = indicator.aroon_up()
        aroon_down = indicator.aroon_down()

        up_current = _get_current_value(aroon_up)
        down_current = _get_current_value(aroon_down)

        if up_current is None or down_current is None:
            return {"error": "all values are NaN", "indicator_name": f"Aroon({period})"}

        if up_current > 70 and down_current < 30:
            signal = "bullish"
            interpretation = f"Aroon Up={up_current:.0f}, Down={down_current:.0f} — strong uptrend"
        elif down_current > 70 and up_current < 30:
            signal = "bearish"
            interpretation = f"Aroon Up={up_current:.0f}, Down={down_current:.0f} — strong downtrend"
        else:
            signal = "neutral"
            interpretation = f"Aroon Up={up_current:.0f}, Down={down_current:.0f} — no clear trend"

        return {
            "indicator_name": f"Aroon({period})",
            "values": {
                "aroon_up": _series_to_list(aroon_up),
                "aroon_down": _series_to_list(aroon_down),
            },
            "current_value": {"aroon_up": up_current, "aroon_down": down_current},
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"Aroon({period})"}


def linear_regression_slope(df: pd.DataFrame, period: int = 14) -> dict[str, Any]:
    """
    Linear Regression Slope — measures the rate of price change over a period.
    Positive slope = uptrend, negative = downtrend. Magnitude indicates strength.
    Useful for identifying trend momentum and potential exhaustion when slope flattens.
    Combine with R-squared for confidence in trend direction.
    """
    try:
        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"LR_Slope({period})"}

        close = df["Close"]
        slopes = pd.Series(index=df.index, dtype=float)

        for i in range(period - 1, len(df)):
            y = close.iloc[i - period + 1:i + 1].values
            x = np.arange(period)
            if len(y) == period:
                slope, _ = np.polyfit(x, y, 1)
                slopes.iloc[i] = slope

        current = _get_current_value(slopes)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"LR_Slope({period})"}

        # Normalize slope by average price for comparison
        avg_price = float(df["Close"].iloc[-period:].mean())
        slope_pct = (current / avg_price) * 100 if avg_price > 0 else 0

        if slope_pct > 0.5:
            signal = "bullish"
            interpretation = f"LR Slope = {current:.2f} ({slope_pct:.2f}% per bar) — positive trend momentum"
        elif slope_pct < -0.5:
            signal = "bearish"
            interpretation = f"LR Slope = {current:.2f} ({slope_pct:.2f}% per bar) — negative trend momentum"
        else:
            signal = "neutral"
            interpretation = f"LR Slope = {current:.2f} ({slope_pct:.2f}% per bar) — flat/consolidating"

        return {
            "indicator_name": f"LR_Slope({period})",
            "values": _series_to_list(slopes),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"LR_Slope({period})"}
