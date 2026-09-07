"""The synchronous MARKET fill engine (P1).

execute_market_fill is the heart of the paper broker: given an order and a
mark price, it computes friction, writes the immutable fill, updates the
derived position (avg-cost), moves cash, and appends a ledger row — all in
Decimal, all from the order's own session (the caller owns commit).

Cost model (no new cost code — reuse services/trading_costs):
  - trading_costs.buy_cost/sell_cost already bake SLIPPAGE_PCT into the
    returned ``charges``. So we fill at the CLEAN mark (the touch) and take
    ALL friction from ``charges`` — applying price slippage on top would
    double-count. slippage_bps records realized-vs-intended drift, which
    is 0 for a synchronous market fill (fill == intended == mark).
  - BUY:  (net_debit, charges) = buy_cost(price, qty); cashflow = -net_debit
  - SELL: (net_credit, charges) = sell_cost(price, qty); cashflow = +net_credit
          realized_pnl = net_credit - qty * avg_cost  (both sides net of cost)

avg_cost includes buy-side charges (net_debit/qty), so realized P&L on the
sell is the true, cost-inclusive number. avg_cost is re-quantized to 4 dp
on each buy, so the per-share basis (and thus cumulative realized_pnl) can
carry a bounded sub-2-paise rounding residue vs the exact cost basis over
a position's life — it never affects CASH (cash moves only by the once-
quantized net_cashflow) and is below display precision. Carrying an
unrounded basis is a later refinement (the avg_cost column is Numeric(18,4)).

Caller contract: execute_market_fill assumes quantity > 0 (the broker
rejects qty <= 0 before reaching here, which also avoids the new_qty
division by zero).

Shorting: CNC (delivery) stays long-only — India bans naked short
delivery, so a CNC SELL past the held quantity is still rejected
("insufficient_position"). MIS (intraday) may sell past the held
quantity, which opens/extends a short (quantity goes negative); a BUY
against a short quantity covers it. Both SELL and BUY split a fill
that crosses zero into a "close the existing side" leg (realizes P&L
at the OLD avg_cost) and an "open the new side" leg (seeds a fresh
avg_cost from this fill's own price) — e.g. selling 15 against a held
10 realizes P&L on 10 and opens a 5-share short at this fill's price.
Equity positions have no separate margin check on the short leg (the
P1 model doesn't price margin) — EOD auto square-off (see
`backend/paper/jobs.py::squareoff_intraday_shorts`) is the guard that
keeps MIS shorts from carrying overnight.

Settlement: P1 uses the SIMPLIFIED model — a MARKET buy debits and a sell
credits BOTH cash_available and cash_settled by net_cashflow, so on the
pure MARKET path they move together. (The resting-BUY reserve moves
cash_available -> cash_reserved without touching cash_settled, so the
exact invariant is cash_settled == cash_available + cash_reserved, i.e.
total owned settled cash; buying power = cash_available - cash_reserved.)
cash_settled is mutated WITHOUT its own ledger row here — only
cash_available is reconcilable by replay in P1; the kind='settlement'
ledger row + strict T+1 (cash_settled lagging until an EOD roll) is a P3
refinement. settles_at is now_ist() + 1 CALENDAR day (display-only);
advancing to the next trading day comes with strict T+1.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from backend.models import (
    ForwardIdea,
    PaperAccount,
    PaperFill,
    PaperLedgerEntry,
    PaperOrder,
    PaperPosition,
)
from backend.paper.money import to_money
from backend.services.trading_costs import buy_cost, sell_cost
from backend.utils.time_utils import now_ist


def _get_or_create_position(
    db: Session, account_id: str, user_id: int, symbol: str,
) -> PaperPosition:
    pos = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == symbol,
        )
        .first()
    )
    if pos is None:
        pos = PaperPosition(
            account_id=account_id, user_id=user_id, symbol=symbol,
            quantity=0, avg_cost=to_money(0), realized_pnl=to_money(0),
        )
        db.add(pos)
        db.flush()
    return pos


def execute_market_fill(
    db: Session, order: PaperOrder, mark_price: Decimal,
) -> Optional[PaperFill]:
    """Fill ``order`` at ``mark_price``. Mutates the order's status in
    place. Returns the PaperFill on success, or None on a reject (the
    order's status/reject_reason are set; no cash/position change)."""
    account = db.get(PaperAccount, order.account_id)
    if account is None:  # FK guarantees this; explicit for safety + typing
        raise ValueError(f"paper account {order.account_id} not found")
    price = to_money(mark_price)
    # Preserve fractional quantity (US shares / crypto units); order.quantity is
    # a Numeric(18,8) Decimal. int() here would silently truncate 2.5 → 2.
    qty = Decimal(str(order.quantity))
    side = str(order.transaction_type).upper()

    # Validate BEFORE touching the position (a reject must leave no
    # position row and no cash change).
    if side == "BUY":
        net_debit_f, charges_f = buy_cost(float(price), qty)
        net_debit = to_money(net_debit_f)
        charges = to_money(charges_f)
        # cash_available is the free balance (the reserve has already been
        # moved into cash_reserved), so it IS the buying power — subtracting
        # cash_reserved again would double-count and reject legit orders.
        buying_power = to_money(account.cash_available)
        if net_debit > buying_power:
            order.status = "rejected"
            order.reject_reason = "insufficient_buying_power"
            db.flush()
            return None
        net_cashflow = -net_debit
        pos = _get_or_create_position(
            db, order.account_id, order.user_id, order.symbol,
        )
        current_qty = pos.quantity
        # Covering leg: this BUY closes an existing short (current_qty < 0)
        # first, realizing P&L at the short's entry avg_cost. Anything left
        # over opens/extends a long. Both legs take their SHARE of the
        # already-rounded net_debit (net_debit * leg_qty / qty) rather than
        # rounding a per-unit price first — a pre-rounded per-unit price,
        # re-multiplied, drifts from the total by a few paise; sharing the
        # total keeps it exact, and collapses to the pre-shorting formula
        # bit-for-bit when there's only one leg (cover_qty or open_qty == qty).
        cover_qty = -current_qty if current_qty < 0 else Decimal(0)
        cover_qty = min(qty, cover_qty)
        open_qty = qty - cover_qty
        existing_long_qty = current_qty if current_qty > 0 else Decimal(0)
        opens_new_side = open_qty > 0 and existing_long_qty == 0

        realized: Optional[Decimal] = None
        if cover_qty > 0:
            realized = to_money(
                to_money(pos.avg_cost) * cover_qty
                - net_debit * cover_qty / qty
            )
            pos.realized_pnl = to_money(pos.realized_pnl) + realized

        new_qty = current_qty + qty
        if open_qty > 0:
            new_long_qty = existing_long_qty + open_qty
            # avg_cost compounds, inclusive of buy charges.
            pos.avg_cost = to_money(
                (to_money(pos.avg_cost) * existing_long_qty
                 + net_debit * open_qty / qty) / new_long_qty
            )
        elif new_qty == 0:
            pos.avg_cost = to_money(0)
        # else: new_qty < 0 — a partial cover, still short; avg_cost (the
        # short's entry price) is untouched.
        pos.quantity = new_qty
        if opens_new_side:
            # Seed an immediate mark + a same-day reference at the moment a
            # position OPENS, rather than leaving last_price/prev_close None
            # until the next scheduler tick / the once-daily EOD snapshot.
            # Without this, P&L reads ₹0 for hours after a fill (last_price
            # None -> falls back to avg_cost) and "Day P&L" has no reference
            # until tomorrow. prev_close=fill_price makes today's day-P&L
            # read as "move since I bought it"; the EOD snapshot correctly
            # overwrites prev_close to the real close from then on.
            pos.last_price = float(price)
            pos.last_mark_at = now_ist()
            pos.prev_close = float(price)
            pos.stale = False
        ledger_kind = "buy_debit"
        settles_at = None
    elif side == "SELL":
        # Query the existing lot WITHOUT creating one, so a rejected order
        # (CNC oversell) leaves nothing behind.
        sell_pos = (
            db.query(PaperPosition)
            .filter(
                PaperPosition.account_id == order.account_id,
                PaperPosition.symbol == order.symbol,
            )
            .first()
        )
        current_qty = sell_pos.quantity if sell_pos is not None else Decimal(0)
        product = str(getattr(order, "product", "CNC") or "CNC").upper()
        shorting_allowed = product == "MIS"
        # CNC stays long-only (no naked delivery shorts): reject an oversell.
        # MIS may sell past the held quantity to open/extend a short.
        if not shorting_allowed and (sell_pos is None or qty > current_qty):
            order.status = "rejected"
            order.reject_reason = "insufficient_position"
            db.flush()
            return None

        net_credit_f, charges_f = sell_cost(float(price), qty)
        net_credit = to_money(net_credit_f)
        charges = to_money(charges_f)
        net_cashflow = net_credit

        pos = sell_pos or _get_or_create_position(
            db, order.account_id, order.user_id, order.symbol,
        )
        # Closing leg: this SELL closes an existing long first (at the
        # long's avg_cost), realizing P&L. Anything left over (only
        # possible with MIS) opens/extends a short. Both legs take their
        # SHARE of the already-rounded net_credit (see the BUY-side note
        # above on why — same reasoning, mirrored).
        close_qty = min(qty, current_qty) if current_qty > 0 else Decimal(0)
        open_qty = qty - close_qty
        existing_short_qty = -current_qty if current_qty < 0 else Decimal(0)
        opens_new_side = open_qty > 0 and existing_short_qty == 0

        realized: Optional[Decimal] = None
        if close_qty > 0:
            realized = to_money(
                net_credit * close_qty / qty
                - to_money(pos.avg_cost) * close_qty
            )
            pos.realized_pnl = to_money(pos.realized_pnl) + realized

        new_qty = current_qty - qty
        if open_qty > 0:
            new_short_qty = existing_short_qty + open_qty
            pos.avg_cost = to_money(
                (to_money(pos.avg_cost) * existing_short_qty
                 + net_credit * open_qty / qty) / new_short_qty
            )
        elif new_qty == 0:
            pos.avg_cost = to_money(0)
        # else: new_qty > 0 — a partial close, still long; avg_cost
        # (the long's cost basis) is untouched.
        pos.quantity = new_qty
        if opens_new_side:
            # Mirrors the BUY-side seeding above — a freshly-opened short
            # needs an immediate mark too.
            pos.last_price = float(price)
            pos.last_mark_at = now_ist()
            pos.prev_close = float(price)
            pos.stale = False
        ledger_kind = "sell_credit"
        settles_at = now_ist() + timedelta(days=1)
    else:
        order.status = "rejected"
        order.reject_reason = f"bad_side:{side}"
        db.flush()
        return None

    gross = to_money(price * qty)
    account.cash_available = to_money(account.cash_available) + net_cashflow
    account.cash_settled = to_money(account.cash_settled) + net_cashflow

    fill = PaperFill(
        order_id=order.id,
        account_id=order.account_id,
        user_id=order.user_id,
        idea_id=order.idea_id,
        symbol=order.symbol,
        transaction_type=side,
        quantity=qty,
        fill_price=float(price),
        gross_value=gross,
        charges=charges,
        net_cashflow=net_cashflow,
        slippage_bps=0.0,  # synchronous market: fill == intended == mark
        realized_pnl=realized,
        settles_at=settles_at,
        filled_at=now_ist(),
    )
    db.add(fill)
    order.status = "filled"
    order.filled_quantity = qty
    db.flush()

    db.add(PaperLedgerEntry(
        account_id=order.account_id,
        fill_id=fill.id,
        kind=ledger_kind,
        amount=net_cashflow,
        balance_after=to_money(account.cash_available),
        note=f"{side} {qty} {order.symbol} @ {price}",
    ))

    # Forward-test (P6): stamp the idea's inception on its FIRST fill. Both
    # MARKET and resting fills route through here, so first-fill is captured
    # once for every fill type; a rejected order returns above, so a reject
    # never dates an idea. inception_date drives the scorecard's maturity.
    if order.idea_id is not None:
        idea = db.get(ForwardIdea, order.idea_id)
        if idea is not None and idea.inception_date is None:
            idea.inception_date = now_ist().date()

    db.flush()
    return fill


def _release_reserve(db: Session, account: PaperAccount, order: PaperOrder) -> None:
    """Release a resting order's reserved cash back to cash_available.

    Moves cash_reserved -> cash_available, writes a 'release' ledger row
    (positive amount), zeroes order.reserved_cash, and flushes. No-op when
    there is nothing reserved. Used by both the fill and cancel paths.
    """
    reserved = to_money(order.reserved_cash) if order.reserved_cash else to_money(0)
    if reserved <= 0:
        return
    account.cash_available = to_money(account.cash_available) + reserved
    account.cash_reserved = to_money(account.cash_reserved) - reserved
    db.add(PaperLedgerEntry(
        account_id=order.account_id,
        kind="release",
        amount=reserved,
        balance_after=to_money(account.cash_available),
        note=f"release {order.symbol}",
    ))
    order.reserved_cash = to_money(0)
    db.flush()


def fill_resting_order(
    db: Session, order: PaperOrder, fill_price: Decimal,
) -> Optional[PaperFill]:
    """Fill a RESTING order at ``fill_price`` (P3 evaluator path).

    Releases any cash this order reserved on placement (a 'release' ledger
    row), then delegates to execute_market_fill — which re-reads cash, so
    the release MUST be flushed first for the buying-power check to pass.
    Reusing execute_market_fill gives the identical position/cash/ledger
    accrual + the immutable PaperFill and sets order.status='filled'.
    Returns the PaperFill on success, or None on a reject.
    """
    account = db.get(PaperAccount, order.account_id)
    if account is None:  # FK guarantees this; explicit for safety + typing
        raise ValueError(f"paper account {order.account_id} not found")
    _release_reserve(db, account, order)
    return execute_market_fill(db, order, fill_price)


def cancel_resting_order(db: Session, order: PaperOrder) -> None:
    """Cancel a resting order: release any reserved cash (cash move + a
    'release' ledger row + zeroed reserved_cash), set status='cancelled',
    and flush. No fill is written and no position changes."""
    account = db.get(PaperAccount, order.account_id)
    if account is None:  # FK guarantees this; explicit for safety + typing
        raise ValueError(f"paper account {order.account_id} not found")
    _release_reserve(db, account, order)
    order.status = "cancelled"
    db.flush()
