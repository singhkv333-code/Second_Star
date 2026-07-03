"""
Volatility Indicators for Pivot.

These indicators measure the degree of price variation over time.
Use them to assess risk, set stop-losses, and identify potential breakouts.
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


def bollinger(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> dict[str, Any]:
    """
    Bollinger Bands — dynamic support/resistance based on volatility.
    Price near upper band = overbought, near lower band = oversold.
    Band squeeze (narrow width) often precedes breakout. %B shows position within bands.
    Bandwidth measures volatility: low bandwidth = consolidation, high = trending.
    """
    try:
        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"Bollinger({period},{std})"}

        indicator = ta.volatility.BollingerBands(close=df["Close"], window=period, window_dev=std)
        upper = indicator.bollinger_hband()
        middle = indicator.bollinger_mavg()
        lower = indicator.bollinger_lband()
        pband = indicator.bollinger_pband()  # %B
        wband = indicator.bollinger_wband()  # Bandwidth

        upper_current = _get_current_value(upper)
        middle_current = _get_current_value(middle)
        lower_current = _get_current_value(lower)
        pband_current = _get_current_value(pband)
        wband_current = _get_current_value(wband)

        if upper_current is None or lower_current is None:
            return {"error": "all values are NaN", "indicator_name": f"Bollinger({period},{std})"}

        close_price = float(df["Close"].iloc[-1])

        if pband_current is not None:
            if pband_current > 1:
                signal = "bearish"
                interpretation = f"Price above upper band (%B={pband_current:.2f}) — overbought, potential reversal"
            elif pband_current < 0:
                signal = "bullish"
                interpretation = f"Price below lower band (%B={pband_current:.2f}) — oversold, potential reversal"
            elif pband_current > 0.8:
                signal = "neutral"
                interpretation = f"Price near upper band (%B={pband_current:.2f}) — approaching overbought"
            elif pband_current < 0.2:
                signal = "neutral"
                interpretation = f"Price near lower band (%B={pband_current:.2f}) — approaching oversold"
            else:
                signal = "neutral"
                interpretation = f"Price in middle of bands (%B={pband_current:.2f}) — neutral"
        else:
            signal = "neutral"
            interpretation = f"Bollinger Bands: Upper={upper_current:.2f}, Lower={lower_current:.2f}"

        return {
            "indicator_name": f"Bollinger({period},{std})",
            "values": {
                "upper": _series_to_list(upper),
                "middle": _series_to_list(middle),
                "lower": _series_to_list(lower),
                "pband": _series_to_list(pband),
                "bandwidth": _series_to_list(wband),
            },
            "current_value": {
                "upper": upper_current,
                "middle": middle_current,
                "lower": lower_current,
                "pband": pband_current,
                "bandwidth": wband_current,
            },
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period, "std": std},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"Bollinger({period},{std})"}


def atr(df: pd.DataFrame, period: int = 14) -> dict[str, Any]:
    """
    ATR (Average True Range) — measures market volatility without direction.
    Higher ATR = more volatile, useful for position sizing and stop-loss placement.
    ATR expanding = increasing volatility, ATR contracting = decreasing volatility.
    Common stop-loss: 1.5-3x ATR from entry. No directional signal.
    """
    try:
        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"ATR({period})"}

        indicator = ta.volatility.AverageTrueRange(
            high=df["High"], low=df["Low"], close=df["Close"], window=period
        )
        values = indicator.average_true_range()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"ATR({period})"}

        close_price = float(df["Close"].iloc[-1])
        atr_pct = (current / close_price) * 100 if close_price > 0 else 0

        # Compare to recent ATR for context
        recent_atr = values.iloc[-period:].mean() if len(values) >= period else current

        if current > recent_atr * 1.2:
            interpretation = f"ATR = {current:.2f} ({atr_pct:.2f}% of price) — elevated volatility, expanding"
        elif current < recent_atr * 0.8:
            interpretation = f"ATR = {current:.2f} ({atr_pct:.2f}% of price) — low volatility, contracting"
        else:
            interpretation = f"ATR = {current:.2f} ({atr_pct:.2f}% of price) — normal volatility"

        return {
            "indicator_name": f"ATR({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": "neutral",
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"ATR({period})"}


def keltner(df: pd.DataFrame, period: int = 20, atr_mult: float = 2.0) -> dict[str, Any]:
    """
    Keltner Channels — ATR-based volatility bands around EMA.
    Similar to Bollinger but uses ATR instead of standard deviation.
    Price breakout above upper channel = strong bullish momentum.
    Price breakout below lower channel = strong bearish momentum.
    Squeeze (Bollinger inside Keltner) often precedes big moves.
    """
    try:
        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"Keltner({period},{atr_mult})"}

        indicator = ta.volatility.KeltnerChannel(
            high=df["High"], low=df["Low"], close=df["Close"],
            window=period, window_atr=period
        )
        upper = indicator.keltner_channel_hband()
        middle = indicator.keltner_channel_mband()
        lower = indicator.keltner_channel_lband()
        pband = indicator.keltner_channel_pband()

        upper_current = _get_current_value(upper)
        middle_current = _get_current_value(middle)
        lower_current = _get_current_value(lower)
        pband_current = _get_current_value(pband)

        if upper_current is None or lower_current is None:
            return {"error": "all values are NaN", "indicator_name": f"Keltner({period},{atr_mult})"}

        close_price = float(df["Close"].iloc[-1])

        if close_price > upper_current:
            signal = "bullish"
            interpretation = f"Price above upper Keltner channel — strong bullish breakout"
        elif close_price < lower_current:
            signal = "bearish"
            interpretation = f"Price below lower Keltner channel — strong bearish breakout"
        elif pband_current is not None and pband_current > 0.8:
            signal = "neutral"
            interpretation = f"Price near upper channel — testing resistance"
        elif pband_current is not None and pband_current < 0.2:
            signal = "neutral"
            interpretation = f"Price near lower channel — testing support"
        else:
            signal = "neutral"
            interpretation = f"Price within Keltner channels — no breakout"

        return {
            "indicator_name": f"Keltner({period},{atr_mult})",
            "values": {
                "upper": _series_to_list(upper),
                "middle": _series_to_list(middle),
                "lower": _series_to_list(lower),
            },
            "current_value": {"upper": upper_current, "middle": middle_current, "lower": lower_current},
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period, "atr_mult": atr_mult},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"Keltner({period},{atr_mult})"}


def donchian(df: pd.DataFrame, period: int = 20) -> dict[str, Any]:
    """
    Donchian Channels — highest high and lowest low over a period.
    Breakout above upper channel = bullish, below lower = bearish.
    Channel width indicates volatility. Used in turtle trading system.
    Middle line is average of upper and lower, shows trend direction.
    """
    try:
        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"Donchian({period})"}

        indicator = ta.volatility.DonchianChannel(
            high=df["High"], low=df["Low"], close=df["Close"], window=period
        )
        upper = indicator.donchian_channel_hband()
        middle = indicator.donchian_channel_mband()
        lower = indicator.donchian_channel_lband()
        width = indicator.donchian_channel_wband()
        pband = indicator.donchian_channel_pband()

        upper_current = _get_current_value(upper)
        middle_current = _get_current_value(middle)
        lower_current = _get_current_value(lower)
        width_current = _get_current_value(width)

        if upper_current is None or lower_current is None:
            return {"error": "all values are NaN", "indicator_name": f"Donchian({period})"}

        close_price = float(df["Close"].iloc[-1])

        if close_price >= upper_current * 0.99:
            signal = "bullish"
            interpretation = f"Price at {period}-period high — bullish breakout"
        elif close_price <= lower_current * 1.01:
            signal = "bearish"
            interpretation = f"Price at {period}-period low — bearish breakout"
        else:
            signal = "neutral"
            interpretation = f"Donchian: Upper={upper_current:.2f}, Lower={lower_current:.2f}, Width={width_current:.2f}"

        return {
            "indicator_name": f"Donchian({period})",
            "values": {
                "upper": _series_to_list(upper),
                "middle": _series_to_list(middle),
                "lower": _series_to_list(lower),
                "width": _series_to_list(width),
            },
            "current_value": {"upper": upper_current, "middle": middle_current, "lower": lower_current, "width": width_current},
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"Donchian({period})"}


def rolling_std(df: pd.DataFrame, period: int = 20) -> dict[str, Any]:
    """
    Rolling Standard Deviation — measures price dispersion over a period.
    Higher values = more volatile. Low values often precede breakouts.
    Used as input for other indicators (Bollinger) and for risk assessment.
    """
    try:
        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"Std({period})"}

        values = df["Close"].rolling(window=period).std()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"Std({period})"}

        close_price = float(df["Close"].iloc[-1])
        std_pct = (current / close_price) * 100 if close_price > 0 else 0

        # Compare to recent std
        avg_std = float(values.dropna().mean())
        if current > avg_std * 1.3:
            interpretation = f"Std = {current:.2f} ({std_pct:.2f}% of price) — high volatility"
        elif current < avg_std * 0.7:
            interpretation = f"Std = {current:.2f} ({std_pct:.2f}% of price) — low volatility, potential breakout setup"
        else:
            interpretation = f"Std = {current:.2f} ({std_pct:.2f}% of price) — normal volatility"

        return {
            "indicator_name": f"Std({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": "neutral",
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"Std({period})"}


def historical_volatility(df: pd.DataFrame, period: int = 20, annualised: bool = True) -> dict[str, Any]:
    """
    Historical Volatility — standard deviation of log returns, optionally annualised.
    Measures how much price has varied historically. Higher = riskier asset.
    Annualised HV allows comparison across assets. Low HV often precedes big moves.
    """
    try:
        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"HV({period})"}

        log_returns = np.log(df["Close"] / df["Close"].shift(1))
        rolling_std = log_returns.rolling(window=period).std()

        if annualised:
            # Assuming 252 trading days
            values = rolling_std * np.sqrt(252) * 100
        else:
            values = rolling_std * 100

        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"HV({period})"}

        suffix = "annualised" if annualised else "period"

        if current > 40:
            interpretation = f"HV = {current:.1f}% ({suffix}) — very high volatility"
        elif current > 25:
            interpretation = f"HV = {current:.1f}% ({suffix}) — elevated volatility"
        elif current > 15:
            interpretation = f"HV = {current:.1f}% ({suffix}) — moderate volatility"
        else:
            interpretation = f"HV = {current:.1f}% ({suffix}) — low volatility"

        return {
            "indicator_name": f"HV({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": "neutral",
            "interpretation": interpretation,
            "params": {"period": period, "annualised": annualised},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"HV({period})"}


def chaikin_volatility(df: pd.DataFrame, period: int = 10) -> dict[str, Any]:
    """
    Chaikin Volatility — measures volatility using high-low range expansion/contraction.
    Rising CV = increasing volatility (often at tops), falling = decreasing (often at bottoms).
    Different from ATR as it focuses on range changes rather than absolute range.
    """
    try:
        if len(df) < period * 2:
            return {"error": f"insufficient data: need >= {period * 2} rows", "indicator_name": f"ChaikinVol({period})"}

        high_low = df["High"] - df["Low"]
        ema_hl = high_low.ewm(span=period, adjust=False).mean()
        chaikin_vol = ((ema_hl - ema_hl.shift(period)) / ema_hl.shift(period)) * 100

        current = _get_current_value(chaikin_vol)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"ChaikinVol({period})"}

        if current > 25:
            interpretation = f"Chaikin Volatility = {current:.1f}% — range expanding sharply"
        elif current > 0:
            interpretation = f"Chaikin Volatility = {current:.1f}% — range expanding"
        elif current > -25:
            interpretation = f"Chaikin Volatility = {current:.1f}% — range contracting"
        else:
            interpretation = f"Chaikin Volatility = {current:.1f}% — range contracting sharply"

        return {
            "indicator_name": f"ChaikinVol({period})",
            "values": _series_to_list(chaikin_vol),
            "current_value": current,
            "signal": "neutral",
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"ChaikinVol({period})"}


def volatility_stop(df: pd.DataFrame, period: int = 14, atr_mult: float = 3.0) -> dict[str, Any]:
    """
    Volatility Stop — dynamic trailing stop based on ATR.
    In uptrend: stop below price by ATR multiple. In downtrend: stop above.
    Use for position management and trend-following exits.
    Stop level adjusts to market volatility automatically.
    """
    try:
        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"VolStop({period},{atr_mult})"}

        atr_ind = ta.volatility.AverageTrueRange(
            high=df["High"], low=df["Low"], close=df["Close"], window=period
        )
        atr_values = atr_ind.average_true_range()

        stop_up = df["Close"] - (atr_mult * atr_values)  # Long stop
        stop_down = df["Close"] + (atr_mult * atr_values)  # Short stop

        # Determine trend based on price vs stops
        close = df["Close"]
        trend = pd.Series(index=df.index, dtype=int)
        vol_stop = pd.Series(index=df.index, dtype=float)

        for i in range(period, len(df)):
            if i == period:
                trend.iloc[i] = 1 if close.iloc[i] > close.iloc[i-1] else -1
                vol_stop.iloc[i] = stop_up.iloc[i] if trend.iloc[i] == 1 else stop_down.iloc[i]
            else:
                if trend.iloc[i-1] == 1:
                    if close.iloc[i] < vol_stop.iloc[i-1]:
                        trend.iloc[i] = -1
                        vol_stop.iloc[i] = stop_down.iloc[i]
                    else:
                        trend.iloc[i] = 1
                        vol_stop.iloc[i] = max(stop_up.iloc[i], vol_stop.iloc[i-1])
                else:
                    if close.iloc[i] > vol_stop.iloc[i-1]:
                        trend.iloc[i] = 1
                        vol_stop.iloc[i] = stop_up.iloc[i]
                    else:
                        trend.iloc[i] = -1
                        vol_stop.iloc[i] = min(stop_down.iloc[i], vol_stop.iloc[i-1])

        stop_current = _get_current_value(vol_stop)
        trend_current = trend.dropna().iloc[-1] if len(trend.dropna()) > 0 else 0

        if stop_current is None:
            return {"error": "all values are NaN", "indicator_name": f"VolStop({period},{atr_mult})"}

        close_price = float(df["Close"].iloc[-1])
        distance = abs(close_price - stop_current)
        atr_current = _get_current_value(atr_values)

        if trend_current == 1:
            signal = "bullish"
            interpretation = f"Volatility Stop = {stop_current:.2f} (long stop), {distance:.2f} from price — uptrend"
        else:
            signal = "bearish"
            interpretation = f"Volatility Stop = {stop_current:.2f} (short stop), {distance:.2f} from price — downtrend"

        return {
            "indicator_name": f"VolStop({period},{atr_mult})",
            "values": {
                "stop": _series_to_list(vol_stop),
                "trend": _series_to_list(trend),
            },
            "current_value": {"stop": stop_current, "trend": int(trend_current), "atr": atr_current},
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period, "atr_mult": atr_mult},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"VolStop({period},{atr_mult})"}
