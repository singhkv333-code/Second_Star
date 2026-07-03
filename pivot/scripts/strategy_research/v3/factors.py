"""v3 clean factor construction — all built FROM NIFTY-500 constituent returns.

The three econometric fixes live here:
  * MKT_exIT   — equal-weight market PURGED of the 27 IT names (critique B).
  * IT_f       — equal-weight IT factor (the event sector).
  * MKT_perpBrent + b_NB — NIFTY orthogonalized against Brent (FWL, critique C).
  * RURAL_f / MKT_exMonsoon — monsoon-sensitive vs purged market (critique D).

Plus ``ols_hac`` — Newey-West HAC t-stats (no statsmodels) for the daily
overlapping connectedness regressions (§1.5).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

IT_INDUSTRY = "Information Technology"
MONSOON_SECTORS = {"Automobile and Auto Components",
                   "Fast Moving Consumer Goods"}

# Reviewed Ag/fertilizer allow-list (NIFTY-500 names that sit inside Chemicals /
# FMCG but are monsoon/rural-input driven). Declared, NOT regex-guessed.
AG_FERT_ALLOW = [
    "COROMANDEL.NS", "CHAMBLFERT.NS", "GNFC.NS", "UPL.NS", "RALLIS.NS",
    "PIIND.NS", "BAYERCROP.NS", "DEEPAKFERT.NS", "GSFC.NS", "FACT.NS",
    "SUMICHEM.NS", "DHANUKA.NS", "INSECTICID.NS", "MANGCHEFER.NS",
]


# ── equal-weight + liquidity-weighted cross-sectional factors ─────────────────
def ew_factor(rets: pd.DataFrame, symbols: list[str]) -> pd.Series:
    """Daily-rebalanced equal-weight cross-sectional mean of the available
    members' returns (NaN members drop out each day — no look-ahead)."""
    cols = [s for s in symbols if s in rets.columns]
    if not cols:
        return pd.Series(dtype=float, index=rets.index)
    return rets[cols].mean(axis=1, skipna=True)


def liq_weighted_factor(rets: pd.DataFrame, symbols: list[str],
                        px: pd.DataFrame, vol: pd.DataFrame) -> pd.Series:
    """Robustness weighting: weight_i ∝ trailing-63d median(close×volume),
    recomputed monthly and SHIFTED one day (no look-ahead). Falls back to EW for
    any date with no liquidity data."""
    cols = [s for s in symbols if s in rets.columns and s in px.columns
            and s in vol.columns]
    if not cols:
        return ew_factor(rets, symbols)
    turnover = (px[cols] * vol[cols])
    liq = turnover.rolling(63, min_periods=20).median()
    # monthly weights, shifted one day to avoid look-ahead
    liq_m = liq.resample("MS").first().reindex(rets.index, method="ffill").shift(1)
    w = liq_m.div(liq_m.sum(axis=1), axis=0)
    r = rets[cols]
    out = (r * w).sum(axis=1, skipna=True)
    # where weights are entirely missing, fall back to EW
    bad = w.sum(axis=1).isna() | (w.sum(axis=1) == 0)
    out[bad] = ew_factor(rets, cols)[bad]
    return out


# ── IT factors (critique B) ───────────────────────────────────────────────────
def it_symbols(industry: dict[str, str]) -> list[str]:
    return [t for t, ind in industry.items() if ind == IT_INDUSTRY]


def mkt_exit(rets: pd.DataFrame, it_syms: list[str]) -> pd.Series:
    """Clean market = EW return of every NIFTY-500 name with data MINUS the IT
    names (the market purged of the event sector)."""
    it_set = set(it_syms)
    non_it = [c for c in rets.columns if c not in it_set]
    return ew_factor(rets, non_it)


def it_factor(rets: pd.DataFrame, it_syms: list[str]) -> pd.Series:
    return ew_factor(rets, it_syms)


# ── crude orthogonalization (critique C) ──────────────────────────────────────
def orthogonalize(r_nifty: pd.Series, r_brent: pd.Series
                  ) -> tuple[pd.Series, float, float]:
    """FWL step 1: r_NIFTY = c + b_NB·r_Brent + u. Returns (MKT_perpBrent = u,
    b_NB, t(b_NB) [Newey-West HAC]). MKT_perpBrent is orthogonal to Brent by
    construction — the residual market with its crude channel removed."""
    df = pd.concat([r_nifty, r_brent], axis=1, keys=["nifty", "brent"]).dropna()
    if len(df) < 100:
        return pd.Series(dtype=float), float("nan"), float("nan")
    y = df["nifty"].values
    X = np.column_stack([np.ones(len(df)), df["brent"].values])
    beta, se, t, _r2, _n = ols_hac(y, X)
    resid = y - X @ beta
    mkt_perp = pd.Series(resid, index=df.index)
    return mkt_perp, float(beta[1]), float(t[1])


# ── monsoon factors (critique D) ──────────────────────────────────────────────
def monsoon_symbols(industry: dict[str, str]) -> list[str]:
    """Monsoon-sensitive set = the demand-side sectors UNION the reviewed
    Ag/fertilizer allow-list (restricted to names that have data upstream)."""
    sect = [t for t, ind in industry.items() if ind in MONSOON_SECTORS]
    return sorted(set(sect) | set(AG_FERT_ALLOW))


def monsoon_factors(rets: pd.DataFrame, monsoon_syms: list[str]
                    ) -> tuple[pd.Series, pd.Series]:
    """(RURAL_f, MKT_exMonsoon): EW rural/monsoon factor and the EW market purged
    of every name used in RURAL_f."""
    mset = set(s for s in monsoon_syms if s in rets.columns)
    rural = ew_factor(rets, list(mset))
    rest = [c for c in rets.columns if c not in mset]
    mkt_ex = ew_factor(rets, rest)
    return rural, mkt_ex


# ── Newey-West HAC OLS (no statsmodels) — §1.5 ────────────────────────────────
def ols_hac(y: np.ndarray, X: np.ndarray, L: Optional[int] = None
            ) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, int]:
    """OLS with Newey-West HAC (Bartlett kernel) standard errors. ``X`` already
    includes the intercept column. Returns (beta, se, t, r2, n).

    Robust to the serial-correlation + heteroskedasticity that bias classical SEs
    on overlapping daily returns. Auto bandwidth L = floor(4·(n/100)^(2/9))."""
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    n, k = X.shape
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ beta
    XtX_inv = np.linalg.inv(X.T @ X)
    if L is None:
        L = int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))
    L = max(0, min(L, n - 1))
    Xe = X * e[:, None]                       # (n,k)
    S = Xe.T @ Xe                             # Gamma_0
    for l in range(1, L + 1):
        w = 1.0 - l / (L + 1.0)               # Bartlett weight
        G = Xe[l:].T @ Xe[:-l]                # Gamma_l
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv                 # HAC sandwich
    se = np.sqrt(np.clip(np.diag(V), 0.0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, beta / se, np.nan)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(e @ e) / ss_tot if ss_tot > 0 else float("nan")
    return beta, se, t, r2, n
