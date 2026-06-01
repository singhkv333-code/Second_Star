"""Cointegration + mean-reversion math for pairs trading — pure numpy.

statsmodels is intentionally NOT a dependency, so the Augmented Dickey-Fuller
regression and the Engle-Granger two-step are implemented here from first
principles and validated on synthetic series (a constructed cointegrated pair
must test cointegrated; two independent random walks must not).

Conventions
-----------
* ``adf_tstat`` returns the t-statistic on the level coefficient of the
  ADF regression; *more negative* = stronger evidence of stationarity. Lag order
  is chosen by AIC up to the Schwert upper bound.
* Critical values are MacKinnon asymptotic values. ``ADF_CRIT`` (with constant)
  is the standard unit-root test; ``EG_CRIT_2VAR`` is the Engle-Granger
  residual-test surface for two variables (cointegrating regression carries the
  constant, so the residual ADF is run with no deterministic term).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

# MacKinnon asymptotic critical values.
ADF_CRIT = {"1%": -3.43, "5%": -2.86, "10%": -2.57}          # unit-root, constant
EG_CRIT_2VAR = {"1%": -3.90, "5%": -3.34, "10%": -3.04}      # Engle-Granger, N=2


def _ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ordinary least squares. Returns ``(beta, se, resid)`` where ``se`` are the
    classical coefficient standard errors."""
    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    dof = max(n - k, 1)
    sigma2 = float(resid @ resid) / dof
    try:
        xtx_inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        xtx_inv = np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.maximum(np.diag(sigma2 * xtx_inv), 0.0))
    return beta, se, resid


def hedge_ratio(y: Sequence[float], x: Sequence[float]) -> tuple[float, float]:
    """OLS hedge ratio of ``y`` on ``x`` with an intercept. Returns ``(alpha, beta)``
    so that ``y ≈ alpha + beta·x`` and the spread is ``y - beta·x``."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    X = np.column_stack([np.ones(x.size), x])
    beta, _, _ = _ols(y, X)
    return float(beta[0]), float(beta[1])


def _schwert_maxlag(n: int) -> int:
    return int(np.floor(12.0 * (n / 100.0) ** 0.25))


def adf_tstat(
    series: Sequence[float],
    *,
    regression: str = "c",
    max_lag: Optional[int] = None,
) -> tuple[float, int, int]:
    """Augmented Dickey-Fuller t-statistic on the level coefficient.

    ``regression``: ``"c"`` includes a constant, ``"n"`` includes no deterministic
    term (used for the Engle-Granger residual test). Lag order minimises AIC over
    ``0..max_lag``. Returns ``(tstat, used_lag, nobs)``.
    """
    y = np.asarray(series, dtype=float)
    n = y.size
    if n < 8:
        return float("nan"), 0, 0
    if max_lag is None:
        max_lag = _schwert_maxlag(n)
    max_lag = max(0, min(max_lag, (n // 2) - 2))

    dy = np.diff(y)            # length n-1
    T = dy.size
    add_const = regression == "c"

    best: Optional[tuple[float, float, int, int]] = None  # (aic, tstat, lag, nobs)
    for p in range(0, max_lag + 1):
        m = T - p             # usable rows
        k = 1 + p + (1 if add_const else 0)   # level + p diff-lags (+ const)
        if m < k + 3:
            continue
        yreg = dy[p:T]                        # Δy_t
        cols = [y[p:T]]                       # y_{t-1} (level)
        for i in range(1, p + 1):
            cols.append(dy[p - i:T - i])      # Δy_{t-i}
        if add_const:
            cols.insert(0, np.ones(m))
        X = np.column_stack(cols)
        beta, se, resid = _ols(yreg, X)
        gamma_idx = 1 if add_const else 0     # the level coefficient
        gamma_se = se[gamma_idx]
        if not np.isfinite(gamma_se) or gamma_se <= 0:
            continue
        tstat = float(beta[gamma_idx] / gamma_se)
        ssr = float(resid @ resid)
        if ssr <= 0:
            continue
        aic = m * np.log(ssr / m) + 2 * k
        if best is None or aic < best[0]:
            best = (aic, tstat, p, m)

    if best is None:
        return float("nan"), 0, 0
    return best[1], best[2], best[3]


def _verdict(tstat: float, crit: dict) -> Optional[str]:
    """The strongest significance level the t-stat clears, or ``None``."""
    if not np.isfinite(tstat):
        return None
    for level in ("1%", "5%", "10%"):
        if tstat < crit[level]:
            return level
    return None


def ou_half_life(spread: Sequence[float]) -> Optional[float]:
    """Mean-reversion half-life (in periods) of an Ornstein-Uhlenbeck fit.

    Regress ``Δs_t`` on ``s_{t-1}`` (with constant); ``b`` is the speed term.
    ``half_life = -ln(2) / b`` when ``b < 0`` (mean-reverting), else ``None``.
    """
    s = np.asarray(spread, dtype=float)
    if s.size < 4:
        return None
    ds = np.diff(s)
    lag = s[:-1]
    X = np.column_stack([np.ones(lag.size), lag])
    beta, _, _ = _ols(ds, X)
    b = float(beta[1])
    if b >= 0 or not np.isfinite(b):
        return None
    hl = -np.log(2.0) / b
    return float(hl) if np.isfinite(hl) and hl > 0 else None


@dataclass
class EngleGrangerResult:
    alpha: float
    beta: float
    adf_tstat: float
    used_lag: int
    nobs: int
    crit_values: dict
    cointegrated_at: Optional[str]   # "1%" / "5%" / "10%" / None
    half_life: Optional[float]
    spread: np.ndarray               # y - beta*x  (the level spread)

    @property
    def is_cointegrated(self) -> bool:
        """At the conventional 5% level."""
        return self.cointegrated_at in ("1%", "5%")


def engle_granger(y: Sequence[float], x: Sequence[float]) -> EngleGrangerResult:
    """Engle-Granger two-step on the dependent ``y`` and the hedge leg ``x``:
    OLS for the hedge ratio, then an ADF unit-root test on the residual spread
    (no constant — the cointegrating regression already carried it)."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    alpha, beta = hedge_ratio(y, x)
    spread = y - beta * x
    tstat, lag, nobs = adf_tstat(spread - spread.mean(), regression="n")
    return EngleGrangerResult(
        alpha=alpha,
        beta=beta,
        adf_tstat=tstat,
        used_lag=lag,
        nobs=nobs,
        crit_values=dict(EG_CRIT_2VAR),
        cointegrated_at=_verdict(tstat, EG_CRIT_2VAR),
        half_life=ou_half_life(spread),
        spread=spread,
    )


def rolling_zscore(
    spread: Sequence[float], window: int
) -> np.ndarray:
    """Causal rolling z-score: ``(s_t - mean_{t-w..t}) / std_{t-w..t}``.

    Uses only data up to and including ``t`` (the value is acted on at ``t+1`` by
    the backtest, so this is look-ahead-free). The first ``window-1`` entries are
    ``NaN``."""
    s = np.asarray(spread, dtype=float)
    n = s.size
    out = np.full(n, np.nan)
    if window < 2 or n < window:
        return out
    for t in range(window - 1, n):
        win = s[t - window + 1:t + 1]
        mu = win.mean()
        sd = win.std(ddof=1)
        out[t] = (s[t] - mu) / sd if sd > 0 else 0.0
    return out
