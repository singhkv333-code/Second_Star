"""Single source of truth for NSE/BSE equity-delivery (CNC) trading costs.

Why this module exists (2026-05-29 backtest audit): five backtest engines
modelled costs three different ways — the legacy DSL path used a realistic
per-share model (`backtester/engine.py::buy_cost/sell_cost`) while the
`services/*` engines hardcoded a flat `_FRICTION = 0.001` (10 bps/side, ~20 bps
round-trip) that UNDER-counts real frictions (STT alone is ~20 bps round-trip).
This module is the one place those rates live. Two consumption shapes:

  * Per-share engines (legacy `backtester/engine.py`, DSL `dsl/backtest/engine.py`)
    call ``buy_cost(price, qty)`` / ``sell_cost(price, qty)`` → ``(net, charges)``.
  * Multiplier engines (`workflow_backtester`, `indicator_backtest`,
    `open_close_backtest`) multiply fills by ``(1 ± leg_bps(side))``.

Rates are current-ish Indian equity-delivery defaults; brokerage and slippage
are env-overridable (broker/liquidity dependent). These are estimates for a
retail backtest, not a billing engine — the goal is realistic, transparent,
consistent (~35–40 bps round-trip), never frictionless.
"""
from __future__ import annotations

import os

# ── Rate table (equity delivery / CNC) ───────────────────────────────
BROKERAGE_PER_ORDER = float(os.getenv("PIVOT_BROKERAGE_PER_ORDER", "20.0"))
SLIPPAGE_PCT = float(os.getenv("PIVOT_SLIPPAGE_PCT", "0.0005"))  # 0.05%/leg (large-cap)
STT_PCT = 0.001          # 0.1% on BOTH buy & sell (delivery equity)
STT_SELL_PCT = STT_PCT   # back-compat alias (legacy engine references this name)
EXCHANGE_PCT = 0.0000297  # NSE transaction charge, per leg
SEBI_PCT = 0.000001       # ₹10 per crore
GST_PCT = 0.18            # 18% on (brokerage + exchange + SEBI)
STAMP_BUY_PCT = 0.00015   # 0.015% on the BUY side only

# Reference notional used to express the fixed ₹ brokerage as a bps fraction
# for the multiplier engines (they apply a flat per-leg %, not per-order ₹).
_REF_NOTIONAL = 100_000.0


def _charges(notional: float, side: str) -> float:
    """Total round-number charges (₹) for one leg at ``notional`` value."""
    notional = abs(float(notional))
    brokerage = BROKERAGE_PER_ORDER
    slippage = notional * SLIPPAGE_PCT
    stt = notional * STT_PCT
    exchange = notional * EXCHANGE_PCT
    sebi = notional * SEBI_PCT
    gst = (brokerage + exchange + sebi) * GST_PCT
    stamp = notional * STAMP_BUY_PCT if side == "buy" else 0.0
    return brokerage + slippage + stt + exchange + sebi + gst + stamp


def buy_cost(price: float, qty: float) -> tuple[float, float]:
    """(net_debit, total_costs) for a delivery BUY of ``qty`` at ``price``."""
    notional = float(price) * float(qty)
    charges = _charges(notional, "buy")
    return notional + charges, charges


def sell_cost(price: float, qty: float) -> tuple[float, float]:
    """(net_credit, total_costs) for a delivery SELL of ``qty`` at ``price``."""
    notional = float(price) * float(qty)
    charges = _charges(notional, "sell")
    return notional - charges, charges


def leg_bps(side: str) -> float:
    """Effective per-leg cost as a FRACTION of notional, for the multiplier
    engines' ``(1 ± x)`` fill math. Computed at a reference notional so the
    fixed ₹ brokerage amortizes to a sensible bps. Buy leg carries stamp duty;
    sell leg carries no stamp — so the two legs are (correctly) asymmetric."""
    return _charges(_REF_NOTIONAL, side) / _REF_NOTIONAL


def round_trip_bps() -> float:
    """Round-trip (buy+sell) cost in basis points — for methodology text."""
    return (leg_bps("buy") + leg_bps("sell")) * 10_000.0


def slippage_bps() -> float:
    return SLIPPAGE_PCT * 10_000.0
