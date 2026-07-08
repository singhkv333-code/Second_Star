"""Multi-position portfolio backtest (Phase 2.4).

A cross-sectional, multi-symbol engine: rank a universe by a signal (momentum
today), build a CONSTRAINED portfolio (max names, gross/net exposure caps,
long-only or dollar-neutral long/short), rebalance on a schedule, and judge the
portfolio equity with the same Phase-1 rigor battery as every other engine.

This is where the 2.1 momentum factor + dollar-neutral L/S short leg land — they
were "gated on prices", and OHLCV is yfinance. The single-symbol tree engine
(Engine 2b) is left untouched; this is a separate multi-name engine.
"""
from .engine import (
    momentum_scores,
    run_portfolio_backtest,
    simulate_portfolio,
    target_weights,
)

__all__ = [
    "momentum_scores",
    "run_portfolio_backtest",
    "simulate_portfolio",
    "target_weights",
]
