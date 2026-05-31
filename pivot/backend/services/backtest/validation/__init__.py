"""Backtest validation / overfitting-defence toolkit (the rigor ladder's middle).

Public surface grows as rungs land:
  * ``monte_carlo_robustness`` — circular-block-bootstrap drawdown + terminal-
    wealth distribution from a realised return path. *(P1.6)*
  * walk-forward, CPCV→PBO — *(P1.4 / P1.5, pending)*
"""
from backend.services.backtest.validation.monte_carlo import (
    monte_carlo_robustness,
)

__all__ = ["monte_carlo_robustness"]
