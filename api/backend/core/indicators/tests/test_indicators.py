"""
Tests for Technical Indicators Vault.

Uses deterministic synthetic OHLCV data with fixed seed.
Tests normal operation and edge cases (insufficient data).
"""

import pytest
import pandas as pd
import numpy as np

from backend.core.indicators import (
    # Trend
    sma, ema, wma, macd, adx, psar, ichimoku, supertrend, aroon, linear_regression_slope,
    # Momentum
    rsi, stochastic, stoch_rsi, roc, williams_r, cci, mfi, trix, ultimate_oscillator, awesome_oscillator,
    # Volatility
    bollinger, atr, keltner, donchian, rolling_std, historical_volatility, chaikin_volatility, volatility_stop,
    # Volume
    volume_raw, volume_ma, obv, vwap, acc_dist, chaikin_mf, volume_roc, ease_of_movement, vpt, force_index,
    # Patterns
    detect_candlestick_patterns, support_resistance_levels, pivot_points, fibonacci_retracements,
)


@pytest.fixture
def ohlcv_100():
    """
    Generate 100-row synthetic OHLCV DataFrame with fixed seed.
    Creates trending up then down pattern for realistic testing.
    """
    rng = np.random.RandomState(42)
    n = 100

    # Base price starting at 100, trending up then down
    trend = np.concatenate([
        np.linspace(100, 130, 50),  # Uptrend
        np.linspace(130, 105, 50),  # Downtrend
    ])

    # Add noise
    noise = rng.normal(0, 2, n)
    close = trend + noise

    # Generate OHLC from close
    high = close + rng.uniform(0.5, 3, n)
    low = close - rng.uniform(0.5, 3, n)
    open_price = close + rng.uniform(-2, 2, n)

    # Ensure OHLC consistency
    high = np.maximum(high, np.maximum(open_price, close))
    low = np.minimum(low, np.minimum(open_price, close))

    # Volume with some variation
    base_volume = 1_000_000
    volume = base_volume + rng.randint(-200_000, 500_000, n)

    df = pd.DataFrame({
        "Open": open_price,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
    })

    return df


@pytest.fixture
def ohlcv_5():
    """Minimal 5-row DataFrame for edge case testing."""
    rng = np.random.RandomState(42)
    n = 5

    close = np.array([100, 101, 102, 101.5, 102.5])
    high = close + rng.uniform(0.5, 1.5, n)
    low = close - rng.uniform(0.5, 1.5, n)
    open_price = close + rng.uniform(-1, 1, n)
    volume = np.array([1000000, 1100000, 900000, 1200000, 1050000])

    df = pd.DataFrame({
        "Open": open_price,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
    })

    return df


