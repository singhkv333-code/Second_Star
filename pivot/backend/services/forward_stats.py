"""Forward-test statistical primitives (PSR / MinTRL / DSR / drawdown).

Companion to ``backtest_metrics`` for the live forward-testing scorecards.
Where ``backtest_metrics.sharpe_sortino`` returns the ANNUALIZED,
rounded display Sharpe used on backtest cards, this module exposes the
RAW per-period statistics needed by the Bailey & Lopez de Prado track-record
machinery (PSR, MinTRL, DSR). Mixing the two is a bug — feed the rounded
annualized number into ``psr`` and you will get garbage out.

Canonical references:
  * Bailey & Lopez de Prado (2012) "The Sharpe Ratio Efficient Frontier" — PSR.
  * Bailey & Lopez de Prado (2012) "The Strategy Approval Decision" — MinTRL.
  * Bailey & Lopez de Prado (2014) "The Deflated Sharpe Ratio" — DSR.

Pure stdlib (math + statistics). No numpy / scipy. Inputs are
``Sequence[float]``; outputs are ``Optional[float]`` so degenerate
inputs (n<2, zero dispersion, divide-by-zero) collapse to ``None``
instead of raising — every caller already null-guards.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from backend.services.backtest_metrics import (  # noqa: F401 — re-exported helpers
    DEFAULT_RF_ANNUAL,
    _TRADING_DAYS,
    daily_returns_from_equity,
)

# Euler-Mascheroni constant — used in the DSR expected-max-SR adjustment.
_EULER_MASCHERONI = 0.5772156649015329


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean(returns: Sequence[float]) -> list[float]:
    """Drop None / non-finite entries; cast to float."""
    out: list[float] = []
    for r in returns:
        if r is None:
            continue
        try:
            v = float(r)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out.append(v)
    return out


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _std_ddof1(xs: Sequence[float], mu: Optional[float] = None) -> float:
    """Sample standard deviation (n-1 in denominator)."""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs) if mu is None else mu
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


# ---------------------------------------------------------------------------
# Normal CDF / PPF (math-only, no scipy)
# ---------------------------------------------------------------------------

def _norm_cdf(z: float) -> float:
    """Standard-normal CDF Phi(z) via the error function (Abramowitz 26.2.29).

    ``math.erf`` is C-accurate; this just wraps it. Result is in (0, 1).
    """
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# Beasley-Springer-Moro / Acklam rational approximation for the inverse
# standard-normal CDF. Max abs error ~1.15e-9 in (0, 1).
_ACKLAM_A = (
    -3.969683028665376e+01,
     2.209460984245205e+02,
    -2.759285104469687e+02,
     1.383577518672690e+02,
    -3.066479806614716e+01,
     2.506628277459239e+00,
)
_ACKLAM_B = (
    -5.447609879822406e+01,
     1.615858368580409e+02,
    -1.556989798598866e+02,
     6.680131188771972e+01,
    -1.328068155288572e+01,
)
_ACKLAM_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e+00,
    -2.549732539343734e+00,
     4.374664141464968e+00,
     2.938163982698783e+00,
)
_ACKLAM_D = (
     7.784695709041462e-03,
     3.224671290700398e-01,
     2.445134137142996e+00,
     3.754408661907416e+00,
)
_ACKLAM_PLOW = 0.02425
_ACKLAM_PHIGH = 1.0 - _ACKLAM_PLOW


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF Phi^{-1}(p) via Acklam's rational approx.

    Returns ``-inf`` for p<=0 and ``+inf`` for p>=1 (mathematically correct;
    callers either clamp p ahead of time or treat infinities as "undefined").
    """
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    # Lower tail
    if p < _ACKLAM_PLOW:
        q = math.sqrt(-2.0 * math.log(p))
        a = _ACKLAM_C
        b = _ACKLAM_D
        num = ((((a[0] * q + a[1]) * q + a[2]) * q + a[3]) * q + a[4]) * q + a[5]
        den = (((b[0] * q + b[1]) * q + b[2]) * q + b[3]) * q + 1.0
        return num / den
    # Upper tail (mirror image of the lower tail)
    if p > _ACKLAM_PHIGH:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        a = _ACKLAM_C
        b = _ACKLAM_D
        num = ((((a[0] * q + a[1]) * q + a[2]) * q + a[3]) * q + a[4]) * q + a[5]
        den = (((b[0] * q + b[1]) * q + b[2]) * q + b[3]) * q + 1.0
        return -num / den
    # Central region
    q = p - 0.5
    r = q * q
    ca = _ACKLAM_A
    cb = _ACKLAM_B
    num = (((((ca[0] * r + ca[1]) * r + ca[2]) * r + ca[3]) * r + ca[4]) * r + ca[5]) * q
    den = ((((cb[0] * r + cb[1]) * r + cb[2]) * r + cb[3]) * r + cb[4]) * r + 1.0
    return num / den


