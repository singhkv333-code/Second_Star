"""
Technical Indicators Vault for Pivot.

Pure-function indicators for trend, momentum, volatility, volume, and pattern detection.
Each function takes a pandas OHLCV DataFrame and returns a standardized dict.
"""

from .trend_indicators import (
    sma, ema, wma, macd, adx, psar, ichimoku, supertrend, aroon, linear_regression_slope
)
from .momentum_indicators import (
    rsi, stochastic, stoch_rsi, roc, williams_r, cci, mfi, trix, ultimate_oscillator, awesome_oscillator
)
from .volatility_indicators import (
    bollinger, atr, keltner, donchian, rolling_std, historical_volatility, chaikin_volatility, volatility_stop
)
from .volume_indicators import (
    volume_raw, volume_ma, obv, vwap, acc_dist, chaikin_mf, volume_roc, ease_of_movement, vpt, force_index
)
from .patterns import (
    detect_candlestick_patterns, support_resistance_levels, pivot_points, fibonacci_retracements
)

__all__ = [
    # Trend
    "sma", "ema", "wma", "macd", "adx", "psar", "ichimoku", "supertrend", "aroon", "linear_regression_slope",
    # Momentum
    "rsi", "stochastic", "stoch_rsi", "roc", "williams_r", "cci", "mfi", "trix", "ultimate_oscillator", "awesome_oscillator",
    # Volatility
    "bollinger", "atr", "keltner", "donchian", "rolling_std", "historical_volatility", "chaikin_volatility", "volatility_stop",
    # Volume
    "volume_raw", "volume_ma", "obv", "vwap", "acc_dist", "chaikin_mf", "volume_roc", "ease_of_movement", "vpt", "force_index",
    # Patterns
    "detect_candlestick_patterns", "support_resistance_levels", "pivot_points", "fibonacci_retracements",
]
