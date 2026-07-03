"""Affordability engine — make every View strategy enterable small, WITHOUT
collapsing the basket into ETFs.

The problem this solves (beta fix, 2026-07-03): a "conservative basket" of
large-caps needs one share of each (₹15,000+ if MRF-class names sneak in).
The old allocator dropped every name whose single share exceeded the budget
and stuffed the remainder into catalog-ETF units — so small tickets became
ETF-only even though the research universe holds full price history for
hundreds of thesis-aligned names, many affordable. Retail users asked for
the strategy, not an index fund.

The engine (all real prices, nothing invented):

* ``fit_allocation`` — the weight→shares formulation:
    1. SUBSTITUTE: a name whose share price busts the budget is replaced by
       the best event-tested affordable candidate from the view's thesis
       bench (``candidate_bench``); the substitute inherits the dropped
       name's weight slot. Stated per-substitution.
    2. RETURN-TILT: target weights are tilted by each name's mean
       per-occurrence event return —
           w'_i = w_i × clip(1 + 0.5 · (r_i − r̄)/max|r − r̄|, 0.3, 1.5)
       then renormalised. Bounded so returns tilt, never dominate.
    3. INTEGER FIT: largest-remainder share seeding, then greedy top-up of
       the most-underweight affordable name.
    4. BUDGET ESCALATION: if the fit isn't faithful at the base budget the
       budget steps up (+₹500) to a stated cap; ``min_entry_inr`` is the
       real spend of the first faithful fit — the honest minimum, not a
       hard-coded floor.
* ``core_satellite`` — the LAST-RESORT mixed route (only when substitution
  still can't build a faithful stock basket): ETF core hard-capped at 50%
  of the ticket + weight-proportional whole-share satellites (≥2). A
  100%-ETF basket is only ever offered as ``etf_substitute`` with an
  explicit note when the bench holds NO affordable name at the cap.
* ``option_entry`` — premium × contract lot (unchanged).
* ``entry_block`` — picks the route, stamps ``selection_method`` (the
  event-study/forward provenance of the numbers used) and lists
  ``substitutions`` + ``dropped`` honestly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

# The product floor the user set: strategies should be enterable around here.
ENTRY_FLOOR_INR = 800.0
ENTRY_TARGET_INR = 1000.0
# The small-ticket base budget.
ENTRY_BUDGET_INR = 2000.0
# Budget escalation: step and honest ceiling for finding a faithful basket.
BUDGET_STEP_INR = 500.0
BUDGET_CAP_INR = 5000.0

# A lite basket is only offered when it still resembles the full strategy.
_MIN_LITE_NAMES = 3
_MAX_WEIGHT_DRIFT = 0.18        # max abs per-name drift (actual vs target)
_MIN_DEPLOYED_FRAC = 0.65       # ≥65% of the budget must actually be invested

# Core-satellite split (last resort): the ETF core may take AT MOST half the
# ticket — the rest holds real strategy/bench names. Even ONE real satellite
# beats a pure-ETF ticket (the never-all-ETF rule); with a bench present the
# fit normally lands 2+.
_CORE_FRAC_MAX = 0.5
_MIN_SATELLITES = 1
_MAX_SATELLITES = 4

# Return-tilt bounds (step 2 of the formulation).
_TILT_STRENGTH = 0.5
_TILT_MIN, _TILT_MAX = 0.3, 1.5


@dataclass
class LiteAllocation:
    legs: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    substitutions: list[dict[str, Any]] = field(default_factory=list)
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


def _tilt_weights(
    targets: dict[str, float],
    expected_returns: Optional[dict[str, float]],
) -> dict[str, float]:
    """Step 2 — bounded return tilt. Names without an event-tested return sit
    at the mean (tilt 1.0). No-op when fewer than 2 names carry returns."""
    if not expected_returns:
        return dict(targets)
    known = [expected_returns[s] for s in targets if s in expected_returns]
    if len(known) < 2:
        return dict(targets)
    r_bar = sum(known) / len(known)
    spread = max(abs(expected_returns[s] - r_bar)
                 for s in targets if s in expected_returns)
    if spread <= 0:
        return dict(targets)
    tilted: dict[str, float] = {}
    for s, w in targets.items():
        r = expected_returns.get(s)
        tilt = 1.0 if r is None else max(
            _TILT_MIN, min(_TILT_MAX, 1.0 + _TILT_STRENGTH * (r - r_bar) / spread)
        )
        tilted[s] = w * tilt
    tot = sum(tilted.values()) or 1.0
    return {s: v / tot for s, v in tilted.items()}


def _integer_fit(
    targets: dict[str, float],
    prices: dict[str, float],
    budget: float,
) -> tuple[dict[str, int], float]:
    """Step 3 — largest-remainder seeding + greedy most-underweight top-up."""
    shares = {s: int(budget * targets[s] // prices[s]) for s in targets}
    spent = sum(shares[s] * prices[s] for s in targets)
    while True:
        leftover = budget - spent
        cands = [s for s in targets if prices[s] <= leftover]
        if not cands:
            break

        def _deficit(s: str) -> float:
            return targets[s] - (shares[s] * prices[s] / budget)

        best = max(cands, key=_deficit)
        if _deficit(best) <= 0 and spent / budget >= _MIN_DEPLOYED_FRAC:
            break
        shares[best] += 1
        spent += prices[best]
    return shares, spent


def fit_allocation(
    weights: dict[str, float],
    prices: dict[str, float],
    *,
    budget: float = ENTRY_BUDGET_INR,
    budget_cap: float = BUDGET_CAP_INR,
    expected_returns: Optional[dict[str, float]] = None,
    bench: Optional[Any] = None,
) -> LiteAllocation:
    """The full weight→shares formulation (substitute → tilt → fit → escalate).

    ``bench`` is a ``candidate_bench.Bench`` (or None). Substitutes come only
    from its event-tested, affordable, not-already-held candidates, best score
    first. The returned allocation states every substitution and drop.
    """
    # ── Step 1: partition + substitute ───────────────────────────────────
    held: dict[str, float] = {}
    dropped: list[dict[str, Any]] = []
    substitutions: list[dict[str, Any]] = []
    # Local price map — substitutes carry their own bench price, which the
    # caller's map (strategy members only) doesn't know about.
    px_map: dict[str, float] = {
        s: float(p) for s, p in prices.items()
        if p is not None and math.isfinite(p) and p > 0
    }

    bench_pool: list[Any] = []
    if bench is not None:
        taken = set(weights)
        bench_pool = [
            c for c in bench.ranked()
            if c.method == "event_study"
            and c.symbol not in taken
            and c.price is not None and 0 < c.price <= budget_cap
            and (c.mean_episode_pct or 0) > 0
        ]

    def _take_substitute(for_sym: str, w: float, reason: str) -> bool:
        while bench_pool:
            cand = bench_pool.pop(0)
            if cand.symbol in held:
                continue
            held[cand.symbol] = w
            px_map[cand.symbol] = float(cand.price)
            if expected_returns is not None and cand.mean_episode_pct is not None:
                expected_returns.setdefault(cand.symbol, cand.mean_episode_pct)
            substitutions.append({
                "in": cand.symbol,
                "out": for_sym,
                "reason": reason,
                "in_mean_episode_pct": cand.mean_episode_pct,
                "in_n_episodes": cand.n_episodes,
            })
            return True
        return False

    for sym, w in weights.items():
        px = prices.get(sym)
        if px is None or not math.isfinite(px) or px <= 0:
            if not _take_substitute(sym, float(w), "no live price"):
                dropped.append({"symbol": sym, "reason": "no live price"})
        elif px > budget_cap:
            reason = (f"one share ≈ ₹{px:,.0f} exceeds even the "
                      f"₹{budget_cap:,.0f} ceiling")
            if not _take_substitute(sym, float(w), reason):
                dropped.append({"symbol": sym, "reason": reason})
        elif w > 0:
            held[sym] = float(w)

    out = LiteAllocation(budget=float(budget))
    out.dropped = dropped
    out.substitutions = substitutions
    if not held:
        return out

    # ── Step 2: renormalise + return-tilt ────────────────────────────────
    tot = sum(held.values())
    targets = _tilt_weights({s: w / tot for s, w in held.items()},
                            expected_returns)

    # ── Steps 3–4: integer fit with budget escalation ────────────────────
    b = float(budget)
    best: Optional[tuple[dict[str, int], float, float]] = None
    while b <= budget_cap + 1e-9:
        affordable = {s: w for s, w in targets.items() if px_map[s] <= b}
        if affordable:
            sub_tot = sum(affordable.values())
            norm = {s: w / sub_tot for s, w in affordable.items()}
            shares, spent = _integer_fit(norm, px_map, b)
            drift = max(
                (abs((shares[s] * px_map[s] / spent if spent > 0 else 0.0)
                     - norm[s]) for s in norm if shares[s] > 0),
                default=1.0,
            )
            n_names = sum(1 for s in shares if shares[s] > 0)
            trial = LiteAllocation(budget=b)
            trial.max_weight_drift = drift
            trial.total_cost = spent
            trial.legs = [{"symbol": s} for s in shares if shares[s] > 0]
            if best is None or n_names > len(best[0]):
                best = (shares, spent, b)
            if (n_names >= min(_MIN_LITE_NAMES, len(targets))
                    and n_names >= 2
                    and drift <= _MAX_WEIGHT_DRIFT
                    and spent / b >= _MIN_DEPLOYED_FRAC):
                best = (shares, spent, b)
                break
        b += BUDGET_STEP_INR

    if best is None:
        return out
    shares, spent, used_budget = best
    out.budget = used_budget

    affordable = {s: w for s, w in targets.items() if px_map[s] <= used_budget}
    sub_tot = sum(affordable.values()) or 1.0
    norm = {s: w / sub_tot for s, w in affordable.items()}
    for s in targets:
        if s not in affordable:
            out.dropped.append({
                "symbol": s,
                "reason": (f"one share ≈ ₹{px_map[s]:,.0f} exceeds the "
                           f"₹{used_budget:,.0f} ticket"),
            })

    for s in sorted(norm, key=lambda x: -norm[x]):
        if shares.get(s, 0) <= 0:
            out.dropped.append({
                "symbol": s,
                "reason": f"weight too small to buy one share at ₹{px_map[s]:,.0f}",
            })
            continue
        cost = shares[s] * px_map[s]
        actual = cost / spent if spent > 0 else 0.0
        leg: dict[str, Any] = {
            "symbol": s, "shares": shares[s],
            "price": round(px_map[s], 2), "cost": round(cost, 2),
            "weight_target": round(norm[s], 4),
            "weight_actual": round(actual, 4),
        }
        if expected_returns and s in expected_returns:
            leg["event_mean_pct"] = round(float(expected_returns[s]), 2)
        if any(sub["in"] == s for sub in substitutions):
            leg["substitute"] = True
        out.legs.append(leg)
    out.total_cost = round(spent, 2)
    out.max_weight_drift = max(
        (abs(l["weight_actual"] - l["weight_target"]) for l in out.legs),
        default=0.0,
    )
    return out


# Backwards-compatible alias (older callers/tests import lite_allocation).
def lite_allocation(
    weights: dict[str, float],
    prices: dict[str, float],
    *,
    budget: float = ENTRY_BUDGET_INR,
) -> LiteAllocation:
    """The pre-bench formulation: same fit, no substitution bench."""
    return fit_allocation(weights, prices, budget=budget, budget_cap=budget)


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


def core_satellite(
    weights: dict[str, float],
    prices: dict[str, float],
    etf: dict[str, Any],
    *,
    budget: float = ENTRY_BUDGET_INR,
    expected_returns: Optional[dict[str, float]] = None,
    bench: Optional[Any] = None,
) -> Optional[dict[str, Any]]:
    """LAST-RESORT mixed route: ETF core (≤50% of the ticket, hard cap) +
    weight-proportional whole-share satellites from the strategy's names or
    the bench. Returns ``None`` when fewer than ``_MIN_SATELLITES`` real
    stocks fit — a mostly-ETF basket is refused, not shipped."""
    px_etf = etf.get("last_price")
    if px_etf is None or px_etf <= 0 or px_etf > budget * _CORE_FRAC_MAX:
        return None
    core_units = max(1, int(budget * _CORE_FRAC_MAX // px_etf))
    core_cost = core_units * float(px_etf)

    # Satellite candidates: strategy names first (by weight), then the bench.
    sat_weights: dict[str, float] = {
        s: w for s, w in weights.items()
        if (px := prices.get(s)) is not None and math.isfinite(px) and 0 < px
    }
    sat_prices = dict(prices)
    if bench is not None:
        for c in bench.ranked():
            if len(sat_weights) >= 12:
                break
            if (c.method == "event_study" and c.symbol not in sat_weights
                    and c.price is not None and c.price > 0
                    and (c.mean_episode_pct or 0) > 0):
                sat_weights[c.symbol] = min(sat_weights.values(), default=0.1)
                sat_prices[c.symbol] = c.price

    sat_budget = budget - core_cost
    fit = fit_allocation(
        sat_weights, sat_prices, budget=sat_budget, budget_cap=sat_budget,
        expected_returns=expected_returns,
    )
    sats = [dict(l, role="satellite") for l in fit.legs[:_MAX_SATELLITES]]
    if len(sats) < _MIN_SATELLITES:
        return None
    sat_cost = sum(s["cost"] for s in sats)

    # Leftover tops up the core ONLY while the core stays under the cap.
    leftover = budget - core_cost - sat_cost
    extra_units = int(leftover // px_etf)
    while extra_units > 0:
        new_core = (core_units + extra_units) * float(px_etf)
        if new_core / (new_core + sat_cost) <= _CORE_FRAC_MAX + 0.05:
            core_units += extra_units
            core_cost = new_core
            break
        extra_units -= 1
    total = core_cost + sat_cost
    return {
        "etf_leg": {
            "symbol": etf.get("symbol"), "units": core_units,
            "price": round(float(px_etf), 2), "cost": round(core_cost, 2),
            "tracks": etf.get("tracks"), "role": "core",
        },
        "satellites": sats,
        "etf_fraction": round(core_cost / total, 3) if total else None,
        "total_cost": round(total, 2),
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
    small_ticket: Optional[dict[str, Any]] = None,
    option_alternates: Optional[list[dict[str, Any]]] = None,
    as_of: Optional[str] = None,
    expected_returns: Optional[dict[str, float]] = None,
    bench: Optional[Any] = None,
    method_note: Optional[str] = None,
) -> dict[str, Any]:
    """The per-expression ``entry`` block the pack/serving layer attaches.

    Route preference for baskets: a faithful basket of real stocks first
    (strategy names, expensive ones substituted from the thesis bench);
    the capped ETF-core mix second; a pure ETF only when no affordable
    stock exists — stated. ``selection_method`` records the provenance of
    the numbers that drove selection/weights.
    """
    block: dict[str, Any] = {"kind": kind, "as_of": as_of}
    if method_note:
        block["selection_method"] = method_note

    hedge_note = (
        " Note: the index-hedge short leg needs margin at your broker — at a "
        "small ticket you hold the long side without the hedge (more market risk)."
        if kind == "hedge" else ""
    )
    if kind in ("basket", "hedge", "multi_asset") and weights and prices:
        er = dict(expected_returns) if expected_returns else None
        fit = fit_allocation(
            weights, prices, expected_returns=er, bench=bench,
        )
        etf_leg = etf_route(etf) if etf else None
        if fit.faithful:
            n_total = fit.n_names + len(fit.dropped)
            sub_note = ""
            if fit.substitutions:
                subs = ", ".join(
                    f"{s['in'].replace('.NS', '')} (for {s['out'].replace('.NS', '')})"
                    for s in fit.substitutions
                )
                sub_note = (
                    f" Priced-out names were replaced by event-tested picks "
                    f"from the same theme: {subs}."
                )
            block.update({
                "basis": "lite_basket",
                "min_entry_inr": round(fit.total_cost, 0),
                "budget_used_inr": round(fit.budget, 0),
                "legs": fit.legs,
                "dropped": fit.dropped,
                "substitutions": fit.substitutions,
                "note": (
                    f"A pared-down version of the same basket — "
                    f"{fit.n_names} of {n_total} names, whole shares only, so "
                    "weights drift a little from the full strategy."
                    + sub_note + hedge_note
                ),
            })
            if etf_leg:
                block["etf_alternative"] = etf_leg
            return block
        cs = core_satellite(
            weights, prices, etf,
            expected_returns=expected_returns, bench=bench,
        ) if etf else None
        if cs:
            sat_syms = ", ".join(
                s["symbol"].replace(".NS", "") for s in cs["satellites"]
            )
            block.update({
                "basis": "etf_core_plus_names",
                "min_entry_inr": round(cs["total_cost"], 0),
                "etf_fraction": cs.get("etf_fraction"),
                "legs": [cs["etf_leg"], *cs["satellites"]],
                "dropped": fit.dropped,
                "substitutions": fit.substitutions,
                "note": (
                    f"An ETF core (≤50% of the ticket) plus real stock picks: "
                    f"{cs['etf_leg']['units']} units of {cs['etf_leg']['symbol']} "
                    f"({cs['etf_leg']['tracks']}) carry the broad exposure and "
                    f"whole shares of {sat_syms} keep the strategy's specific "
                    "tilt." + hedge_note
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
                "dropped": fit.dropped,
                "note": (
                    "No affordable thesis-aligned stock cleared the screen at "
                    f"this ticket; the cheapest honest way in is "
                    f"{etf_leg['units']} unit(s) of {etf_leg['symbol']} "
                    f"({etf_leg['tracks']}) — one instrument, but each unit "
                    "itself holds the full index basket." + hedge_note
                ),
            })
            return block
        block.update({
            "basis": "unaffordable",
            "min_entry_inr": None,
            "dropped": fit.dropped,
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
        if small_ticket:
            # A DIFFERENT structure (single long far-OTM option) that fits a
            # small budget — carried alongside, never sold as the same trade.
            block["small_ticket"] = small_ticket
        if option_alternates:
            block["option_alternates"] = option_alternates
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
