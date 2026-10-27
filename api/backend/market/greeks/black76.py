"""Owned, vectorized Black-76 pricing + Greeks (numpy/scipy only).

This module is the deterministic risk path — no numba, no py_vollib, no
JIT warm-up: plain vectorized numpy over the whole strike array. A
600-strike chain prices + full first-order Greeks in well under a
millisecond, which clears the few-second screening cadence with two
orders of magnitude of headroom. ``py_vollib`` (scalar) is the unit-test
oracle for this module, never a runtime dependency.

Formulas (forward F, strike K, vol σ, time T years, discount rate r):

    d1 = [ln(F/K) + 0.5·σ²·T] / (σ·√T)        d2 = d1 − σ·√T
    Call = e^(−rT)·[F·N(d1) − K·N(d2)]
    Put  = e^(−rT)·[K·N(−d2) − F·N(−d1)]

Greeks conventions (retail-display units, pinned in the package
docstring): delta w.r.t. the future; gamma per ₹ of future; theta per
calendar DAY; vega per 1 vol PERCENTAGE POINT; rho = −T·price (pure
discounting; tiny intraday, reported for completeness).

Degenerate inputs (T ≤ 0 or σ ≤ 0) collapse to discounted intrinsic with
step-function delta — never NaN.
"""
from __future__ import annotations

from datetime import datetime, time as dt_time
from typing import Mapping

import numpy as np
import pytz
from scipy.special import ndtr  # vectorized standard-normal CDF

IST = pytz.timezone("Asia/Kolkata")

_SQRT_2PI = float(np.sqrt(2.0 * np.pi))

# Session close per exchange segment — the expiry instant the T-clock
# counts down to. NSE/BSE derivatives stop at 15:30 IST; MCX runs the
# evening session (energy/metals track global hours).
_SEGMENT_CLOSE: Mapping[str, dt_time] = {
    "NFO-OPT": dt_time(15, 30),
    "NFO-FUT": dt_time(15, 30),
    "BFO-OPT": dt_time(15, 30),
    "BFO-FUT": dt_time(15, 30),
    "MCX-OPT": dt_time(23, 30),
    "MCX-FUT": dt_time(23, 30),
}
_DEFAULT_CLOSE = dt_time(15, 30)


def year_fraction(expiry, *, segment: str = "NFO-OPT", now: datetime | None = None) -> float:
    """Calendar-365 year fraction from ``now`` to expiry-day session close.

    Intraday clock — on expiry week the hours remaining matter (a weekly
    option loses ~20% of its remaining life per trading day; a
    date-resolution T mis-states theta by a full day). ``expiry`` is a
    ``datetime.date``. Floors at 0.0 (expired)."""
    close = _SEGMENT_CLOSE.get(segment, _DEFAULT_CLOSE)
    expiry_dt = IST.localize(datetime.combine(expiry, close))
    now_dt = now or datetime.now(IST)
    if now_dt.tzinfo is None:
        now_dt = IST.localize(now_dt)
    seconds = (expiry_dt - now_dt).total_seconds()
    return max(seconds, 0.0) / (365.0 * 24.0 * 3600.0)


def _phi(x: np.ndarray) -> np.ndarray:
    """Standard normal pdf, vectorized."""
    return np.exp(-0.5 * x * x) / _SQRT_2PI


def black76_price(
    F, K, sigma, T, r: float = 0.065, flag=1,
) -> np.ndarray:
    """Black-76 option price. ``flag`` +1 call / −1 put (scalar or array).

    All array args broadcast; returns ndarray. Degenerate (T≤0 or σ≤0)
    elements price at discounted intrinsic."""
    F = np.asarray(F, dtype=float)
    K = np.asarray(K, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    T = np.asarray(T, dtype=float)
    flag = np.asarray(flag, dtype=float)

    df = np.exp(-r * np.maximum(T, 0.0))
    intrinsic = df * np.maximum(flag * (F - K), 0.0)

    live = (T > 0.0) & (sigma > 0.0) & (F > 0.0) & (K > 0.0)
    sqrtT = np.sqrt(np.where(live, T, 1.0))
    sig_sqrtT = np.where(live, sigma, 1.0) * sqrtT
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(np.where(live, F / K, 1.0)) + 0.5 * sigma * sigma * T) / sig_sqrtT
    d2 = d1 - sig_sqrtT
    bs = df * flag * (F * ndtr(flag * d1) - K * ndtr(flag * d2))
    return np.where(live, bs, intrinsic)


def black76_greeks(
    F, K, sigma, T, r: float = 0.065, flag=1,
) -> dict[str, np.ndarray]:
    """Price + first-order Greeks, vectorized. Returns dict of ndarrays:
    ``price, delta, gamma, theta, vega, rho``.

    Units: theta per calendar day; vega per 1 vol point (i.e. σ moving
    0.20→0.21); delta/gamma w.r.t. the future; rho = −T·price."""
    F = np.asarray(F, dtype=float)
    K = np.asarray(K, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    T = np.asarray(T, dtype=float)
    flag = np.asarray(flag, dtype=float)

    df = np.exp(-r * np.maximum(T, 0.0))
    live = (T > 0.0) & (sigma > 0.0) & (F > 0.0) & (K > 0.0)

    sqrtT = np.sqrt(np.where(live, T, 1.0))
    sig_sqrtT = np.where(live, sigma, 1.0) * sqrtT
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(np.where(live, F / K, 1.0)) + 0.5 * sigma * sigma * T) / sig_sqrtT
    d2 = d1 - sig_sqrtT
    pdf_d1 = _phi(d1)

    price = df * flag * (F * ndtr(flag * d1) - K * ndtr(flag * d2))
    delta = df * flag * ndtr(flag * d1)
    gamma = df * pdf_d1 / (F * sig_sqrtT)
    # Vega per 1.00 of vol, then scaled to per-percentage-point below.
    vega_raw = df * F * pdf_d1 * sqrtT
    # Theta (per year): dV/dt = −∂V/∂T = r·price − e^(−rT)·F·φ(d1)·σ/(2√T)
    theta_yr = r * price - df * F * pdf_d1 * sigma / (2.0 * sqrtT)

    intrinsic = df * np.maximum(flag * (F - K), 0.0)
    # Step delta at the degenerate limit (deep ITM ≈ ±df, OTM 0).
    step_delta = df * flag * (flag * (F - K) > 0.0)

    out = {
        "price": np.where(live, price, intrinsic),
        "delta": np.where(live, delta, step_delta),
        "gamma": np.where(live, gamma, 0.0),
        "theta": np.where(live, theta_yr / 365.0, 0.0),
        "vega": np.where(live, vega_raw / 100.0, 0.0),
        "rho": np.where(live, -T * price, 0.0),
    }
    return out
