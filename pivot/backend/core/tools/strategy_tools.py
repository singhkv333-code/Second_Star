"""LLM-facing bridge over /core/ indicators + calculations + data.

The /core/indicators/ and /core/calculations/ modules take pandas
DataFrames / Series. The LLM speaks in tickers and periods. This
module is the thin glue: ticker → fetch OHLCV → run indicator/calc
→ return a structured dict.

Design rules carried from the underlying modules:
  - Pure (no logger / I/O beyond the data fetch).
  - Errors as values: every wrapper catches and returns
    `{"error": ..., "symbol": ...}` so the chat handler never sees
    an exception leak.
  - Output dicts pass through untouched from the underlying
    indicator/calc — schema is `{indicator_name|metric_name,
    values, current_value|value, signal, interpretation, params}`.

Conservative scope: only the most useful 6 tools wired to the LLM.
The full vault (66 functions) is callable from Python; the LLM
sees a tight subset to keep the prompt-cache prefix small.
"""
from __future__ import annotations

import logging
from typing import Any

from backend.core.calculations import (
    compare_assets,
    correlation_matrix,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
    value_at_risk,
    volatility,
)
from backend.core.calculations.returns import (
    annualised_return,
    cumulative_returns,
    simple_return,
)
from backend.core.data.historical import (
    DataUnavailableError,
    get_close_dict,
    get_close_series,
    get_ohlcv,
)
from backend.core.indicators import momentum_indicators as mom
from backend.core.indicators import trend_indicators as trend
from backend.core.indicators import volatility_indicators as vol_ind
from backend.core.indicators import volume_indicators as vol

logger = logging.getLogger(__name__)


# ── Indicator dispatch table ─────────────────────────────────────────


_INDICATOR_TABLE: dict[str, Any] = {
    # Trend
    "sma": trend.sma,
    "ema": trend.ema,
    "wma": trend.wma,
    "macd": trend.macd,
    "adx": trend.adx,
    "supertrend": trend.supertrend,
    "ichimoku": trend.ichimoku,
    "psar": trend.psar,
    "aroon": trend.aroon,
    # Momentum
    "rsi": mom.rsi,
    "stoch": mom.stochastic,
    "stochastic": mom.stochastic,
    "stoch_rsi": mom.stoch_rsi,
    "roc": mom.roc,
    "williams_r": mom.williams_r,
    "cci": mom.cci,
    "mfi": mom.mfi,
    "trix": mom.trix,
    # Volatility
    "bollinger": vol_ind.bollinger,
    "bb": vol_ind.bollinger,
    "atr": vol_ind.atr,
    "keltner": vol_ind.keltner,
    "donchian": vol_ind.donchian,
    "historical_vol": vol_ind.historical_volatility,
    # Volume
    "obv": vol.obv,
    "vwap": vol.vwap,
    "mfi_volume": vol.chaikin_mf,
    "volume_ma": vol.volume_ma,
    "volume_roc": vol.volume_roc,
}


# ── Helpers ─────────────────────────────────────────────────────────


def _err(msg: str, **kw) -> dict[str, Any]:
    out = {"error": msg}
    out.update(kw)
    return out


def _normalise_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


# ── LLM-facing tools ────────────────────────────────────────────────


def get_indicator(
    symbol: str,
    indicator: str,
    period: int = 14,
    history_period: str = "6mo",
) -> dict[str, Any]:
    """Compute a single technical indicator for an NSE-listed ticker.

    Use for: "what's RELIANCE's RSI", "TCS 50-day SMA", "INFY MACD".
    `indicator` is keyed against a small alias table (rsi/sma/ema/macd/
    adx/supertrend/atr/bollinger/obv/vwap/cci/mfi/etc.). `period` is
    passed through where applicable; ignored for indicators with
    composite parameters (MACD/Bollinger/Supertrend use defaults).
    """
    sym = _normalise_symbol(symbol)
    key = (indicator or "").strip().lower()
    fn = _INDICATOR_TABLE.get(key)
    if fn is None:
        return _err(
            f"unknown indicator {indicator!r}; supported: "
            + ", ".join(sorted(_INDICATOR_TABLE.keys())),
            symbol=sym, indicator=indicator,
        )
    try:
        df = get_ohlcv(sym, period=history_period)
    except DataUnavailableError as e:
        return _err(f"no data for {sym}: {e}", symbol=sym)
    except Exception as e:
        return _err(f"data fetch failed: {type(e).__name__}", symbol=sym)

    # Composite indicators don't take a `period` kwarg — call without it.
    composite = {"macd", "bollinger", "bb", "supertrend", "ichimoku", "psar"}
    try:
        if key in composite:
            result = fn(df)
        else:
            result = fn(df, period=period)
    except TypeError:
        # Argument shape mismatch — fall back to default args.
        result = fn(df)
    except Exception as e:
        return _err(f"compute failed: {type(e).__name__}", symbol=sym, indicator=key)

    result = dict(result)
    result["symbol"] = sym
    return result


