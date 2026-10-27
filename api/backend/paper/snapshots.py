"""Daily account-grain NAV snapshots — the equity curve (P3).

Persists one ``PaperNavSnapshot`` per (account, as_of_date) by upserting the
output of ``valuation.compute_account_nav``. This module is the WRITER half of
NAV: ``valuation`` computes (and marks), this module commits the daily point
and rolls each open position's ``prev_close`` forward so tomorrow's day-P&L is
measured against today's close.

Money fields are written straight from the computed Decimals (already 4 dp);
``nifty_close`` is the only Float (cast at write). Caller owns commit — we
``flush`` only.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy.orm import Session

from backend.models import PaperNavSnapshot, PaperPosition

PriceFn = Callable[[str], Optional[Decimal]]


def snapshot_account_nav(
    db: Session,
    account,
    as_of_date: dt.date,
    price_fn: Optional[PriceFn] = None,
    nifty_close=None,
) -> PaperNavSnapshot:
    """Compute and UPSERT the account's NAV row for ``as_of_date``.

    Marks open positions + computes NAV via ``compute_account_nav``, then
    upserts the (account_id, as_of_date) snapshot in place (one row per day —
    re-running for the same date updates, never duplicates). After persisting,
    rolls each OPEN position's ``prev_close`` to its current ``last_price`` so
    the next day's day-P&L baseline is today's close. Flushes; returns the row.
    """
    # Imported here so the (already-written) valuation module is resolved at
    # call time and to keep the import graph a clean leaf.
    from backend.paper.valuation import compute_account_nav

    computed = compute_account_nav(db, account, price_fn)

    row = (
        db.query(PaperNavSnapshot)
        .filter(
            PaperNavSnapshot.account_id == account.id,
            PaperNavSnapshot.as_of_date == as_of_date,
        )
        .first()
    )
    if row is None:
        row = PaperNavSnapshot(
            account_id=account.id,
            user_id=account.user_id,
            as_of_date=as_of_date,
        )
        db.add(row)

    row.user_id = account.user_id
    row.cash_available = computed["cash_available"]
    row.cash_settled = computed["cash_settled"]
    row.positions_mv = computed["positions_mv"]
    row.nav = computed["nav"]
    row.realized_pnl_cum = computed["realized_pnl_cum"]
    row.unrealized_pnl = computed["unrealized_pnl"]
    row.nifty_close = float(nifty_close) if nifty_close is not None else None
    row.is_stale = computed["is_stale"]

    # Roll each open position's prev_close forward to today's mark so the next
    # snapshot's day-P&L measures vs today's close. Only when last_price is set
    # (a never-marked / stale-from-start lot has no close to carry).
    open_positions = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account.id,
            PaperPosition.quantity != 0,
        )
        .all()
    )
    for pos in open_positions:
        if pos.last_price is not None:
            pos.prev_close = pos.last_price

    db.flush()
    return row


def latest_nav(db: Session, account_id: str) -> Optional[PaperNavSnapshot]:
    """Most recent snapshot for the account by ``as_of_date`` (or None)."""
    return (
        db.query(PaperNavSnapshot)
        .filter(PaperNavSnapshot.account_id == account_id)
        .order_by(PaperNavSnapshot.as_of_date.desc())
        .first()
    )


def nav_series(
    db: Session,
    account_id: str,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
) -> list[PaperNavSnapshot]:
    """Snapshots for the account ordered by ``as_of_date`` ascending.

    Optional inclusive ``start``/``end`` date bounds — backs the FE equity
    curve.
    """
    q = db.query(PaperNavSnapshot).filter(
        PaperNavSnapshot.account_id == account_id
    )
    if start is not None:
        q = q.filter(PaperNavSnapshot.as_of_date >= start)
    if end is not None:
        q = q.filter(PaperNavSnapshot.as_of_date <= end)
    return q.order_by(PaperNavSnapshot.as_of_date.asc()).all()
