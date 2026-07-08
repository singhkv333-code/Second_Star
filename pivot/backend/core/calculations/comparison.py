"""
Asset comparison calculations for Pivot.

Pure functions for comparing multiple assets across various metrics.
All functions return dicts with standardised schema including metric_name,
value, params, and interpretation fields.

Indian market defaults:
- periods_per_year = 252 (Indian trading days)
- risk_free_rate = 0.06 (6% G-sec yield)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def _price_to_returns(prices: pd.Series) -> pd.Series:
    """Convert price series to returns series."""
    return prices.pct_change().dropna()


def _calculate_total_return(prices: pd.Series) -> float:
    """Calculate total return from price series."""
    p = prices.dropna()
    if len(p) < 2 or p.iloc[0] == 0:
        return float("nan")
    return float((p.iloc[-1] / p.iloc[0]) - 1)


def _calculate_volatility(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Calculate annualised volatility."""
    r = returns.dropna()
    if len(r) < 2:
        return float("nan")
    return float(r.std() * np.sqrt(periods_per_year))


def _calculate_sharpe(returns: pd.Series, risk_free_rate: float = 0.06, periods_per_year: int = 252) -> float:
    """Calculate Sharpe ratio."""
    r = returns.dropna()
    if len(r) < 20:
        return float("nan")

    rf_per_period = risk_free_rate / periods_per_year
    excess = r - rf_per_period
    std = r.std()

    if std == 0:
        return float("nan")

    return float((excess.mean() / std) * np.sqrt(periods_per_year))


def _calculate_max_dd(prices: pd.Series) -> float:
    """Calculate maximum drawdown (as positive number)."""
    p = prices.dropna()
    if len(p) < 2:
        return float("nan")

    running_max = p.cummax()
    drawdown = (p - running_max) / running_max
    return float(abs(drawdown.min()))


def _calculate_cagr(prices: pd.Series, periods_per_year: int = 252) -> float:
    """Calculate CAGR."""
    p = prices.dropna()
    if len(p) < 2 or p.iloc[0] <= 0:
        return float("nan")

    total_return = p.iloc[-1] / p.iloc[0]
    n_years = (len(p) - 1) / periods_per_year

    if n_years <= 0 or total_return <= 0:
        return float("nan")

    return float((total_return ** (1 / n_years)) - 1)


