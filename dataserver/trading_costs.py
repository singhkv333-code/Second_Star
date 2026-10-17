"""What a simulated trade actually costs — the one place the rates live.

VENDORED from Pivot (`backend/services/trading_costs.py`, the module its own
five backtest engines were consolidated onto). Copied rather than imported for
one reason: Charto's paper book is Charto's ledger, and a ledger that cannot
be reconciled without a second deployment on the import path is not durable.
Execution mode may borrow Pivot's builder; the money may not depend on it.

The option block is deliberately NOT carried over. There are no options on this
surface (`execution_bridge.PIVOT_TOOLS` says why), and a vendored function with
no caller is an invitation to grow one.

Rates are Indian equity-DELIVERY (CNC) defaults, ~35-40 bps round trip. Two
things worth knowing before reading a P&L off them:

  * `buy_cost`/`sell_cost` already bake SLIPPAGE_PCT into `charges`. A fill
    therefore happens at the CLEAN mark and takes ALL its friction from the
    charges figure — applying a price slip on top would count it twice.
  * Zerodha charges no brokerage on delivery equity, so BROKERAGE_PER_ORDER
    defaults to 0. A flat Rs 20 here added ~1.5% to a small buy, which read as
    an instant loss the moment a position opened.
"""
from __future__ import annotations

import os

# ── Rate table (equity delivery / CNC) ───────────────────────────────
# Zerodha — our primary broker — charges ₹0 brokerage on CNC (delivery) equity;
# the ₹20/order flat rate is intraday/F&O, NOT delivery. This module is the
# delivery-cost source, so the correct default is 0. A flat ₹20 here silently
# added ~1.5% to a small delivery buy (₹20 on a ₹1,299 trade), which showed up
# as an instant "loss" the moment a position opened. Override via env for a
# full-service broker that does bill per delivery order.
BROKERAGE_PER_ORDER = float(os.getenv("PIVOT_BROKERAGE_PER_ORDER", "0.0"))
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
