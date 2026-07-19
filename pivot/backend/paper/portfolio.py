"""READ-ONLY portfolio read service for the paper book (P4 REST API).

A LEAF read layer: every function takes ``(db, user_id, ...)`` and returns
JSON-ready dicts/lists (plain floats, never Decimal). It NEVER writes or
commits — but it DOES mark each open position LIVE on read (via
``marks.get_mark_price``, the same resolver the agent Positions view uses) so
every surface shows one consistent price. This is an in-memory mark-on-read:
the live price is passed into the valuation helpers, never persisted to the
row (persisting marks is still the scheduler's job, P3). When no live price is
available it falls back to the stored ``last_price``, then ``avg_cost`` — so an
offline read degrades to book value rather than fabricating a move.

Money columns arrive as ``decimal.Decimal``; we cast every returned money field
to ``float`` via ``money_to_float`` so the payload is JSON-serialisable.

If the user has no ``PaperAccount`` we return the documented empty shape
(``{"exists": False}`` for the summary, ``[]`` for the lists) rather than
raising — a never-traded user is a valid, empty book.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy.orm import Session

from backend.models import PaperAccount, PaperFill, PaperOrder, PaperPosition
from backend.paper.money import money_to_float, to_money
from backend.paper.quantity import qty_display
from backend.paper.snapshots import nav_series
from backend.paper.valuation import (
    position_day_pnl,
    position_market_value,
    position_unrealized_pnl,
)
from backend.routers.portfolio import resolve_sector, universe_by_symbols

PriceFn = Callable[[str], Optional[Decimal]]


def _live_mark_fn(price_fn: Optional[PriceFn]) -> PriceFn:
    """The live mark-on-read resolver — the SAME ``marks.get_mark_price`` the
    agent Positions view uses, so the portfolio and the agent panels can never
    disagree on a symbol's price. Tests inject an offline ``price_fn``.

    Market-hours gate (2026-07-10): when the NSE is CLOSED, per-symbol live
    network marks are BOTH pointless (prices are frozen) and pathologically
    slow — a 50-position book (many expired option legs) turned /paper/summary
    and /paper/holdings into ~20s hangs that never resolved the Home portfolio
    card. Closed → return None per symbol so each position values at its stored
    ``last_price`` (the scheduler's last intraday mark = the correct close),
    with zero network. During market hours the live resolver runs, backed by a
    short per-symbol cache in ``marks`` so a burst of concurrent portfolio
    reads marks each symbol once."""
    if price_fn is not None:
        return price_fn
    from backend.paper import marks
    try:
        from backend.utils.time_utils import is_market_open
        if not is_market_open():
            # NSE closed: Indian marks are frozen anyway (and a per-symbol
            # network storm is slow), so skip them. But US equities (evening
            # IST session) and crypto (24/7) DO keep trading — mark ONLY those,
            # detected cheaply (no DB), so their positions don't freeze at the
            # NSE close while their real markets move.
            from backend.view_markets.security_meta import is_us_or_crypto_fast

            def _closed_mark(sym: str):
                try:
                    if is_us_or_crypto_fast(sym):
                        return marks.get_mark_price(sym)
                except Exception:  # noqa: BLE001
                    pass
                return None

            return _closed_mark
    except Exception:  # noqa: BLE001 — never let the gate break a read
        pass
    return lambda sym: marks.get_mark_price(sym)


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


def account_summary(
    db: Session, user_id: int, price_fn: Optional[PriceFn] = None,
) -> dict:
    """READ-ONLY account roll-up, LIVE-MARKED on read.

    Returns ``{"exists": False}`` when the user has no paper account. Else a
    JSON-ready dict of floats plus counts/flags. Each open position is valued
    at its live mark (``_live_mark_fn``) so NAV / Total P&L / Day P&L reflect
    the current price — matching the holdings table and the agent Positions
    view — instead of a stale or absent stored mark. NAV includes reserved
    cash (still owned, held against a resting BUY) so the equity curve does not
    dip when an order rests: ``nav = cash_available + cash_reserved + positions_mv``.
    """
    account = _get_account(db, user_id)
    if account is None:
        return {"exists": False}

    pf = _live_mark_fn(price_fn)

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
        if pos.quantity != 0:
            num_positions += 1
            mark = pf(pos.symbol)  # live price, or None → book fallback
            positions_mv += position_market_value(pos, mark=mark)
            invested += to_money(pos.quantity * to_money(pos.avg_cost))
            unrealized_pnl += position_unrealized_pnl(pos, mark=mark)
            day_pnl += position_day_pnl(pos, mark=mark)
            # A position we just marked live is not stale for this read; only
            # one we couldn't price (mark is None) inherits the stored flag.
            if mark is None and pos.stale:
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


def holdings(
    db: Session, user_id: int, price_fn: Optional[PriceFn] = None,
) -> list[dict]:
    """One dict per OPEN position (quantity != 0), sorted by market value desc.

    Returns ``[]`` when the user has no account. Each row is LIVE-MARKED on
    read (``_live_mark_fn``): ``last_price``, ``market_value``,
    ``unrealized_pnl`` and ``day_pnl`` all derive from the SAME live price, so
    a row can never show a live LTP next to a ₹0 P&L. Falls back to the stored
    ``last_price`` (then book) when no live price is available. All money
    fields are floats. A short option leg (quantity < 0) is a genuine open
    position and is included here too.
    """
    account = _get_account(db, user_id)
    if account is None:
        return []

    pf = _live_mark_fn(price_fn)

    positions = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account.id,
            PaperPosition.quantity != 0,
        )
        .all()
    )

    # Rich sector per symbol (hand-map → screener universe → "Other"), built
    # once for the whole book so a name outside the tiny hand-map still shows
    # its real sector in paper mode (the FE reads this row's `sector`).
    umap = universe_by_symbols({p.symbol for p in positions})

    # Clean weighted-average BUY price per symbol (the price actually paid,
    # EXCLUDING charges), so the holdings table can show "the price it was
    # bought at" next to the LTP — instead of the charge-inclusive cost basis,
    # which made a fresh buy look like an instant loss equal to the charges.
    buy_agg: dict[str, list[float]] = {}
    for _sym, _px, _q in (
        db.query(PaperFill.symbol, PaperFill.fill_price, PaperFill.quantity)
        .filter(
            PaperFill.account_id == account.id,
            PaperFill.transaction_type == "BUY",
        )
        .all()
    ):
        try:
            agg = buy_agg.setdefault(str(_sym), [0.0, 0.0])
            agg[0] += float(_px) * float(_q)
            agg[1] += float(_q)
        except (TypeError, ValueError):
            continue

    def _buy_price(sym: str, fallback: float) -> float:
        agg = buy_agg.get(sym)
        return round(agg[0] / agg[1], 4) if agg and agg[1] else fallback

    rows = []
    for pos in positions:
        mark = pf(pos.symbol)  # live price, or None → stored/book fallback
        # The price actually shown as LTP + used for every P&L field in this
        # row: live mark, else the stored mark, else None (book-valued).
        display_price = (
            mark if mark is not None
            else (to_money(pos.last_price) if pos.last_price is not None else None)
        )
        mv = position_market_value(pos, mark=mark)
        invested = to_money(pos.quantity * to_money(pos.avg_cost))
        unrealized = position_unrealized_pnl(pos, mark=mark)
        unrealized_pct = (
            float(unrealized / invested * 100) if invested != 0 else 0.0
        )
        rows.append({
            "symbol": pos.symbol,
            "quantity": qty_display(pos.quantity),
            "avg_cost": money_to_float(pos.avg_cost),
            # The clean price paid (ex-charges) — what the UI shows as "Avg".
            "buy_price": _buy_price(
                str(pos.symbol), money_to_float(pos.avg_cost)
            ),
            "last_price": (
                float(display_price) if display_price is not None else None
            ),
            "market_value": money_to_float(mv),
            "unrealized_pnl": money_to_float(unrealized),
            "unrealized_pct": unrealized_pct,
            "day_pnl": money_to_float(position_day_pnl(pos, mark=mark)),
            "invested": money_to_float(invested),
            "realized_pnl": money_to_float(pos.realized_pnl),
            "sector": resolve_sector(pos.symbol, umap.get(pos.symbol)),
            # Live-marked rows aren't stale; only an unpriced one inherits it.
            "stale": bool(pos.stale) if mark is None else False,
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
        "quantity": qty_display(o.quantity),
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


def fills_journal(
    db: Session, user_id: int, limit: int = 50, offset: int = 0,
) -> list[dict]:
    """The fills log, newest first, one ``limit``-sized page starting at
    ``offset`` (lazy-loaded pagination on the Portfolio trade history)."""
    account = _get_account(db, user_id)
    if account is None:
        return []

    fills = (
        db.query(PaperFill)
        .filter(PaperFill.account_id == account.id)
        .order_by(PaperFill.filled_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return [{
        "id": f.id,
        "symbol": f.symbol,
        "side": f.transaction_type,
        "quantity": qty_display(f.quantity),
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