def compare_assets(
    price_dict: dict[str, pd.Series],
    metrics: list[str] | None = None,
) -> dict:
    """
    Compare multiple assets across specified metrics.

    Use this to get a side-by-side comparison table for portfolio analysis.
    Default metrics: total_return, annualised_return, volatility, sharpe, max_drawdown.

    Args:
        price_dict: Dict mapping ticker symbols to price Series
        metrics: List of metrics to compute. Options: "total_return", "annualised_return",
                 "volatility", "sharpe", "max_drawdown", "cagr". Default computes all.

    Returns:
        Dict with results (ticker -> metric -> value), params, and interpretation.
    """
    try:
        if not price_dict or len(price_dict) == 0:
            return {"error": "Need at least 1 asset to compare", "metric_name": "Asset Comparison"}

        available_metrics = ["total_return", "annualised_return", "volatility", "sharpe", "max_drawdown", "cagr"]

        if metrics is None:
            metrics = ["total_return", "volatility", "sharpe", "max_drawdown"]

        # Validate metrics
        invalid_metrics = [m for m in metrics if m not in available_metrics]
        if invalid_metrics:
            return {"error": f"Invalid metrics: {invalid_metrics}. Available: {available_metrics}", "metric_name": "Asset Comparison"}

        results = {}
        periods_per_year = 252
        risk_free_rate = 0.06

        for ticker, prices in price_dict.items():
            if not isinstance(prices, pd.Series):
                results[ticker] = {"error": "Not a valid Series"}
                continue

            prices = prices.dropna()
            if len(prices) < 2:
                results[ticker] = {"error": "Insufficient data"}
                continue

            returns = _price_to_returns(prices)
            ticker_results = {}

            for metric in metrics:
                if metric == "total_return":
                    val = _calculate_total_return(prices)
                    ticker_results[metric] = val
                    ticker_results[f"{metric}_pct"] = val * 100 if np.isfinite(val) else float("nan")

                elif metric == "annualised_return" or metric == "cagr":
                    val = _calculate_cagr(prices, periods_per_year)
                    ticker_results[metric] = val
                    ticker_results[f"{metric}_pct"] = val * 100 if np.isfinite(val) else float("nan")

                elif metric == "volatility":
                    val = _calculate_volatility(returns, periods_per_year)
                    ticker_results[metric] = val
                    ticker_results[f"{metric}_pct"] = val * 100 if np.isfinite(val) else float("nan")

                elif metric == "sharpe":
                    ticker_results[metric] = _calculate_sharpe(returns, risk_free_rate, periods_per_year)

                elif metric == "max_drawdown":
                    val = _calculate_max_dd(prices)
                    ticker_results[metric] = val
                    ticker_results[f"{metric}_pct"] = val * 100 if np.isfinite(val) else float("nan")

            results[ticker] = ticker_results

        # Find best performer by first metric
        first_metric = metrics[0]
        best_ticker = None
        best_value = float("-inf")

        for ticker, ticker_results in results.items():
            if "error" in ticker_results:
                continue
            val = ticker_results.get(first_metric, float("-inf"))
            if np.isfinite(val) and val > best_value:
                best_value = val
                best_ticker = ticker

        if best_ticker:
            interpretation = f"Compared {len(results)} assets. Best {first_metric}: {best_ticker}"
        else:
            interpretation = f"Compared {len(results)} assets across {len(metrics)} metrics"

        return {
            "metric_name": "Asset Comparison",
            "results": results,
            "tickers": list(results.keys()),
            "metrics_computed": metrics,
            "params": {"risk_free_rate": risk_free_rate, "periods_per_year": periods_per_year},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Asset Comparison"}


def relative_performance(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
) -> dict:
    """
    Calculate relative performance metrics vs a benchmark.

    Use this to understand how an asset performed relative to its benchmark.
    Returns excess return, tracking error, and information ratio.

    Args:
        asset_returns: A pandas Series of asset returns
        benchmark_returns: A pandas Series of benchmark returns

    Returns:
        Dict with excess_return, tracking_error, information_ratio, and interpretation.
    """
    try:
        if asset_returns is None or benchmark_returns is None:
            return {"error": "Returns cannot be None", "metric_name": "Relative Performance"}

        if not isinstance(asset_returns, pd.Series) or not isinstance(benchmark_returns, pd.Series):
            return {"error": "Inputs must be pandas Series", "metric_name": "Relative Performance"}

        asset = asset_returns.dropna()
        benchmark = benchmark_returns.dropna()

        common_idx = asset.index.intersection(benchmark.index)
        if len(common_idx) < 20:
            return {"error": "Insufficient overlapping data (need at least 20)", "metric_name": "Relative Performance"}

        asset = asset.loc[common_idx]
        benchmark = benchmark.loc[common_idx]

        periods_per_year = 252

        # Active return
        active_return = asset - benchmark
        mean_active = float(active_return.mean())
        annualised_excess = mean_active * periods_per_year

        # Tracking error
        tracking_error = float(active_return.std())
        annualised_te = tracking_error * np.sqrt(periods_per_year)

        # Information ratio
        if tracking_error == 0:
            info_ratio = float("nan")
        else:
            info_ratio = (mean_active / tracking_error) * np.sqrt(periods_per_year)

        # Cumulative relative performance
        cum_asset = (1 + asset).prod() - 1
        cum_benchmark = (1 + benchmark).prod() - 1
        cum_excess = cum_asset - cum_benchmark

        if annualised_excess > 0:
            if info_ratio > 0.5:
                quality = "consistent outperformance"
            else:
                quality = "outperformance with high variance"
        else:
            quality = "underperformance"

        interpretation = f"Annualised excess return of {annualised_excess*100:.2f}% with tracking error {annualised_te*100:.2f}% - {quality}"

        return {
            "metric_name": "Relative Performance",
            "excess_return": float(annualised_excess),
            "excess_return_pct": float(annualised_excess * 100),
            "tracking_error": float(annualised_te),
            "tracking_error_pct": float(annualised_te * 100),
            "information_ratio": float(info_ratio) if np.isfinite(info_ratio) else None,
            "cumulative_excess": float(cum_excess),
            "cumulative_excess_pct": float(cum_excess * 100),
            "params": {"periods_per_year": periods_per_year, "num_observations": len(common_idx)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Relative Performance"}


def ranking(
    price_dict: dict[str, pd.Series],
    metric: str,
    ascending: bool = False,
) -> dict:
    """
    Rank multiple assets by a chosen metric.

    Use this to quickly identify top or bottom performers. Returns
    ranked list of tickers with their metric values.

    Args:
        price_dict: Dict mapping ticker symbols to price Series
        metric: Metric to rank by ("sharpe", "total_return", "volatility", "max_drawdown", "cagr")
        ascending: If True, lower values rank higher (default False = higher is better)

    Returns:
        Dict with rankings list (ticker, value, rank), params, and interpretation.
    """
    try:
        if not price_dict or len(price_dict) == 0:
            return {"error": "Need at least 1 asset to rank", "metric_name": "Asset Ranking"}

        valid_metrics = ["sharpe", "total_return", "volatility", "max_drawdown", "cagr"]
        if metric not in valid_metrics:
            return {"error": f"Invalid metric '{metric}'. Valid options: {valid_metrics}", "metric_name": "Asset Ranking"}

        periods_per_year = 252
        risk_free_rate = 0.06

        scores = []

        for ticker, prices in price_dict.items():
            if not isinstance(prices, pd.Series):
                continue

            prices = prices.dropna()
            if len(prices) < 2:
                continue

            returns = _price_to_returns(prices)

            if metric == "sharpe":
                value = _calculate_sharpe(returns, risk_free_rate, periods_per_year)
            elif metric == "total_return":
                value = _calculate_total_return(prices)
            elif metric == "volatility":
                value = _calculate_volatility(returns, periods_per_year)
            elif metric == "max_drawdown":
                value = _calculate_max_dd(prices)
            elif metric == "cagr":
                value = _calculate_cagr(prices, periods_per_year)
            else:
                value = float("nan")

            if np.isfinite(value):
                scores.append({"ticker": ticker, "value": value})

        if len(scores) == 0:
            return {"error": "No valid data to rank", "metric_name": "Asset Ranking"}

        # Sort
        scores.sort(key=lambda x: x["value"], reverse=not ascending)

        # Add ranks
        for i, item in enumerate(scores):
            item["rank"] = i + 1

        top_ticker = scores[0]["ticker"]
        top_value = scores[0]["value"]

        # Format interpretation based on metric
        if metric in ["sharpe"]:
            value_str = f"{top_value:.2f}"
        elif metric in ["total_return", "volatility", "max_drawdown", "cagr"]:
            value_str = f"{top_value*100:.2f}%"
        else:
            value_str = f"{top_value:.4f}"

        interpretation = f"Top ranked by {metric}: {top_ticker} ({value_str})"

        return {
            "metric_name": "Asset Ranking",
            "rankings": scores,
            "metric_used": metric,
            "ascending": ascending,
            "params": {"risk_free_rate": risk_free_rate, "periods_per_year": periods_per_year},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Asset Ranking"}
