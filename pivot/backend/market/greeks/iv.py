"""Implied-vol solving with per-strike status — we NEVER fabricate an IV.

Solver preference order:
  1. ``py_vollib_vectorized`` (Jäckel "Let's Be Rational" rational
     approximation — non-iterative, machine precision) when the package
     is installed. Optional dependency: the app must keep working on a
     box where the wheel didn't build (numba toolchain), so…
  2. …the owned fallback: vectorized Newton-Raphson on price→vol with
     analytic Black-76 vega, seeded by the Brenner-Subrahmanyam ATM
     guess, with a scalar Brent bisection rescue for the few strikes
     where NR diverges (vega-collapse deep ITM/OTM).

Every strike gets an ``iv_status``:
  ok              IV solved on the mid price.
  no_arb          mid violates intrinsic/forward no-arbitrage bounds —
                  crossed/locked book or stale junk. NOT solved.
  no_solution     in-bounds but the solver could not converge.
  wide_spread     spread% above the liquidity guard — IV solved anyway
                  but flagged (screeners must treat as low-trust).
  illiquid        zero bid / missing quote — nothing to solve on.
  stale           quote older than the freshness guard.

Statuses other than ``ok``/``wide_spread`` carry ``iv = nan``: a missing
IV is information, a made-up one is a bug that poisons every metric
downstream (IVP, skew, expected move).
"""
from __future__ import annotations

import logging
import os

import numpy as np
from scipy.optimize import brentq

from backend.market.greeks.black76 import black76_greeks, black76_price

logger = logging.getLogger(__name__)

IV_OK = "ok"
IV_NO_ARB = "no_arb"
IV_NO_SOLUTION = "no_solution"
IV_WIDE_SPREAD = "wide_spread"
IV_ILLIQUID = "illiquid"
IV_STALE = "stale"

# Spread% above which an IV is solved-but-flagged. Index ATM trades ~0.1%;
# anything past 5% of mid is an untradeable quote for retail purposes.
WIDE_SPREAD_PCT = float(os.getenv("PIVOT_OPT_WIDE_SPREAD_PCT", "0.05"))

_IV_LO, _IV_HI = 1e-4, 5.0

# py_vollib_vectorized is optional (numba toolchain). Resolve once.
_VECTORIZED_IV = None
if os.getenv("PIVOT_OPT_DISABLE_PYVOLLIB", "0") != "1":
    try:  # pragma: no cover - import path depends on environment
        from py_vollib_vectorized import vectorized_implied_volatility_black

        _VECTORIZED_IV = vectorized_implied_volatility_black
    except Exception:  # ImportError or numba init failure
        _VECTORIZED_IV = None


def _newton_brent_iv(
    price: np.ndarray, F: np.ndarray, K: np.ndarray, T: np.ndarray,
    r: float, flag: np.ndarray,
) -> np.ndarray:
    """Owned fallback solver. Vectorized NR (≤12 iters) + scalar Brent
    rescue on the non-converged mask. Returns nan where unsolvable."""
    # Brenner-Subrahmanyam ATM seed on the undiscounted price.
    undisc = price * np.exp(r * T)
    with np.errstate(divide="ignore", invalid="ignore"):
        seed = np.sqrt(2.0 * np.pi / np.maximum(T, 1e-12)) * undisc / np.maximum(F, 1e-12)
    sigma = np.clip(np.nan_to_num(seed, nan=0.3), 0.05, 3.0)

    tol = np.maximum(1e-8, 1e-6 * price)
    converged = np.zeros_like(sigma, dtype=bool)
    for _ in range(12):
        g = black76_greeks(F, K, sigma, T, r=r, flag=flag)
        diff = g["price"] - price
        converged = np.abs(diff) <= tol
        if converged.all():
            break
        vega_raw = g["vega"] * 100.0  # back to per-1.0-vol
        # Freeze tiny-vega strikes for NR (Brent rescues them below).
        step = np.where(vega_raw > 1e-10, diff / np.maximum(vega_raw, 1e-10), 0.0)
        sigma = np.clip(sigma - np.where(converged, 0.0, step), _IV_LO, _IV_HI)

    out = np.where(converged, sigma, np.nan)

    # Scalar Brent rescue for whatever NR left behind.
    for i in np.flatnonzero(~converged):
        f, k, t, p, fl = float(F.flat[i]), float(K.flat[i]), float(T.flat[i]), float(price.flat[i]), float(flag.flat[i])

        def _obj(s: float) -> float:
            return float(black76_price(f, k, s, t, r=r, flag=fl)) - p

        try:
            lo, hi = _obj(_IV_LO), _obj(_IV_HI)
            if lo * hi <= 0.0:
                out.flat[i] = brentq(_obj, _IV_LO, _IV_HI, xtol=1e-8, maxiter=100)
        except Exception:  # no bracket / numeric failure → stays nan
            pass
    return out


