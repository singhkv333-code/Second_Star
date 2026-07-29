"""Comparable gain/loss metrics for a strategy's per-occurrence returns.

The view screen used to show "average profit" (mean of ALL occurrences) next
to "max drop" (worst intra-window drawdown) — two numbers on different bases
that can't be compared across strategies. This module computes the four
metrics that ARE comparable, all from the same per-occurrence return
distribution:

  avg_gain_pct   mean return of the positive occurrences
  avg_loss_pct   mean return of the negative occurrences (signed, ≤ 0)
  max_gain_pct   the best single occurrence
  max_loss_pct   the worst single occurrence (signed, ≤ 0)

For modelled option structures (no historical occurrences) the analogue comes
from the payoff model — max loss is the premium (−100% of capital), max gain
the modelled cap — with ``basis: "modelled"`` so the FE never presents model
output as history. Averages stay ``None`` there: an average needs a history.
"""
from __future__ import annotations

import math
from typing import Any, Optional


def _mean(xs: list[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 2) if xs else None


def gain_loss_stats(per_episode_pct: list[float]) -> Optional[dict[str, Any]]:
    """The four comparable metrics from a per-occurrence % return list.
    ``None`` when there is no usable history (never zeros-as-data)."""
    vals = [float(r) for r in (per_episode_pct or [])
            if r is not None and math.isfinite(float(r))]
    if not vals:
        return None
    gains = [r for r in vals if r > 0]
    losses = [r for r in vals if r <= 0]
    return {
        "avg_gain_pct": _mean(gains),
        "avg_loss_pct": _mean(losses),
        "max_gain_pct": round(max(vals), 2),
        "max_loss_pct": round(min(vals), 2),
        "n_gain": len(gains),
        "n_loss": len(losses),
        "basis": "episodes",
    }


def modelled_option_stats(option_payload: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The modelled analogue for option tiers: bounded loss (the premium) and
    the modelled profit cap. Averages are None — there is no history."""
    if not option_payload:
        return None
    return {
        "avg_gain_pct": None,
        "avg_loss_pct": None,
        "max_gain_pct": option_payload.get("max_profit_pct"),
        "max_loss_pct": option_payload.get("max_loss_pct"),
        "max_gain_uncapped": bool(option_payload.get("max_profit_uncapped")),
        "n_gain": None,
        "n_loss": None,
        "basis": "modelled",
    }
