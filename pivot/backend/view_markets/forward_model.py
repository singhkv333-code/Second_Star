"""Forward expected-return model for views whose event has NO historical
precedent — the honest replacement for calendar-window pseudo-backtests.

Why this exists
---------------
"Will AI create more jobs by 2027?" has never resolved before. Slicing ten
years of prices into 6-month windows and calling each one an "episode" is a
fabricated track record (doctrine A2: raw calendar windows launder beta into
"edge"). But refusing to say anything forward-looking is also wrong — the user
asked what the belief could be worth.

The defensible middle is a SCENARIO model, every input of which is real or
explicitly stated:

    1. beta        — a real regression of the strategy book on the view's
                     DRIVER series (gold, crude, Nifty, USDINR …), weekly
                     overlapping-free returns, t-stat and N reported.
    2. scenario    — "if the view resolves YES the driver moves X%; NO → Y%"
                     — a stated editorial assumption, shipped as such.
    3. probability — what's priced in (market-implied when a source exists,
                     an explicitly-labelled neutral assumption otherwise).
    4. shrinkage   — the model's own claim is shrunk 50% toward zero
                     (McLean–Pontiff decay base rate) BEFORE costs.
    5. costs       — Indian round-trip costs subtracted, never hidden.
    6. a band      — the output is a range at the book's real volatility,
                     never a point promise.

Output contract: ``no_history: true`` always; every assumption listed; the
verdict stays ``insufficient_data``-class (a model is not a track record).
"""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

# Doctrine A4: a modelled edge ships pre-shrunk toward zero by at least the
# published-anomaly decay base rate (~50%, McLean & Pontiff 2016).
MODEL_SHRINKAGE = 0.5

# z-scores for the displayed band.
_Z_P25, _Z_P05 = 0.6745, 1.6449


def weekly_returns(daily: pd.Series) -> pd.Series:
    """Weekly (Fri-anchored) compounded simple returns from a daily series —
    less microstructure noise than daily for a beta estimate."""
    s = daily.dropna()
    if s.empty:
        return s
    return (1.0 + s).resample("W-FRI").prod().dropna() - 1.0


def driver_beta(
    portfolio_daily: pd.Series,
    driver_daily: pd.Series,
    *,
    min_weeks: int = 52,
) -> Optional[dict[str, Any]]:
    """OLS beta of the strategy book on the view's driver, on weekly returns.

    Returns {beta, t_stat, r2, n_weeks} or ``None`` when there is not enough
    overlapping history for an honest estimate.
    """
    pw, dw = weekly_returns(portfolio_daily), weekly_returns(driver_daily)
    idx = pw.index.intersection(dw.index)
    if len(idx) < min_weeks:
        return None
    y = pw.reindex(idx).values.astype(float)
    x = dw.reindex(idx).values.astype(float)
    ok = np.isfinite(y) & np.isfinite(x)
    y, x = y[ok], x[ok]
    n = len(y)
    if n < min_weeks:
        return None
    xm, ym = x - x.mean(), y - y.mean()
    sxx = float(np.dot(xm, xm))
    if sxx <= 0:
        return None
    beta = float(np.dot(xm, ym) / sxx)
    resid = ym - beta * xm
    dof = n - 2
    se = math.sqrt(float(np.dot(resid, resid)) / dof / sxx) if dof > 0 else float("inf")
    ss_tot = float(np.dot(ym, ym))
    r2 = 1.0 - float(np.dot(resid, resid)) / ss_tot if ss_tot > 0 else 0.0
    return {
        "beta": round(beta, 3),
        "t_stat": round(beta / se, 2) if se > 0 else None,
        "r2": round(r2, 3),
        "n_weeks": n,
    }


def scenario_forward(
    *,
    p_yes: float,
    p_source: str,
    driver: str,
    driver_move_yes_pct: float,
    driver_move_no_pct: float,
    beta_block: dict[str, Any],
    sigma_annual: float,
    horizon_days: int,
    round_trip_bps: float = 30.0,
    shrinkage: float = MODEL_SHRINKAGE,
) -> Optional[dict[str, Any]]:
    """The scenario-weighted, shrunk, cost-adjusted forward return band.

    ``beta_block`` must come from :func:`driver_beta` (a real regression).
    All percentages are plain percent (12 = 12%). Returns ``None`` on
    degenerate inputs — never a fabricated shape.
    """
    if not beta_block or sigma_annual <= 0 or horizon_days <= 0:
        return None
    p = min(max(float(p_yes), 0.0), 1.0)
    beta = float(beta_block["beta"])

    r_yes = beta * float(driver_move_yes_pct)
    r_no = beta * float(driver_move_no_pct)
    er_gross = p * r_yes + (1.0 - p) * r_no
    costs_pct = round_trip_bps / 100.0
    er_net = shrinkage * er_gross - costs_pct

    sigma_h_pct = sigma_annual * math.sqrt(horizon_days / 252.0) * 100.0
    band = {
        "p25": round(er_net - _Z_P25 * sigma_h_pct, 1),
        "p50": round(er_net, 1),
        "p75": round(er_net + _Z_P25 * sigma_h_pct, 1),
        "p05": round(er_net - _Z_P05 * sigma_h_pct, 1),
    }
    return {
        "method": "scenario_beta_v1",
        "no_history": True,
        "driver": driver,
        "beta": beta_block,
        "p_yes": round(p, 3),
        "p_source": p_source,
        "scenario": {
            "yes_driver_move_pct": float(driver_move_yes_pct),
            "no_driver_move_pct": float(driver_move_no_pct),
            "yes_book_move_pct": round(r_yes, 1),
            "no_book_move_pct": round(r_no, 1),
        },
        "expected_gross_pct": round(er_gross, 1),
        "expected_net_pct": round(er_net, 1),
        "band_pct": band,
        "sigma_horizon_pct": round(sigma_h_pct, 1),
        "shrinkage": shrinkage,
        "costs_bps": round_trip_bps,
        "horizon_days": int(horizon_days),
        "assumptions": [
            f"Driver scenario is a stated assumption: YES → {driver} "
            f"{driver_move_yes_pct:+.0f}%, NO → {driver_move_no_pct:+.0f}%.",
            f"Book sensitivity is a real regression: beta {beta_block['beta']} "
            f"(t={beta_block.get('t_stat')}, {beta_block.get('n_weeks')} weeks).",
            f"Probability {p:.0%} — {p_source}.",
            f"The modelled edge is shrunk {shrinkage:.0%} toward zero and "
            f"{round_trip_bps:.0f}bps round-trip costs are subtracted before display.",
            "This event has not happened before — a model is not a track record.",
        ],
    }
