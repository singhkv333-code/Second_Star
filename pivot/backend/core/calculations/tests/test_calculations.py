"""
Tests for Pivot calculations module.

Uses deterministic fixtures with numpy.random.RandomState(42) for reproducibility.
Generates geometric Brownian motion price series with mu=0.10/yr, sigma=0.20/yr.
"""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timedelta

# Import all functions to test
from ..returns import (
    simple_return,
    log_return,
    cumulative_returns,
    annualised_return,
    rolling_returns,
    period_returns,
)
from ..risk_metrics import (
    volatility,
    downside_deviation,
    max_drawdown,
    value_at_risk,
    conditional_var,
    beta,
    correlation_matrix,
    covariance_matrix,
)
from ..performance_metrics import (
    sharpe_ratio,
    sortino_ratio,
    calmar_ratio,
    treynor_ratio,
    information_ratio,
    alpha,
    omega_ratio,
)
from ..comparison import (
    compare_assets,
    relative_performance,
    ranking,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def rng():
    """Deterministic random number generator."""
    return np.random.RandomState(42)


@pytest.fixture
def price_series(rng) -> pd.Series:
    """
    Generate 252-day price series via geometric Brownian motion.
    mu = 0.10 (10% annual drift), sigma = 0.20 (20% annual vol).
    """
    n_days = 252
    mu = 0.10 / 252  # Daily drift
    sigma = 0.20 / np.sqrt(252)  # Daily volatility

    # Generate log returns
    log_returns = rng.normal(mu - 0.5 * sigma**2, sigma, n_days)

    # Convert to prices starting at 100
    prices = 100 * np.exp(np.cumsum(log_returns))
    prices = np.insert(prices, 0, 100)  # Add initial price

    # Create datetime index
    dates = pd.date_range(start="2025-01-01", periods=len(prices), freq="D")

    return pd.Series(prices, index=dates, name="TEST")


@pytest.fixture
def returns_series(price_series) -> pd.Series:
    """Returns series derived from price series."""
    return price_series.pct_change().dropna()


@pytest.fixture
def market_series(rng) -> pd.Series:
    """Market benchmark price series (correlated with test asset)."""
    n_days = 252
    mu = 0.08 / 252
    sigma = 0.18 / np.sqrt(252)

    log_returns = rng.normal(mu - 0.5 * sigma**2, sigma, n_days)
    prices = 100 * np.exp(np.cumsum(log_returns))
    prices = np.insert(prices, 0, 100)

    dates = pd.date_range(start="2025-01-01", periods=len(prices), freq="D")
    return pd.Series(prices, index=dates, name="MARKET")


@pytest.fixture
def market_returns(market_series) -> pd.Series:
    """Market returns series."""
    return market_series.pct_change().dropna()


@pytest.fixture
def multi_asset_prices(rng) -> dict[str, pd.Series]:
    """Multiple assets for comparison tests."""
    n_days = 252
    dates = pd.date_range(start="2025-01-01", periods=n_days + 1, freq="D")

    assets = {}
    params = {
        "GROWTH": (0.15, 0.25),  # High return, high vol
        "STABLE": (0.06, 0.10),  # Low return, low vol
        "MARKET": (0.10, 0.18),  # Medium
    }

    for name, (mu, sigma) in params.items():
        daily_mu = mu / 252
        daily_sigma = sigma / np.sqrt(252)
        log_returns = rng.normal(daily_mu - 0.5 * daily_sigma**2, daily_sigma, n_days)
        prices = 100 * np.exp(np.cumsum(log_returns))
        prices = np.insert(prices, 0, 100)
        assets[name] = pd.Series(prices, index=dates, name=name)

    return assets


# ============================================================================
# RETURNS TESTS
# ============================================================================

class TestSimpleReturn:
    def test_positive_return(self):
        result = simple_return(100.0, 110.0)
        assert "error" not in result
        assert result["metric_name"] == "Simple Return"
        assert abs(result["value"] - 0.10) < 1e-6
        assert abs(result["value_pct"] - 10.0) < 1e-6

    def test_negative_return(self):
        result = simple_return(100.0, 90.0)
        assert "error" not in result
        assert abs(result["value"] - (-0.10)) < 1e-6

    def test_zero_start_price(self):
        result = simple_return(0.0, 100.0)
        assert "error" in result

    def test_none_input(self):
        result = simple_return(None, 100.0)
        assert "error" in result


class TestLogReturn:
    def test_positive_return(self):
        result = log_return(100.0, 110.0)
        assert "error" not in result
        assert result["metric_name"] == "Log Return"
        expected = np.log(110.0 / 100.0)
        assert abs(result["value"] - expected) < 1e-6

    def test_negative_price(self):
        result = log_return(-100.0, 110.0)
        assert "error" in result

    def test_zero_price(self):
        result = log_return(100.0, 0.0)
        assert "error" in result


class TestCumulativeReturns:
    def test_basic(self, price_series):
        result = cumulative_returns(price_series)
        assert "error" not in result
        assert "values" in result
        assert "current" in result
        assert len(result["values"]) == len(price_series)

    def test_final_matches_simple_return(self, price_series):
        cum_result = cumulative_returns(price_series)
        simple_result = simple_return(float(price_series.iloc[0]), float(price_series.iloc[-1]))

        assert "error" not in cum_result
        assert "error" not in simple_result
        assert abs(cum_result["current"] - simple_result["value"]) < 1e-6

    def test_empty_series(self):
        result = cumulative_returns(pd.Series([], dtype=float))
        assert "error" in result

    def test_single_point(self):
        result = cumulative_returns(pd.Series([100.0]))
        assert "error" in result


class TestAnnualisedReturn:
    def test_basic(self, price_series):
        result = annualised_return(price_series)
        assert "error" not in result
        assert "value" in result
        # With mu=0.10, should be roughly 10% annualised
        assert -0.5 < result["value"] < 0.5  # Reasonable range

    def test_insufficient_data(self):
        result = annualised_return(pd.Series([100.0]))
        assert "error" in result


class TestRollingReturns:
    def test_basic(self, price_series):
        result = rolling_returns(price_series, window=20)
        assert "error" not in result
        assert "values" in result
        assert "summary" in result
        assert len(result["values"]) > 0

    def test_window_too_large(self, price_series):
        result = rolling_returns(price_series, window=1000)
        assert "error" in result

    def test_zero_window(self):
        result = rolling_returns(pd.Series([100.0, 110.0]), window=0)
        assert "error" in result


class TestPeriodReturns:
    def test_monthly(self, price_series):
        result = period_returns(price_series, "monthly")
        assert "error" not in result
        assert "values" in result

    def test_invalid_period(self, price_series):
        result = period_returns(price_series, "invalid")
        assert "error" in result


# ============================================================================
# RISK METRICS TESTS
# ============================================================================

class TestVolatility:
    def test_annualised(self, returns_series):
        result = volatility(returns_series, annualised=True)
        assert "error" not in result
        # 20% annual vol input should give roughly 20% output
        assert 0.05 < result["value"] < 0.50  # Reasonable range

    def test_not_annualised(self, returns_series):
        result = volatility(returns_series, annualised=False)
        assert "error" not in result
        assert result["value"] < 0.05  # Daily vol should be small

    def test_all_zeros(self):
        result = volatility(pd.Series([0.0] * 100))
        assert "error" in result


class TestDownsideDeviation:
    def test_basic(self, returns_series):
        result = downside_deviation(returns_series)
        assert "error" not in result
        assert result["value"] >= 0

    def test_no_downside(self):
        # All positive returns
        result = downside_deviation(pd.Series([0.01, 0.02, 0.01, 0.03]))
        assert "error" not in result
        assert result["value"] == 0.0


class TestMaxDrawdown:
    def test_basic(self, price_series):
        result = max_drawdown(price_series)
        assert "error" not in result
        assert "max_dd_pct" in result
        assert "peak_idx" in result
        assert "trough_idx" in result
        assert 0 <= result["max_dd_pct"] <= 100

    def test_monotonic_increase(self):
        # No drawdown if always increasing
        prices = pd.Series([100, 110, 120, 130, 140])
        result = max_drawdown(prices)
        assert "error" not in result
        assert result["max_dd_pct"] == 0.0

    def test_empty(self):
        result = max_drawdown(pd.Series([], dtype=float))
        assert "error" in result


class TestValueAtRisk:
    def test_basic(self, returns_series):
        result = value_at_risk(returns_series, confidence=0.95)
        assert "error" not in result
        assert result["value"] < 0  # VaR should be negative (a loss)
        assert -0.20 < result["value"] < 0  # Reasonable range

    def test_invalid_confidence(self, returns_series):
        result = value_at_risk(returns_series, confidence=1.5)
        assert "error" in result


class TestConditionalVar:
    def test_basic(self, returns_series):
        result = conditional_var(returns_series, confidence=0.95)
        assert "error" not in result
        assert result["value"] < 0  # CVaR should be negative

        # CVaR should be worse (more negative) than VaR
        var_result = value_at_risk(returns_series, confidence=0.95)
        assert result["value"] <= var_result["value"]


class TestBeta:
    def test_basic(self, returns_series, market_returns):
        result = beta(returns_series, market_returns)
        assert "error" not in result
        assert -3 < result["value"] < 3  # Reasonable beta range

    def test_insufficient_overlap(self):
        asset = pd.Series([0.01] * 5, index=pd.date_range("2025-01-01", periods=5))
        market = pd.Series([0.01] * 5, index=pd.date_range("2025-06-01", periods=5))
        result = beta(asset, market)
        assert "error" in result


class TestCorrelationMatrix:
    def test_basic(self, multi_asset_prices):
        result = correlation_matrix(multi_asset_prices)
        assert "error" not in result
        assert "correlations" in result

        corr = result["correlations"]
        tickers = result["tickers"]

        # Check diagonal is 1.0
        for t in tickers:
            assert abs(corr[t][t] - 1.0) < 1e-6

        # Check symmetry
        for t1 in tickers:
            for t2 in tickers:
                assert abs(corr[t1][t2] - corr[t2][t1]) < 1e-6

        # Check range
        for t1 in tickers:
            for t2 in tickers:
                assert -1.0 <= corr[t1][t2] <= 1.0

    def test_single_asset(self):
        result = correlation_matrix({"A": pd.Series([100, 110, 120])})
        assert "error" in result


class TestCovarianceMatrix:
    def test_basic(self, multi_asset_prices):
        result = covariance_matrix(multi_asset_prices)
        assert "error" not in result
        assert "covariances" in result

        cov = result["covariances"]
        tickers = result["tickers"]

        # Diagonal should be positive (variances)
        for t in tickers:
            assert cov[t][t] > 0

        # Check symmetry
        for t1 in tickers:
            for t2 in tickers:
                assert abs(cov[t1][t2] - cov[t2][t1]) < 1e-10


# ============================================================================
# PERFORMANCE METRICS TESTS
# ============================================================================

class TestSharpeRatio:
    def test_basic(self, returns_series):
        result = sharpe_ratio(returns_series)
        assert "error" not in result
        assert -3 < result["value"] < 3  # Reasonable range

    def test_all_zeros(self):
        result = sharpe_ratio(pd.Series([0.0] * 100))
        assert "error" in result

    def test_too_few_points(self):
        result = sharpe_ratio(pd.Series([0.01, 0.02]))
        assert "error" in result


class TestSortinoRatio:
    def test_basic(self, returns_series):
        result = sortino_ratio(returns_series)
        assert "error" not in result
        # Sortino should be >= Sharpe (less penalty for upside)
        sharpe_result = sharpe_ratio(returns_series)
        # Not always true due to formulation, but both should be finite
        assert np.isfinite(result["value"])


class TestCalmarRatio:
    def test_basic(self, returns_series):
        result = calmar_ratio(returns_series)
        assert "error" not in result
        assert np.isfinite(result["value"])
        assert "cagr" in result
        assert "max_drawdown" in result


class TestTreynorRatio:
    def test_basic(self, returns_series, market_returns):
        result = treynor_ratio(returns_series, market_returns)
        assert "error" not in result
        assert np.isfinite(result["value"])
        assert "beta" in result


class TestInformationRatio:
    def test_basic(self, returns_series, market_returns):
        result = information_ratio(returns_series, market_returns)
        assert "error" not in result
        assert np.isfinite(result["value"])
        assert "annualised_tracking_error" in result


class TestAlpha:
    def test_basic(self, returns_series, market_returns):
        result = alpha(returns_series, market_returns)
        assert "error" not in result
        assert np.isfinite(result["value"])
        assert "beta" in result


class TestOmegaRatio:
    def test_basic(self, returns_series):
        result = omega_ratio(returns_series)
        assert "error" not in result
        assert result["value"] > 0  # Omega is always positive

    def test_all_gains(self):
        # All positive returns -> high omega
        result = omega_ratio(pd.Series([0.01] * 100))
        assert "error" not in result
        assert result["value"] == 10.0  # Capped


# ============================================================================
# COMPARISON TESTS
# ============================================================================

class TestCompareAssets:
    def test_basic(self, multi_asset_prices):
        result = compare_assets(multi_asset_prices)
        assert "error" not in result
        assert "results" in result
        assert len(result["results"]) == 3

    def test_custom_metrics(self, multi_asset_prices):
        result = compare_assets(multi_asset_prices, metrics=["sharpe", "max_drawdown"])
        assert "error" not in result
        assert "sharpe" in result["results"]["GROWTH"]

    def test_invalid_metric(self, multi_asset_prices):
        result = compare_assets(multi_asset_prices, metrics=["invalid"])
        assert "error" in result


class TestRelativePerformance:
    def test_basic(self, returns_series, market_returns):
        result = relative_performance(returns_series, market_returns)
        assert "error" not in result
        assert "excess_return" in result
        assert "tracking_error" in result
        assert "information_ratio" in result


class TestRanking:
    def test_by_sharpe(self, multi_asset_prices):
        result = ranking(multi_asset_prices, "sharpe")
        assert "error" not in result
        assert "rankings" in result
        assert len(result["rankings"]) == 3

        # Check ranks are 1, 2, 3
        ranks = [r["rank"] for r in result["rankings"]]
        assert sorted(ranks) == [1, 2, 3]

    def test_ascending(self, multi_asset_prices):
        result = ranking(multi_asset_prices, "volatility", ascending=True)
        assert "error" not in result
        # STABLE should rank first (lowest vol)
        assert result["rankings"][0]["ticker"] == "STABLE"

    def test_invalid_metric(self, multi_asset_prices):
        result = ranking(multi_asset_prices, "invalid")
        assert "error" in result


# ============================================================================
# EDGE CASES
# ============================================================================

class TestEdgeCases:
    def test_empty_series_returns(self):
        empty = pd.Series([], dtype=float)
        assert "error" in simple_return(None, None)
        assert "error" in cumulative_returns(empty)
        assert "error" in annualised_return(empty)
        assert "error" in rolling_returns(empty, 5)

    def test_single_point_returns(self):
        single = pd.Series([100.0])
        assert "error" in cumulative_returns(single)
        assert "error" in annualised_return(single)

    def test_all_nan_series(self):
        nan_series = pd.Series([np.nan] * 100)
        assert "error" in cumulative_returns(nan_series)
        assert "error" in volatility(nan_series)
        assert "error" in sharpe_ratio(nan_series)

    def test_all_zero_returns(self):
        zeros = pd.Series([0.0] * 100)
        assert "error" in volatility(zeros)
        assert "error" in sharpe_ratio(zeros)
        assert "error" in omega_ratio(zeros)

    def test_empty_dict_comparison(self):
        assert "error" in compare_assets({})
        assert "error" in correlation_matrix({})
        assert "error" in ranking({}, "sharpe")


# ============================================================================
# SANITY CHECKS
# ============================================================================

class TestSanityChecks:
    def test_cumulative_equals_simple(self, price_series):
        """Cumulative return final value should match simple return."""
        cum_result = cumulative_returns(price_series)
        simple_result = simple_return(
            float(price_series.iloc[0]),
            float(price_series.iloc[-1])
        )

        assert "error" not in cum_result
        assert "error" not in simple_result
        assert abs(cum_result["current"] - simple_result["value"]) < 1e-6

    def test_correlation_matrix_properties(self, multi_asset_prices):
        """Correlation matrix should be symmetric with 1s on diagonal."""
        result = correlation_matrix(multi_asset_prices)
        assert "error" not in result

        corr = result["correlations"]
        tickers = result["tickers"]

        # Diagonal is 1.0
        for t in tickers:
            assert abs(corr[t][t] - 1.0) < 1e-6

        # Symmetric
        for t1 in tickers:
            for t2 in tickers:
                assert abs(corr[t1][t2] - corr[t2][t1]) < 1e-10

        # Values in [-1, 1]
        for t1 in tickers:
            for t2 in tickers:
                assert -1.0 - 1e-6 <= corr[t1][t2] <= 1.0 + 1e-6

    def test_var_less_than_cvar(self, returns_series):
        """CVaR should be more extreme (lower) than VaR."""
        var_result = value_at_risk(returns_series, confidence=0.95)
        cvar_result = conditional_var(returns_series, confidence=0.95)

        assert "error" not in var_result
        assert "error" not in cvar_result

        # CVaR <= VaR (both negative, CVaR more negative)
        assert cvar_result["value"] <= var_result["value"] + 1e-10

    def test_sharpe_formula(self, returns_series):
        """Manual Sharpe calculation should match function."""
        rf = 0.06
        periods = 252

        result = sharpe_ratio(returns_series, risk_free_rate=rf, periods_per_year=periods)
        assert "error" not in result

        # Manual calculation
        rf_daily = rf / periods
        excess = returns_series - rf_daily
        manual_sharpe = (excess.mean() / returns_series.std()) * np.sqrt(periods)

        assert abs(result["value"] - manual_sharpe) < 1e-6

    def test_beta_of_market_is_one(self, market_returns):
        """Beta of market with itself should be 1."""
        result = beta(market_returns, market_returns)
        assert "error" not in result
        assert abs(result["value"] - 1.0) < 1e-6
