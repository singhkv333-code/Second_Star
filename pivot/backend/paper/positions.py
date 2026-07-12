"""Kite-SHAPED paper reads (P4).

A LEAF in the paper subsystem: the squareoff / cancel executors in the
workflow engine were written against the live Kite API and key off its
exact dict shapes (``get_positions`` -> {"net": [...], "day": [...]} and
``get_orders`` -> [{...}]). These two read functions project the paper
book into those same shapes so those executors can run unchanged against a
paper account.

READ-ONLY: both functions compute from STORED position state (last_price,
falling back to avg_cost when last_price is None). They never mark, write,
or commit — marking is the P3 scheduler's job, not a GET's. Money columns
read back as Decimal, so every numeric in a returned dict is cast to float
at the JSON edge via ``money_to_float`` / ``float()``.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import PaperAccount, PaperOrder, PaperPosition
from backend.paper.money import money_to_float
from backend.paper.valuation import position_unrealized_pnl

# Paper order_types that map to Kite's "TRIGGER PENDING" status (the rest
# of a resting blotter is plain "OPEN"). These are the trigger-based
# varieties cancel_orders distinguishes when squaring a book.
_TRIGGER_TYPES = {"SL", "SL-M", "GTT"}


def _qty_out(q):
    """Project a Numeric(18,8) quantity to the read shape: a plain int for a
    whole number (Indian shares/lots), a float when fractional (US shares /
    crypto units). Keeps existing integer consumers unchanged."""
    try:
        f = float(q)
    except Exception:  # noqa: BLE001
        return 0
    return int(f) if f == int(f) else f


def _account(db: Session, user_id: int):
    """The user's paper account, or None (read-only lookup)."""
    return (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == int(user_id))
        .first()
    )


def paper_positions_kite_shape(db: Session, user_id: int) -> dict:
    """Project OPEN paper positions into Kite ``get_positions`` shape.

    Returns {"net": [...], "day": [...]} where each entry mirrors a Kite
    position dict. Only OPEN positions (quantity != 0) for the user's account
    are included; a fully-closed lot (quantity 0) keeps its row but is
    excluded here. A short option leg (quantity < 0) is open too and must be
    included so squareoff/cancel executors can see and close it. "day"
    mirrors "net" — the paper book has no separate intraday vs overnight
    split. If the user has no paper account, both legs are empty.
    """
    account = _account(db, user_id)
    if account is None:
        return {"net": [], "day": []}

    positions = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account.id,
            PaperPosition.quantity != 0,
        )
        .all()
    )

    net = []
    for pos in positions:
        last_price = (
            pos.last_price if pos.last_price is not None else pos.avg_cost
        )
        net.append(
            {
                "tradingsymbol": pos.symbol,
                "exchange": "NSE",
                "product": "CNC",
                "quantity": _qty_out(pos.quantity),
                "average_price": money_to_float(pos.avg_cost),
                "last_price": money_to_float(last_price),
                "pnl": money_to_float(position_unrealized_pnl(pos)),
            }
        )

    # Paper has no separate intraday book; "day" mirrors "net" with
    # independent dict copies so a caller mutating one leg can't alias both.
    return {"net": net, "day": [dict(p) for p in net]}


def paper_open_orders_kite_shape(db: Session, user_id: int) -> list[dict]:
    """Project RESTING paper orders into Kite ``get_orders`` shape.

    Returns a list of order dicts so cancel_orders can filter the paper
    blotter the way it filters the live one. Only status=='resting' orders
    are returned. Trigger-based order_types (SL / SL-M / GTT) report Kite's
    "TRIGGER PENDING" status; everything else resting reports "OPEN". If the
    user has no paper account, the list is empty.
    """
    account = _account(db, user_id)
    if account is None:
        return []

    orders = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.account_id == account.id,
            PaperOrder.status == "resting",
        )
        .all()
    )

    out = []
    for order in orders:
        status = (
            "TRIGGER PENDING"
            if order.order_type in _TRIGGER_TYPES
            else "OPEN"
        )
        out.append(
            {
                "order_id": order.id,
                "tradingsymbol": order.symbol,
                "exchange": "NSE",
                "transaction_type": order.transaction_type,
                "quantity": _qty_out(order.quantity),
                "order_type": order.order_type,
                "product": "CNC",
                "status": status,
            }
        )
    return out
