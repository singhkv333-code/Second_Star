"""Mark-to-market + NAV computation for the paper book (P3).

A LEAF in the paper subsystem: depends only on the ORM models, ``money``
(Decimal quantization) and ``marks`` (live-price resolution). No fills, no
broker, no ledger writes — this module READS positions, stamps their marks,
and derives portfolio value. Persisting NAV snapshots is a separate module.

All money math is in Decimal via ``to_money`` (4 dp). The position columns
``last_price`` and ``prev_close`` are SQL Float, so we route them through
``to_money`` (which goes via str()) before any Decimal arithmetic — never a
raw ``Decimal(float)`` that would inherit binary-float noise.

Mark fallbacks for valuation (NOT for refresh):
  - position_market_value uses ``last_price`` when set, else ``avg_cost``
    (a never-marked lot is valued at book, not zero).
  - position_unrealized_pnl / position_day_pnl return 0 when the required
    mark(s) are missing — an unmarked lot has no measurable P&L yet.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy.orm import Session

from backend.models import PaperPosition
from backend.paper import marks
from backend.paper.money import to_money
from backend.utils.time_utils import now_ist

PriceFn = Callable[[str], Optional[Decimal]]


def _resolve_price_fn(price_fn: Optional[PriceFn]) -> PriceFn:
    """Default to the live mark resolver; tests inject an offline fn."""
    if price_fn is not None:
        return price_fn
    return lambda sym: marks.get_mark_price(sym)


def mark_positions(
    db: Session, account_id: str, price_fn: Optional[PriceFn] = None,
) -> int:
    """Refresh marks for every OPEN (quantity > 0) position in the account.

    For each position, resolve px = price_fn(symbol). A positive Decimal
    stamps last_price/last_mark_at and clears ``stale``; None or <= 0 marks
    the position ``stale`` and leaves last_price untouched (stale value
    falls back to the last known / book mark). Flushes once. Returns the
    number of positions whose price was successfully refreshed.
    """
    pf = _resolve_price_fn(price_fn)
    positions = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account_id,
            PaperPosition.quantity > 0,
        )
        .all()
    )
    refreshed = 0
    for pos in positions:
        px = pf(pos.symbol)
        if px is not None and to_money(px) > 0:
            pos.last_price = float(to_money(px))
            pos.last_mark_at = now_ist()
            pos.stale = False
            refreshed += 1
        else:
            pos.stale = True
    db.flush()
    return refreshed


def position_market_value(pos: PaperPosition) -> Decimal:
    """Quantity * mark, where mark = last_price if marked else avg_cost."""
    mark = (
        to_money(pos.last_price) if pos.last_price is not None
        else to_money(pos.avg_cost)
    )
    return to_money(pos.quantity * mark)


def position_unrealized_pnl(pos: PaperPosition) -> Decimal:
    """Quantity * (last_price - avg_cost); 0 when never marked."""
    if pos.last_price is not None:
        return to_money(pos.quantity * (to_money(pos.last_price) - to_money(pos.avg_cost)))
    return to_money(0)


def position_day_pnl(pos: PaperPosition) -> Decimal:
    """Quantity * (last_price - prev_close); 0 unless BOTH are set."""
    if pos.last_price is not None and pos.prev_close is not None:
        return to_money(
            pos.quantity * (to_money(pos.last_price) - to_money(pos.prev_close))
        )
    return to_money(0)


def compute_account_nav(
    db: Session, account, price_fn: Optional[PriceFn] = None,
) -> dict:
    """Mark all open positions, then aggregate the account's NAV.

    Returns Decimals for every money field and a bool ``is_stale`` (True if
    any OPEN position is stale). realized_pnl_cum sums realized P&L across
    ALL positions including fully-closed ones (which retain their realized
    total). nav = cash_available + cash_reserved + positions_mv — i.e. TOTAL
    owned cash (reserved cash held against a resting BUY is still owned, so
    NAV must include it or the equity curve dips when an order rests and
    jumps when it fills).
    """
    mark_positions(db, account.id, price_fn)

    positions = (
        db.query(PaperPosition)
        .filter(PaperPosition.account_id == account.id)
        .all()
    )

    positions_mv = to_money(0)
    unrealized_pnl = to_money(0)
    realized_pnl_cum = to_money(0)
    is_stale = False
    for pos in positions:
        realized_pnl_cum += to_money(pos.realized_pnl)
        if pos.quantity > 0:
            positions_mv += position_market_value(pos)
            unrealized_pnl += position_unrealized_pnl(pos)
            if pos.stale:
                is_stale = True

    cash_available = to_money(account.cash_available)
    cash_settled = to_money(account.cash_settled)
    cash_reserved = to_money(account.cash_reserved)
    # Total owned cash (available + reserved) + market value. Reserved cash
    # is held against resting BUYs but still belongs to the account.
    nav = to_money(cash_available + cash_reserved + positions_mv)

    return {
        "cash_available": cash_available,
        "cash_settled": cash_settled,
        "cash_reserved": cash_reserved,
        "positions_mv": positions_mv,
        "nav": nav,
        "realized_pnl_cum": realized_pnl_cum,
        "unrealized_pnl": unrealized_pnl,
        "is_stale": is_stale,
    }
