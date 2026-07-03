"""READ-ONLY portfolio read service for the paper book (P4 REST API).

A LEAF read layer: every function takes ``(db, user_id, ...)`` and returns
JSON-ready dicts/lists (plain floats, never Decimal). It NEVER writes, marks,
or commits — it computes purely from the STORED ``position.last_price`` (with
the documented fall-back to ``avg_cost`` when ``last_price`` is None, handled
inside ``backend.paper.valuation``). Marking is the scheduler's job (P3); a GET
must not mutate the book.

Money columns arrive as ``decimal.Decimal``; we cast every returned money field
to ``float`` via ``money_to_float`` so the payload is JSON-serialisable.

If the user has no ``PaperAccount`` we return the documented empty shape
(``{"exists": False}`` for the summary, ``[]`` for the lists) rather than
raising — a never-traded user is a valid, empty book.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy.orm import Session

from backend.models import PaperAccount, PaperFill, PaperOrder, PaperPosition
from backend.paper.money import money_to_float, to_money
from backend.paper.snapshots import nav_series
from backend.paper.valuation import (
    position_day_pnl,
    position_market_value,
    position_unrealized_pnl,
)
from backend.routers.portfolio import SECTOR_MAP


def _get_account(db: Session, user_id: int) -> Optional[PaperAccount]:
    return (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == int(user_id))
        .first()
    )


def _iso(value) -> Optional[str]:
    """ISO-8601 string for a date/datetime, or None."""
    if value is None:
        return None
    return value.isoformat()


def account_summary(db: Session, user_id: int) -> dict:
    """READ-ONLY account roll-up from the STORED marks.

    Returns ``{"exists": False}`` when the user has no paper account. Else a
    JSON-ready dict of floats plus counts/flags. NAV includes reserved cash
    (still owned, held against a resting BUY) so the equity curve does not dip
    when an order rests: ``nav = cash_available + cash_reserved + positions_mv``.
    """
    account = _get_account(db, user_id)
    if account is None:
        return {"exists": False}

    positions = (
        db.query(PaperPosition)
        .filter(PaperPosition.account_id == account.id)
        .all()
    )

    positions_mv = to_money(0)
    invested = to_money(0)
    unrealized_pnl = to_money(0)
    day_pnl = to_money(0)
    realized_pnl_cum = to_money(0)
    num_positions = 0
    is_stale = False
    for pos in positions:
        # realized P&L accrues across ALL positions, incl. fully-closed lots
        # that retain their realized total at quantity 0.
        realized_pnl_cum += to_money(pos.realized_pnl)
        if pos.quantity > 0:
            num_positions += 1
            positions_mv += position_market_value(pos)
            invested += to_money(pos.quantity * to_money(pos.avg_cost))
            unrealized_pnl += position_unrealized_pnl(pos)
            day_pnl += position_day_pnl(pos)
            if pos.stale:
                is_stale = True

    cash_available = to_money(account.cash_available)
    cash_settled = to_money(account.cash_settled)
    cash_reserved = to_money(account.cash_reserved)
    starting_capital = to_money(account.starting_capital)

    # Buying power IS cash_available: the reserve already moved money out of
    # cash_available into cash_reserved (broker), so subtracting it again
    # would double-count the reserve (and go negative once anything rests).
    buying_power = cash_available
    nav = cash_available + cash_reserved + positions_mv
    total_pnl = nav - starting_capital

    total_pnl_pct = (
        float(total_pnl / starting_capital * 100)
        if starting_capital != 0 else 0.0
    )
    unrealized_pct = (
        float(unrealized_pnl / invested * 100) if invested != 0 else 0.0
    )

    num_open_orders = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.account_id == account.id,
            PaperOrder.status == "resting",
        )
        .count()
    )

    return {
        "exists": True,
        "mode": account.mode,
        "starting_capital": money_to_float(starting_capital),
        "cash_available": money_to_float(cash_available),
        "cash_settled": money_to_float(cash_settled),
        "cash_reserved": money_to_float(cash_reserved),
        "buying_power": money_to_float(buying_power),
        "positions_mv": money_to_float(positions_mv),
        "invested": money_to_float(invested),
        "nav": money_to_float(nav),
        "unrealized_pnl": money_to_float(unrealized_pnl),
        "realized_pnl_cum": money_to_float(realized_pnl_cum),
        "day_pnl": money_to_float(day_pnl),
        "total_pnl": money_to_float(total_pnl),
        "total_pnl_pct": total_pnl_pct,
        "unrealized_pct": unrealized_pct,
        "num_positions": num_positions,
        "num_open_orders": num_open_orders,
        "is_stale": is_stale,
    }


def holdings(db: Session, user_id: int) -> list[dict]:
    """One dict per OPEN position (quantity > 0), sorted by market value desc.

    Returns ``[]`` when the user has no account. ``last_price`` is None for an
    unmarked lot (its market value falls back to book/avg_cost). All money
    fields are floats.
    """
    account = _get_account(db, user_id)
    if account is None:
        return []

    positions = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account.id,
            PaperPosition.quantity > 0,
        )
        .all()
    )

    rows = []
    for pos in positions:
        mv = position_market_value(pos)
        invested = to_money(pos.quantity * to_money(pos.avg_cost))
        unrealized = position_unrealized_pnl(pos)
        unrealized_pct = (
            float(unrealized / invested * 100) if invested != 0 else 0.0
        )
        rows.append({
            "symbol": pos.symbol,
            "quantity": pos.quantity,
            "avg_cost": money_to_float(pos.avg_cost),
            "last_price": (
                float(pos.last_price) if pos.last_price is not None else None
            ),
            "market_value": money_to_float(mv),
            "unrealized_pnl": money_to_float(unrealized),
            "unrealized_pct": unrealized_pct,
            "day_pnl": money_to_float(position_day_pnl(pos)),
            "invested": money_to_float(invested),
            "realized_pnl": money_to_float(pos.realized_pnl),
            "sector": SECTOR_MAP.get(pos.symbol, "Other"),
            "stale": bool(pos.stale),
            "last_mark_at": _iso(pos.last_mark_at),
        })

    rows.sort(key=lambda r: r["market_value"], reverse=True)
    return rows


def open_orders(db: Session, user_id: int) -> list[dict]:
    """Resting orders (status == 'resting'), newest first. JSON-ready dicts."""
    account = _get_account(db, user_id)
    if account is None:
        return []

    orders = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.account_id == account.id,
            PaperOrder.status == "resting",
        )
        .order_by(PaperOrder.created_at.desc())
        .all()
    )

    return [{
        "id": o.id,
        "symbol": o.symbol,
        "side": o.transaction_type,
        "order_type": o.order_type,
        "quantity": o.quantity,
        "limit_price": (
            float(o.limit_price) if o.limit_price is not None else None
        ),
        "trigger_price": (
            float(o.trigger_price) if o.trigger_price is not None else None
        ),
        "reserved_cash": money_to_float(o.reserved_cash),
        "status": o.status,
        "source": o.source,
        "origin_kind": o.origin_kind,
        "created_at": _iso(o.created_at),
    } for o in orders]


def fills_journal(db: Session, user_id: int, limit: int = 50) -> list[dict]:
    """The fills log, newest first, capped at ``limit``. JSON-ready dicts."""
    account = _get_account(db, user_id)
    if account is None:
        return []

    fills = (
        db.query(PaperFill)
        .filter(PaperFill.account_id == account.id)
        .order_by(PaperFill.filled_at.desc())
        .limit(limit)
        .all()
    )

    return [{
        "id": f.id,
        "symbol": f.symbol,
        "side": f.transaction_type,
        "quantity": f.quantity,
        "fill_price": float(f.fill_price),
        "gross_value": money_to_float(f.gross_value),
        "charges": money_to_float(f.charges),
        "net_cashflow": money_to_float(f.net_cashflow),
        "realized_pnl": (
            money_to_float(f.realized_pnl)
            if f.realized_pnl is not None else None
        ),
        "filled_at": _iso(f.filled_at),
        "order_id": f.order_id,
    } for f in fills]


def nav_curve(
    db: Session,
    user_id: int,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
) -> list[dict]:
    """The equity curve: NAV snapshots oldest first. JSON-ready dicts."""
    account = _get_account(db, user_id)
    if account is None:
        return []

    series = nav_series(db, account.id, start=start, end=end)
    return [{
        "as_of_date": _iso(s.as_of_date),
        "nav": money_to_float(s.nav),
        "cash_available": money_to_float(s.cash_available),
        "positions_mv": money_to_float(s.positions_mv),
        "realized_pnl_cum": money_to_float(s.realized_pnl_cum),
        "unrealized_pnl": money_to_float(s.unrealized_pnl),
        "nifty_close": (
            float(s.nifty_close) if s.nifty_close is not None else None
        ),
    } for s in series]
