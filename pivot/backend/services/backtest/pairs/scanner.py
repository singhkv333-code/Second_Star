"""Pairwise cointegration scanner over a universe of symbols.

Fetches each symbol once, then tests every pair (both directions — Engle-Granger
is not symmetric in the choice of dependent variable, so we keep the stronger of
y~x and x~y) and ranks the cointegrated ones by ADF strength. A short OU
half-life is what makes a cointegrated pair actually *tradable*, so it's surfaced
alongside.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from backend.market.yfinance_service import canonical_symbol, fetch_price_history

from .cointegration import engle_granger

_LEVEL_ORDER = {"1%": 3, "5%": 2, "10%": 1, None: 0}
_MIN_OVERLAP = 80


def _clears(level: Optional[str], threshold: str) -> bool:
    return _LEVEL_ORDER.get(level, 0) >= _LEVEL_ORDER.get(threshold, 2)


def scan_pairs(
    symbols: list[str],
    *,
    period: str = "2y",
    min_level: str = "5%",
    top: int = 20,
) -> dict:
    """Test all pairs in ``symbols`` for cointegration. Returns the ranked
    cointegrated pairs (clearing ``min_level``) plus how many were tested."""
    series: dict[str, dict] = {}
    for s in symbols:
        recs = fetch_price_history(s, period, "1d")
        if recs:
            series[canonical_symbol(s)] = {r["date"]: r["close"] for r in recs}

    canons = sorted(series.keys())
    if len(canons) < 2:
        return {"tested": 0, "cointegrated": [], "n_symbols": len(canons),
                "skipped": "fewer than 2 symbols returned data"}

    tested = 0
    rows = []
    for i in range(len(canons)):
        for j in range(i + 1, len(canons)):
            a, b = canons[i], canons[j]
            common = sorted(set(series[a]) & set(series[b]))
            if len(common) < _MIN_OVERLAP:
                continue
            sa = np.array([series[a][d] for d in common], dtype=float)
            sb = np.array([series[b][d] for d in common], dtype=float)
            tested += 1
            # Both directions; keep the stronger (more negative t-stat).
            eg_ab = engle_granger(sa, sb)
            eg_ba = engle_granger(sb, sa)
            if not (np.isfinite(eg_ab.adf_tstat) or np.isfinite(eg_ba.adf_tstat)):
                continue
            t_ab = eg_ab.adf_tstat if np.isfinite(eg_ab.adf_tstat) else np.inf
            t_ba = eg_ba.adf_tstat if np.isfinite(eg_ba.adf_tstat) else np.inf
            if t_ab <= t_ba:
                dep, ind, eg = a, b, eg_ab
            else:
                dep, ind, eg = b, a, eg_ba
            if eg.cointegrated_at is None or not _clears(eg.cointegrated_at, min_level):
                continue
            rows.append({
                "dependent": dep,
                "independent": ind,
                "beta": round(eg.beta, 4),
                "adf_tstat": round(eg.adf_tstat, 4),
                "cointegrated_at": eg.cointegrated_at,
                "half_life_days": round(eg.half_life, 2) if eg.half_life else None,
                "n_obs": len(common),
            })

    rows.sort(key=lambda r: r["adf_tstat"])  # most negative = strongest
    return {
        "tested": tested,
        "n_symbols": len(canons),
        "min_level": min_level,
        "cointegrated": rows[:top],
    }
