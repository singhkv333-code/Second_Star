"""Affordability engine — make every View strategy enterable from ~₹800–1,000.

The problem: a "conservative basket" of 7 large-caps needs one share of each
(₹15,000+ if MRF-class names sneak in), an option spread needs premium × lot,
and nothing anywhere computed a rupee minimum — the FE showed only a word
("Low"/"Medium"). Retail users start with ₹1,000, not ₹1,00,000.

The engine (all real prices, nothing invented):

* ``lite_allocation`` — integer-share basket construction under a small budget:
  names whose price exceeds the budget are dropped (stated), the remaining
  target weights are renormalised, shares seeded by largest-remainder and the
  leftover cash spent greedily on the most-underweight affordable name. The
  result states its own tracking honesty (weight drift vs the full basket).
* ``etf_route`` — the cheapest honest expression of a category exposure:
  N units of the catalog ETF such that the outlay clears the floor.
* ``option_entry`` — premium × contract lot (the true minimum for an option
  structure; options are only "cheap" when that number says so).
* ``entry_block`` — the serving-layer block: picks the best affordable route
  for an expression (lite basket when it stays faithful, ETF substitution
  when it doesn't), and states the basis + as-of date.

Honesty contract: ``min_entry_inr`` is a real sum of real last prices, dated;
when a strategy simply cannot be entered small (a margin-heavy pair, a fat
option lot) the block SAYS so instead of inventing a number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

# The product floor the user set: strategies should be enterable around here.
ENTRY_FLOOR_INR = 800.0
ENTRY_TARGET_INR = 1000.0

# A lite basket is only offered when it still resembles the full strategy.
_MIN_LITE_NAMES = 3
_MAX_WEIGHT_DRIFT = 0.18        # max abs per-name drift (actual vs target)
_MIN_DEPLOYED_FRAC = 0.65       # ≥65% of the budget must actually be invested


@dataclass
class LiteAllocation:
    legs: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    total_cost: float = 0.0
    budget: float = ENTRY_TARGET_INR
    max_weight_drift: float = 0.0

    @property
    def n_names(self) -> int:
        return len(self.legs)

    @property
    def deployed_frac(self) -> float:
        return self.total_cost / self.budget if self.budget > 0 else 0.0

    @property
    def faithful(self) -> bool:
        """Does the lite basket still honestly resemble the full strategy?"""
        return (self.n_names >= min(_MIN_LITE_NAMES, self.n_names + len(self.dropped))
                and self.n_names >= 2
                and self.max_weight_drift <= _MAX_WEIGHT_DRIFT
                and self.deployed_frac >= _MIN_DEPLOYED_FRAC)


def lite_allocation(
    weights: dict[str, float],
    prices: dict[str, float],
    *,
    budget: float = ENTRY_TARGET_INR,
) -> LiteAllocation:
    """Integer-share allocation of ``budget`` toward ``weights`` at ``prices``.

    Names with no usable price are dropped (stated); names whose single share
    exceeds the budget are dropped (stated — this is the "cheaper stocks get
    the seat" rule); remaining targets renormalise. Largest-remainder seeding,
    then greedy top-up of the most underweight name that still fits.
    """
    out = LiteAllocation(budget=float(budget))
    usable: dict[str, float] = {}
    for sym, w in weights.items():
        px = prices.get(sym)
        if px is None or not math.isfinite(px) or px <= 0:
            out.dropped.append({"symbol": sym, "reason": "no live price"})
        elif px > budget:
            out.dropped.append(
                {"symbol": sym,
                 "reason": f"one share ≈ ₹{px:,.0f} exceeds the ₹{budget:,.0f} budget"})
        elif w > 0:
            usable[sym] = float(w)
    if not usable:
        return out
    tot = sum(usable.values())
    targets = {s: w / tot for s, w in usable.items()}

    shares = {s: int(budget * targets[s] // prices[s]) for s in usable}
    spent = sum(shares[s] * prices[s] for s in usable)

    # Greedy top-up: most-underweight affordable name first.
    while True:
        leftover = budget - spent
        cands = [s for s in usable if prices[s] <= leftover]
        if not cands:
            break
        def _deficit(s: str) -> float:
            return targets[s] - (shares[s] * prices[s] / budget)
        best = max(cands, key=_deficit)
        if _deficit(best) <= 0 and spent / budget >= _MIN_DEPLOYED_FRAC:
            break                                    # balanced enough; stop
        shares[best] += 1
        spent += prices[best]

    for s in sorted(usable, key=lambda x: -targets[x]):
        if shares[s] <= 0:
            out.dropped.append(
                {"symbol": s,
                 "reason": f"weight too small to buy one share at ₹{prices[s]:,.0f}"})
            continue
        cost = shares[s] * prices[s]
        actual = cost / spent if spent > 0 else 0.0
        out.legs.append({
            "symbol": s, "shares": shares[s],
            "price": round(prices[s], 2), "cost": round(cost, 2),
            "weight_target": round(targets[s], 4),
            "weight_actual": round(actual, 4),
        })
    out.total_cost = round(spent, 2)
    out.max_weight_drift = max(
        (abs(l["weight_actual"] - l["weight_target"]) for l in out.legs),
        default=0.0,
    )
    return out


def etf_route(
    etf: dict[str, Any],
    *,
    floor: float = ENTRY_FLOOR_INR,
) -> Optional[dict[str, Any]]:
    """N units of a catalog ETF clearing the entry floor. ``None`` if the
    entry has no usable price (never invent one)."""
    px = etf.get("last_price")
    if px is None or px <= 0:
        return None
    units = max(1, math.ceil(floor / px))
    return {
        "symbol": etf.get("symbol"),
        "units": units,
        "price": round(float(px), 2),
        "cost": round(units * float(px), 2),
        "tracks": etf.get("tracks"),
        "as_of": etf.get("as_of"),
    }


def option_entry(
    *,
    spot: float,
    premium_pct_of_spot: float,
    lot_size: Optional[int],
) -> Optional[dict[str, Any]]:
    """True rupee minimum of an option structure: net premium × contract lot.
    ``None`` when the lot size is unknown — stated, not guessed."""
    if not lot_size or spot <= 0 or premium_pct_of_spot <= 0:
        return None
    per_unit = spot * premium_pct_of_spot / 100.0
    cost = per_unit * lot_size
    return {
        "lot_size": int(lot_size),
        "premium_per_lot_inr": round(cost, 0),
        "affordable": cost <= ENTRY_TARGET_INR * 1.5,
    }


def entry_block(
    *,
    kind: str,
    weights: Optional[dict[str, float]] = None,
    prices: Optional[dict[str, float]] = None,
    etf: Optional[dict[str, Any]] = None,
    option: Optional[dict[str, Any]] = None,
    as_of: Optional[str] = None,
) -> dict[str, Any]:
    """The per-expression ``entry`` block the pack/serving layer attaches.

    Route preference for baskets: a faithful lite basket of the strategy's own
    names first; the category ETF when the lite basket would distort the
    strategy; an honest boundary statement when neither works.
    """
    block: dict[str, Any] = {"kind": kind, "as_of": as_of}

    if kind in ("basket", "hedge", "multi_asset") and weights and prices:
        lite = lite_allocation(weights, prices)
        etf_leg = etf_route(etf) if etf else None
        if lite.faithful:
            block.update({
                "basis": "lite_basket",
                "min_entry_inr": round(lite.total_cost, 0),
                "legs": lite.legs,
                "dropped": lite.dropped,
                "note": (
                    "A pared-down version of the same basket — "
                    f"{lite.n_names} of {lite.n_names + len(lite.dropped)} names, "
                    "whole shares only, so weights drift a little from the full strategy."
                ),
            })
            if etf_leg:
                block["etf_alternative"] = etf_leg
            return block
        if etf_leg:
            block.update({
                "basis": "etf_substitute",
                "min_entry_inr": etf_leg["cost"],
                "etf": etf_leg,
                "dropped": lite.dropped,
                "note": (
                    f"The full basket needs bigger capital to hold faithfully; the "
                    f"cheapest honest way in is {etf_leg['units']} unit(s) of "
                    f"{etf_leg['symbol']} ({etf_leg['tracks']}) — the same exposure, "
                    "one instrument."
                ),
            })
            return block
        block.update({
            "basis": "unaffordable",
            "min_entry_inr": None,
            "dropped": lite.dropped,
            "note": "This strategy can't honestly be entered at a small size — "
                    "whole-share prices are too large and no tracking ETF exists.",
        })
        return block

    if kind == "option_strategy":
        if option:
            block.update({
                "basis": "option_premium",
                "min_entry_inr": option.get("premium_per_lot_inr"),
                "lot_size": option.get("lot_size"),
                "note": "One lot's net premium — the smallest real ticket for this "
                        "structure. Final premium is set at deploy.",
            })
        else:
            block.update({
                "basis": "priced_at_deploy",
                "min_entry_inr": None,
                "note": "Minimum = one lot's net premium, fixed when the strikes are "
                        "picked at deploy.",
            })
        return block

    if kind == "pair":
        block.update({
            "basis": "margin_required",
            "min_entry_inr": None,
            "note": "The short leg needs margin at your broker — realistic pair "
                    "sizes start well above a small ticket. The long side alone is "
                    "a different (unhedged) trade.",
        })
        return block

    block.update({"basis": "unknown", "min_entry_inr": None})
    return block