def get_multiple_indicators(
    symbol: str,
    indicators: list[str],
    history_period: str = "6mo",
) -> dict[str, Any]:
    """Compute several indicators for one ticker in a single call.

    Use for: "give me RSI, MACD, and Bollinger for ETERNAL". Saves a
    chat round-trip vs calling get_indicator three times.
    """
    sym = _normalise_symbol(symbol)
    if not indicators:
        return _err("indicators list is empty", symbol=sym)
    results: dict[str, Any] = {"symbol": sym, "indicators": {}}
    for ind in indicators:
        results["indicators"][ind] = get_indicator(
            sym, ind, history_period=history_period,
        )
    return results


def get_performance_metrics(
    symbol: str,
    period: str = "1y",
    metrics: list[str] | None = None,
) -> dict[str, Any]:
    """Compute risk-adjusted performance metrics for one ticker.

    Default metrics: sharpe, sortino, volatility, max_drawdown,
    var, total_return. Returns each as a structured dict with
    value + interpretation.

    Use for: "how risky is TCS", "what's RELIANCE's Sharpe ratio
    over 1 year", "show me INFY's max drawdown".
    """
    sym = _normalise_symbol(symbol)
    requested = set(m.lower() for m in (metrics or [
        "total_return", "annualised_return", "volatility",
        "sharpe", "sortino", "max_drawdown", "var",
    ]))
    try:
        prices = get_close_series(sym, period=period)
    except DataUnavailableError as e:
        return _err(str(e), symbol=sym)
    if len(prices) < 30:
        return _err("insufficient history for performance metrics", symbol=sym)
    rets = prices.pct_change().dropna()

    out: dict[str, Any] = {"symbol": sym, "period": period, "metrics": {}}
    if "total_return" in requested:
        out["metrics"]["total_return"] = simple_return(
            float(prices.iloc[0]), float(prices.iloc[-1]),
        )
    if "annualised_return" in requested:
        out["metrics"]["annualised_return"] = annualised_return(prices)
    if "volatility" in requested:
        out["metrics"]["volatility"] = volatility(rets)
    if "sharpe" in requested:
        out["metrics"]["sharpe"] = sharpe_ratio(rets)
    if "sortino" in requested:
        out["metrics"]["sortino"] = sortino_ratio(rets)
    if "max_drawdown" in requested:
        out["metrics"]["max_drawdown"] = max_drawdown(prices)
    if "var" in requested:
        out["metrics"]["var"] = value_at_risk(rets)
    return out


def compare_performance(
    symbols: list[str],
    period: str = "1y",
    metric: str = "sharpe",
) -> dict[str, Any]:
    """Rank a list of tickers by a chosen metric.

    `metric` ∈ {sharpe, total_return, volatility, max_drawdown}.
    Use for: "rank RELIANCE TCS INFY by Sharpe", "which of these
    has best risk-adjusted return", "compare these stocks 6m return".
    """
    syms = [_normalise_symbol(s) for s in symbols if s]
    if len(syms) < 2:
        return _err("need at least 2 symbols", symbols=syms)
    try:
        price_dict = get_close_dict(syms, period=period)
    except Exception as e:
        return _err(f"data fetch failed: {type(e).__name__}", symbols=syms)
    if len(price_dict) < 2:
        return _err(
            "data unavailable for too many symbols",
            available=list(price_dict.keys()),
        )
    table = compare_assets(price_dict)
    return {
        "period": period,
        "metric": metric,
        "symbols": list(price_dict.keys()),
        "comparison": table,
    }


def get_correlation_matrix(
    symbols: list[str],
    period: str = "6mo",
) -> dict[str, Any]:
    """Pairwise return correlations for a basket of tickers.

    Use for: "how correlated are TCS, INFY, WIPRO", "diversification
    check on my portfolio". Output is a {ticker → {ticker → ρ}} grid.
    """
    syms = [_normalise_symbol(s) for s in symbols if s]
    if len(syms) < 2:
        return _err("need at least 2 symbols", symbols=syms)
    try:
        price_dict = get_close_dict(syms, period=period)
    except Exception as e:
        return _err(f"data fetch failed: {type(e).__name__}", symbols=syms)
    return correlation_matrix(price_dict)


def get_returns(
    symbol: str,
    period: str = "1y",
    cumulative: bool = False,
) -> dict[str, Any]:
    """Return summary for one ticker — total return + (optionally)
    the cumulative-return curve over the period.

    Use for: "what's TCS up YTD", "how has RELIANCE done over 5 years",
    "INFY return last quarter".
    """
    sym = _normalise_symbol(symbol)
    try:
        prices = get_close_series(sym, period=period)
    except DataUnavailableError as e:
        return _err(str(e), symbol=sym)
    if len(prices) < 2:
        return _err("insufficient history", symbol=sym)
    out: dict[str, Any] = {
        "symbol": sym, "period": period,
        "simple_return": simple_return(
            float(prices.iloc[0]), float(prices.iloc[-1]),
        ),
        "annualised": annualised_return(prices),
    }
    if cumulative:
        out["cumulative"] = cumulative_returns(prices)
    return out


# Public API names — used by the chat tool registration.
__all__ = [
    "get_indicator",
    "get_multiple_indicators",
    "get_performance_metrics",
    "compare_performance",
    "get_correlation_matrix",
    "get_returns",
]
