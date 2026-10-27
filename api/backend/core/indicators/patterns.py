"""
Pattern Detection for Pivot.

Candlestick patterns and technical levels detected without TA-Lib.
Uses deterministic pandas math on body/wick ratios.
"""

from typing import Any
import pandas as pd
import numpy as np


def _get_current_value(series: pd.Series) -> float | None:
    """Extract last non-null value from a series."""
    valid = series.dropna()
    if len(valid) == 0:
        return None
    return float(valid.iloc[-1])


def _series_to_list(series: pd.Series) -> list[float]:
    """Convert pandas Series to list, replacing NaN with None."""
    return [None if pd.isna(v) else float(v) for v in series]


def detect_candlestick_patterns(df: pd.DataFrame, lookback: int = 5) -> dict[str, Any]:
    """
    Detect common candlestick patterns in recent bars.
    Patterns detected: doji, hammer, inverted hammer, shooting star, engulfing (bull/bear),
    morning star, evening star, harami (bull/bear).
    Returns list of patterns found with index, name, and direction.
    Use patterns for reversal signals and entry timing.
    """
    try:
        if len(df) < max(3, lookback):
            return {"error": f"insufficient data: need >= {max(3, lookback)} rows", "indicator_name": "CandlePatterns"}

        patterns_found = []

        # Helper functions
        def body_size(idx):
            return abs(df["Close"].iloc[idx] - df["Open"].iloc[idx])

        def upper_wick(idx):
            return df["High"].iloc[idx] - max(df["Open"].iloc[idx], df["Close"].iloc[idx])

        def lower_wick(idx):
            return min(df["Open"].iloc[idx], df["Close"].iloc[idx]) - df["Low"].iloc[idx]

        def total_range(idx):
            return df["High"].iloc[idx] - df["Low"].iloc[idx]

        def is_bullish(idx):
            return df["Close"].iloc[idx] > df["Open"].iloc[idx]

        def is_bearish(idx):
            return df["Close"].iloc[idx] < df["Open"].iloc[idx]

        # Average body size for comparison
        recent_bodies = [body_size(i) for i in range(-lookback, 0)]
        avg_body = np.mean(recent_bodies) if recent_bodies else 1

        # Scan recent bars
        for i in range(-lookback, 0):
            idx = len(df) + i
            rng = total_range(i)
            if rng == 0:
                continue

            body = body_size(i)
            u_wick = upper_wick(i)
            l_wick = lower_wick(i)

            # Doji: very small body relative to range
            if body < rng * 0.1:
                patterns_found.append({
                    "name": "doji",
                    "idx": idx,
                    "direction": "neutral",
                    "description": "Indecision candle, small body"
                })
                continue

            # Hammer: small body at top, long lower wick, minimal upper wick
            if l_wick > body * 2 and u_wick < body * 0.5 and body < rng * 0.4:
                patterns_found.append({
                    "name": "hammer",
                    "idx": idx,
                    "direction": "bullish",
                    "description": "Bullish reversal at support"
                })

            # Inverted Hammer: small body at bottom, long upper wick
            if u_wick > body * 2 and l_wick < body * 0.5 and body < rng * 0.4:
                if is_bearish(i) or (i < -1 and is_bearish(i-1)):
                    patterns_found.append({
                        "name": "inverted_hammer",
                        "idx": idx,
                        "direction": "bullish",
                        "description": "Potential bullish reversal"
                    })

            # Shooting Star: small body at bottom, long upper wick (after uptrend)
            if u_wick > body * 2 and l_wick < body * 0.5 and body < rng * 0.4:
                if i >= -lookback + 1:
                    prev_close = df["Close"].iloc[i-1]
                    if df["Open"].iloc[i] > prev_close:
                        patterns_found.append({
                            "name": "shooting_star",
                            "idx": idx,
                            "direction": "bearish",
                            "description": "Bearish reversal at resistance"
                        })

            # Engulfing patterns (need previous bar)
            if i >= -lookback + 1:
                prev_body = body_size(i-1)
                curr_body = body

                # Bullish Engulfing
                if is_bearish(i-1) and is_bullish(i):
                    if df["Open"].iloc[i] < df["Close"].iloc[i-1] and df["Close"].iloc[i] > df["Open"].iloc[i-1]:
                        if curr_body > prev_body:
                            patterns_found.append({
                                "name": "bullish_engulfing",
                                "idx": idx,
                                "direction": "bullish",
                                "description": "Strong bullish reversal"
                            })

                # Bearish Engulfing
                if is_bullish(i-1) and is_bearish(i):
                    if df["Open"].iloc[i] > df["Close"].iloc[i-1] and df["Close"].iloc[i] < df["Open"].iloc[i-1]:
                        if curr_body > prev_body:
                            patterns_found.append({
                                "name": "bearish_engulfing",
                                "idx": idx,
                                "direction": "bearish",
                                "description": "Strong bearish reversal"
                            })

            # Morning Star (3-bar pattern)
            if i >= -lookback + 2:
                # First: large bearish
                first_bearish = is_bearish(i-2) and body_size(i-2) > avg_body * 0.8
                # Second: small body (doji-like)
                second_small = body_size(i-1) < avg_body * 0.5
                # Third: large bullish
                third_bullish = is_bullish(i) and body_size(i) > avg_body * 0.8

                if first_bearish and second_small and third_bullish:
                    if df["Close"].iloc[i] > (df["Open"].iloc[i-2] + df["Close"].iloc[i-2]) / 2:
                        patterns_found.append({
                            "name": "morning_star",
                            "idx": idx,
                            "direction": "bullish",
                            "description": "Three-bar bullish reversal"
                        })

            # Evening Star (3-bar pattern)
            if i >= -lookback + 2:
                # First: large bullish
                first_bullish = is_bullish(i-2) and body_size(i-2) > avg_body * 0.8
                # Second: small body
                second_small = body_size(i-1) < avg_body * 0.5
                # Third: large bearish
                third_bearish = is_bearish(i) and body_size(i) > avg_body * 0.8

                if first_bullish and second_small and third_bearish:
                    if df["Close"].iloc[i] < (df["Open"].iloc[i-2] + df["Close"].iloc[i-2]) / 2:
                        patterns_found.append({
                            "name": "evening_star",
                            "idx": idx,
                            "direction": "bearish",
                            "description": "Three-bar bearish reversal"
                        })

        # Determine overall signal
        bullish_count = sum(1 for p in patterns_found if p["direction"] == "bullish")
        bearish_count = sum(1 for p in patterns_found if p["direction"] == "bearish")

        if bullish_count > bearish_count:
            signal = "bullish"
            interpretation = f"Found {len(patterns_found)} patterns: {bullish_count} bullish, {bearish_count} bearish"
        elif bearish_count > bullish_count:
            signal = "bearish"
            interpretation = f"Found {len(patterns_found)} patterns: {bearish_count} bearish, {bullish_count} bullish"
        else:
            signal = "neutral"
            interpretation = f"Found {len(patterns_found)} patterns: mixed signals"

        return {
            "indicator_name": "CandlePatterns",
            "values": {"patterns_found": patterns_found},
            "current_value": len(patterns_found),
            "signal": signal,
            "interpretation": interpretation,
            "params": {"lookback": lookback},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": "CandlePatterns"}


def support_resistance_levels(df: pd.DataFrame, lookback: int = 50) -> dict[str, Any]:
    """
    Detect support and resistance levels using pivot point detection.
    Identifies local highs (resistance) and lows (support) within lookback period.
    Returns nearest support/resistance levels to current price.
    Use for entry/exit planning and stop-loss placement.
    """
    try:
        if len(df) < lookback:
            return {"error": f"insufficient data: need >= {lookback} rows", "indicator_name": "S/R Levels"}

        data = df.iloc[-lookback:]
        highs = data["High"].values
        lows = data["Low"].values
        close = float(df["Close"].iloc[-1])

        resistance_levels = []
        support_levels = []

        # Find local maxima (resistance) and minima (support)
        window = 5  # Bars on each side to confirm pivot

        for i in range(window, len(highs) - window):
            # Check if this is a local high
            if highs[i] == max(highs[i-window:i+window+1]):
                resistance_levels.append(highs[i])

            # Check if this is a local low
            if lows[i] == min(lows[i-window:i+window+1]):
                support_levels.append(lows[i])

        # Remove duplicates and sort
        resistance_levels = sorted(set([round(r, 2) for r in resistance_levels]), reverse=True)
        support_levels = sorted(set([round(s, 2) for s in support_levels]), reverse=True)

        # Filter levels near current price
        near_resistance = [r for r in resistance_levels if r > close][:3]
        near_support = [s for s in support_levels if s < close][:3]

        nearest_resistance = near_resistance[0] if near_resistance else None
        nearest_support = near_support[0] if near_support else None

        if nearest_resistance and nearest_support:
            r_dist = nearest_resistance - close
            s_dist = close - nearest_support
            if r_dist < s_dist:
                interpretation = f"Nearest resistance: {nearest_resistance:.2f} ({r_dist:.2f} away), support: {nearest_support:.2f}"
            else:
                interpretation = f"Nearest support: {nearest_support:.2f} ({s_dist:.2f} away), resistance: {nearest_resistance:.2f}"
        elif nearest_resistance:
            interpretation = f"Nearest resistance: {nearest_resistance:.2f}, no clear support found"
        elif nearest_support:
            interpretation = f"Nearest support: {nearest_support:.2f}, no clear resistance found"
        else:
            interpretation = "No clear support/resistance levels detected"

        return {
            "indicator_name": "S/R Levels",
            "values": {
                "resistance_levels": resistance_levels[:5],
                "support_levels": support_levels[:5],
            },
            "current_value": {
                "nearest_resistance": nearest_resistance,
                "nearest_support": nearest_support,
            },
            "signal": "neutral",
            "interpretation": interpretation,
            "params": {"lookback": lookback},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": "S/R Levels"}


def pivot_points(df: pd.DataFrame, method: str = "classic") -> dict[str, Any]:
    """
    Calculate pivot points using various methods.
    Methods: 'classic' (standard), 'fibonacci', 'camarilla'.
    Pivot is the central level; R1/R2/R3 are resistance, S1/S2/S3 are support.
    Useful for intraday trading and identifying key price levels.
    """
    try:
        if len(df) < 1:
            return {"error": "insufficient data: need >= 1 row", "indicator_name": f"Pivot({method})"}

        # Use previous bar for pivot calculation (standard practice)
        if len(df) >= 2:
            high = float(df["High"].iloc[-2])
            low = float(df["Low"].iloc[-2])
            close = float(df["Close"].iloc[-2])
        else:
            high = float(df["High"].iloc[-1])
            low = float(df["Low"].iloc[-1])
            close = float(df["Close"].iloc[-1])

        current_price = float(df["Close"].iloc[-1])
        pivot = (high + low + close) / 3

        if method == "classic":
            r1 = 2 * pivot - low
            s1 = 2 * pivot - high
            r2 = pivot + (high - low)
            s2 = pivot - (high - low)
            r3 = high + 2 * (pivot - low)
            s3 = low - 2 * (high - pivot)

        elif method == "fibonacci":
            diff = high - low
            r1 = pivot + 0.382 * diff
            s1 = pivot - 0.382 * diff
            r2 = pivot + 0.618 * diff
            s2 = pivot - 0.618 * diff
            r3 = pivot + 1.0 * diff
            s3 = pivot - 1.0 * diff

        elif method == "camarilla":
            diff = high - low
            r1 = close + diff * 1.1 / 12
            s1 = close - diff * 1.1 / 12
            r2 = close + diff * 1.1 / 6
            s2 = close - diff * 1.1 / 6
            r3 = close + diff * 1.1 / 4
            s3 = close - diff * 1.1 / 4

        else:
            return {"error": f"unknown method: {method}", "indicator_name": f"Pivot({method})"}

        # Determine current position
        if current_price > r1:
            if current_price > r2:
                interpretation = f"Price above R2 ({r2:.2f}) — strongly bullish, testing R3"
            else:
                interpretation = f"Price above R1 ({r1:.2f}) — bullish, R2 at {r2:.2f}"
        elif current_price < s1:
            if current_price < s2:
                interpretation = f"Price below S2 ({s2:.2f}) — strongly bearish, testing S3"
            else:
                interpretation = f"Price below S1 ({s1:.2f}) — bearish, S2 at {s2:.2f}"
        else:
            interpretation = f"Price between S1 ({s1:.2f}) and R1 ({r1:.2f}) — neutral zone"

        return {
            "indicator_name": f"Pivot({method})",
            "values": {
                "pivot": pivot,
                "r1": r1, "r2": r2, "r3": r3,
                "s1": s1, "s2": s2, "s3": s3,
            },
            "current_value": {
                "pivot": pivot,
                "r1": r1, "s1": s1,
            },
            "signal": "neutral",
            "interpretation": interpretation,
            "params": {"method": method},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": f"Pivot({method})"}


def fibonacci_retracements(df: pd.DataFrame, swing_lookback: int = 50) -> dict[str, Any]:
    """
    Calculate Fibonacci retracement levels from recent swing high/low.
    Levels: 23.6%, 38.2%, 50%, 61.8%, 78.6% between swing high and low.
    Use for identifying potential support/resistance during retracements.
    61.8% is the golden ratio level, often key for trend continuation.
    """
    try:
        if len(df) < swing_lookback:
            return {"error": f"insufficient data: need >= {swing_lookback} rows", "indicator_name": "Fibonacci"}

        data = df.iloc[-swing_lookback:]
        swing_high = float(data["High"].max())
        swing_low = float(data["Low"].min())
        current_price = float(df["Close"].iloc[-1])

        high_idx = data["High"].idxmax()
        low_idx = data["Low"].idxmin()

        # Determine trend direction
        if high_idx > low_idx:
            # Uptrend: retracement from high
            trend = "up"
            diff = swing_high - swing_low
            levels = {
                "0.0": swing_high,
                "23.6": swing_high - diff * 0.236,
                "38.2": swing_high - diff * 0.382,
                "50.0": swing_high - diff * 0.5,
                "61.8": swing_high - diff * 0.618,
                "78.6": swing_high - diff * 0.786,
                "100.0": swing_low,
            }
        else:
            # Downtrend: retracement from low
            trend = "down"
            diff = swing_high - swing_low
            levels = {
                "0.0": swing_low,
                "23.6": swing_low + diff * 0.236,
                "38.2": swing_low + diff * 0.382,
                "50.0": swing_low + diff * 0.5,
                "61.8": swing_low + diff * 0.618,
                "78.6": swing_low + diff * 0.786,
                "100.0": swing_high,
            }

        # Find current level
        sorted_levels = sorted(levels.items(), key=lambda x: x[1])
        current_level = "below all"
        for i, (name, value) in enumerate(sorted_levels):
            if current_price < value:
                if i > 0:
                    current_level = f"between {sorted_levels[i-1][0]}% and {name}%"
                else:
                    current_level = f"below {name}%"
                break
        else:
            current_level = f"above {sorted_levels[-1][0]}%"

        if trend == "up":
            interpretation = f"Uptrend retracement: price at {current_level} level, key support at 61.8% ({levels['61.8']:.2f})"
            signal = "bullish" if current_price > levels["50.0"] else "neutral"
        else:
            interpretation = f"Downtrend retracement: price at {current_level} level, key resistance at 61.8% ({levels['61.8']:.2f})"
            signal = "bearish" if current_price < levels["50.0"] else "neutral"

        return {
            "indicator_name": "Fibonacci",
            "values": levels,
            "current_value": {
                "swing_high": swing_high,
                "swing_low": swing_low,
                "trend": trend,
                "current_level": current_level,
            },
            "signal": signal,
            "interpretation": interpretation,
            "params": {"swing_lookback": swing_lookback},
            "computed_at_idx": len(df) - 1,
        }
    except Exception as e:
        return {"error": str(e)[:200], "indicator_name": "Fibonacci"}
