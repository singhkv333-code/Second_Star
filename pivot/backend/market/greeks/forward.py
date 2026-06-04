"""Synthetic forward via ATM put-call parity.

When the same-expiry future is illiquid (common in stock options, and
for far expiries), the no-arb forward is still recoverable from the
option chain itself:

    C − P = e^(−rT)·(F − K)   ⇒   F = K + e^(rT)·(C − P)

evaluated at the strike nearest the spot (highest gamma → tightest
parity). Strictly better than guessing a dividend yield — the chain
already prices it.
"""
from __future__ import annotations

import math
from typing import Sequence


def synthetic_forward(
    strikes: Sequence[float],
    call_mids: Sequence[float],
    put_mids: Sequence[float],
    spot: float,
    T: float,
    r: float = 0.065,
) -> float | None:
    """Median put-call-parity forward over the 3 strikes nearest spot.

    Median over a small ATM window damps a single bad quote. Returns
    ``None`` when no strike has both sides quoted (caller falls back to
    spot or the future LTP)."""
    rows = [
        (abs(k - spot), k, c, p)
        for k, c, p in zip(strikes, call_mids, put_mids)
        if k and c and p and c > 0.0 and p > 0.0
    ]
    if not rows:
        return None
    rows.sort(key=lambda t: t[0])
    growth = math.exp(r * max(T, 0.0))
    fwds = sorted(k + growth * (c - p) for _, k, c, p in rows[:3])
    return fwds[len(fwds) // 2]
