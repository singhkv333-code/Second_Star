"""
Volume Indicators for Pivot.

These indicators analyze trading volume to confirm price moves and identify divergences.
Volume confirms trend strength: rising prices with rising volume = strong trend.
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


def volume_raw(df: pd.DataFrame) -> dict[str, Any]:
    """
    Raw Volume — trading volume for each period.
    High volume on price moves confirms trend strength.
    Volume spikes often occur at tops/bottoms and breakouts.
    Compare to average volume for context.
    """
    try:
        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": "Volume"}

        values = df["Volume"]
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": "Volume"}

        avg_volume = float(values.iloc[-20:].mean()) if len(values) >= 20 else float(values.mean())
        ratio = current / avg_volume if avg_volume > 0 else 1

        if ratio > 2:
            interpretation = f"Volume = {current:,.0f} ({ratio:.1f}x average) — very high volume, significant activity"
        elif ratio > 1.5:
            interpretation = f"Volume = {current:,.0f} ({ratio:.1f}x average) — above average volume"
        elif ratio < 0.5:
            interpretation = f"Volume = {current:,.0f} ({ratio:.1f}x average) — below average volume, low interest"
        else:
            interpretation = f"Volume = {current:,.0f} ({ratio:.1f}x average) — normal volume"

        return {
            "indicator_name": "Volume",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": "neutral",
            "interpretation": interpretation,
            "params": {},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": "Volume"}


def volume_ma(df: pd.DataFrame, period: int = 20) -> dict[str, Any]:
    """
    Volume Moving Average — smoothed volume for trend comparison.
    Current volume above VMA = higher than average interest.
    Rising VMA during uptrend = strong buyer participation.
    Falling VMA during price rise = potential weakness.
    """
    try:
        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": f"VolumeMA({period})"}

        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"VolumeMA({period})"}

        values = df["Volume"].rolling(window=period).mean()
        current = _get_current_value(values)
        current_vol = float(df["Volume"].iloc[-1])

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"VolumeMA({period})"}

        ratio = current_vol / current if current > 0 else 1

        if ratio > 1.5:
            interpretation = f"VolumeMA({period}) = {current:,.0f}, current {ratio:.1f}x — volume surge"
        elif ratio > 1:
            interpretation = f"VolumeMA({period}) = {current:,.0f}, current {ratio:.1f}x — above average"
        else:
            interpretation = f"VolumeMA({period}) = {current:,.0f}, current {ratio:.1f}x — below average"

        return {
            "indicator_name": f"VolumeMA({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": "neutral",
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"VolumeMA({period})"}


def obv(df: pd.DataFrame) -> dict[str, Any]:
    """
    OBV (On-Balance Volume) — cumulative volume based on price direction.
    Rising OBV confirms uptrend, falling OBV confirms downtrend.
    OBV divergence from price often precedes reversals.
    Look for OBV breaking out before price for early signals.
    """
    try:
        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": "OBV"}

        if len(df) < 2:
            return {"error": "insufficient data: need >= 2 rows", "indicator_name": "OBV"}

        indicator = ta.volume.OnBalanceVolumeIndicator(close=df["Close"], volume=df["Volume"])
        values = indicator.on_balance_volume()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": "OBV"}

        # Check OBV trend vs price trend
        obv_change = values.iloc[-1] - values.iloc[-5] if len(values) >= 5 else 0
        price_change = df["Close"].iloc[-1] - df["Close"].iloc[-5] if len(df) >= 5 else 0

        if obv_change > 0 and price_change > 0:
            signal = "bullish"
            interpretation = f"OBV = {current:,.0f} — rising with price, uptrend confirmed"
        elif obv_change < 0 and price_change < 0:
            signal = "bearish"
            interpretation = f"OBV = {current:,.0f} — falling with price, downtrend confirmed"
        elif obv_change > 0 and price_change < 0:
            signal = "bullish"
            interpretation = f"OBV = {current:,.0f} — bullish divergence, accumulation"
        elif obv_change < 0 and price_change > 0:
            signal = "bearish"
            interpretation = f"OBV = {current:,.0f} — bearish divergence, distribution"
        else:
            signal = "neutral"
            interpretation = f"OBV = {current:,.0f} — no clear pattern"

        return {
            "indicator_name": "OBV",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": "OBV"}


def vwap(df: pd.DataFrame) -> dict[str, Any]:
    """
    VWAP (Volume Weighted Average Price) — average price weighted by volume.
    Price above VWAP = bullish intraday bias, below = bearish.
    Institutional benchmark for execution quality. Resets daily.
    Works best for intraday analysis.
    """
    try:
        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": "VWAP"}

        if len(df) < 1:
            return {"error": "insufficient data: need >= 1 row", "indicator_name": "VWAP"}

        indicator = ta.volume.VolumeWeightedAveragePrice(
            high=df["High"], low=df["Low"], close=df["Close"], volume=df["Volume"]
        )
        values = indicator.volume_weighted_average_price()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": "VWAP"}

        close_price = float(df["Close"].iloc[-1])
        diff_pct = ((close_price - current) / current) * 100 if current > 0 else 0

        if close_price > current * 1.005:
            signal = "bullish"
            interpretation = f"VWAP = {current:.2f}, price {diff_pct:.2f}% above — bullish bias"
        elif close_price < current * 0.995:
            signal = "bearish"
            interpretation = f"VWAP = {current:.2f}, price {diff_pct:.2f}% below — bearish bias"
        else:
            signal = "neutral"
            interpretation = f"VWAP = {current:.2f}, price near VWAP — neutral"

        return {
            "indicator_name": "VWAP",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": "VWAP"}


def acc_dist(df: pd.DataFrame) -> dict[str, Any]:
    """
    Accumulation/Distribution Line — tracks money flow based on close position in range.
    Rising A/D = accumulation (buying), falling = distribution (selling).
    A/D divergence from price signals potential reversal.
    More sensitive than OBV to intrabar price action.
    """
    try:
        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": "A/D"}

        if len(df) < 2:
            return {"error": "insufficient data: need >= 2 rows", "indicator_name": "A/D"}

        indicator = ta.volume.AccDistIndexIndicator(
            high=df["High"], low=df["Low"], close=df["Close"], volume=df["Volume"]
        )
        values = indicator.acc_dist_index()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": "A/D"}

        # Check A/D trend
        ad_change = values.iloc[-1] - values.iloc[-5] if len(values) >= 5 else 0
        price_change = df["Close"].iloc[-1] - df["Close"].iloc[-5] if len(df) >= 5 else 0

        if ad_change > 0 and price_change > 0:
            signal = "bullish"
            interpretation = f"A/D = {current:,.0f} — accumulation, confirms uptrend"
        elif ad_change < 0 and price_change < 0:
            signal = "bearish"
            interpretation = f"A/D = {current:,.0f} — distribution, confirms downtrend"
        elif ad_change > 0 and price_change < 0:
            signal = "bullish"
            interpretation = f"A/D = {current:,.0f} — bullish divergence, smart money buying"
        elif ad_change < 0 and price_change > 0:
            signal = "bearish"
            interpretation = f"A/D = {current:,.0f} — bearish divergence, smart money selling"
        else:
            signal = "neutral"
            interpretation = f"A/D = {current:,.0f} — no clear signal"

        return {
            "indicator_name": "A/D",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": "A/D"}


def chaikin_mf(df: pd.DataFrame, period: int = 20) -> dict[str, Any]:
    """
    Chaikin Money Flow — measures buying/selling pressure over a period.
    CMF > 0 = buying pressure (accumulation), CMF < 0 = selling pressure (distribution).
    CMF > 0.25 = strong buying, CMF < -0.25 = strong selling.
    Zero-line crossovers signal shifts in money flow.
    """
    try:
        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": f"CMF({period})"}

        if len(df) < period:
            return {"error": f"insufficient data: need >= {period} rows", "indicator_name": f"CMF({period})"}

        indicator = ta.volume.ChaikinMoneyFlowIndicator(
            high=df["High"], low=df["Low"], close=df["Close"], volume=df["Volume"], window=period
        )
        values = indicator.chaikin_money_flow()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"CMF({period})"}

        if current > 0.25:
            signal = "bullish"
            interpretation = f"CMF = {current:.3f} — strong buying pressure"
        elif current > 0.05:
            signal = "bullish"
            interpretation = f"CMF = {current:.3f} — moderate buying pressure"
        elif current < -0.25:
            signal = "bearish"
            interpretation = f"CMF = {current:.3f} — strong selling pressure"
        elif current < -0.05:
            signal = "bearish"
            interpretation = f"CMF = {current:.3f} — moderate selling pressure"
        else:
            signal = "neutral"
            interpretation = f"CMF = {current:.3f} — neutral money flow"

        return {
            "indicator_name": f"CMF({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"CMF({period})"}


def volume_roc(df: pd.DataFrame, period: int = 12) -> dict[str, Any]:
    """
    Volume Rate of Change — percentage change in volume over a period.
    High positive VROC = volume surge, potential breakout or climax.
    Negative VROC = declining interest. Use with price to confirm moves.
    """
    try:
        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": f"VROC({period})"}

        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"VROC({period})"}

        current_vol = df["Volume"].iloc[-1]
        past_vol = df["Volume"].iloc[-period-1]
        vroc_value = ((current_vol - past_vol) / past_vol) * 100 if past_vol > 0 else 0

        values = pd.Series(index=df.index, dtype=float)
        for i in range(period, len(df)):
            past = df["Volume"].iloc[i - period]
            curr = df["Volume"].iloc[i]
            values.iloc[i] = ((curr - past) / past) * 100 if past > 0 else 0

        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"VROC({period})"}

        if current > 100:
            interpretation = f"VROC = {current:.1f}% — volume more than doubled, significant event"
        elif current > 50:
            interpretation = f"VROC = {current:.1f}% — volume surge"
        elif current > 0:
            interpretation = f"VROC = {current:.1f}% — volume increasing"
        elif current > -50:
            interpretation = f"VROC = {current:.1f}% — volume declining"
        else:
            interpretation = f"VROC = {current:.1f}% — volume dropped significantly"

        return {
            "indicator_name": f"VROC({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": "neutral",
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"VROC({period})"}


def ease_of_movement(df: pd.DataFrame, period: int = 14) -> dict[str, Any]:
    """
    Ease of Movement — relates price change to volume needed for that change.
    High positive EMV = price rising easily on low volume. High negative = falling easily.
    Values near zero = price movement requires significant volume (resistance).
    Zero-line crossovers can signal trend changes.
    """
    try:
        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": f"EMV({period})"}

        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"EMV({period})"}

        indicator = ta.volume.EaseOfMovementIndicator(
            high=df["High"], low=df["Low"], volume=df["Volume"], window=period
        )
        values = indicator.sma_ease_of_movement()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"EMV({period})"}

        if current > 0.5:
            signal = "bullish"
            interpretation = f"EMV = {current:.4f} — price rising easily, bullish"
        elif current > 0:
            signal = "neutral"
            interpretation = f"EMV = {current:.4f} — slight ease of upward movement"
        elif current > -0.5:
            signal = "neutral"
            interpretation = f"EMV = {current:.4f} — slight ease of downward movement"
        else:
            signal = "bearish"
            interpretation = f"EMV = {current:.4f} — price falling easily, bearish"

        return {
            "indicator_name": f"EMV({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"EMV({period})"}


def vpt(df: pd.DataFrame) -> dict[str, Any]:
    """
    VPT (Volume Price Trend) — cumulative volume weighted by percentage price change.
    Rising VPT confirms uptrend, falling confirms downtrend.
    Similar to OBV but more sensitive to price magnitude.
    VPT divergence from price signals potential reversal.
    """
    try:
        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": "VPT"}

        if len(df) < 2:
            return {"error": "insufficient data: need >= 2 rows", "indicator_name": "VPT"}

        indicator = ta.volume.VolumePriceTrendIndicator(close=df["Close"], volume=df["Volume"])
        values = indicator.volume_price_trend()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": "VPT"}

        # Check VPT trend
        vpt_change = values.iloc[-1] - values.iloc[-5] if len(values) >= 5 else 0
        price_change = df["Close"].iloc[-1] - df["Close"].iloc[-5] if len(df) >= 5 else 0

        if vpt_change > 0 and price_change > 0:
            signal = "bullish"
            interpretation = f"VPT = {current:,.0f} — rising with price, confirms uptrend"
        elif vpt_change < 0 and price_change < 0:
            signal = "bearish"
            interpretation = f"VPT = {current:,.0f} — falling with price, confirms downtrend"
        elif vpt_change > 0 and price_change < 0:
            signal = "bullish"
            interpretation = f"VPT = {current:,.0f} — bullish divergence"
        elif vpt_change < 0 and price_change > 0:
            signal = "bearish"
            interpretation = f"VPT = {current:,.0f} — bearish divergence"
        else:
            signal = "neutral"
            interpretation = f"VPT = {current:,.0f} — no clear signal"

        return {
            "indicator_name": "VPT",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": "VPT"}


def force_index(df: pd.DataFrame, period: int = 13) -> dict[str, Any]:
    """
    Force Index — combines price change and volume to measure buying/selling force.
    Positive FI = buyers in control, negative = sellers in control.
    Extreme values indicate strong conviction. Zero-line crossovers signal trend changes.
    13-period EMA smoothed for swing trading.
    """
    try:
        if "Volume" not in df.columns:
            return {"error": "Volume column required", "indicator_name": f"FI({period})"}

        if len(df) < period + 1:
            return {"error": f"insufficient data: need >= {period + 1} rows", "indicator_name": f"FI({period})"}

        indicator = ta.volume.ForceIndexIndicator(close=df["Close"], volume=df["Volume"], window=period)
        values = indicator.force_index()
        current = _get_current_value(values)

        if current is None:
            return {"error": "all values are NaN", "indicator_name": f"FI({period})"}

        # Normalize by recent range for interpretation
        fi_std = values.dropna().std()
        normalized = current / fi_std if fi_std > 0 else 0

        if normalized > 1.5:
            signal = "bullish"
            interpretation = f"Force Index = {current:,.0f} — strong buying force"
        elif normalized > 0.5:
            signal = "bullish"
            interpretation = f"Force Index = {current:,.0f} — buyers in control"
        elif normalized < -1.5:
            signal = "bearish"
            interpretation = f"Force Index = {current:,.0f} — strong selling force"
        elif normalized < -0.5:
            signal = "bearish"
            interpretation = f"Force Index = {current:,.0f} — sellers in control"
        else:
            signal = "neutral"
            interpretation = f"Force Index = {current:,.0f} — balanced forces"

        return {
            "indicator_name": f"FI({period})",
            "values": _series_to_list(values),
            "current_value": current,
            "signal": signal,
            "interpretation": interpretation,
            "params": {"period": period},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"FI({period})"}