def implied_vol(
    mid,
    F,
    K,
    T,
    flag,
    *,
    r: float = 0.065,
    bid=None,
    ask=None,
) -> tuple[np.ndarray, np.ndarray]:
    """Solve IV on the MID price for a strike array.

    Returns ``(iv, status)`` ndarrays. ``flag`` is +1 call / −1 put
    (scalar or array). ``bid``/``ask`` (optional arrays) feed the
    illiquid / wide-spread classification; when omitted only the no-arb
    and convergence checks run."""
    mid = np.atleast_1d(np.asarray(mid, dtype=float))
    F = np.broadcast_to(np.asarray(F, dtype=float), mid.shape).copy()
    K = np.broadcast_to(np.asarray(K, dtype=float), mid.shape).copy()
    T = np.broadcast_to(np.asarray(T, dtype=float), mid.shape).copy()
    flag = np.broadcast_to(np.asarray(flag, dtype=float), mid.shape).copy()

    status = np.full(mid.shape, IV_OK, dtype=object)
    iv = np.full(mid.shape, np.nan)

    # Liquidity classification first — nothing to solve on a dead quote.
    if bid is not None and ask is not None:
        bid = np.asarray(bid, dtype=float)
        ask = np.asarray(ask, dtype=float)
        dead = ~(bid > 0.0) | ~(ask > 0.0) | (ask < bid)
        status[dead] = IV_ILLIQUID
        with np.errstate(divide="ignore", invalid="ignore"):
            spread_pct = (ask - bid) / np.maximum((ask + bid) / 2.0, 1e-9)
        wide = ~dead & (spread_pct > WIDE_SPREAD_PCT)
    else:
        dead = ~(mid > 0.0)
        status[dead] = IV_ILLIQUID
        wide = np.zeros(mid.shape, dtype=bool)

    # No-arbitrage pre-filter: df·intrinsic < mid < df·F (call) / df·K (put),
    # with a tick of tolerance. Out-of-bounds prices get flagged, not solved.
    df = np.exp(-r * np.maximum(T, 0.0))
    intrinsic = df * np.maximum(flag * (F - K), 0.0)
    upper = df * np.where(flag > 0, F, K)
    tick = 0.05
    no_arb = ~dead & ((mid < intrinsic - tick) | (mid > upper + tick))
    status[no_arb] = IV_NO_ARB

    solvable = ~dead & ~no_arb & (T > 0.0) & (mid > intrinsic + 1e-9)
    if solvable.any():
        sm, sF, sK, sT, sfl = (a[solvable] for a in (mid, F, K, T, flag))
        solved = None
        if _VECTORIZED_IV is not None:
            try:
                flag_chars = np.where(sfl > 0, "c", "p")
                solved = np.asarray(
                    _VECTORIZED_IV(
                        sm, sF, sK, r, sT, flag_chars, return_as="numpy",
                    ),
                    dtype=float,
                )
            except Exception as exc:  # pragma: no cover - vendor quirk path
                logger.warning("py_vollib_vectorized failed (%s); using fallback", exc)
                solved = None
        if solved is None:
            solved = _newton_brent_iv(sm, sF, sK, sT, r, sfl)
        iv[solvable] = solved

    # mid == intrinsic (expiry / zero-time-value) is unsolvable but in-arb.
    unsolved = ~dead & ~no_arb & np.isnan(iv)
    status[unsolved] = IV_NO_SOLUTION
    ok = ~dead & ~no_arb & ~np.isnan(iv)
    status[ok & wide] = IV_WIDE_SPREAD
    return iv, status