# ---------------------------------------------------------------------------
# Moment estimators
# ---------------------------------------------------------------------------

def observed_sharpe(returns: Sequence[float]) -> Optional[float]:
    """Per-period observed Sharpe ratio = mean(r) / std(r, ddof=1).

    NO annualization, NO rounding. This is the SR_hat that feeds PSR/MinTRL/DSR.
    Returns ``None`` if n<2 or std is non-positive.
    """
    rs = _clean(returns)
    if len(rs) < 2:
        return None
    mu = _mean(rs)
    sd = _std_ddof1(rs, mu)
    if sd <= 0.0:
        return None
    return mu / sd


def skewness(returns: Sequence[float]) -> Optional[float]:
    """Skewness = E[(r-mu)^3] / sigma^3.

    Population 3rd moment in the numerator (sum / n), sample sigma (ddof=1) in
    the denominator — the convention used by Bailey & Lopez de Prado (gamma_3)
    in the PSR / MinTRL formulas. Returns ``None`` on degenerate input.
    """
    rs = _clean(returns)
    n = len(rs)
    if n < 2:
        return None
    mu = _mean(rs)
    sd = _std_ddof1(rs, mu)
    if sd <= 0.0:
        return None
    m3 = sum((x - mu) ** 3 for x in rs) / n
    return m3 / (sd ** 3)


def kurtosis(returns: Sequence[float], *, excess: bool = False) -> Optional[float]:
    """Kurtosis = E[(r-mu)^4] / sigma^4 (raw; normal distribution = 3).

    Same hybrid convention as ``skewness`` (population 4th moment over sample
    sigma^4) so this matches the gamma_4 used in PSR's variance term. Pass
    ``excess=True`` for the more common "excess" form (subtracts 3, normal=0).
    Returns ``None`` on degenerate input.
    """
    rs = _clean(returns)
    n = len(rs)
    if n < 2:
        return None
    mu = _mean(rs)
    sd = _std_ddof1(rs, mu)
    if sd <= 0.0:
        return None
    m4 = sum((x - mu) ** 4 for x in rs) / n
    k = m4 / (sd ** 4)
    return k - 3.0 if excess else k


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------

def max_drawdown_pct(equity: Sequence[float]) -> Optional[float]:
    """Running-peak max drawdown of an equity curve, as a NEGATIVE percent.

    Returns 0.0 for a monotone-non-decreasing curve, ``None`` if fewer than
    2 finite points. Used for the ``mdd`` scorecard field; max DD is
    deliberately NOT in ``backtest_metrics`` so the live and backtest curves
    can use the same definition here.
    """
    pts: list[float] = []
    for v in equity:
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x):
            pts.append(x)
    if len(pts) < 2:
        return None
    peak = pts[0]
    worst = 0.0
    for x in pts:
        if x > peak:
            peak = x
        if peak > 0.0:
            dd = (x / peak) - 1.0  # <= 0
            if dd < worst:
                worst = dd
    return worst * 100.0


# ---------------------------------------------------------------------------
# Probabilistic / Deflated Sharpe Ratio
# ---------------------------------------------------------------------------

def _psr_variance_term(sr_hat: float, skew: float, kurt: float) -> Optional[float]:
    """Variance-style scalar  1 - gamma_3*SR + ((gamma_4-1)/4)*SR^2.

    The B&LdP formula models the variance of SR_hat (around the true SR)
    given non-normal moments. Must stay > 0 for the formulas to be defined.
    """
    val = 1.0 - skew * sr_hat + ((kurt - 1.0) / 4.0) * (sr_hat ** 2)
    if not math.isfinite(val) or val <= 0.0:
        return None
    return val


def psr(
    sharpe_hat: Optional[float],
    n: int,
    skew: Optional[float],
    kurt: Optional[float],
    *,
    sr_threshold: float = 0.0,
) -> Optional[float]:
    """Probabilistic Sharpe Ratio: P(true_SR > sr_threshold | SR_hat, moments).

    Formula (Bailey & Lopez de Prado, 2012):
        PSR = Phi(  (SR_hat - SR*) * sqrt(n - 1)
                  / sqrt(1 - gamma_3*SR_hat + ((gamma_4 - 1)/4)*SR_hat^2)  )

    where gamma_3 is skewness, gamma_4 is the RAW (non-excess) kurtosis
    (normal = 3), Phi is the standard-normal CDF. Returns a probability in
    (0, 1) or ``None`` on degenerate / invalid inputs.
    """
    if sharpe_hat is None or skew is None or kurt is None:
        return None
    if n is None or n < 2:
        return None
    var_term = _psr_variance_term(sharpe_hat, skew, kurt)
    if var_term is None:
        return None
    denom = math.sqrt(var_term)
    if denom <= 0.0:
        return None
    z = (sharpe_hat - sr_threshold) * math.sqrt(n - 1) / denom
    return _norm_cdf(z)


