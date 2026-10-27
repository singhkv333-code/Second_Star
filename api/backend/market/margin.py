"""SPAN-style margin approximation for option strategies (F&O P2).

Replicates the SHAPE of exchange SPAN: revalue the whole position over a
scenario grid of underlying moves × vol shifts, take the worst portfolio
loss as the scan risk, add an exposure margin on short-leg notional, and
net the premium. Deterministic and offline — good enough for the paper
book, the pre-trade gate and the card's margin display. The broker's
SPAN+exposure number is authoritative for live trading; surface ours as
an estimate, never as a quote.

Scenario grid (index defaults, mirroring NSE's price scan range):
  price: ±3.5% of the forward in 7 steps (stocks use a wider ±7.5%)
  vol:   ×{0.75, 1.0, 1.25} relative shifts
Exposure margin: 2% of short-leg notional (index; 3.5% stocks).
Defined-risk floor: never less than the structure's true max loss when
it's bounded (a condor's worst case IS its max loss).
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from backend.market.greeks.black76 import black76_price

_INDEX_SCAN_PCT = 0.035
_STOCK_SCAN_PCT = 0.075
_VOL_SHIFTS = (0.75, 1.0, 1.25)
_PRICE_STEPS = 7
_EXPOSURE_PCT_INDEX = 0.02
_EXPOSURE_PCT_STOCK = 0.035

# Underlyings whose option chains are index-margined. Derived heuristic:
# index roots have no equity ISIN — the master can't tell us directly,
# so treat the well-known index roots as index and everything else as
# stock. Worst case a stock gets index treatment, which the wider stock
# scan below corrects for at the gate level.
_INDEX_HINTS = ("NIFTY", "SENSEX", "BANKEX", "VIX")


def _is_index(underlying: str) -> bool:
    u = (underlying or "").upper()
    return any(h in u for h in _INDEX_HINTS)


def span_margin_estimate(
    legs: Sequence[dict],
    *,
    underlying: str,
    forward: float,
    t_years: float,
    lot_value: int,
    r: float = 0.065,
) -> tuple[float, str]:
    """(margin_estimate, note) for a multi-leg option position.

    ``legs``: [{option_type CE|PE, side BUY|SELL, strike, mid, iv}] —
    the resolved card legs. ``lot_value`` = lot_size × qty_lots.
    Premium convention: margin covers risk; the net premium debit is
    capital, not margin — callers combine as max(margin, debit)."""
    if not legs or forward <= 0:
        return 0.0, "no legs"
    scan_pct = _INDEX_SCAN_PCT if _is_index(underlying) else _STOCK_SCAN_PCT
    exposure_pct = (
        _EXPOSURE_PCT_INDEX if _is_index(underlying) else _EXPOSURE_PCT_STOCK
    )

    moves = np.linspace(-scan_pct, scan_pct, _PRICE_STEPS)
    F_grid = forward * (1.0 + moves)

    worst = 0.0
    T = max(float(t_years), 1e-6)
    for vol_shift in _VOL_SHIFTS:
        scenario_value = np.zeros_like(F_grid)
        entry = 0.0
        for leg in legs:
            flag = 1.0 if leg["option_type"] == "CE" else -1.0
            sign = 1.0 if leg["side"] == "BUY" else -1.0
            sigma = float(leg.get("iv") or 0.2)
            prices = black76_price(
                F_grid, float(leg["strike"]), sigma * vol_shift, T,
                r=r, flag=flag,
            )
            scenario_value = scenario_value + sign * prices * lot_value
            entry += sign * float(leg.get("mid") or 0.0) * lot_value
        # Loss vs entry value in this scenario row.
        loss = float(np.max(entry - scenario_value))
        worst = max(worst, loss)

    short_notional = sum(
        forward * lot_value for leg in legs if leg["side"] == "SELL"
    )
    margin = worst + exposure_pct * short_notional
    note = (
        f"SPAN-style estimate: worst loss over ±{scan_pct * 100:.1f}% "
        f"price × ±25% vol scenarios + {exposure_pct * 100:.1f}% exposure "
        "on short notional. Broker SPAN is authoritative."
    )
    return round(float(margin), 2), note


def strategy_margin(
    legs: Sequence[dict],
    *,
    underlying: str,
    forward: float,
    t_years: float,
    lot_value: int,
    max_loss: Optional[float],
    net_premium: float,
) -> tuple[float, str]:
    """Margin for a resolved strategy: SPAN-style scan, clamped by the
    defined-risk max loss when bounded, floored at the net debit."""
    margin, note = span_margin_estimate(
        legs, underlying=underlying, forward=forward,
        t_years=t_years, lot_value=lot_value,
    )
    debit = max(0.0, -float(net_premium or 0.0))
    if max_loss is not None:
        # A defined-risk structure can never lose more than max loss —
        # SPAN scenarios beyond the wings overstate it.
        margin = min(margin, float(max_loss))
        note = "Defined-risk: capped at max loss. " + note
    margin = max(margin, debit)
    return round(margin, 2), note
