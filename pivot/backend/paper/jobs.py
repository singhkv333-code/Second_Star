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

from backend.models import PaperAccount, PaperOrder
from backend.paper.evaluator import evaluate_resting_orders
from backend.paper.snapshots import snapshot_account_nav
from backend.utils.time_utils import now_ist

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
    marks), so this stays light — it only touches accounts with resting work."""
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
        # Per-account SAVEPOINT: one bad account rolls back only its own
        # work and the pass continues, so a single failure can't abort the
        # whole tick (or the scheduler's end-of-pass commit).
        try:
            with db.begin_nested():
                res = evaluate_resting_orders(db, acct, price_fn)
        except Exception:
            summary["failed"].append(aid)
            logger.warning("paper tick failed for account %s", aid, exc_info=True)
            continue
        summary["accounts"] += 1
        summary["filled"].extend(res.get("filled", []))
        summary["cancelled"].extend(res.get("cancelled", []))
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
