"""
Performance metrics calculations for Pivot.

Pure functions for computing risk-adjusted performance measures.
All functions return dicts with standardised schema including metric_name,
value, params, and interpretation fields.

Indian market defaults:
- periods_per_year = 252 (Indian trading days)
- risk_free_rate = 0.06 (6% G-sec yield, annual)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def _validate_returns(returns: pd.Series, min_length: int = 2, metric_name: str = "unknown") -> dict | None:
    """
    Validate input returns series. Returns error dict if invalid, None if valid.
    """
    if returns is None:
        return {"error": "Input returns is None", "metric_name": metric_name}

    if not isinstance(returns, pd.Series):
        return {"error": "Input must be a pandas Series", "metric_name": metric_name}

    if len(returns) < min_length:
        return {"error": f"Series must have at least {min_length} data points, got {len(returns)}", "metric_name": metric_name}

    valid_returns = returns.dropna()
    if len(valid_returns) < min_length:
        return {"error": f"Series has fewer than {min_length} non-NaN values", "metric_name": metric_name}

    return None


def sharpe_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.06,
    periods_per_year: int = 252,
) -> dict:
    """
    Calculate the Sharpe Ratio for a returns series.

    Use this to measure risk-adjusted returns. Higher Sharpe means better
    return per unit of risk. A Sharpe above 1 is generally considered good,
    above 2 is very good, above 3 is excellent.

    Default risk-free rate is 6% (Indian G-sec yield).

    Args:
        returns: A pandas Series of returns (not prices)
        risk_free_rate: Annual risk-free rate as decimal (default 0.06 = 6%)
        periods_per_year: Number of periods in a year (default 252 for daily)

    Returns:
        Dict with value (Sharpe ratio), params, and interpretation.
    """
    try:
        err = _validate_returns(returns, min_length=20, metric_name="Sharpe Ratio")
        if err:
            return err

        rets = returns.dropna()

        # Check for all-zero returns
        if (rets == 0).all():
            return {"error": "All returns are zero", "metric_name": "Sharpe Ratio"}

        # Convert annual risk-free rate to per-period
        rf_per_period = risk_free_rate / periods_per_year

        # Calculate excess returns
        excess_returns = rets - rf_per_period

        mean_excess = float(excess_returns.mean())
        std_returns = float(rets.std())

        if std_returns == 0:
            return {"error": "Returns have zero volatility", "metric_name": "Sharpe Ratio"}

        # Annualised Sharpe = (mean excess * sqrt(periods)) / std
        sharpe = (mean_excess / std_returns) * np.sqrt(periods_per_year)

        if sharpe > 2:
            quality = "excellent risk-adjusted returns"
        elif sharpe > 1:
            quality = "good risk-adjusted returns"
        elif sharpe > 0:
            quality = "positive but modest risk-adjusted returns"
        else:
            quality = "negative risk-adjusted returns"

        interpretation = f"Sharpe of {sharpe:.2f} - {quality}"

        return {
            "metric_name": "Sharpe Ratio",
            "value": float(sharpe),
            "annualised_return": float(mean_excess * periods_per_year + risk_free_rate),
            "annualised_volatility": float(std_returns * np.sqrt(periods_per_year)),
            "params": {"risk_free_rate": risk_free_rate, "periods_per_year": periods_per_year},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Sharpe Ratio"}


def sortino_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.06,
    target: float = 0.0,
    periods_per_year: int = 252,
) -> dict:
    """
    Calculate the Sortino Ratio for a returns series.

    Use this when you care more about downside risk than total volatility.
    Like Sharpe but only penalises downside deviation. Better for asymmetric
    return distributions.

    Default risk-free rate is 6% (Indian G-sec yield).

    Args:
        returns: A pandas Series of returns
        risk_free_rate: Annual risk-free rate as decimal (default 0.06 = 6%)
        target: Minimum acceptable return per period (default 0.0)
        periods_per_year: Number of periods in a year (default 252 for daily)

    Returns:
        Dict with value (Sortino ratio), params, and interpretation.
    """
    try:
        err = _validate_returns(returns, min_length=20, metric_name="Sortino Ratio")
        if err:
            return err

        rets = returns.dropna()

        if (rets == 0).all():
            return {"error": "All returns are zero", "metric_name": "Sortino Ratio"}

        # Convert annual risk-free rate to per-period
        rf_per_period = risk_free_rate / periods_per_year

        # Mean excess return
        mean_excess = float(rets.mean() - rf_per_period)

        # Downside deviation
        downside = rets[rets < target] - target
        if len(downside) == 0:
            # No downside returns - infinite Sortino (cap at 10)
            return {
                "metric_name": "Sortino Ratio",
                "value": 10.0,
                "downside_deviation": 0.0,
                "params": {"risk_free_rate": risk_free_rate, "target": target, "periods_per_year": periods_per_year},
                "interpretation": "Sortino of 10+ (no downside periods) - exceptional",
            }

        dd = float(np.sqrt((downside ** 2).mean()))

        if dd == 0:
            return {"error": "Downside deviation is zero", "metric_name": "Sortino Ratio"}

        # Annualised Sortino
        sortino = (mean_excess / dd) * np.sqrt(periods_per_year)

        if sortino > 3:
            quality = "exceptional downside-adjusted returns"
        elif sortino > 2:
            quality = "excellent downside-adjusted returns"
        elif sortino > 1:
            quality = "good downside-adjusted returns"
        elif sortino > 0:
            quality = "positive but modest downside-adjusted returns"
        else:
            quality = "negative downside-adjusted returns"

        interpretation = f"Sortino of {sortino:.2f} - {quality}"

        return {
            "metric_name": "Sortino Ratio",
            "value": float(sortino),
            "downside_deviation": float(dd * np.sqrt(periods_per_year)),
            "params": {"risk_free_rate": risk_free_rate, "target": target, "periods_per_year": periods_per_year},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Sortino Ratio"}


def calmar_ratio(returns: pd.Series, periods_per_year: int = 252) -> dict:
    """
    Calculate the Calmar Ratio (CAGR / Max Drawdown).

    Use this to evaluate returns relative to the worst loss experienced.
    Higher Calmar means better compensation for drawdown risk. Particularly
    useful for trend-following and momentum strategies.

    Args:
        returns: A pandas Series of returns
        periods_per_year: Number of periods in a year (default 252 for daily)

    Returns:
        Dict with value (Calmar ratio), params, and interpretation.
    """
    try:
        err = _validate_returns(returns, min_length=20, metric_name="Calmar Ratio")
        if err:
            return err

        rets = returns.dropna()

        if (rets == 0).all():
            return {"error": "All returns are zero", "metric_name": "Calmar Ratio"}

        # Calculate cumulative return to get CAGR
        cum_return = (1 + rets).prod()
        n_years = len(rets) / periods_per_year

        if n_years <= 0:
            return {"error": "Insufficient data for annualisation", "metric_name": "Calmar Ratio"}

        if cum_return <= 0:
            return {"error": "Cumulative return is negative or zero", "metric_name": "Calmar Ratio"}

        cagr = (cum_return ** (1 / n_years)) - 1

        # Calculate max drawdown from cumulative returns
        cum_values = (1 + rets).cumprod()
        running_max = cum_values.cummax()
        drawdowns = (cum_values - running_max) / running_max
        max_dd = abs(float(drawdowns.min()))

        if max_dd == 0:
            # No drawdown - infinite Calmar (cap at 10)
            return {
                "metric_name": "Calmar Ratio",
                "value": 10.0,
                "cagr": float(cagr),
                "max_drawdown": 0.0,
                "params": {"periods_per_year": periods_per_year},
                "interpretation": "Calmar of 10+ (no drawdown) - exceptional",
            }

        calmar = cagr / max_dd

        if calmar > 3:
            quality = "excellent return/drawdown profile"
        elif calmar > 1:
            quality = "good return/drawdown profile"
        elif calmar > 0:
            quality = "positive but modest return/drawdown profile"
        else:
            quality = "negative returns relative to drawdown"

        interpretation = f"Calmar of {calmar:.2f} - {quality}"

        return {
            "metric_name": "Calmar Ratio",
            "value": float(calmar),
            "cagr": float(cagr),
            "cagr_pct": float(cagr * 100),
            "max_drawdown": float(max_dd),
            "max_drawdown_pct": float(max_dd * 100),
            "params": {"periods_per_year": periods_per_year, "years": float(n_years)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Calmar Ratio"}


def treynor_ratio(
    asset_returns: pd.Series,
    market_returns: pd.Series,
    risk_free_rate: float = 0.06,
) -> dict:
    """
    Calculate the Treynor Ratio (excess return / beta).

    Use this to measure returns relative to systematic (market) risk only.
    Unlike Sharpe which uses total risk, Treynor only considers market risk.
    Better for evaluating diversified portfolios.

    Default risk-free rate is 6% (Indian G-sec yield).

    Args:
        asset_returns: A pandas Series of asset returns
        market_returns: A pandas Series of market/benchmark returns
        risk_free_rate: Annual risk-free rate as decimal (default 0.06 = 6%)

    Returns:
        Dict with value (Treynor ratio), params, and interpretation.
    """
    try:
        err = _validate_returns(asset_returns, min_length=20, metric_name="Treynor Ratio")
        if err:
            return err

        err = _validate_returns(market_returns, min_length=20, metric_name="Treynor Ratio")
        if err:
            return err

        # Align series
        asset = asset_returns.dropna()
        market = market_returns.dropna()

        common_idx = asset.index.intersection(market.index)
        if len(common_idx) < 20:
            return {"error": "Insufficient overlapping data points (need at least 20)", "metric_name": "Treynor Ratio"}

        asset = asset.loc[common_idx]
        market = market.loc[common_idx]

        # Calculate beta
        covariance = float(np.cov(asset, market)[0, 1])
        market_var = float(market.var())

        if market_var == 0:
            return {"error": "Market returns have zero variance", "metric_name": "Treynor Ratio"}

        b = covariance / market_var

        if b == 0:
            return {"error": "Beta is zero - cannot calculate Treynor", "metric_name": "Treynor Ratio"}

        # Annualised excess return
        # Assume daily returns, annualise
        periods_per_year = 252
        rf_per_period = risk_free_rate / periods_per_year

        mean_excess = float((asset - rf_per_period).mean())
        annualised_excess = mean_excess * periods_per_year

        treynor = annualised_excess / b

        if treynor > 0.15:
            quality = "excellent systematic risk-adjusted returns"
        elif treynor > 0.08:
            quality = "good systematic risk-adjusted returns"
        elif treynor > 0:
            quality = "positive systematic risk-adjusted returns"
        else:
            quality = "negative systematic risk-adjusted returns"

        interpretation = f"Treynor of {treynor:.4f} - {quality}"

        return {
            "metric_name": "Treynor Ratio",
            "value": float(treynor),
            "beta": float(b),
            "annualised_excess_return": float(annualised_excess),
            "params": {"risk_free_rate": risk_free_rate, "num_observations": len(common_idx)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Treynor Ratio"}


def information_ratio(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    periods_per_year: int = 252,
) -> dict:
    """
    Calculate the Information Ratio (active return / tracking error).

    Use this to evaluate active management skill. Measures excess return
    over benchmark per unit of tracking error. Higher IR means more
    consistent outperformance.

    Args:
        asset_returns: A pandas Series of asset/portfolio returns
        benchmark_returns: A pandas Series of benchmark returns
        periods_per_year: Number of periods in a year (default 252 for daily)

    Returns:
        Dict with value (Information ratio), params, and interpretation.
    """
    try:
        err = _validate_returns(asset_returns, min_length=20, metric_name="Information Ratio")
        if err:
            return err

        err = _validate_returns(benchmark_returns, min_length=20, metric_name="Information Ratio")
        if err:
            return err

        # Align series
        asset = asset_returns.dropna()
        benchmark = benchmark_returns.dropna()

        common_idx = asset.index.intersection(benchmark.index)
        if len(common_idx) < 20:
            return {"error": "Insufficient overlapping data points (need at least 20)", "metric_name": "Information Ratio"}

        asset = asset.loc[common_idx]
        benchmark = benchmark.loc[common_idx]

        # Active return = asset - benchmark
        active_return = asset - benchmark
        mean_active = float(active_return.mean())
        tracking_error = float(active_return.std())

        if tracking_error == 0:
            return {"error": "Tracking error is zero - returns identical to benchmark", "metric_name": "Information Ratio"}

        # Annualised Information Ratio
        ir = (mean_active / tracking_error) * np.sqrt(periods_per_year)

        if ir > 1:
            quality = "exceptional active management"
        elif ir > 0.5:
            quality = "good active management"
        elif ir > 0:
            quality = "positive but modest outperformance"
        else:
            quality = "underperforming benchmark"

        interpretation = f"Information Ratio of {ir:.2f} - {quality}"

        return {
            "metric_name": "Information Ratio",
            "value": float(ir),
            "annualised_active_return": float(mean_active * periods_per_year),
            "annualised_tracking_error": float(tracking_error * np.sqrt(periods_per_year)),
            "params": {"periods_per_year": periods_per_year, "num_observations": len(common_idx)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Information Ratio"}


def alpha(
    asset_returns: pd.Series,
    market_returns: pd.Series,
    risk_free_rate: float = 0.06,
) -> dict:
    """
    Calculate Jensen's Alpha (risk-adjusted excess return).

    Use this to measure how much an asset outperformed (or underperformed)
    its expected return given its beta. Positive alpha means outperformance,
    negative means underperformance.

    Default risk-free rate is 6% (Indian G-sec yield).

    Args:
        asset_returns: A pandas Series of asset returns
        market_returns: A pandas Series of market/benchmark returns
        risk_free_rate: Annual risk-free rate as decimal (default 0.06 = 6%)

    Returns:
        Dict with value (alpha, annualised), params, and interpretation.
    """
    try:
        err = _validate_returns(asset_returns, min_length=20, metric_name="Alpha")
        if err:
            return err

        err = _validate_returns(market_returns, min_length=20, metric_name="Alpha")
        if err:
            return err

        # Align series
        asset = asset_returns.dropna()
        market = market_returns.dropna()

        common_idx = asset.index.intersection(market.index)
        if len(common_idx) < 20:
            return {"error": "Insufficient overlapping data points (need at least 20)", "metric_name": "Alpha"}

        asset = asset.loc[common_idx]
        market = market.loc[common_idx]

        # Calculate beta
        covariance = float(np.cov(asset, market)[0, 1])
        market_var = float(market.var())

        if market_var == 0:
            return {"error": "Market returns have zero variance", "metric_name": "Alpha"}

        b = covariance / market_var

        # Daily risk-free
        periods_per_year = 252
        rf_per_period = risk_free_rate / periods_per_year

        # Alpha = asset_return - [rf + beta * (market_return - rf)]
        expected_return = rf_per_period + b * (market.mean() - rf_per_period)
        daily_alpha = float(asset.mean() - expected_return)
        annualised_alpha = daily_alpha * periods_per_year

        alpha_pct = annualised_alpha * 100

        if annualised_alpha > 0.05:
            quality = "strong outperformance"
        elif annualised_alpha > 0:
            quality = "positive alpha"
        elif annualised_alpha > -0.05:
            quality = "slight underperformance"
        else:
            quality = "significant underperformance"

        interpretation = f"Annualised alpha of {alpha_pct:.2f}% - {quality}"

        return {
            "metric_name": "Alpha",
            "value": float(annualised_alpha),
            "value_pct": float(alpha_pct),
            "beta": float(b),
            "params": {"risk_free_rate": risk_free_rate, "num_observations": len(common_idx)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Alpha"}


def omega_ratio(returns: pd.Series, threshold: float = 0.0) -> dict:
    """
    Calculate the Omega Ratio.

    Use this as an alternative to Sharpe that considers the entire return
    distribution. Omega = (sum of returns above threshold) / (sum of returns
    below threshold). Omega > 1 is good.

    Args:
        returns: A pandas Series of returns
        threshold: The threshold return per period (default 0.0)

    Returns:
        Dict with value (Omega ratio), params, and interpretation.
    """
    try:
        err = _validate_returns(returns, min_length=20, metric_name="Omega Ratio")
        if err:
            return err

        rets = returns.dropna()

        if (rets == 0).all():
            return {"error": "All returns are zero", "metric_name": "Omega Ratio"}

        # Returns above threshold
        gains = rets[rets > threshold] - threshold
        # Returns below threshold
        losses = threshold - rets[rets < threshold]

        sum_gains = float(gains.sum()) if len(gains) > 0 else 0.0
        sum_losses = float(losses.sum()) if len(losses) > 0 else 0.0

        if sum_losses == 0:
            if sum_gains == 0:
                return {"error": "No returns different from threshold", "metric_name": "Omega Ratio"}
            # Infinite Omega (cap at 10)
            return {
                "metric_name": "Omega Ratio",
                "value": 10.0,
                "sum_gains": sum_gains,
                "sum_losses": 0.0,
                "params": {"threshold": threshold},
                "interpretation": "Omega of 10+ (no losses below threshold) - exceptional",
            }

        omega = sum_gains / sum_losses

        if omega > 2:
            quality = "excellent gain/loss ratio"
        elif omega > 1:
            quality = "favorable gain/loss ratio"
        else:
            quality = "unfavorable - losses outweigh gains"

        interpretation = f"Omega of {omega:.2f} - {quality}"

        return {
            "metric_name": "Omega Ratio",
            "value": float(omega),
            "sum_gains": sum_gains,
            "sum_losses": sum_losses,
            "num_gains": len(gains),
            "num_losses": len(losses),
            "params": {"threshold": threshold},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Omega Ratio"}
