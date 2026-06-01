"""Pairs / statistical-arbitrage as a first-class backtest object (Phase 2.3).

Cointegration (Engle-Granger), hedge-ratio estimation, the mean-reverting spread
instrument with its own z-score entry/exit, the OU half-life diagnostic, and a
pairwise cointegration scanner — all on yfinance daily closes, judged through the
same Phase-1 rigor battery as every other engine.
"""
from .cointegration import (
    EG_CRIT_2VAR,
    EngleGrangerResult,
    adf_tstat,
    engle_granger,
    hedge_ratio,
    ou_half_life,
    rolling_zscore,
)
from .engine import run_pairs_backtest, simulate_pairs
from .scanner import scan_pairs

__all__ = [
    "EG_CRIT_2VAR",
    "EngleGrangerResult",
    "adf_tstat",
    "engle_granger",
    "hedge_ratio",
    "ou_half_life",
    "rolling_zscore",
    "run_pairs_backtest",
    "simulate_pairs",
    "scan_pairs",
]
