"""
Risk metrics calculations for Pivot.

Pure functions for computing risk measures on return/price series.
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


def _validate_price_series(series: pd.Series, min_length: int = 2, metric_name: str = "unknown") -> dict | None:
    """
    Validate input price series. Returns error dict if invalid, None if valid.
    """
    if series is None:
        return {"error": "Input series is None", "metric_name": metric_name}

    if not isinstance(series, pd.Series):
        return {"error": "Input must be a pandas Series", "metric_name": metric_name}

    if len(series) < min_length:
        return {"error": f"Series must have at least {min_length} data points, got {len(series)}", "metric_name": metric_name}

    valid_series = series.dropna()
    if len(valid_series) < min_length:
        return {"error": f"Series has fewer than {min_length} non-NaN values", "metric_name": metric_name}

    return None


def volatility(returns: pd.Series, annualised: bool = True, periods_per_year: int = 252) -> dict:
    """
    Calculate the volatility (standard deviation) of returns.

    Use this to measure the dispersion of returns around their mean.
    Higher volatility indicates more risk. Default returns annualised
    volatility assuming 252 trading days per year.

    Args:
        returns: A pandas Series of returns (not prices)
        annualised: Whether to annualise the volatility (default True)
        periods_per_year: Number of periods in a year (default 252 for daily)

    Returns:
        Dict with value (volatility as decimal), params, and interpretation.
    """
    try:
        err = _validate_returns(returns, min_length=2, metric_name="Volatility")
        if err:
            return err

        rets = returns.dropna()

        # Check for all-zero returns
        if (rets == 0).all():
            return {"error": "All returns are zero", "metric_name": "Volatility"}

        vol = float(rets.std())

        if annualised:
            vol = vol * np.sqrt(periods_per_year)

        vol_pct = vol * 100

        if vol < 0.10:
            risk_level = "low"
        elif vol < 0.25:
            risk_level = "moderate"
        else:
            risk_level = "high"

        ann_str = "Annualised" if annualised else "Period"
        interpretation = f"{ann_str} volatility of {vol_pct:.2f}% indicates {risk_level} risk"

        return {
            "metric_name": "Volatility",
            "value": vol,
            "value_pct": vol_pct,
            "params": {"annualised": annualised, "periods_per_year": periods_per_year},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Volatility"}


def downside_deviation(returns: pd.Series, target: float = 0.0) -> dict:
    """
    Calculate downside deviation relative to a target return.

    Use this instead of standard deviation when you care more about
    downside risk than upside variability. Returns below the target
    are penalised; returns above are ignored.

    Args:
        returns: A pandas Series of returns
        target: The target return threshold (default 0.0)

    Returns:
        Dict with value (downside deviation), params, and interpretation.
    """
    try:
        err = _validate_returns(returns, min_length=2, metric_name="Downside Deviation")
        if err:
            return err

        rets = returns.dropna()

        # Calculate downside returns
        downside = rets[rets < target] - target

        if len(downside) == 0:
            return {
                "metric_name": "Downside Deviation",
                "value": 0.0,
                "value_pct": 0.0,
                "params": {"target": target},
                "interpretation": f"No returns below target of {target*100:.2f}% - zero downside deviation",
            }

        dd = float(np.sqrt((downside ** 2).mean()))
        dd_pct = dd * 100

        interpretation = f"Downside deviation of {dd_pct:.2f}% below target {target*100:.2f}%"

        return {
            "metric_name": "Downside Deviation",
            "value": dd,
            "value_pct": dd_pct,
            "params": {"target": target, "num_downside_periods": len(downside)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Downside Deviation"}


def max_drawdown(price_series: pd.Series) -> dict:
    """
    Calculate the maximum drawdown from a price series.

    Use this to measure the largest peak-to-trough decline. Essential for
    understanding worst-case loss scenarios. Returns both percentage and
    absolute drawdown, plus the indices of peak, trough, and recovery.

    Args:
        price_series: A pandas Series of prices

    Returns:
        Dict with max_dd_pct, max_dd_value, peak_idx, trough_idx, recovery_idx.
    """
    try:
        err = _validate_price_series(price_series, min_length=2, metric_name="Max Drawdown")
        if err:
            return err

        prices = price_series.dropna()

        # Calculate running maximum
        running_max = prices.cummax()
        drawdown = (prices - running_max) / running_max

        # Find max drawdown
        max_dd = float(drawdown.min())
        max_dd_pct = abs(max_dd) * 100

        # Find indices
        trough_idx = drawdown.idxmin()

        # Peak is the max before the trough
        peak_series = prices.loc[:trough_idx]
        peak_idx = peak_series.idxmax()
        peak_value = float(prices.loc[peak_idx])
        trough_value = float(prices.loc[trough_idx])

        max_dd_value = peak_value - trough_value

        # Find recovery (if any)
        after_trough = prices.loc[trough_idx:]
        recovered = after_trough[after_trough >= peak_value]
        recovery_idx = recovered.index[0] if len(recovered) > 0 else None

        interpretation = f"Maximum drawdown of {max_dd_pct:.2f}% from peak"
        if recovery_idx is not None:
            interpretation += " (recovered)"
        else:
            interpretation += " (not yet recovered)"

        return {
            "metric_name": "Max Drawdown",
            "value": max_dd,
            "max_dd_pct": max_dd_pct,
            "max_dd_value": max_dd_value,
            "peak_idx": str(peak_idx),
            "peak_value": peak_value,
            "trough_idx": str(trough_idx),
            "trough_value": trough_value,
            "recovery_idx": str(recovery_idx) if recovery_idx is not None else None,
            "params": {"num_periods": len(prices)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Max Drawdown"}


def value_at_risk(returns: pd.Series, confidence: float = 0.95) -> dict:
    """
    Calculate historical Value at Risk (VaR).

    Use this to estimate the maximum loss at a given confidence level.
    For example, 95% VaR of -2% means there's a 5% chance of losing
    more than 2% in a single period.

    Args:
        returns: A pandas Series of returns
        confidence: Confidence level (default 0.95)

    Returns:
        Dict with value (VaR), params, and interpretation.
    """
    try:
        if confidence <= 0 or confidence >= 1:
            return {"error": "Confidence must be between 0 and 1", "metric_name": "Value at Risk"}

        err = _validate_returns(returns, min_length=10, metric_name="Value at Risk")
        if err:
            return err

        rets = returns.dropna()

        # VaR is the (1-confidence) quantile of returns
        var = float(np.percentile(rets, (1 - confidence) * 100))
        var_pct = var * 100

        interpretation = f"At {confidence*100:.0f}% confidence, expect daily loss no worse than {abs(var_pct):.2f}% (based on historical data)"

        return {
            "metric_name": "Value at Risk",
            "value": var,
            "value_pct": var_pct,
            "params": {"confidence": confidence, "num_observations": len(rets)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Value at Risk"}


def conditional_var(returns: pd.Series, confidence: float = 0.95) -> dict:
    """
    Calculate Conditional Value at Risk (CVaR / Expected Shortfall).

    Use this to understand the expected loss in worst-case scenarios
    beyond the VaR threshold. CVaR is the average of all losses worse
    than VaR, making it a more conservative risk measure.

    Args:
        returns: A pandas Series of returns
        confidence: Confidence level (default 0.95)

    Returns:
        Dict with value (CVaR), params, and interpretation.
    """
    try:
        if confidence <= 0 or confidence >= 1:
            return {"error": "Confidence must be between 0 and 1", "metric_name": "Conditional VaR"}

        err = _validate_returns(returns, min_length=10, metric_name="Conditional VaR")
        if err:
            return err

        rets = returns.dropna()

        # Calculate VaR threshold
        var_threshold = np.percentile(rets, (1 - confidence) * 100)

        # CVaR is the mean of returns below VaR
        tail_returns = rets[rets <= var_threshold]

        if len(tail_returns) == 0:
            return {"error": "No tail returns to calculate CVaR", "metric_name": "Conditional VaR"}

        cvar = float(tail_returns.mean())
        cvar_pct = cvar * 100

        interpretation = f"In the worst {(1-confidence)*100:.0f}% of cases, expect average loss of {abs(cvar_pct):.2f}%"

        return {
            "metric_name": "Conditional VaR",
            "value": cvar,
            "value_pct": cvar_pct,
            "var_threshold": float(var_threshold),
            "params": {"confidence": confidence, "num_tail_observations": len(tail_returns)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Conditional VaR"}


def beta(asset_returns: pd.Series, market_returns: pd.Series) -> dict:
    """
    Calculate the beta of an asset relative to a market benchmark.

    Use this to measure systematic risk - how much the asset moves
    relative to the market. Beta > 1 means more volatile than market,
    beta < 1 means less volatile.

    Args:
        asset_returns: A pandas Series of asset returns
        market_returns: A pandas Series of market/benchmark returns

    Returns:
        Dict with value (beta), params, and interpretation.
    """
    try:
        err = _validate_returns(asset_returns, min_length=10, metric_name="Beta")
        if err:
            return err

        err = _validate_returns(market_returns, min_length=10, metric_name="Beta")
        if err:
            return err

        # Align the series
        asset = asset_returns.dropna()
        market = market_returns.dropna()

        common_idx = asset.index.intersection(market.index)
        if len(common_idx) < 10:
            return {"error": "Insufficient overlapping data points (need at least 10)", "metric_name": "Beta"}

        asset = asset.loc[common_idx]
        market = market.loc[common_idx]

        # Beta = Cov(asset, market) / Var(market)
        covariance = float(np.cov(asset, market)[0, 1])
        market_var = float(market.var())

        if market_var == 0:
            return {"error": "Market returns have zero variance", "metric_name": "Beta"}

        b = covariance / market_var

        if b > 1.2:
            risk_profile = "aggressive (high systematic risk)"
        elif b > 0.8:
            risk_profile = "market-like"
        elif b > 0:
            risk_profile = "defensive (low systematic risk)"
        else:
            risk_profile = "inverse to market"

        interpretation = f"Beta of {b:.2f} - {risk_profile}"

        return {
            "metric_name": "Beta",
            "value": float(b),
            "covariance": covariance,
            "market_variance": market_var,
            "params": {"num_observations": len(common_idx)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Beta"}


def correlation_matrix(price_dict: dict[str, pd.Series]) -> dict:
    """
    Calculate the correlation matrix for multiple assets.

    Use this to understand how different assets move together.
    Correlations near +1 mean assets move together, near -1 means
    they move opposite, near 0 means little relationship.

    Args:
        price_dict: Dict mapping ticker symbols to price Series

    Returns:
        Dict with correlations (2D dict), underlying returns, and interpretation.
    """
    try:
        if not price_dict or len(price_dict) < 2:
            return {"error": "Need at least 2 assets for correlation matrix", "metric_name": "Correlation Matrix"}

        # Convert prices to returns and align
        returns_dict = {}
        for ticker, prices in price_dict.items():
            if not isinstance(prices, pd.Series):
                return {"error": f"Price series for {ticker} must be a pandas Series", "metric_name": "Correlation Matrix"}

            prices = prices.dropna()
            if len(prices) < 2:
                return {"error": f"Not enough data for {ticker}", "metric_name": "Correlation Matrix"}

            returns_dict[ticker] = prices.pct_change().dropna()

        # Build DataFrame
        returns_df = pd.DataFrame(returns_dict).dropna()

        if len(returns_df) < 10:
            return {"error": "Insufficient overlapping data (need at least 10 common dates)", "metric_name": "Correlation Matrix"}

        # Calculate correlation matrix
        corr_matrix = returns_df.corr()
        tickers = list(corr_matrix.columns)

        # Convert to nested dict
        correlations = {}
        for t1 in tickers:
            correlations[t1] = {}
            for t2 in tickers:
                correlations[t1][t2] = float(corr_matrix.loc[t1, t2])

        # Find highest and lowest correlations (excluding diagonal)
        pairs = []
        for i, t1 in enumerate(tickers):
            for j, t2 in enumerate(tickers):
                if i < j:
                    pairs.append((t1, t2, correlations[t1][t2]))

        if pairs:
            pairs.sort(key=lambda x: x[2])
            lowest = pairs[0]
            highest = pairs[-1]
            interpretation = f"Correlation matrix for {len(tickers)} assets. Highest: {highest[0]}/{highest[1]} ({highest[2]:.2f}). Lowest: {lowest[0]}/{lowest[1]} ({lowest[2]:.2f})"
        else:
            interpretation = f"Correlation matrix for {len(tickers)} assets"

        return {
            "metric_name": "Correlation Matrix",
            "correlations": correlations,
            "tickers": tickers,
            "params": {"num_assets": len(tickers), "num_observations": len(returns_df)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Correlation Matrix"}


def covariance_matrix(price_dict: dict[str, pd.Series]) -> dict:
    """
    Calculate the covariance matrix for multiple assets.

    Use this for portfolio optimisation and risk calculations.
    Covariance measures how assets move together in absolute terms
    (unlike correlation which is normalised).

    Args:
        price_dict: Dict mapping ticker symbols to price Series

    Returns:
        Dict with covariances (2D dict), underlying returns, and interpretation.
    """
    try:
        if not price_dict or len(price_dict) < 2:
            return {"error": "Need at least 2 assets for covariance matrix", "metric_name": "Covariance Matrix"}

        # Convert prices to returns and align
        returns_dict = {}
        for ticker, prices in price_dict.items():
            if not isinstance(prices, pd.Series):
                return {"error": f"Price series for {ticker} must be a pandas Series", "metric_name": "Covariance Matrix"}

            prices = prices.dropna()
            if len(prices) < 2:
                return {"error": f"Not enough data for {ticker}", "metric_name": "Covariance Matrix"}

            returns_dict[ticker] = prices.pct_change().dropna()

        # Build DataFrame
        returns_df = pd.DataFrame(returns_dict).dropna()

        if len(returns_df) < 10:
            return {"error": "Insufficient overlapping data (need at least 10 common dates)", "metric_name": "Covariance Matrix"}

        # Calculate covariance matrix
        cov_matrix = returns_df.cov()
        tickers = list(cov_matrix.columns)

        # Convert to nested dict
        covariances = {}
        for t1 in tickers:
            covariances[t1] = {}
            for t2 in tickers:
                covariances[t1][t2] = float(cov_matrix.loc[t1, t2])

        # Average variance and covariance for interpretation
        variances = [covariances[t][t] for t in tickers]
        avg_var = sum(variances) / len(variances)

        interpretation = f"Covariance matrix for {len(tickers)} assets. Average variance: {avg_var:.6f}"

        return {
            "metric_name": "Covariance Matrix",
            "covariances": covariances,
            "tickers": tickers,
            "params": {"num_assets": len(tickers), "num_observations": len(returns_df)},
            "interpretation": interpretation,
        }
    except Exception as e:
        return {"error": str(e), "metric_name": "Covariance Matrix"}
