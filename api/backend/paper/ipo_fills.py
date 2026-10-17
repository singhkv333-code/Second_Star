"""IPO listing-day credit into the paper book (P3.1).

When an allotted simulated IPO allocation's ``listing_date`` arrives, this
module credits the allotted shares into the user's paper book as a
synchronous MARKET BUY at the ISSUE price (cost basis). The existing
mark-to-market loop on ``PaperPosition`` then values the credited position
at the live (post-listing) price -> the forward-test listing P&L shows up
in paper NAV + Holdings automatically.

Hard rules (these are load-bearing — read them before changing anything):

* Paper money ONLY. Never touches a real broker, ASBA, UPI mandate or
  the live order router. The only mutation surface is the paper-domain
  tables (PaperAccount, PaperOrder, PaperFill, PaperPosition,
  PaperLedgerEntry, PaperIpoAllocation).
* REUSE the canonical fill path. ``execute_market_fill`` is the single
  tested buy-accounting path; we hand it a freshly persisted PaperOrder
  with ``transaction_type='BUY'`` and ``mark_price = allocation.issue_price``.
  All cash / position / ledger math is delegated. Do NOT hand-roll the
  arithmetic here — every line of accounting code outside execute_market_fill
  is a chance to drift.
* Idempotent in two layers:
    (1) ``allocation.book_credited`` guards re-entry: the function is a
        no-op on a row that is already True (terminally credited OR
        terminally skipped). This is the cheap first check.
    (2) The PaperOrder we build carries a STABLE
        ``client_request_id = f"ipo-listing-{allocation.id}"``. That
        column is UNIQUE on ``paper_orders``, so even if (1) is bypassed
        the broker's idempotency layer replays the prior order rather
        than minting a new fill. Belt + braces; either alone would
        suffice, both together make double-credit impossible.

Cost-basis policy
-----------------
The allotted shares enter at the ISSUE price (mark_price = issue_price).
This is what an IPO subscriber's cost basis WOULD be in reality on the
day of allotment. ``execute_market_fill`` then applies the standard paper
buy_cost (slippage + brokerage + taxes via services.trading_costs), so
the credited ``avg_cost`` is marginally above the headline issue price.
For real-world IPO allotments brokerage is typically zero — modelling the
allotment as a paper MARKET BUY with full friction is a documented
simplification (a few rupees per lot of cost basis drift); we keep it so
the credited row reconciles by replay through the same fills/ledger code
path every other paper book entry uses. A future refinement would route
through a zero-friction sibling of execute_market_fill that still books
the position; that is intentionally out of scope for P3.1.

Settlement
----------
``execute_market_fill`` debits BOTH cash_available and cash_settled by
the net debit (the P1 simplified settlement model). This means an IPO
listing credit consumes paper buying power at credit time, which mirrors
the real-world experience that the user "paid" for the IPO at listing.

NotJ-yet-listed price
---------------------
After the fill succeeds we snapshot ``listing_price`` via
``marks.get_mark_price`` (the same resolver every other paper component
uses: live Kite quote -> yfinance last close -> None). When the resolver
returns None (the just-listed scrip has no quote yet on a still-quiet
exchange feed) we honestly store ``listing_price = None`` and leave
``simulated_pnl = None``; the live mark-to-market loop will then catch
up on the next refresh and ``simulated_pnl`` simply records the
credit-time snapshot, NOT a continuously-updated number.

Caller contract
---------------
* ``credit_listed_allotment(db, allocation)`` mutates the session and
  FLUSHES. The CALLER owns commit (matches every other paper helper).
* Pass a freshly loaded ``PaperIpoAllocation`` (the listing-credit
  poller does this in a per-row try/commit loop).
* Returns the PaperFill on success, or ``None`` on a skipped credit
  (the allocation row's ``book_credited``/``book_note`` will record
  the terminal state).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from backend.models import (
    PaperAccount,
    PaperFill,
    PaperIpoAllocation,
    PaperOrder,
)
from backend.paper.accounts import get_or_create_account
from backend.paper.fills import execute_market_fill
from backend.paper.marks import get_mark_price
from backend.paper.money import to_money

logger = logging.getLogger(__name__)


def _listing_client_request_id(allocation: PaperIpoAllocation) -> str:
    """Stable per-allocation idempotency key.

    The paper_orders.client_request_id column is UNIQUE, so this string
    must never collide for two different listing credits and must be
    EXACTLY the same on every retry of the SAME allocation. Allocation
    id is a uuid String(36), so the namespaced key is well under the
    column's 120-char ceiling.
    """
    return f"ipo-listing-{allocation.id}"


def credit_listed_allotment(
    db: Session, allocation: PaperIpoAllocation,
) -> Optional[PaperFill]:
    """Credit an allotted IPO allocation into the paper book at issue price.

    Idempotent. Returns the PaperFill on a successful credit, or ``None``
    on a skipped credit (the allocation row's terminal state is recorded
    via ``book_credited=True`` + ``book_note=...``). The caller owns
    commit.

    Skip conditions (no fill written, returns None):
      * ``allotment_status != 'allotted'`` — not_allotted or pending rows
        are never credited.
      * ``quantity_allotted <= 0`` — defensive (an "allotted" row with
        zero qty is malformed but we don't crash).
      * ``book_credited is True`` — already terminally handled.
      * ``execute_market_fill`` rejects (e.g. insufficient buying power):
        we flip ``book_credited=True`` (TERMINAL — we will not retry the
        same allocation forever) and record the reject reason in
        ``book_note``.
    """
    # ── Guards: only resolve once, and only for allotted rows ────────────
    if allocation.book_credited:
        return None
    if allocation.allotment_status != "allotted":
        return None
    qty_allotted = int(allocation.quantity_allotted or 0)
    if qty_allotted <= 0:
        return None

    # ── Resolve the paper account ────────────────────────────────────────
    # The allocation row carries a hard FK to paper_accounts.id, so the
    # account row must exist. Fall back to get_or_create defensively so a
    # rare cleanup-orphaned row doesn't crash the poller.
    account: Optional[PaperAccount] = db.get(
        PaperAccount, str(allocation.paper_account_id),
    )
    if account is None:
        account = get_or_create_account(db, int(allocation.user_id))

    # ── Build the BUY order ──────────────────────────────────────────────
    # NOTE: we set status='pending' here; execute_market_fill flips it to
    # 'filled' (or 'rejected') in place. The stable client_request_id is
    # the SECOND idempotency layer (UNIQUE column on paper_orders).
    order = PaperOrder(
        account_id=str(account.id),
        user_id=int(allocation.user_id),
        client_request_id=_listing_client_request_id(allocation),
        symbol=str(allocation.ipo_symbol).upper(),
        exchange="NSE",
        transaction_type="BUY",
        order_type="MARKET",
        product="CNC",
        variety="regular",
        quantity=qty_allotted,
        status="pending",
        source="ipo_listing",
        idea_id=None,
    )
    db.add(order)
    db.flush()

    # ── Drive the canonical fill path at the ISSUE price ────────────────
    # mark_price = issue_price is THE cost-basis decision: the allotted
    # shares enter at the IPO subscription price, not the (potentially
    # very different) live LTP on day one. execute_market_fill handles
    # the rest: position upsert, avg-cost compound, cash debit, ledger
    # entry, immutable PaperFill row.
    fill = execute_market_fill(
        db, order, to_money(allocation.issue_price),
    )

    if fill is None:
        # Reject (insufficient buying power / price unavailable / bad
        # side). TERMINAL: we do not want to retry the same allocation
        # forever — flip book_credited so the poller skips it next tick
        # and record the reject reason for the UI to surface.
        allocation.book_credited = True  # type: ignore[assignment]
        allocation.book_note = (  # type: ignore[assignment]
            f"not credited: {order.reject_reason or 'unknown'}"
        )
        db.flush()
        logger.info(
            "ipo-listing-credit: SKIP user=%s symbol=%s alloc=%s qty=%s "
            "reason=%s",
            allocation.user_id, allocation.ipo_symbol, allocation.id,
            qty_allotted, order.reject_reason,
        )
        return None

    # ── Successful credit: stamp the listing snapshot ───────────────────
    allocation.book_credited = True  # type: ignore[assignment]
    allocation.paper_fill_id = str(fill.id)  # type: ignore[assignment]

    # listing_price is a SNAPSHOT at credit time; the live mark-to-market
    # on the resulting PaperPosition takes over from here. get_mark_price
    # is honest about a stale/missing quote (returns None) — we do NOT
    # fabricate a number.
    lp = get_mark_price(str(allocation.ipo_symbol).upper())
    if lp is not None:
        lp_money = to_money(lp)
        allocation.listing_price = lp_money  # type: ignore[assignment]
        allocation.simulated_pnl = (  # type: ignore[assignment]
            (lp_money - to_money(allocation.issue_price))
            * qty_allotted
        )
    else:
        allocation.listing_price = None  # type: ignore[assignment]
        allocation.simulated_pnl = None  # type: ignore[assignment]
    db.flush()

    logger.info(
        "ipo-listing-credit: OK user=%s symbol=%s alloc=%s qty=%s "
        "issue_price=%s fill_id=%s listing_price=%s",
        allocation.user_id, allocation.ipo_symbol, allocation.id,
        qty_allotted, allocation.issue_price, fill.id,
        allocation.listing_price,
    )
    return fill
