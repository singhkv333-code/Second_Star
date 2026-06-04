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


# ── Options (F&O P0) ─────────────────────────────────────────────────
#
# Same single-source-of-truth contract as the equity block above, for
# NSE option PREMIUM legs. All percentages apply to the premium value
# (premium × qty), NOT the contract notional — that's how options are
# actually billed. Rates current as of FY26:
#   * STT 0.1% on the SELL side premium (raised from 0.0625% in Oct 2024).
#   * NSE transaction charge ~0.03503% of premium (revised Apr 2025).
#   * Stamp 0.003% on the BUY side premium.
#   * Brokerage flat ₹20/order, SEBI ₹10/cr, GST 18% on
#     (brokerage + exchange + SEBI) — same shape as equity.
# Slippage is NOT modelled here — the paper fill model handles it
# spread-aware (mid ± half-spread), which is more honest for options
# than a flat % of premium. MCX rates differ; MCX is research-only in
# v1 so its costs are display-only and use the same NSE-shaped numbers
# with the MCX exchange rate.
OPT_STT_SELL_PCT = 0.001            # 0.1% of premium, sell side only
OPT_EXCHANGE_PCT = 0.0003503        # NSE premium transaction charge
OPT_EXCHANGE_PCT_MCX = 0.000418     # MCX options premium txn charge
OPT_STAMP_BUY_PCT = 0.00003         # 0.003% of premium, buy side only


def _option_charges(premium_value: float, side: str, segment: str = "NFO-OPT") -> float:
    """Total charges (₹) for one option leg at ``premium_value`` =
    premium × qty (qty in units, i.e. lots × lot_size)."""
    premium_value = abs(float(premium_value))
    brokerage = BROKERAGE_PER_ORDER
    exchange_pct = (
        OPT_EXCHANGE_PCT_MCX if segment.startswith("MCX") else OPT_EXCHANGE_PCT
    )
    exchange = premium_value * exchange_pct
    sebi = premium_value * SEBI_PCT
    stt = premium_value * OPT_STT_SELL_PCT if side == "sell" else 0.0
    stamp = premium_value * OPT_STAMP_BUY_PCT if side == "buy" else 0.0
    gst = (brokerage + exchange + sebi) * GST_PCT
    return brokerage + exchange + sebi + stt + stamp + gst


def option_buy_cost(
    premium: float, qty: float, *, segment: str = "NFO-OPT",
) -> tuple[float, float]:
    """(net_debit, total_charges) for BUYing ``qty`` units at ``premium``."""
    value = float(premium) * float(qty)
    charges = _option_charges(value, "buy", segment)
    return value + charges, charges


def option_sell_cost(
    premium: float, qty: float, *, segment: str = "NFO-OPT",
) -> tuple[float, float]:
    """(net_credit, total_charges) for SELLing ``qty`` units at ``premium``."""
    value = float(premium) * float(qty)
    charges = _option_charges(value, "sell", segment)
    return value - charges, charges


def option_leg_bps(side: str, *, segment: str = "NFO-OPT") -> float:
    """Per-leg option cost as a FRACTION of premium value at a reference
    premium notional — for multiplier-style engines (mirrors leg_bps)."""
    return _option_charges(_REF_NOTIONAL, side, segment) / _REF_NOTIONAL