def min_track_record_length(
    sharpe_hat: Optional[float],
    skew: Optional[float],
    kurt: Optional[float],
    *,
    sr_threshold: float = 0.0,
    confidence: float = 0.95,
) -> Optional[float]:
    """Minimum Track Record Length — observations needed for PSR >= confidence.

    Formula:
        MinTRL = 1 + [1 - gamma_3*SR_hat + ((gamma_4 - 1)/4)*SR_hat^2]
                     * (Z_alpha / (SR_hat - SR*)) ** 2

    where Z_alpha = Phi^{-1}(confidence). Units are observations (compare
    against ``n_obs``). Returns ``None`` if SR_hat <= SR* (un-passable) or
    the variance term is non-positive.
    """
    if sharpe_hat is None or skew is None or kurt is None:
        return None
    if not 0.0 < confidence < 1.0:
        return None
    excess_sr = sharpe_hat - sr_threshold
    if excess_sr <= 0.0:
        return None
    var_term = _psr_variance_term(sharpe_hat, skew, kurt)
    if var_term is None:
        return None
    z_alpha = _norm_ppf(confidence)
    if not math.isfinite(z_alpha):
        return None
    return 1.0 + var_term * (z_alpha / excess_sr) ** 2


def _expected_max_sr(num_trials: int, sr_variance: float) -> float:
    """Expected maximum SR across ``num_trials`` independent trials.

    Bailey & Lopez de Prado (2014) eq. 6:
        E[max SR_hat] = sqrt(V) * [ (1 - gamma_e) * Phi^{-1}(1 - 1/N)
                                  + gamma_e      * Phi^{-1}(1 - 1/(N*e)) ]

    With ``num_trials <= 1`` the inverse-normal arguments would be 0/1 (i.e.
    ``Phi^{-1}`` blows up), so we clamp SR0 = 0 — DSR collapses to PSR(0).
    """
    if num_trials <= 1 or sr_variance <= 0.0:
        return 0.0
    p1 = 1.0 - 1.0 / num_trials
    p2 = 1.0 - 1.0 / (num_trials * math.e)
    # Clamp to (eps, 1-eps) — for very large N these approach 1.0 and PPF -> +inf.
    eps = 1e-12
    p1 = min(max(p1, eps), 1.0 - eps)
    p2 = min(max(p2, eps), 1.0 - eps)
    z1 = _norm_ppf(p1)
    z2 = _norm_ppf(p2)
    return math.sqrt(sr_variance) * ((1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2)


def deflated_sharpe_ratio(
    sharpe_hat: Optional[float],
    n: int,
    skew: Optional[float],
    kurt: Optional[float],
    num_trials: int,
    *,
    sr_variance: Optional[float] = None,
) -> Optional[float]:
    """Deflated Sharpe Ratio = PSR(sharpe_hat | sr_threshold = E[max SR]).

    DSR penalises multiple-trials selection bias: with ``num_trials`` candidate
    strategies, the best in-sample SR is biased upward by SR0 = E[max SR_hat].
    A live SR_hat must beat SR0 (not 0) to pass.

    ``sr_variance`` defaults to the documented fallback
    ``V_hat = (1 - gamma_3*SR_hat + ((gamma_4 - 1)/4)*SR_hat^2) / (n - 1)``
    (the variance of the SR estimator under non-normality).

    N <= 1 -> SR0 = 0 -> DSR == PSR(0) (no deflation, single trial).
    """
    if sharpe_hat is None or skew is None or kurt is None:
        return None
    if n is None or n < 2:
        return None
    var_term = _psr_variance_term(sharpe_hat, skew, kurt)
    if var_term is None:
        return None
    if sr_variance is None:
        sr_variance = var_term / (n - 1)
    if sr_variance < 0.0:
        return None
    sr0 = _expected_max_sr(num_trials, sr_variance)
    return psr(sharpe_hat, n, skew, kurt, sr_threshold=sr0)


__all__ = [
    "DEFAULT_RF_ANNUAL",
    "daily_returns_from_equity",
    "observed_sharpe",
    "skewness",
    "kurtosis",
    "max_drawdown_pct",
    "psr",
    "min_track_record_length",
    "deflated_sharpe_ratio",
]
