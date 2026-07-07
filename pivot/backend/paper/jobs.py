"""Paper-trading scheduler orchestrators (P3).

Two sync entry points the scheduler's async jobs wrap (caller owns commit):

  - tick_paper_accounts: fill resting LIMIT/SL/GTT orders whose live price
    has crossed (runs on a market-hours interval).
  - snapshot_all_navs:    write each paper account's daily NAV row — the
    equity curve (runs once at EOD).

Both accept an injectable ``price_fn`` so tests stay offline.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from backend.models import PaperAccount, PaperOrder, PaperPosition
from backend.paper.evaluator import evaluate_resting_orders
from backend.paper.snapshots import snapshot_account_nav
from backend.paper.valuation import mark_positions
from backend.utils.time_utils import is_market_open, now_ist

logger = logging.getLogger(__name__)

PriceFn = Optional[Callable[[str], Any]]

# NOTE (mock/dev): with no real Kite session, marks resolve via yfinance
# LAST CLOSE (daily), not the intraday LTP — so resting fills + NAV mark
# against the prior close intraday. Prod should thread each account's Kite
# token into the price_fn for true intraday marks (a follow-up).


def _active_paper_accounts(db: Session) -> list[PaperAccount]:
    return (
        db.query(PaperAccount)
        .filter(PaperAccount.is_active.is_(True), PaperAccount.mode == "paper")
        .all()
    )


def tick_paper_accounts(db: Session, price_fn: PriceFn = None) -> dict:
    """Evaluate + fill resting orders for every paper account that has any.
    Returns a summary. Marking-to-market is lazy-on-read (compute_account_nav
    marks), so this stays light — it only touches accounts with resting work.

    Market-hours gate: when ``paper_respect_market_hours`` is on, resting
    orders (incl. queued MARKET orders) are only filled while the NSE is open
    (09:15-15:30 IST, mon-fri) so nothing fills at a stale closed-market price.
    The scheduler cron already restricts to hours 9-15; this tightens it to the
    real session and skips the pre-open/post-close edge ticks."""
    from backend.config import settings as _cfg
    if getattr(_cfg, "paper_respect_market_hours", True) and not is_market_open():
        return {
            "accounts": 0, "filled": [], "cancelled": [], "failed": [],
            "skipped_market_closed": True,
        }
    acct_ids = [
        row[0]
        for row in db.query(PaperOrder.account_id)
        .filter(PaperOrder.status == "resting")
        .distinct()
        .all()
    ]
    summary: dict[str, Any] = {
        "accounts": 0, "filled": [], "cancelled": [], "failed": [],
    }
    for aid in acct_ids:
        acct = db.get(PaperAccount, aid)
        if acct is None or not acct.is_active or str(acct.mode) != "paper":
            continue
        # Resolve THIS account owner's live Kite token so resting/queued fills
        # mark against live LTP (during market hours) once they've logged into
        # Kite; falls back to yfinance close otherwise. A caller-supplied
        # price_fn (tests) always wins.
        acct_price_fn = price_fn
        if acct_price_fn is None:
            from backend.paper.marks import get_mark_price, user_kite_token
            _tok = user_kite_token(db, int(acct.user_id))
            acct_price_fn = lambda sym, _t=_tok: get_mark_price(sym, token=_t)
        # Per-account SAVEPOINT: one bad account rolls back only its own
        # work and the pass continues, so a single failure can't abort the
        # whole tick (or the scheduler's end-of-pass commit).
        try:
            with db.begin_nested():
                res = evaluate_resting_orders(db, acct, acct_price_fn)
        except Exception:
            summary["failed"].append(aid)
            logger.warning("paper tick failed for account %s", aid, exc_info=True)
            continue
        summary["accounts"] += 1
        summary["filled"].extend(res.get("filled", []))
        summary["cancelled"].extend(res.get("cancelled", []))
    return summary


def mark_open_positions(db: Session, price_fn: PriceFn = None) -> dict:
    """Refresh ``last_price`` (and thus unrealized/day P&L) for every OPEN
    position across every active paper account, on a market-hours interval.

    BUG this fixes (found 2026-07-06 live-testing the beta): a fresh fill
    seeds its own mark (see ``fills.execute_market_fill``), but nothing
    subsequently refreshed it — ``mark_positions`` was only ever invoked
    from the once-daily 15:37 IST NAV snapshot job, so a position's P&L and
    LTP were frozen at their fill-time value for the entire trading day.
    Runs on the same 5-min market-hours cadence as ``paper_tick_resting``
    (see backend/scheduler.py); market-hours gated the same way. Returns
    ``{"accounts": int, "positions_marked": int, "failed": [account_id]}``.
    """
    from backend.config import settings as _cfg
    if getattr(_cfg, "paper_respect_market_hours", True) and not is_market_open():
        return {"accounts": 0, "positions_marked": 0, "failed": [],
                "skipped_market_closed": True}
    acct_ids = [
        row[0]
        for row in db.query(PaperPosition.account_id)
        .filter(PaperPosition.quantity != 0)
        .distinct()
        .all()
    ]
    summary: dict[str, Any] = {"accounts": 0, "positions_marked": 0, "failed": []}
    for aid in acct_ids:
        acct = db.get(PaperAccount, aid)
        if acct is None or not acct.is_active or str(acct.mode) != "paper":
            continue
        acct_price_fn = price_fn
        if acct_price_fn is None:
            from backend.paper.marks import get_mark_price, user_kite_token
            _tok = user_kite_token(db, int(acct.user_id))
            acct_price_fn = lambda sym, _t=_tok: get_mark_price(sym, token=_t)
        try:
            with db.begin_nested():
                n = mark_positions(db, acct.id, acct_price_fn)
        except Exception:
            summary["failed"].append(aid)
            logger.warning("position marking failed for account %s", aid, exc_info=True)
            continue
        summary["accounts"] += 1
        summary["positions_marked"] += n
    return summary


def snapshot_all_navs(
    db: Session,
    as_of_date: Optional[dt.date] = None,
    price_fn: PriceFn = None,
    nifty_close: Optional[float] = None,
) -> int:
    """Write/refresh the daily NAV snapshot for every active paper account.
    Returns the number of accounts snapshotted."""
    if as_of_date is None:
        as_of_date = now_ist().date()
    n = 0
    for acct in _active_paper_accounts(db):
        try:
            with db.begin_nested():
                snapshot_account_nav(db, acct, as_of_date, price_fn, nifty_close)
        except Exception:
            logger.warning(
                "paper NAV snapshot failed for account %s", acct.id, exc_info=True,
            )
            continue
        n += 1
    return n
