"""Backtest validation / overfitting-defence toolkit (the rigor ladder's middle).

Public surface grows as rungs land:
  * ``monte_carlo_robustness`` — circular-block-bootstrap drawdown + terminal-
    wealth distribution from a realised return path. *(P1.6)*
  * ``sub_period_robustness`` — time-concentration of the edge across contiguous
    sub-periods (one lucky window vs. spread). *(P1.7)*
  * ``record_and_deflate`` / ``strategy_fingerprint`` — per-session trial counter
    that deflates DSR for how many variants were backtested. *(P1.3)*
  * ``trust_verdict`` — synthesises the battery into one actionable call. *(P1.8)*
  * walk-forward, CPCV→PBO — *(P1.4 / P1.5, pending)*
"""
from backend.services.backtest.validation.monte_carlo import (
    monte_carlo_robustness,
)
from backend.services.backtest.validation.sub_periods import (
    sub_period_robustness,
)
from backend.services.backtest.validation.trials import (
    record_and_deflate,
    reset_group,
    strategy_fingerprint,
)
from backend.services.backtest.validation.verdict import (
    trust_verdict,
)

__all__ = [
    "monte_carlo_robustness",
    "sub_period_robustness",
    "record_and_deflate",
    "reset_group",
    "strategy_fingerprint",
    "trust_verdict",
]
