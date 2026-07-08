"""Portfolio weighting schemes for the strategy builder (Workstream B, §3a Step 2).

This module turns a basket of equity symbols into **normalised target weights**
(summing to 1.0) under one of the named :data:`WeightingScheme` policies from
:mod:`backend.services.strategy_contracts`:

  * ``equal``           — 1/N. The only scheme that needs no data.
  * ``mcap``            — market-cap proportional (needs ``mcap``).
  * ``risk_parity``     — equal risk contribution (ERC) from the **shrinkage**
    covariance of ``price_history``.
  * ``min_variance``    — global minimum-variance portfolio from shrinkage cov.
  * ``black_litterman`` — mcap prior blended with chat ``views`` (per-symbol
    expected-return tilts) as the BL view vector, then mean-variance optimised.
  * ``factor``          — factor-score weighting from a value/quality/momentum/
    low-vol blend (quality/value supplied via ``views``-style scores; momentum
    and low-vol derived from ``price_history``).

Design choices (per the approved plan / contract):

  * **Covariance uses shrinkage.** We estimate the daily-return covariance with
    Ledoit-Wolf shrinkage (``sklearn.covariance.LedoitWolf``) when available, and
    fall back to a plain shrink-to-diagonal estimator otherwise. Shrinkage keeps
    the matrix well-conditioned and invertible for the ``min_variance`` /
    ``black_litterman`` optimisers, which a raw sample covariance often is not.

  * **Honest equal-weight fallback.** Covariance-based schemes (``risk_parity`` /
    ``min_variance`` / ``black_litterman``) require enough overlapping history.
    When any symbol has fewer than :data:`MIN_HISTORY_BARS_FOR_COV` usable bars —
    or the optimiser is otherwise ill-posed — :func:`compute_weights` FALLS BACK
    to equal-weight and records the reason on the result so the caller can surface
    "(assumed … → equal-weight)" in the card. We never emit an unreliable fit.

  * **Pure / typed / numpy-based.** Every helper is a pure function with strict
    typing; there is no I/O at import time. The one external dependency is the
    project's existing price-history fetch, which the *caller* performs — this
    module only consumes the ``price_history`` mapping it is handed (so the
    optimisers stay unit-test-friendly with synthetic series).

Reuse: ``price_history`` follows the same per-symbol shape produced by
:func:`backend.core.data.historical.get_close_dict` / ``get_multiple_ohlcv``
(a Close ``pd.Series`` or an OHLCV ``pd.DataFrame``); :func:`_close_series`
normalises either form (and a raw list-of-dict records fallback) to a Close
series before returns are computed, mirroring the convention in
:func:`backend.core.calculations.risk_metrics.covariance_matrix`.

Style: ``from __future__ import annotations``, numpy/pandas, strict typing, no
import-time side effects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import numpy as np
import pandas as pd

from backend.services.strategy_contracts import (
    MIN_HISTORY_BARS_FOR_COV,
    WeightingScheme,
)

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
# Tunable constants (all decision-relevant numbers live here, not inline)
# ════════════════════════════════════════════════════════════════════════════

_MIN_OVERLAP_BARS: int = 10
"""Minimum *overlapping* daily-return rows (after aligning the basket) before a
covariance fit is trusted. Mirrors the ``len(returns_df) < 10`` floor in
``risk_metrics.covariance_matrix``; the per-symbol floor is the stricter
:data:`MIN_HISTORY_BARS_FOR_COV`."""

_DIAG_SHRINK_INTENSITY: float = 0.10
"""Shrink intensity for the manual shrink-to-diagonal fallback (used only when
``sklearn`` is unavailable). ``Σ_shrunk = (1-δ)·Σ_sample + δ·diag(Σ_sample)``."""

_RIDGE_EPS: float = 1e-8
"""Tiny ridge added to the covariance diagonal before inversion so a (rare)
singular shrunk matrix still inverts. Negligible vs. daily-return variances."""

_ERC_MAX_ITERS: int = 500
"""Max iterations for the cyclical-coordinate ERC (risk-parity) solver."""

_ERC_TOL: float = 1e-8
"""Convergence tolerance on the ERC weight update (L1 step size)."""

_BL_TAU: float = 0.05
"""Black-Litterman prior-uncertainty scalar τ (scales the prior covariance of
the equilibrium returns). 0.025-0.05 is the common retail range."""

_BL_RISK_AVERSION: float = 2.5
"""Black-Litterman risk-aversion δ used to imply equilibrium returns from the
mcap prior (Π = δ·Σ·w_mkt). ~2.5 is the standard market value."""

# Factor-blend weights (value / quality / momentum / low-vol). Equal blend by
# default — the builder can re-weight later; kept here so the mix is explicit.
_FACTOR_BLEND: dict[str, float] = {
    "value": 0.25,
    "quality": 0.25,
    "momentum": 0.25,
    "low_vol": 0.25,
}

_MOMENTUM_LOOKBACK: int = 126
"""Trading days (~6mo) for the momentum factor (trailing total return)."""


# ════════════════════════════════════════════════════════════════════════════
# Result type
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class WeightingResult:
    """A weighting outcome that records *how* it was produced.

    :attr:`weights` always sums to 1.0 and covers exactly the input symbols.
    :attr:`scheme_used` is the scheme that actually ran — it differs from the
    requested scheme when a covariance-based scheme fell back to ``equal``.
    :attr:`fallback_reason` is ``None`` on the happy path, or a human-readable
    sentence the caller can surface as an honest-boundary assumption line."""

    weights: dict[str, float]
    scheme_used: WeightingScheme
    fallback_reason: Optional[str] = None
    diagnostics: dict[str, float] = field(default_factory=dict)

    @property
    def fell_back(self) -> bool:
        """True when the requested scheme could not run and we used equal-weight."""
        return self.fallback_reason is not None


# ════════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════════


def compute_weights(
    symbols: "Sequence[str]",
    scheme: WeightingScheme,
    *,
    price_history: "Mapping[str, object]",
    mcap: Optional["Mapping[str, float]"] = None,
    views: Optional["Mapping[str, float]"] = None,
    factor_emphasis: Optional[str] = None,
) -> dict[str, float]:
    """Return normalised target weights (summing to 1.0) keyed by symbol.

    This is the contract entry point (matches
    :meth:`strategy_contracts.Weighting.compute_weights`). It dispatches to the
    requested :data:`WeightingScheme`, applying the shrinkage-covariance /
    equal-weight-fallback policy for the covariance-based schemes. Callers that
    need the fallback *reason* (to surface "(assumed …)" in the card) should call
    :func:`compute_weights_detailed` and read :attr:`WeightingResult.fallback_reason`.

    ``factor_emphasis`` (one of ``value`` / ``quality`` / ``momentum`` /
    ``low_vol``) only applies to ``scheme == "factor"`` — it tilts the factor
    blend toward the requested style so "a strategy that benefits from momentum"
    produces a genuinely momentum-led basket rather than an equal factor blend.

    See the module docstring for the per-scheme definitions and the covariance
    policy. Weights are clipped to ``>= 0`` and renormalised before return.
    """
    return compute_weights_detailed(
        symbols,
        scheme,
        price_history=price_history,
        mcap=mcap,
        views=views,
        factor_emphasis=factor_emphasis,
    ).weights


def compute_weights_detailed(
    symbols: "Sequence[str]",
    scheme: WeightingScheme,
    *,
    price_history: "Mapping[str, object]",
    mcap: Optional["Mapping[str, float]"] = None,
    views: Optional["Mapping[str, float]"] = None,
    factor_emphasis: Optional[str] = None,
) -> WeightingResult:
    """Like :func:`compute_weights` but returns the full :class:`WeightingResult`.

    The detailed form exposes :attr:`WeightingResult.scheme_used` and
    :attr:`WeightingResult.fallback_reason` so the strategy builder can record an
    honest-boundary assumption when a covariance-based scheme falls back to
    equal-weight (thin/illiquid history or an ill-posed optimiser).
    """
    syms = [str(s) for s in symbols if s]
    if not syms:
        return WeightingResult(weights={}, scheme_used="equal")
    if len(syms) == 1:
        return WeightingResult(weights={syms[0]: 1.0}, scheme_used="equal")

    if scheme == "equal":
        return WeightingResult(weights=_equal(syms), scheme_used="equal")

    if scheme == "mcap":
        return _mcap_weights(syms, mcap)

    if scheme == "factor":
        return _factor_weights(
            syms, price_history=price_history, scores=views, emphasis=factor_emphasis
        )

    # ── Covariance-based schemes: risk_parity / min_variance / black_litterman ──
    cov, ret_index, cov_reason = _shrinkage_cov(syms, price_history)
    if cov is None or ret_index is None:
        return WeightingResult(
            weights=_equal(syms),
            scheme_used="equal",
            fallback_reason=(
                f"{scheme} needs a reliable covariance estimate but "
                f"{cov_reason}; defaulted to equal-weight."
            ),
        )

    # ret_index is the subset of syms that survived the history/overlap filter,
    # in covariance row/column order. Symbols dropped for thin history would make
    # the scheme partial/unreliable -> honest equal-weight fallback over the
    # FULL basket rather than silently weighting a subset.
    if list(ret_index) != syms:
        dropped = [s for s in syms if s not in set(ret_index)]
        return WeightingResult(
            weights=_equal(syms),
            scheme_used="equal",
            fallback_reason=(
                f"{scheme} needs ≥{MIN_HISTORY_BARS_FOR_COV} overlapping daily "
                f"bars for every name; {', '.join(dropped)} fell short, so the "
                "basket defaulted to equal-weight."
            ),
        )

    try:
        if scheme == "risk_parity":
            raw = _risk_parity_weights(cov)
        elif scheme == "min_variance":
            raw = _min_variance_weights(cov)
        elif scheme == "black_litterman":
            raw = _black_litterman_weights(syms, cov, mcap=mcap, views=views)
        else:  # pragma: no cover - exhaustive over WeightingScheme
            raise ValueError(f"Unknown weighting scheme: {scheme!r}")
    except (np.linalg.LinAlgError, ValueError, FloatingPointError) as exc:
        logger.warning("weighting %s failed (%s); falling back to equal", scheme, exc)
        return WeightingResult(
            weights=_equal(syms),
            scheme_used="equal",
            fallback_reason=(
                f"{scheme} optimiser was ill-posed ({type(exc).__name__}); "
                "defaulted to equal-weight."
            ),
        )

    weights = _finalise(list(ret_index), raw)
    return WeightingResult(
        weights=weights,
        scheme_used=scheme,
        diagnostics={"n_bars": float(cov.shape[0] and _cov_n_bars(cov))},
    )


# ════════════════════════════════════════════════════════════════════════════
# Scheme implementations (pure, numpy-based)
# ════════════════════════════════════════════════════════════════════════════


def _equal(symbols: list[str]) -> dict[str, float]:
    """1/N weights."""
    w = 1.0 / len(symbols)
    return {s: w for s in symbols}


def _mcap_weights(
    symbols: list[str],
    mcap: Optional["Mapping[str, float]"],
) -> WeightingResult:
    """Market-cap proportional weights. Falls back to equal when mcap is missing
    or non-positive for any name (honest boundary, not a silent zero-weight)."""
    if not mcap:
        return WeightingResult(
            weights=_equal(symbols),
            scheme_used="equal",
            fallback_reason="mcap weighting needs market caps but none were "
            "supplied; defaulted to equal-weight.",
        )
    caps = np.array([float(mcap.get(s, 0.0) or 0.0) for s in symbols], dtype=float)
    if not np.all(np.isfinite(caps)) or np.any(caps <= 0.0) or caps.sum() <= 0.0:
        missing = [s for s, c in zip(symbols, caps) if not (c > 0.0)]
        return WeightingResult(
            weights=_equal(symbols),
            scheme_used="equal",
            fallback_reason=(
                f"mcap weighting is missing a positive market cap for "
                f"{', '.join(missing)}; defaulted to equal-weight."
            ),
        )
    return WeightingResult(
        weights=_finalise(symbols, caps),
        scheme_used="mcap",
    )


def _risk_parity_weights(cov: np.ndarray) -> np.ndarray:
    """Equal-risk-contribution (ERC) weights via cyclical coordinate descent.

    Solves for long-only weights where every asset contributes the same share of
    total portfolio variance, i.e. ``w_i·(Σw)_i`` is constant across ``i``. The
    cyclical-coordinate update is the standard Spinu/Griveau-Billion-Richard-Roncalli
    fixed point and converges monotonically for a PSD ``Σ``; far more robust than
    a generic optimiser and dependency-free.
    """
    n = cov.shape[0]
    w = np.full(n, 1.0 / n, dtype=float)
    target = 1.0 / n  # equal risk budget per asset

    for _ in range(_ERC_MAX_ITERS):
        w_prev = w.copy()
        sigma_w = cov @ w
        for i in range(n):
            # Solve the per-asset quadratic a·w_i^2 + b·w_i - target = 0 with
            # a = Σ_ii, b = Σ_{j≠i} Σ_ij w_j (the cross term, excludes i).
            a = cov[i, i]
            b = sigma_w[i] - cov[i, i] * w[i]
            if a <= 0.0:
                continue
            disc = b * b + 4.0 * a * target
            w_i_new = (-b + np.sqrt(disc)) / (2.0 * a)
            sigma_w += cov[:, i] * (w_i_new - w[i])  # incremental Σw update
            w[i] = w_i_new
        w = np.clip(w, 0.0, None)
        s = w.sum()
        if s <= 0.0:
            raise ValueError("risk-parity weights collapsed to zero")
        w /= s
        if np.abs(w - w_prev).sum() < _ERC_TOL:
            break
    return w


def _min_variance_weights(cov: np.ndarray) -> np.ndarray:
    """Long-only global minimum-variance weights.

    Closed-form unconstrained solution is ``w ∝ Σ⁻¹·1``. When that produces
    negatives (a short leg, which register-not-execute long baskets can't hold)
    we project onto the long-only simplex via the same ERC-style cyclical
    descent restricted to minimum-variance — implemented here as an iterative
    clip-and-renormalise of the analytic solution, which is exact for the
    common case and a safe long-only approximation otherwise.
    """
    n = cov.shape[0]
    ones = np.ones(n, dtype=float)
    inv = _safe_inv(cov)
    raw = inv @ ones
    s = raw.sum()
    if s == 0.0:
        raise ValueError("min-variance solution is degenerate")
    w = raw / s
    if np.all(w >= -1e-9):
        return np.clip(w, 0.0, None)
    # Long-only projection: drop the most-negative name, re-solve on the rest,
    # repeat until all weights are non-negative (active-set style).
    active = np.ones(n, dtype=bool)
    for _ in range(n):
        idx = np.where(active)[0]
        sub = cov[np.ix_(idx, idx)]
        sub_w = _safe_inv(sub) @ np.ones(len(idx))
        sub_w = sub_w / sub_w.sum()
        if np.all(sub_w >= -1e-9):
            full = np.zeros(n, dtype=float)
            full[idx] = np.clip(sub_w, 0.0, None)
            return full
        active[idx[int(np.argmin(sub_w))]] = False
        if active.sum() <= 1:
            break
    full = np.zeros(n, dtype=float)
    full[np.where(active)[0]] = 1.0 / max(active.sum(), 1)
    return full


def _black_litterman_weights(
    symbols: list[str],
    cov: np.ndarray,
    *,
    mcap: Optional["Mapping[str, float]"],
    views: Optional["Mapping[str, float]"],
) -> np.ndarray:
    """Black-Litterman posterior weights: mcap prior + chat views.

    1. **Prior** ``w_mkt`` from market caps (equal-weight if caps absent).
    2. **Implied equilibrium returns** ``Π = δ·Σ·w_mkt``.
    3. **Views** ``P·μ = Q`` — here absolute per-symbol tilts from the parsed
       chat view (``views[symbol]`` is an expected-return delta vs. equilibrium;
       sign = bull/bear, magnitude = conviction). Each view is one row of ``P``.
    4. **Posterior returns** via the canonical BL master formula, then a
       long-only mean-variance map back to weights.

    With no views this reduces to the mcap prior, so it degrades gracefully.
    """
    n = cov.shape[0]
    # 1. prior weights
    if mcap:
        caps = np.array([float(mcap.get(s, 0.0) or 0.0) for s in symbols], dtype=float)
        if caps.sum() > 0 and np.all(caps >= 0):
            w_mkt = caps / caps.sum()
        else:
            w_mkt = np.full(n, 1.0 / n)
    else:
        w_mkt = np.full(n, 1.0 / n)

    # 2. implied equilibrium returns
    pi = _BL_RISK_AVERSION * (cov @ w_mkt)

    view_items = (
        [(symbols.index(s), float(v)) for s, v in views.items()
         if s in symbols and v is not None and np.isfinite(float(v)) and float(v) != 0.0]
        if views else []
    )
    if not view_items:
        # No actionable views -> posterior == prior -> return prior weights.
        return w_mkt

    # 3. build P (k×n), Q (k), Ω (k×k diagonal). Confidence is encoded in the
    #    view magnitude: larger |tilt| -> tighter Ω -> stronger pull.
    k = len(view_items)
    P = np.zeros((k, n), dtype=float)
    Q = np.zeros(k, dtype=float)
    for row, (col, tilt) in enumerate(view_items):
        P[row, col] = 1.0
        Q[row] = tilt
    tau_cov = _BL_TAU * cov
    # Ω from the view variances (Idzorek-lite): diag(P·τΣ·Pᵀ) scaled inversely by
    # conviction (|tilt|). Stronger view -> smaller Ω -> more weight on the view.
    base = np.diag(P @ tau_cov @ P.T)
    conv = np.array([max(abs(t), 1e-6) for _, t in view_items])
    omega = np.diag(np.maximum(base / conv, _RIDGE_EPS))

    # 4. BL master formula for posterior expected returns
    tau_cov_inv = _safe_inv(tau_cov)
    omega_inv = _safe_inv(omega)
    post_cov = _safe_inv(tau_cov_inv + P.T @ omega_inv @ P)
    mu = post_cov @ (tau_cov_inv @ pi + P.T @ omega_inv @ Q)

    # mean-variance map: w ∝ (δΣ)⁻¹ μ, long-only projected
    raw = _safe_inv(_BL_RISK_AVERSION * cov) @ mu
    raw = np.clip(raw, 0.0, None)
    if raw.sum() <= 0.0:
        # Views pushed everything non-positive; fall back to the prior.
        return w_mkt
    return raw / raw.sum()


def _factor_blend(emphasis: Optional[str]) -> dict[str, float]:
    """The factor blend weights, optionally tilted toward one factor.

    With no emphasis this is the balanced :data:`_FACTOR_BLEND`. With an
    ``emphasis`` naming a known factor (``value``/``quality``/``momentum``/
    ``low_vol``) that factor carries the majority of the blend and the rest is
    split evenly, so a "momentum" ask produces a momentum-led composite rather
    than an equal four-factor mix. An unknown emphasis falls back to balanced."""
    if not emphasis or emphasis not in _FACTOR_BLEND:
        return dict(_FACTOR_BLEND)
    lead, rest = 0.55, 0.15
    return {k: (lead if k == emphasis else rest) for k in _FACTOR_BLEND}


def _factor_weights(
    symbols: list[str],
    *,
    price_history: "Mapping[str, object]",
    scores: Optional["Mapping[str, float]"],
    emphasis: Optional[str] = None,
) -> WeightingResult:
    """Factor-score weighting: blend value/quality (fundamental, via ``scores``)
    with momentum/low-vol (derived from ``price_history``).

    ``scores`` carries the caller's pre-computed fundamental factor scores keyed
    by symbol (the builder fills these from the fundamentals DB — higher = better
    value+quality). When absent we proceed on the price-only factors. The final
    composite is a z-scored, blend-weighted sum mapped through a softmax-free
    non-negative normalisation so every selected name keeps a positive weight.

    ``emphasis`` tilts the blend toward one factor (see :func:`_factor_blend`) so
    a factor-style ask ("benefits from momentum") is actually led by that factor.

    Always succeeds (no covariance inversion) — degrades to equal-weight only if
    no factor signal is available at all.
    """
    blend_weights = _factor_blend(emphasis)
    n = len(symbols)
    # Price-derived factors
    momentum = np.full(n, np.nan)
    low_vol = np.full(n, np.nan)
    for i, s in enumerate(symbols):
        ser = _close_series(price_history.get(s) if price_history else None)
        if ser is None or len(ser) < 2:
            continue
        rets = ser.pct_change().dropna()
        if len(rets) >= 2:
            # momentum: trailing total return over the lookback window
            window = ser.iloc[-(_MOMENTUM_LOOKBACK + 1):]
            if len(window) >= 2 and float(window.iloc[0]) > 0:
                momentum[i] = float(window.iloc[-1]) / float(window.iloc[0]) - 1.0
            # low-vol factor: negative annualised vol (lower vol -> higher score)
            low_vol[i] = -float(rets.std()) * np.sqrt(252.0)

    # Fundamental value+quality factor from caller-supplied scores
    fund = np.array(
        [float(scores.get(s, np.nan)) if scores else np.nan for s in symbols],
        dtype=float,
    )

    factor_cols: dict[str, np.ndarray] = {
        "value": fund,      # value+quality composite from the fundamentals DB
        "quality": fund,    # (same source; the DB score blends both)
        "momentum": momentum,
        "low_vol": low_vol,
    }

    composite = np.zeros(n, dtype=float)
    used_blend = 0.0
    for name, col in factor_cols.items():
        z = _zscore(col)
        if z is None:
            continue
        blend = blend_weights[name]
        composite += blend * z
        used_blend += blend

    if used_blend <= 0.0:
        return WeightingResult(
            weights=_equal(symbols),
            scheme_used="equal",
            fallback_reason="no usable factor signal (no fundamental scores and "
            "insufficient price history); defaulted to equal-weight.",
        )

    composite /= used_blend
    # Map z-scores to non-negative weights: shift so the min is a small floor,
    # keeping relative ordering and giving every selected name positive weight.
    shifted = composite - composite.min() + 0.10
    return WeightingResult(weights=_finalise(symbols, shifted), scheme_used="factor")


# ════════════════════════════════════════════════════════════════════════════
# Covariance estimation (shrinkage) + price-history normalisation
# ════════════════════════════════════════════════════════════════════════════


def _shrinkage_cov(
    symbols: list[str],
    price_history: "Mapping[str, object]",
) -> tuple[Optional[np.ndarray], Optional[list[str]], str]:
    """Estimate the shrinkage covariance of daily returns for ``symbols``.

    Returns ``(cov, ordered_symbols, reason)``:
      * On success ``cov`` is an (m×m) PSD shrinkage covariance and
        ``ordered_symbols`` are the ``m`` symbols (in row/column order) that had
        enough history; ``reason`` is "".
      * On failure ``cov`` and ``ordered_symbols`` are ``None`` and ``reason``
        explains why (used verbatim in the equal-weight fallback message).

    Per-symbol history must have ≥ :data:`MIN_HISTORY_BARS_FOR_COV` Close bars;
    after aligning to common dates the overlap must be ≥ :data:`_MIN_OVERLAP_BARS`.
    Shrinkage is Ledoit-Wolf (sklearn) with a shrink-to-diagonal fallback.
    """
    if not price_history:
        return None, None, "no price history was supplied"

    returns: dict[str, pd.Series] = {}
    short: list[str] = []
    for s in symbols:
        ser = _close_series(price_history.get(s))
        if ser is None or len(ser) < MIN_HISTORY_BARS_FOR_COV:
            short.append(s)
            continue
        returns[s] = ser.pct_change().dropna()

    if short:
        return (
            None,
            None,
            f"{', '.join(short)} had fewer than {MIN_HISTORY_BARS_FOR_COV} "
            "daily bars",
        )
    if len(returns) < 2:
        return None, None, "fewer than 2 symbols had usable history"

    returns_df = pd.DataFrame(returns).dropna()
    if len(returns_df) < _MIN_OVERLAP_BARS:
        return (
            None,
            None,
            f"only {len(returns_df)} overlapping return rows "
            f"(need ≥{_MIN_OVERLAP_BARS})",
        )

    ordered = list(returns_df.columns)
    sample = returns_df.to_numpy(dtype=float)
    cov = _ledoit_wolf_cov(sample)
    # ridge for guaranteed invertibility downstream
    cov = cov + _RIDGE_EPS * np.eye(cov.shape[0])
    return cov, ordered, ""


def _ledoit_wolf_cov(returns: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf shrinkage covariance of a (T×n) return matrix.

    Uses ``sklearn.covariance.LedoitWolf`` when importable; otherwise a manual
    shrink-to-diagonal estimator ``(1-δ)·S + δ·diag(S)`` with a fixed intensity.
    Both return an (n×n) PSD matrix scaled to per-period (daily) variance.
    """
    try:
        from sklearn.covariance import LedoitWolf

        lw = LedoitWolf(assume_centered=False).fit(returns)
        return np.asarray(lw.covariance_, dtype=float)
    except Exception as exc:  # pragma: no cover - sklearn missing / fit failure
        logger.info("LedoitWolf unavailable (%s); shrink-to-diagonal fallback", exc)
        sample = np.cov(returns, rowvar=False)
        sample = np.atleast_2d(sample)
        diag = np.diag(np.diag(sample))
        delta = _DIAG_SHRINK_INTENSITY
        return (1.0 - delta) * sample + delta * diag


def _close_series(obj: object) -> Optional[pd.Series]:
    """Coerce one symbol's price history into a clean Close ``pd.Series``.

    Accepts the shapes the project's fetchers hand out (so the builder can pass
    its data through unchanged): a Close ``pd.Series`` (``get_close_dict``), an
    OHLCV ``pd.DataFrame`` (``get_ohlcv`` / ``get_multiple_ohlcv``, Close column
    matched case-insensitively), or a raw ``list[dict]`` of records with a
    ``close``/``Close`` key (the ``fetch_price_history`` record form). Returns a
    sorted, NaN-free float Series, or ``None`` if no Close can be extracted.
    """
    if obj is None:
        return None
    if isinstance(obj, pd.Series):
        ser = obj
    elif isinstance(obj, pd.DataFrame):
        col = next(
            (c for c in obj.columns if str(c).lower() == "close"),
            None,
        )
        if col is None:
            return None
        ser = obj[col]
    elif isinstance(obj, (list, tuple)):
        closes = []
        for rec in obj:
            if isinstance(rec, dict):
                v = rec.get("close", rec.get("Close"))
                if v is not None:
                    closes.append(v)
        if len(closes) < 2:
            return None
        ser = pd.Series(closes, dtype=float)
    else:
        return None

    ser = pd.to_numeric(ser, errors="coerce").dropna()
    if hasattr(ser.index, "is_monotonic_increasing") and not ser.index.is_monotonic_increasing:
        ser = ser.sort_index()
    return ser if len(ser) >= 2 else None


# ════════════════════════════════════════════════════════════════════════════
# Small numeric utilities
# ════════════════════════════════════════════════════════════════════════════


def _safe_inv(mat: np.ndarray) -> np.ndarray:
    """Invert a covariance-like matrix, adding a tiny ridge on singularity."""
    try:
        return np.linalg.inv(mat)
    except np.linalg.LinAlgError:
        return np.linalg.inv(mat + _RIDGE_EPS * np.eye(mat.shape[0]))


def _zscore(col: np.ndarray) -> Optional[np.ndarray]:
    """Z-score a factor column, ignoring NaNs (filled to the column mean).

    Returns ``None`` when the column is all-NaN or has zero spread (no signal)."""
    finite = np.isfinite(col)
    if not finite.any():
        return None
    mean = float(col[finite].mean())
    filled = np.where(finite, col, mean)
    std = float(filled.std())
    if std <= 0.0:
        return None
    return (filled - mean) / std


def _cov_n_bars(cov: np.ndarray) -> float:
    """Diagnostic helper — matrix dimension (number of names), as a float."""
    return float(cov.shape[0])


def _finalise(symbols: list[str], raw: np.ndarray) -> dict[str, float]:
    """Clip to non-negative, renormalise to sum 1.0, and key by symbol.

    A final guard: if the vector collapses to ~0 (all clipped away), revert to
    equal weights so the contract's "weights sum to 1.0" never breaks."""
    w = np.clip(np.asarray(raw, dtype=float), 0.0, None)
    total = w.sum()
    if not np.isfinite(total) or total <= 0.0:
        w = np.full(len(symbols), 1.0 / len(symbols))
    else:
        w = w / total
    return {s: float(wi) for s, wi in zip(symbols, w)}


__all__ = [
    "WeightingResult",
    "compute_weights",
    "compute_weights_detailed",
]
