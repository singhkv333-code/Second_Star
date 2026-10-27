"""Sub-period robustness — is the edge spread across time, or one lucky window?

PSR/DSR ask "is the Sharpe real?"; Monte-Carlo asks "how lucky was the path?".
Neither catches *time-concentration*: a strategy that earns a Sharpe of 1.5 by
making all its money in a single fortnight (and ~nothing the rest of the time)
is fragile — it's one regime bet, not a system. This splits the equity curve
into ``n_periods`` contiguous spans and reports:

  * ``period_returns_pct`` — each span's return (their product ≈ total return).
  * ``positive_period_frac`` — share of spans that made money (consistency).
  * ``concentration`` — |largest span's log-return| / Σ|log-returns|, in (0, 1].
    ~1/n_periods = evenly spread (robust); near 1 = almost all the action in one
    span (fragile, regime-dependent).

Computed PURELY from the equity curve (no re-run), so it rides on every backtest.
Stdlib only.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

# Need at least a couple of points per span for the split to mean anything.
_MIN_PTS_PER_PERIOD = 2


def _clean(values: Sequence[float]) -> list[float]:
    out: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x):
            out.append(x)
    return out


def sub_period_robustness(
    equity_values: Sequence[float], *, n_periods: int = 4,
) -> Optional[dict]:
    """Split the equity curve into ``n_periods`` contiguous spans and measure how
    evenly the return was earned across them. Returns ``None`` when the curve is
    too short to split, or auto-reduces ``n_periods`` so each span keeps at least
    ``_MIN_PTS_PER_PERIOD`` points."""
    vals = _clean(equity_values)
    n = len(vals)
    if n < 2 * _MIN_PTS_PER_PERIOD:
        return None
    # Shrink n_periods so every span has enough points (and at least 2 spans).
    n_periods = max(2, min(n_periods, n // _MIN_PTS_PER_PERIOD))

    # Boundary indices partition [0, n-1] into n_periods contiguous spans; the
    # product of (1 + span_return) telescopes to vals[-1]/vals[0].
    bounds = [round(i * (n - 1) / n_periods) for i in range(n_periods + 1)]
    period_returns: list[float] = []
    log_returns: list[float] = []
    for i in range(n_periods):
        s, e = vals[bounds[i]], vals[bounds[i + 1]]
        if s > 0:
            period_returns.append(e / s - 1.0)
            log_returns.append(math.log(e / s) if e > 0 else float("-inf"))
        else:
            period_returns.append(0.0)
            log_returns.append(0.0)

    finite_logs = [x for x in log_returns if math.isfinite(x)]
    abs_sum = sum(abs(x) for x in finite_logs)
    concentration: Optional[float] = None
    if abs_sum > 0 and finite_logs:
        concentration = max(abs(x) for x in finite_logs) / abs_sum

    positive = sum(1 for r in period_returns if r > 0)
    return {
        "n_periods": n_periods,
        "period_returns_pct": [round(r * 100.0, 2) for r in period_returns],
        "positive_period_frac": round(positive / n_periods, 3),
        "best_period_return_pct": round(max(period_returns) * 100.0, 2),
        "worst_period_return_pct": round(min(period_returns) * 100.0, 2),
        "concentration": round(concentration, 3) if concentration is not None else None,
    }


__all__ = ["sub_period_robustness"]