class TestTrendIndicators:
    """Test trend indicator functions."""

    def test_sma(self, ohlcv_100):
        result = sma(ohlcv_100, period=20)
        assert "error" not in result
        assert result["indicator_name"] == "SMA(20)"
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_ema(self, ohlcv_100):
        result = ema(ohlcv_100, period=20)
        assert "error" not in result
        assert result["indicator_name"] == "EMA(20)"
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_wma(self, ohlcv_100):
        result = wma(ohlcv_100, period=20)
        assert "error" not in result
        assert result["indicator_name"] == "WMA(20)"
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_macd(self, ohlcv_100):
        result = macd(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "MACD(12,26,9)"
        assert isinstance(result["current_value"], dict)
        assert np.isfinite(result["current_value"]["macd"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_adx(self, ohlcv_100):
        result = adx(ohlcv_100, period=14)
        assert "error" not in result
        assert result["indicator_name"] == "ADX(14)"
        assert isinstance(result["current_value"], dict)
        assert np.isfinite(result["current_value"]["adx"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_psar(self, ohlcv_100):
        result = psar(ohlcv_100)
        assert "error" not in result
        assert "PSAR" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_ichimoku(self, ohlcv_100):
        result = ichimoku(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "Ichimoku"
        assert isinstance(result["current_value"], dict)
        assert np.isfinite(result["current_value"]["tenkan_sen"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_supertrend(self, ohlcv_100):
        result = supertrend(ohlcv_100)
        assert "error" not in result
        assert "Supertrend" in result["indicator_name"]
        assert isinstance(result["current_value"], dict)
        assert np.isfinite(result["current_value"]["supertrend"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_aroon(self, ohlcv_100):
        result = aroon(ohlcv_100)
        assert "error" not in result
        assert "Aroon" in result["indicator_name"]
        assert isinstance(result["current_value"], dict)
        assert np.isfinite(result["current_value"]["aroon_up"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_linear_regression_slope(self, ohlcv_100):
        result = linear_regression_slope(ohlcv_100)
        assert "error" not in result
        assert "LR_Slope" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    # Edge cases
    def test_sma_insufficient_data(self, ohlcv_5):
        result = sma(ohlcv_5, period=20)
        assert "error" in result
        assert "insufficient data" in result["error"]

    def test_ichimoku_insufficient_data(self, ohlcv_5):
        result = ichimoku(ohlcv_5)
        assert "error" in result


class TestMomentumIndicators:
    """Test momentum indicator functions."""

    def test_rsi(self, ohlcv_100):
        result = rsi(ohlcv_100, period=14)
        assert "error" not in result
        assert result["indicator_name"] == "RSI(14)"
        assert np.isfinite(result["current_value"])
        assert 0 <= result["current_value"] <= 100
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_stochastic(self, ohlcv_100):
        result = stochastic(ohlcv_100)
        assert "error" not in result
        assert "Stochastic" in result["indicator_name"]
        assert isinstance(result["current_value"], dict)
        assert np.isfinite(result["current_value"]["k"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_stoch_rsi(self, ohlcv_100):
        result = stoch_rsi(ohlcv_100)
        assert "error" not in result
        assert "StochRSI" in result["indicator_name"]
        assert isinstance(result["current_value"], dict)
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_roc(self, ohlcv_100):
        result = roc(ohlcv_100)
        assert "error" not in result
        assert "ROC" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_williams_r(self, ohlcv_100):
        result = williams_r(ohlcv_100)
        assert "error" not in result
        assert "Williams_R" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert -100 <= result["current_value"] <= 0
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_cci(self, ohlcv_100):
        result = cci(ohlcv_100)
        assert "error" not in result
        assert "CCI" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_mfi(self, ohlcv_100):
        result = mfi(ohlcv_100)
        assert "error" not in result
        assert "MFI" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_trix(self, ohlcv_100):
        result = trix(ohlcv_100)
        assert "error" not in result
        assert "TRIX" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_ultimate_oscillator(self, ohlcv_100):
        result = ultimate_oscillator(ohlcv_100)
        assert "error" not in result
        assert "UO" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_awesome_oscillator(self, ohlcv_100):
        result = awesome_oscillator(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "AO"
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    # Edge cases
    def test_rsi_insufficient_data(self, ohlcv_5):
        result = rsi(ohlcv_5, period=14)
        assert "error" in result

    def test_mfi_no_volume(self, ohlcv_100):
        df_no_vol = ohlcv_100.drop(columns=["Volume"])
        result = mfi(df_no_vol)
        assert "error" in result


class TestVolatilityIndicators:
    """Test volatility indicator functions."""

    def test_bollinger(self, ohlcv_100):
        result = bollinger(ohlcv_100)
        assert "error" not in result
        assert "Bollinger" in result["indicator_name"]
        assert isinstance(result["current_value"], dict)
        assert np.isfinite(result["current_value"]["upper"])
        assert np.isfinite(result["current_value"]["lower"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_atr(self, ohlcv_100):
        result = atr(ohlcv_100)
        assert "error" not in result
        assert "ATR" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["current_value"] > 0
        assert result["signal"] == "neutral"

    def test_keltner(self, ohlcv_100):
        result = keltner(ohlcv_100)
        assert "error" not in result
        assert "Keltner" in result["indicator_name"]
        assert isinstance(result["current_value"], dict)
        assert np.isfinite(result["current_value"]["upper"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_donchian(self, ohlcv_100):
        result = donchian(ohlcv_100)
        assert "error" not in result
        assert "Donchian" in result["indicator_name"]
        assert isinstance(result["current_value"], dict)
        assert np.isfinite(result["current_value"]["upper"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_rolling_std(self, ohlcv_100):
        result = rolling_std(ohlcv_100)
        assert "error" not in result
        assert "Std" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["current_value"] >= 0
        assert result["signal"] == "neutral"

    def test_historical_volatility(self, ohlcv_100):
        result = historical_volatility(ohlcv_100)
        assert "error" not in result
        assert "HV" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] == "neutral"

    def test_chaikin_volatility(self, ohlcv_100):
        result = chaikin_volatility(ohlcv_100)
        assert "error" not in result
        assert "ChaikinVol" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] == "neutral"

    def test_volatility_stop(self, ohlcv_100):
        result = volatility_stop(ohlcv_100)
        assert "error" not in result
        assert "VolStop" in result["indicator_name"]
        assert isinstance(result["current_value"], dict)
        assert np.isfinite(result["current_value"]["stop"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    # Edge cases
    def test_atr_insufficient_data(self, ohlcv_5):
        result = atr(ohlcv_5, period=14)
        assert "error" in result


class TestVolumeIndicators:
    """Test volume indicator functions."""

    def test_volume_raw(self, ohlcv_100):
        result = volume_raw(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "Volume"
        assert np.isfinite(result["current_value"])
        assert result["signal"] == "neutral"

    def test_volume_ma(self, ohlcv_100):
        result = volume_ma(ohlcv_100)
        assert "error" not in result
        assert "VolumeMA" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] == "neutral"

    def test_obv(self, ohlcv_100):
        result = obv(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "OBV"
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_vwap(self, ohlcv_100):
        result = vwap(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "VWAP"
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_acc_dist(self, ohlcv_100):
        result = acc_dist(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "A/D"
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_chaikin_mf(self, ohlcv_100):
        result = chaikin_mf(ohlcv_100)
        assert "error" not in result
        assert "CMF" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_volume_roc(self, ohlcv_100):
        result = volume_roc(ohlcv_100)
        assert "error" not in result
        assert "VROC" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] == "neutral"

    def test_ease_of_movement(self, ohlcv_100):
        result = ease_of_movement(ohlcv_100)
        assert "error" not in result
        assert "EMV" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_vpt(self, ohlcv_100):
        result = vpt(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "VPT"
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_force_index(self, ohlcv_100):
        result = force_index(ohlcv_100)
        assert "error" not in result
        assert "FI" in result["indicator_name"]
        assert np.isfinite(result["current_value"])
        assert result["signal"] in ("bullish", "bearish", "neutral")

    # Edge cases
    def test_obv_insufficient_data(self, ohlcv_5):
        # OBV only needs 2 rows, so 5 should work
        result = obv(ohlcv_5)
        assert "error" not in result

    def test_volume_no_column(self, ohlcv_100):
        df_no_vol = ohlcv_100.drop(columns=["Volume"])
        result = volume_raw(df_no_vol)
        assert "error" in result


class TestPatterns:
    """Test pattern detection functions."""

    def test_candlestick_patterns(self, ohlcv_100):
        result = detect_candlestick_patterns(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "CandlePatterns"
        assert isinstance(result["values"]["patterns_found"], list)
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_support_resistance_levels(self, ohlcv_100):
        result = support_resistance_levels(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "S/R Levels"
        assert isinstance(result["values"]["resistance_levels"], list)
        assert isinstance(result["values"]["support_levels"], list)
        assert result["signal"] == "neutral"

    def test_pivot_points_classic(self, ohlcv_100):
        result = pivot_points(ohlcv_100, method="classic")
        assert "error" not in result
        assert "Pivot(classic)" in result["indicator_name"]
        assert isinstance(result["values"], dict)
        assert "pivot" in result["values"]
        assert "r1" in result["values"]
        assert "s1" in result["values"]
        assert result["signal"] == "neutral"

    def test_pivot_points_fibonacci(self, ohlcv_100):
        result = pivot_points(ohlcv_100, method="fibonacci")
        assert "error" not in result
        assert "Pivot(fibonacci)" in result["indicator_name"]

    def test_pivot_points_camarilla(self, ohlcv_100):
        result = pivot_points(ohlcv_100, method="camarilla")
        assert "error" not in result
        assert "Pivot(camarilla)" in result["indicator_name"]

    def test_pivot_points_invalid_method(self, ohlcv_100):
        result = pivot_points(ohlcv_100, method="invalid")
        assert "error" in result

    def test_fibonacci_retracements(self, ohlcv_100):
        result = fibonacci_retracements(ohlcv_100)
        assert "error" not in result
        assert result["indicator_name"] == "Fibonacci"
        assert isinstance(result["values"], dict)
        assert "61.8" in result["values"]
        assert result["signal"] in ("bullish", "bearish", "neutral")

    # Edge cases
    def test_candlestick_patterns_insufficient_data(self, ohlcv_5):
        result = detect_candlestick_patterns(ohlcv_5, lookback=10)
        assert "error" in result

    def test_support_resistance_insufficient_data(self, ohlcv_5):
        result = support_resistance_levels(ohlcv_5, lookback=50)
        assert "error" in result


class TestOutputSchema:
    """Verify all indicators return the expected schema."""

    def test_schema_keys(self, ohlcv_100):
        """All successful results should have required keys."""
        required_keys = {"indicator_name", "values", "current_value", "signal", "interpretation", "params", "computed_at_idx"}

        # Test a sampling of indicators
        indicators = [
            sma(ohlcv_100, 20),
            rsi(ohlcv_100, 14),
            bollinger(ohlcv_100),
            obv(ohlcv_100),
            detect_candlestick_patterns(ohlcv_100),
        ]

        for result in indicators:
            if "error" not in result:
                assert required_keys.issubset(result.keys()), f"Missing keys in {result.get('indicator_name')}"

    def test_error_schema(self, ohlcv_5):
        """Error results should have error and indicator_name keys."""
        result = ichimoku(ohlcv_5)
        assert "error" in result
        assert "indicator_name" in result
