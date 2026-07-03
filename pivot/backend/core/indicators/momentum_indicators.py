"""
Momentum Indicators for Pivot.

These indicators measure the speed and magnitude of price movements.
Use them to identify overbought/oversold conditions and potential reversals.
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


def rsi(df: pd.DataFrame, period: int = 14) -> dict[str, Any]:
    """
    RSI (Relative Strength Index) — measures speed and change of price movements.
    RSI < 30 = oversold (potential bullish reversal), RSI > 70 = overbought (potential bearish reversal).
    50 is the centerline: above 50 = bullish momentum, below 50 = bearish momentum.
    Divergences between price and RSI often precede reversals.
    """
    try:
        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"RSI({period})"}

        indicator = ta.momentum.RSIIndicator(close=df["Close"], window=period)
        values = indicator.rsi()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"RSI({period})"}

        if current < 30:
            signal = "bullish"
            interpretation = f"RSI = {current:.1f} — oversold territory, potential bullish reversal"
        elif current > 70:
            signal = "bearish"
            interpretation = f"RSI = {current:.1f} — overbought territory, potential bearish reversal"
        elif current > 50:
            signal = "neutral"
            interpretation = f"RSI = {current:.1f} — above 50, bullish momentum but not overbought"
        else:
            signal = "neutral"
            interpretation = f"RSI = {current:.1f} — below 50, bearish momentum but not oversold"

        return {
            "indicator_name": f"RSI({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"RSI({period})"}


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> dict[str, Any]:
    """
    Stochastic Oscillator — compares closing price to price range over a period.
    %K < 20 = oversold, %K > 80 = overbought. %K crossing above %D = bullish signal.
    Works best in ranging markets. In strong trends, can stay overbought/oversold for extended periods.
    Look for divergences and %K/%D crossovers for trade signals.
    """
    try:
        if len(df) < k_period + d_period:
            return {"error": f"insufficient data: need >= {k_period + d_period} rows", "indicator_name": f"Stochastic({k_period},{d_period})"}

        indicator = ta.momentum.StochasticOscillator(
            high=df["High"], low=df["Low"], close=df["Close"],
            window=k_period, smooth_window=d_period
        )
        k_values = indicator.stoch()
        d_values = indicator.stoch_signal()

        k_current = _get_current_value(k_values)
        d_current = _get_current_value(d_values)

        if k_current is None:
            return {"error": "all values are NaN", "indicator_name": f"Stochastic({k_period},{d_period})"}

        if k_current < 20:
            signal = "bullish"
            interpretation = f"%K={k_current:.1f}, %D={d_current:.1f} — oversold, potential bullish reversal"
        elif k_current > 80:
            signal = "bearish"
            interpretation = f"%K={k_current:.1f}, %D={d_current:.1f} — overbought, potential bearish reversal"
        elif k_current > d_current:
            signal = "neutral"
            interpretation = f"%K={k_current:.1f} > %D={d_current:.1f} — bullish crossover, momentum rising"
        else:
            signal = "neutral"
            interpretation = f"%K={k_current:.1f} < %D={d_current:.1f} — bearish crossover, momentum falling"

        return {
            "indicator_name": f"Stochastic({k_period},{d_period})",
            "values": {
                "k": _series_to_list(k_values),
                "d": _series_to_list(d_values),
            },
            "current_value": {"k": k_current, "d": d_current},
            "signal": signal,
            "interpretation": interpretation,
            "params": {"k_period": k_period, "d_period": d_period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"Stochastic({k_period},{d_period})"}


def stoch_rsi(df: pd.DataFrame, period: int = 14) -> dict[str, Any]:
    """
    Stochastic RSI — applies Stochastic formula to RSI values instead of price.
    More sensitive than RSI alone. Values 0-1: < 0.2 = oversold, > 0.8 = overbought.
    Generates more signals but also more false signals. Best used with confirmation.
    """
    try:
        if len(df) < period * 2:
            return {"error": f"insufficient data: need >= {period * 2} rows", "indicator_name": f"StochRSI({period})"}

        indicator = ta.momentum.StochRSIIndicator(close=df["Close"], window=period)
        stoch_rsi_values = indicator.stochrsi()
        k_values = indicator.stochrsi_k()
        d_values = indicator.stochrsi_d()

        current = _get_current_value(stoch_rsi_values)
        k_current = _get_current_value(k_values)
        d_current = _get_current_value(d_values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"StochRSI({period})"}

        if current < 0.2:
            signal = "bullish"
            interpretation = f"StochRSI = {current:.2f} — oversold, potential bullish reversal"
        elif current > 0.8:
            signal = "bearish"
            interpretation = f"StochRSI = {current:.2f} — overbought, potential bearish reversal"
        else:
            signal = "neutral"
            interpretation = f"StochRSI = {current:.2f} — neutral zone"

        return {
            "indicator_name": f"StochRSI({period})",
            "values": {
                "stoch_rsi": _series_to_list(stoch_rsi_values),
                "k": _series_to_list(k_values),
                "d": _series_to_list(d_values),
            },
            "current_value": {"stoch_rsi": current, "k": k_current, "d": d_current},
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"StochRSI({period})"}


def roc(df: pd.DataFrame, period: int = 12) -> dict[str, Any]:
    """
    Rate of Change — percentage change in price over a period.
    ROC > 0 = price increased, ROC < 0 = price decreased. Magnitude shows momentum strength.
    Zero-line crossovers can signal trend changes. Extreme values may indicate overbought/oversold.
    """
    try:
        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"ROC({period})"}

        indicator = ta.momentum.ROCIndicator(close=df["Close"], window=period)
        values = indicator.roc()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"ROC({period})"}

        if current > 5:
            signal = "bullish"
            interpretation = f"ROC = {current:.2f}% — strong positive momentum"
        elif current < -5:
            signal = "bearish"
            interpretation = f"ROC = {current:.2f}% — strong negative momentum"
        elif current > 0:
            signal = "neutral"
            interpretation = f"ROC = {current:.2f}% — mild positive momentum"
        else:
            signal = "neutral"
            interpretation = f"ROC = {current:.2f}% — mild negative momentum"

        return {
            "indicator_name": f"ROC({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"ROC({period})"}


def williams_r(df: pd.DataFrame, period: int = 14) -> dict[str, Any]:
    """
    Williams %R — momentum oscillator similar to Stochastic but inverted (0 to -100).
    %R > -20 = overbought, %R < -80 = oversold.
    Use for identifying potential reversal points and timing entries/exits.
    """
    try:
        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"Williams_R({period})"}

        indicator = ta.momentum.WilliamsRIndicator(
            high=df["High"], low=df["Low"], close=df["Close"], lbp=period
        )
        values = indicator.williams_r()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"Williams_R({period})"}

        if current < -80:
            signal = "bullish"
            interpretation = f"Williams %R = {current:.1f} — oversold, potential bullish reversal"
        elif current > -20:
            signal = "bearish"
            interpretation = f"Williams %R = {current:.1f} — overbought, potential bearish reversal"
        else:
            signal = "neutral"
            interpretation = f"Williams %R = {current:.1f} — neutral zone"

        return {
            "indicator_name": f"Williams_R({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"Williams_R({period})"}


def cci(df: pd.DataFrame, period: int = 20) -> dict[str, Any]:
    """
    CCI (Commodity Channel Index) — measures price deviation from statistical mean.
    CCI > 100 = overbought, CCI < -100 = oversold. CCI > 0 = above average, < 0 = below average.
    Zero-line crossovers indicate trend changes. Extreme values often precede reversals.
    """
    try:
        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"CCI({period})"}

        indicator = ta.trend.CCIIndicator(
            high=df["High"], low=df["Low"], close=df["Close"], window=period
        )
        values = indicator.cci()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"CCI({period})"}

        if current < -100:
            signal = "bullish"
            interpretation = f"CCI = {current:.1f} — oversold, potential bullish reversal"
        elif current > 100:
            signal = "bearish"
            interpretation = f"CCI = {current:.1f} — overbought, potential bearish reversal"
        elif current > 0:
            signal = "neutral"
            interpretation = f"CCI = {current:.1f} — above average, mild bullish bias"
        else:
            signal = "neutral"
            interpretation = f"CCI = {current:.1f} — below average, mild bearish bias"

        return {
            "indicator_name": f"CCI({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"CCI({period})"}


def mfi(df: pd.DataFrame, period: int = 14) -> dict[str, Any]:
    """
    MFI (Money Flow Index) — volume-weighted RSI, measures buying/selling pressure.
    MFI < 20 = oversold, MFI > 80 = overbought. Incorporates volume for more reliable signals.
    Divergences between price and MFI can signal trend exhaustion.
    """
    try:
        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"MFI({period})"}

        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": f"MFI({period})"}

        indicator = ta.volume.MFIIndicator(
            high=df["High"], low=df["Low"], close=df["Close"], volume=df["Volume"], window=period
        )
        values = indicator.money_flow_index()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"MFI({period})"}

        if current < 20:
            signal = "bullish"
            interpretation = f"MFI = {current:.1f} — oversold with volume confirmation, potential bullish reversal"
        elif current > 80:
            signal = "bearish"
            interpretation = f"MFI = {current:.1f} — overbought with volume confirmation, potential bearish reversal"
        elif current > 50:
            signal = "neutral"
            interpretation = f"MFI = {current:.1f} — above 50, buying pressure dominant"
        else:
            signal = "neutral"
            interpretation = f"MFI = {current:.1f} — below 50, selling pressure dominant"

        return {
            "indicator_name": f"MFI({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"MFI({period})"}


def trix(df: pd.DataFrame, period: int = 15) -> dict[str, Any]:
    """
    TRIX — triple-smoothed EMA rate of change, filters out short-term noise.
    TRIX > 0 = bullish momentum, TRIX < 0 = bearish momentum.
    Zero-line crossovers signal trend changes. Very smooth, lags but reliable.
    """
    try:
        if len(df) < period * 3:
            return {"error": f"insufficient data: need >= {period * 3} rows", "indicator_name": f"TRIX({period})"}

        indicator = ta.trend.TRIXIndicator(close=df["Close"], window=period)
        values = indicator.trix()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"TRIX({period})"}

        if current > 0.05:
            signal = "bullish"
            interpretation = f"TRIX = {current:.4f} — positive momentum, bullish trend"
        elif current < -0.05:
            signal = "bearish"
            interpretation = f"TRIX = {current:.4f} — negative momentum, bearish trend"
        else:
            signal = "neutral"
            interpretation = f"TRIX = {current:.4f} — near zero, trend unclear"

        return {
            "indicator_name": f"TRIX({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"TRIX({period})"}


def ultimate_oscillator(df: pd.DataFrame, s: int = 7, m: int = 14, l: int = 28) -> dict[str, Any]:
    """
    Ultimate Oscillator — combines short, medium, and long-term momentum.
    < 30 = oversold (bullish reversal candidate), > 70 = overbought (bearish reversal candidate).
    Uses multiple timeframes to reduce false signals. Look for divergences for best signals.
    """
    try:
        if len(df) < l + 1:
            return {"error": f"insufficient data: need >= {l + 1} rows", "indicator_name": f"UO({s},{m},{l})"}

        indicator = ta.momentum.UltimateOscillator(
            high=df["High"], low=df["Low"], close=df["Close"],
            window1=s, window2=m, window3=l
        )
        values = indicator.ultimate_oscillator()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"UO({s},{m},{l})"}

        if current < 30:
            signal = "bullish"
            interpretation = f"Ultimate Oscillator = {current:.1f} — oversold across timeframes, potential bullish reversal"
        elif current > 70:
            signal = "bearish"
            interpretation = f"Ultimate Oscillator = {current:.1f} — overbought across timeframes, potential bearish reversal"
        elif current > 50:
            signal = "neutral"
            interpretation = f"Ultimate Oscillator = {current:.1f} — above 50, mild bullish momentum"
        else:
            signal = "neutral"
            interpretation = f"Ultimate Oscillator = {current:.1f} — below 50, mild bearish momentum"

        return {
            "indicator_name": f"UO({s},{m},{l})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"short": s, "medium": m, "long": l},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"UO({s},{m},{l})"}


def awesome_oscillator(df: pd.DataFrame) -> dict[str, Any]:
    """
    Awesome Oscillator — difference between 5-period and 34-period SMA of median price.
    AO > 0 = bullish momentum, AO < 0 = bearish momentum.
    Zero-line crossovers and twin peaks/saucers patterns signal entries.
    Color changes (green/red) show momentum direction changes.
    """
    try:
        if len(df) < 34:
            return {"error": "insufficient data: need >= 34 rows", "indicator_name": "AO"}

        indicator = ta.momentum.AwesomeOscillatorIndicator(high=df["High"], low=df["Low"])
        values = indicator.awesome_oscillator()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": "AO"}

        # Check trend direction
        prev = _get_current_value(values.iloc[:-1]) if len(values) > 1 else current

        if current > 0:
            if current > prev:
                signal = "bullish"
                interpretation = f"AO = {current:.2f} — positive and rising, strong bullish momentum"
            else:
                signal = "neutral"
                interpretation = f"AO = {current:.2f} — positive but falling, weakening bullish momentum"
        else:
            if current < prev:
                signal = "bearish"
                interpretation = f"AO = {current:.2f} — negative and falling, strong bearish momentum"
            else:
                signal = "neutral"
                interpretation = f"AO = {current:.2f} — negative but rising, weakening bearish momentum"

        return {
            "indicator_name": "AO",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"fast": 5, "slow": 34},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": "AO"}
