# Returns & Performance Calculations for Pivot
# Layer 3: Pure computation functions for portfolio analytics

from .returns import (
    simple_return,
    log_return,
    cumulative_returns,
    annualised_return,
    rolling_returns,
    period_returns,
)

from .risk_metrics import (
    volatility,
    downside_deviation,
    max_drawdown,
    value_at_risk,
    conditional_var,
    beta,
    correlation_matrix,
    covariance_matrix,
)

from .performance_metrics import (
    sharpe_ratio,
    sortino_ratio,
    calmar_ratio,
    treynor_ratio,
    information_ratio,
    alpha,
    omega_ratio,
)

from .comparison import (
    compare_assets,
    relative_performance,
    ranking,
)

__all__ = [
    # Returns
    "simple_return",
    "log_return",
    "cumulative_returns",
    "annualised_return",
    "rolling_returns",
    "period_returns",
    # Risk metrics
    "volatility",
    "downside_deviation",
    "max_drawdown",
    "value_at_risk",
    "conditional_var",
    "beta",
    "correlation_matrix",
    "covariance_matrix",
    # Performance metrics
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "treynor_ratio",
    "information_ratio",
    "alpha",
    "omega_ratio",
    # Comparison
    "compare_assets",
    "relative_performance",
    "ranking",
]
