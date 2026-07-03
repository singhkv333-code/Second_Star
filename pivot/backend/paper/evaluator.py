"""The resting-order fill evaluator (P3).

A MARKET order fills synchronously at placement; LIMIT / SL / SL-M / GTT
orders REST (status='resting') until the live price crosses their
trigger/limit. This module is the loop that walks an account's resting
orders on a price tick and fills the ones whose condition is now met,
delegating the actual cash/position/ledger accrual to
backend.paper.fills.fill_resting_order (and cancel_resting_order for the
OCO sibling).

Decision vs effect are split:
  - should_fill(order, mark) is a PURE function: given a resting order and
    a mark, it returns the fill price if the order should fill now, else
    None. No DB, no side effects.
  - evaluate_resting_orders(db, account, price_fn) is the driver: it reads
    a live mark per order, captures the trigger reference on first sighting
    (order.intended_price), asks should_fill, and on a yes fills the order
    + cancels any OCO sibling. The caller owns commit (we only flush).

Trigger direction (SL / SL-M / GTT): the reference price is
order.intended_price (the LTP at decision time). A trigger AT OR BELOW the
reference is a DOWNSIDE trigger (a sell stop-loss below entry, a buy dip
order) and fires when the mark falls to/through it; a trigger ABOVE the
reference is an UPSIDE trigger (a take-profit above entry) and fires when
the mark rises to/through it. When intended_price is still None (a row
constructed without one), evaluate_resting_orders captures the FIRST mark
it sees as the reference before deciding — so the very first tick only
arms the trigger, it never fills.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy.orm import Session

from backend.models import PaperAccount, PaperOrder
from backend.paper import marks
from backend.paper.fills import cancel_resting_order, fill_resting_order
from backend.paper.money import to_money

# Trigger-based resting types share the reference/direction logic; LIMIT
# is price-relative and handled directly.
_TRIGGER_TYPES = {"SL", "SL-M", "GTT"}


def should_fill(order: PaperOrder, mark: Decimal) -> Optional[Decimal]:
    """Pure fill decision. Return the fill price (== mark) if ``order``
    should fill at this ``mark``, else None.

    LIMIT BUY fills when mark <= limit_price; LIMIT SELL when
    mark >= limit_price. Trigger orders (SL / SL-M / GTT) fire by direction
    relative to order.intended_price (the reference): a trigger at/below
    the reference is downside (fills when mark <= trigger), a trigger above
    is upside (fills when mark >= trigger). MARKET / unknown never rest, so
    return None.
    """
    mark = to_money(mark)
    ot = str(order.order_type).upper()
    side = str(order.transaction_type).upper()

    if ot == "LIMIT":
        if order.limit_price is None:
            return None
        limit = to_money(order.limit_price)
        if side == "BUY" and mark <= limit:
            return mark
        if side == "SELL" and mark >= limit:
            return mark
        return None

    if ot in _TRIGGER_TYPES:
        if order.trigger_price is None:
            return None
        trigger = to_money(order.trigger_price)
        # Reference defaults to the trigger itself only if intended_price
        # is somehow still unset (the driver normally captures it first);
        # trigger == reference is treated as downside (mark <= trigger).
        reference = (
            to_money(order.intended_price)
            if order.intended_price is not None
            else trigger
        )
        if trigger <= reference:  # downside trigger
            if mark <= trigger:
                return mark
        else:  # upside trigger
            if mark >= trigger:
                return mark
        return None

    # MARKET / unknown: should not be resting.
    return None


def evaluate_resting_orders(
    db: Session,
    account: PaperAccount,
    price_fn: Optional[Callable[[str], Optional[Decimal]]] = None,
) -> dict:
    """Walk ``account``'s resting orders and fill the ones whose
    trigger/limit the live price has crossed.

    For each resting order (oldest first): resolve a mark via ``price_fn``
    (default backend.paper.marks.get_mark_price); skip when it's None or
    <= 0. Capture the trigger reference on first sighting by setting
    order.intended_price to the mark when it's None. Ask should_fill; on a
    fill price, call fill_resting_order, and if the order belongs to a
    gtt_oco_group, cancel every OTHER still-resting order in that group
    (OCO: one fills, the sibling cancels). The caller owns commit.

    Returns {"evaluated": int, "filled": [order_id, ...],
    "cancelled": [order_id, ...], "skipped": [order_id, ...]}.
    """
    resolve = price_fn if price_fn is not None else marks.get_mark_price

    resting = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.account_id == account.id,
            PaperOrder.status == "resting",
        )
        .order_by(PaperOrder.created_at)
        .all()
    )

    filled: list = []
    cancelled: list = []
    skipped: list = []
    rejected: list = []

    for order in resting:
        # A prior OCO fill in this same pass may have cancelled this
        # sibling already; never act on a no-longer-resting row.
        if order.status != "resting":
            continue

        raw = resolve(order.symbol)
        if raw is None:
            skipped.append(order.id)
            continue
        mark = to_money(raw)
        if mark <= 0:
            skipped.append(order.id)
            continue

        # Capture the trigger reference the first time we see this order.
        if order.intended_price is None:
            order.intended_price = float(mark)
            db.flush()

        fp = should_fill(order, mark)
        if fp is None:
            skipped.append(order.id)
            continue

        fill = fill_resting_order(db, order, fp)
        if fill is None:
            # The fill SELF-REJECTED (e.g. insufficient buying power after
            # the reserve release, or an oversell). Do NOT report it as a
            # fill and do NOT touch OCO siblings — they must stay resting.
            rejected.append(order.id)
            continue
        filled.append(order.id)

        # OCO: cancel every OTHER still-resting order in the same group.
        # NOTE: this consumer is correct and tested, but no producer writes
        # gtt_oco_group yet — bracket orders (a shared SL+TP group) are wired
        # in a later phase. Until then this branch is dormant by design.
        if order.gtt_oco_group:
            siblings = (
                db.query(PaperOrder)
                .filter(
                    PaperOrder.account_id == account.id,
                    PaperOrder.gtt_oco_group == order.gtt_oco_group,
                    PaperOrder.status == "resting",
                    PaperOrder.id != order.id,
                )
                .all()
            )
            for sib in siblings:
                cancel_resting_order(db, sib)
                cancelled.append(sib.id)

    return {
        "evaluated": len(resting),
        "filled": filled,
        "cancelled": cancelled,
        "skipped": skipped,
        "rejected": rejected,
    }
