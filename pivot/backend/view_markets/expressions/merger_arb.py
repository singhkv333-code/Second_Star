"""View Markets — Phase 3 merger / open-offer arb calculator (E7).

The retail-friendly Indian event arb (spec §2 E7, §6 GAP row): buy the target at
or below the offer price and tender at the offer for cash (SEBI takeover /
buyback retail quota). Long-only — register-not-execute clean, no acquirer short
(stock-swap long-target/short-acquirer is infeasible in India; offer only the
long-target cash version and say the acquirer-short is out of scope).

This is a small, pure calculator (no DB, no I/O): the spread IS the market's
break-probability estimate. It encodes:

  * **spread** = offer_price − target_price (absolute + %).
  * **implied break probability** — the risk-neutral break-prob the spread
    implies given a downside-if-broken estimate (Bayesian one-liner:
    spread / (offer − broken_price), clamped to [0, 1]).
  * **annualized return** — the gross spread return annualized over
    ``days_to_close`` (the SEBI process is typically 3–4 months).
  * **proration** — when the open offer / buyback accepts only a fraction of
    tendered shares (retail quota acceptance ratio), the blended return after
    proration + the residual market exposure on the un-accepted stub.

All numbers are computed from the inputs the caller supplies (offer price,
target price, days-to-close, optional broken-price + acceptance-ratio) — never
fabricated. Returns ``None`` fields where an input is missing rather than
guessing.

Function raises ``NotImplementedError`` in the skeleton; the result shape is
frozen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MergerArbMetrics:
    """The computed open-offer / buyback arb metrics for one deal."""

    target_price: float
    offer_price: float
    days_to_close: int
    spread_abs: float                 # offer − target
    spread_pct: float                 # spread / target × 100
    gross_return_pct: float           # spread / target × 100 (held to close)
    annualized_return_pct: Optional[float]   # 365/days_to_close scaling
    implied_break_prob: Optional[float]      # 0..1, from spread vs downside
    broken_price: Optional[float]            # assumed price if the deal breaks
    acceptance_ratio: Optional[float]        # retail quota proration (0..1)
    prorated_return_pct: Optional[float]     # blended return after proration
    note: str


def merger_arb_metrics(
    *,
    target_price: float,
    offer_price: float,
    days_to_close: int,
    broken_price: Optional[float] = None,
    acceptance_ratio: Optional[float] = None,
) -> MergerArbMetrics:
    """Compute the long-only open-offer / buyback arb metrics.

    Parameters
    ----------
    target_price
        Current traded price of the target (what you'd buy at).
    offer_price
        The open-offer / buyback tender price (what you'd tender at).
    days_to_close
        Calendar days to the expected SEBI/CCI/NCLT completion (drives the
        annualization).
    broken_price
        Estimated price if the deal BREAKS (the downside). Required to compute
        ``implied_break_prob`` (``spread / (offer − broken)``); ``None`` leaves
        the break-prob ``None`` rather than guessing.
    acceptance_ratio
        Retail-quota proration (fraction of tendered shares accepted). When set,
        computes the blended ``prorated_return_pct`` over the accepted + stub
        legs. ``None`` leaves proration fields ``None``.

    Returns
    -------
    MergerArbMetrics
        All fields derived from the inputs; missing-input fields are ``None``.
        Raises ``ValueError`` on non-positive prices / days (caller guards).
    """
    if target_price <= 0 or offer_price <= 0:
        raise ValueError("target_price and offer_price must be positive")
    if days_to_close <= 0:
        raise ValueError("days_to_close must be a positive number of days")
    if broken_price is not None and broken_price <= 0:
        raise ValueError("broken_price, when supplied, must be positive")
    if acceptance_ratio is not None and not 0.0 < acceptance_ratio <= 1.0:
        raise ValueError("acceptance_ratio must be in (0, 1]")

    # --- spread -----------------------------------------------------------
    spread_abs = offer_price - target_price
    spread_pct = spread_abs / target_price * 100.0
    # Gross return if held to close and fully tendered/accepted at the offer.
    gross_return_pct = spread_pct

    # --- annualized (simple 365/days scaling, the merger-arb desk standard) ---
    annualized_return_pct: Optional[float] = gross_return_pct * 365.0 / days_to_close

    notes: list[str] = [
        "Long-only buy-and-tender: buy the target at/under the offer and tender "
        "for cash. The acquirer-short (stock-swap) leg is out of scope — no "
        "retail cash-delivery short exists in India.",
    ]
    if spread_abs < 0:
        notes.append(
            "Target trades ABOVE the offer (negative spread): the market is "
            "pricing a bump / competing bid, not a clean tender arb."
        )

    # --- implied break probability ---------------------------------------
    # Risk-neutral breakeven: (1-p)*(offer-target) = p*(target-broken)
    #   => p = (offer - target) / (offer - broken)  [the spread IS the odds].
    implied_break_prob: Optional[float] = None
    if broken_price is not None:
        downside_span = offer_price - broken_price
        if downside_span > 0 and spread_abs >= 0:
            implied_break_prob = spread_abs / downside_span
            # Clamp into a valid probability — the spread can imply >1 / <0 when
            # the broken-price estimate is mis-scaled; never report a fake odds.
            implied_break_prob = max(0.0, min(1.0, implied_break_prob))
            notes.append(
                "Implied break-probability = spread / (offer - broken_price): "
                "the risk-neutral odds the spread is already paying for a break."
            )
        else:
            notes.append(
                "Implied break-probability not computed: broken_price must sit "
                "below the offer (and the spread be non-negative) for the "
                "risk-neutral odds to be well-defined."
            )

    # --- proration (retail-quota acceptance) ------------------------------
    # A fraction `acceptance_ratio` is accepted at the offer; the un-accepted
    # stub stays exposed to the market. We value the stub at broken_price when a
    # downside estimate is supplied (conservative: the un-disturbed price the
    # residual tends to drift back toward), else at cost (zero stub P&L) rather
    # than fabricating a post-event price.
    prorated_return_pct: Optional[float] = None
    if acceptance_ratio is not None:
        stub_fraction = 1.0 - acceptance_ratio
        stub_price = broken_price if broken_price is not None else target_price
        accepted_pnl = acceptance_ratio * (offer_price - target_price)
        stub_pnl = stub_fraction * (stub_price - target_price)
        prorated_return_pct = (accepted_pnl + stub_pnl) / target_price * 100.0
        if broken_price is not None:
            stub_basis = "valued at broken_price (conservative downside)"
        else:
            stub_basis = "held flat at cost (no downside estimate supplied)"
        notes.append(
            f"Proration: {acceptance_ratio:.0%} tendered/accepted at the offer; "
            f"the {stub_fraction:.0%} un-accepted stub {stub_basis}. "
            "Real proration depends on the retail-quota subscription on the day."
        )

    return MergerArbMetrics(
        target_price=target_price,
        offer_price=offer_price,
        days_to_close=days_to_close,
        spread_abs=spread_abs,
        spread_pct=spread_pct,
        gross_return_pct=gross_return_pct,
        annualized_return_pct=annualized_return_pct,
        implied_break_prob=implied_break_prob,
        broken_price=broken_price,
        acceptance_ratio=acceptance_ratio,
        prorated_return_pct=prorated_return_pct,
        note=" ".join(notes),
    )


__all__ = [
    "MergerArbMetrics",
    "merger_arb_metrics",
]
