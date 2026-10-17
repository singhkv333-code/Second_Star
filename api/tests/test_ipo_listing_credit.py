"""P3.1 tests — IPO listing-day credit into the paper book.

These tests guard the load-bearing P3.1 invariants:

  (a) credit_listed_allotment writes a BUY PaperFill at the issue
      price, upserts a PaperPosition with quantity == quantity_allotted
      and an avg_cost ~= issue_price (+ buy-side charges), debits
      cash_available by the net debit, and stamps book_credited +
      paper_fill_id.
  (b) IDEMPOTENT: calling the function twice on the same allocation
      yields exactly ONE PaperPosition / ONE PaperFill and does NOT
      double-debit cash. (The book_credited guard short-circuits the
      second call BEFORE the broker layer even runs; the stable
      client_request_id is the second-layer safeguard.)
  (c) ``allotment_status in {'not_allotted', 'pending'}`` allocations are
      NEVER credited.
  (d) Insufficient buying power: execute_market_fill rejects ->
      book_credited TRUE (terminal — no infinite retry) + book_note set
      + NO PaperPosition / NO PaperFill.
  (e) The resolution job (_poll_ipo_listing_fills) credits allocations
      with listing_date <= today and SKIPS rows whose listing_date is
      in the future or NULL.
  (f) After credit, mark_positions / compute_account_nav reflect the
      credited PaperPosition at a stubbed live price (listing gain
      tracks automatically once the position is in the book).

Test setup mirrors test_paper_ipo_sim.py: the shared in-memory SQLite
test DB + the auth_headers / db / client conftest fixtures. We bypass
the chat path and drive the simulator + credit function directly so the
seed inputs are explicitly controlled.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from backend.models import (
    PaperAccount,
    PaperFill,
    PaperIpoAllocation,
    PaperPosition,
    User,
)
from backend.paper.accounts import get_or_create_account
from backend.paper.ipo_fills import credit_listed_allotment
from backend.paper.ipo_sim import (
    serialize_paper_ipo_allocation,
    simulate_paper_ipo_allocation,
)
from backend.services.ipo_application_service import persist_ipo_application


# ── Constants for a deterministic seed allocation ────────────────────────

_TIKONA_IPO = {
    "name": "Tikona Infinet",
    "symbol": "TIKONA",
    "price_band": "125-132",
    "open_date": "2026-06-03",
    "close_date": "2026-06-05",
    "lot_size": 110,
    "issue_size": "Rs 1,200 cr",
    "type": "mainboard",
    "status": "open",
    # subscription < 1.0 so prob clamps -> default-mainboard fallback
    # (0.30). The deterministic seed for this user_id + symbol + app_id
    # combination may still land in the loss bucket — we override the
    # allocation_status in the helper below so tests are stable
    # regardless of the lottery.
}


def _seed_allotted_allocation(
    db: Any,
    *,
    email: str = "p31@example.com",
    symbol: str = "TIKONA",
    lots: int = 1,
    lot_size: int = 110,
    issue_price: Decimal = Decimal("132.0000"),
    listing_offset_days: int = 0,
    allotment_status: str = "allotted",
) -> PaperIpoAllocation:
    """Create a User + PaperAccount + IPOApplication + PaperIpoAllocation
    in one shot, with the allotment lottery FORCED so tests are stable.

    Returns the freshly-flushed PaperIpoAllocation row. The caller can
    then call ``credit_listed_allotment(db, alloc)`` directly.
    """
    user = User(email=email, hashed_password="x")
    db.add(user)
    db.flush()
    get_or_create_account(db, user.id)

    app_row = persist_ipo_application(
        db, user.id,
        ipo_symbol=symbol,
        ipo_name="Tikona Infinet",
        ipo_type="mainboard",
        category="retail",
        quantity_lots=lots,
        lot_size=lot_size,
        bid_price_mode="cutoff",
        bid_price=None,
        amount_estimate=float(issue_price) * lots * lot_size,
        upi_id_masked=None,
        conversation_id=None,
        source="test",
        paper_mode=True,
    )
    db.flush()
    db.refresh(app_row)

    # Drive the simulator so the row has the same shape it would on the
    # production path. We then OVERWRITE the lottery outcome so the test
    # exercises a deterministic post-condition.
    ipo_record = {**_TIKONA_IPO, "lot_size": lot_size}
    alloc = simulate_paper_ipo_allocation(
        db, user.id,
        app_row=app_row,
        ipo_record=ipo_record,
        source="test-seed",
    )
    db.flush()

    # Force the lottery outcome to a known state. The deterministic seed
    # may otherwise land on 'not_allotted' for some user_ids and break
    # the (a) / (b) / (d) / (f) cases — we want each test to control
    # the input clearly.
    alloc.allotment_status = allotment_status  # type: ignore[assignment]
    if allotment_status == "allotted":
        alloc.quantity_allotted = lots * lot_size  # type: ignore[assignment]
    else:
        alloc.quantity_allotted = 0  # type: ignore[assignment]
    # listing_date pinned exactly to today + offset.
    alloc.listing_date = date.today() + timedelta(  # type: ignore[assignment]
        days=listing_offset_days,
    )
    # issue_price pinned (the simulator already wrote this but we make
    # the test's expectation explicit).
    alloc.issue_price = issue_price  # type: ignore[assignment]
    db.flush()
    return alloc


# ── (a) credit_listed_allotment writes one position + one fill ───────────

def test_credit_listed_allotment_writes_position_and_fill(db) -> None:
    alloc = _seed_allotted_allocation(db, email="p31a@example.com")
    user_id = alloc.user_id

    fill = credit_listed_allotment(db, alloc)

    assert fill is not None, "credit must succeed"
    # One PaperPosition for this user at qty_allotted.
    positions = (
        db.query(PaperPosition)
        .filter(PaperPosition.user_id == user_id)
        .all()
    )
    assert len(positions) == 1
    pos = positions[0]
    assert pos.symbol == "TIKONA"
    assert pos.quantity == alloc.quantity_allotted == 110
    # avg_cost compounds with buy-side charges -> slightly above the
    # headline issue price. Bound by issue_price + a small friction
    # band; well under 1% of the IPO price for any realistic config.
    assert Decimal(str(pos.avg_cost)) >= alloc.issue_price
    assert Decimal(str(pos.avg_cost)) <= alloc.issue_price * Decimal("1.01")

    # One PaperFill linked to the order.
    fills = (
        db.query(PaperFill)
        .filter(PaperFill.user_id == user_id)
        .all()
    )
    assert len(fills) == 1
    f = fills[0]
    assert f.transaction_type == "BUY"
    assert f.quantity == 110
    assert Decimal(str(f.fill_price)) == alloc.issue_price

    # Cash was debited by net_debit (= gross + charges).
    acct = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id)
        .one()
    )
    # cash_available < starting_capital by ~ qty * issue_price (+ a bit).
    debit = Decimal(str(acct.starting_capital)) - Decimal(
        str(acct.cash_available),
    )
    expected_gross = Decimal(alloc.quantity_allotted) * alloc.issue_price
    assert debit >= expected_gross
    # within 1% friction band
    assert debit <= expected_gross * Decimal("1.01")

    # Allocation row stamped.
    db.refresh(alloc)
    assert alloc.book_credited is True
    assert alloc.paper_fill_id == fill.id


# ── (b) Idempotent: second call is a no-op ───────────────────────────────

def test_credit_listed_allotment_is_idempotent(db) -> None:
    alloc = _seed_allotted_allocation(db, email="p31b@example.com")
    user_id = alloc.user_id

    fill1 = credit_listed_allotment(db, alloc)
    assert fill1 is not None
    cash_after_first = Decimal(
        str(
            db.query(PaperAccount)
            .filter(PaperAccount.user_id == user_id)
            .one()
            .cash_available
        )
    )

    # Second call must short-circuit on book_credited.
    fill2 = credit_listed_allotment(db, alloc)
    assert fill2 is None, "second credit must be a no-op"

    # Exactly one PaperPosition / one PaperFill.
    positions = (
        db.query(PaperPosition)
        .filter(PaperPosition.user_id == user_id)
        .all()
    )
    assert len(positions) == 1
    assert positions[0].quantity == 110  # NOT 220

    fills = (
        db.query(PaperFill)
        .filter(PaperFill.user_id == user_id)
        .all()
    )
    assert len(fills) == 1, "second credit must NOT mint a second fill"

    # Cash unchanged after the second call.
    cash_after_second = Decimal(
        str(
            db.query(PaperAccount)
            .filter(PaperAccount.user_id == user_id)
            .one()
            .cash_available
        )
    )
    assert cash_after_second == cash_after_first


# ── (c) not_allotted / pending allocations are never credited ────────────

def test_not_allotted_allocation_is_never_credited(db) -> None:
    alloc = _seed_allotted_allocation(
        db, email="p31c1@example.com",
        allotment_status="not_allotted",
    )
    fill = credit_listed_allotment(db, alloc)
    assert fill is None
    assert alloc.book_credited is False  # untouched
    assert db.query(PaperPosition).count() == 0
    assert db.query(PaperFill).count() == 0


def test_pending_allocation_is_never_credited(db) -> None:
    alloc = _seed_allotted_allocation(
        db, email="p31c2@example.com",
        allotment_status="pending",
    )
    fill = credit_listed_allotment(db, alloc)
    assert fill is None
    assert alloc.book_credited is False
    assert db.query(PaperPosition).count() == 0
    assert db.query(PaperFill).count() == 0


# ── (d) Insufficient buying power -> terminal skip + book_note ───────────

def test_insufficient_buying_power_is_terminal_skip(db) -> None:
    """When the BUY would over-debit the paper account, execute_market_fill
    rejects. We TERMINALLY flip book_credited so the poller does not
    retry forever, record the reject reason in book_note, and leave no
    PaperPosition / no PaperFill."""
    # Allotment cost = 5000 * 132 = 660,000 — well over the default
    # SEED_CAPITAL of 150,000.
    alloc = _seed_allotted_allocation(
        db, email="p31d@example.com", lots=5000, lot_size=1,
        issue_price=Decimal("132.0000"),
    )
    fill = credit_listed_allotment(db, alloc)
    assert fill is None

    db.refresh(alloc)
    assert alloc.book_credited is True, "must be TERMINAL — no retry"
    assert alloc.book_note is not None
    assert "insufficient_buying_power" in alloc.book_note
    # No paper_fill_id when the credit was skipped.
    assert alloc.paper_fill_id is None

    # No position / no fill.
    assert db.query(PaperPosition).filter(
        PaperPosition.user_id == alloc.user_id,
    ).count() == 0
    assert db.query(PaperFill).filter(
        PaperFill.user_id == alloc.user_id,
    ).count() == 0


# ── (e) Resolution job credits only listing_date<=today, allotted rows ───

def _wrap_session_factory(db: Any) -> Any:
    """Patch shim: the production poller opens its own SessionLocal()
    against the real engine, but the conftest test session lives on a
    StaticPool in-memory engine that the production binding can't see.
    Returning a wrapper around the test session lets the scheduler's
    SessionLocal-using helpers read the same in-memory rows the test
    writes. ``close`` is a no-op so the test fixture's session lifecycle
    is unaffected (the test still owns rollback).
    """
    class _Wrapper:
        def __init__(self, s: Any) -> None:
            self._s = s

        def close(self) -> None:
            pass

        def __getattr__(self, n: str) -> Any:
            return getattr(self._s, n)

    return _Wrapper(db)


def test_listing_credit_poll_only_credits_due_allotted(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through the scheduler's per-row credit path.

    We seed three allocations:
      * allotted + listing_date == today        -> credited
      * allotted + listing_date == tomorrow     -> NOT credited (future)
      * allotted + listing_date is None         -> NOT credited (missing)

    We exercise ``_scan_due_listing_allocations`` and the per-row
    credit helper directly (the asyncio dispatcher is a trivial wrapper
    that hands ids to ``_credit_one_allocation``; covering the scan +
    per-row credit is the load-bearing layer).
    """
    from backend.workflows import scheduler as scheduler_mod

    # Re-bind SessionLocal so the poller's per-row session reaches the
    # in-memory test DB (the test fixture's session + the production
    # SessionLocal otherwise see different stores; see news_events
    # retraction tests for the same pattern).
    monkeypatch.setattr(
        scheduler_mod, "SessionLocal", lambda: _wrap_session_factory(db),
    )

    # Three users so each one's PaperAccount can independently take the
    # debit on its allocation.
    today_alloc = _seed_allotted_allocation(
        db, email="p31e1@example.com", listing_offset_days=0,
    )
    future_alloc = _seed_allotted_allocation(
        db, email="p31e2@example.com", listing_offset_days=7,
    )
    null_alloc = _seed_allotted_allocation(
        db, email="p31e3@example.com", listing_offset_days=0,
    )
    null_alloc.listing_date = None  # type: ignore[assignment]
    db.flush()

    from backend.workflows.scheduler import (
        _credit_one_allocation,
        _scan_due_listing_allocations,
    )

    # The scan is the date gate: it returns ONLY allocations whose
    # listing_date <= today (and not future / null). The credit
    # function itself does not re-check listing_date — the contract is
    # "scan filters, credit acts". Asserting this scan filter is the
    # load-bearing invariant for (e).
    due_ids = _scan_due_listing_allocations()
    assert today_alloc.id in due_ids
    assert future_alloc.id not in due_ids, (
        "future listing_date must NOT appear in the due-set"
    )
    assert null_alloc.id not in due_ids, (
        "null listing_date must NOT appear in the due-set"
    )

    # Drive the per-row helper for every id the scan returned (mirrors
    # _poll_ipo_listing_fills's actual dispatch loop).
    for alloc_id in due_ids:
        _credit_one_allocation(alloc_id)

    db.expire_all()
    a_today = db.get(PaperIpoAllocation, today_alloc.id)
    a_future = db.get(PaperIpoAllocation, future_alloc.id)
    a_null = db.get(PaperIpoAllocation, null_alloc.id)
    assert a_today is not None and a_today.book_credited is True
    assert a_future is not None and a_future.book_credited is False
    assert a_null is not None and a_null.book_credited is False

    # Exactly one PaperPosition (today_alloc's) — the gating happened
    # at the scan, not the credit, so future/null never reach the
    # broker.
    positions = db.query(PaperPosition).all()
    assert len(positions) == 1
    assert positions[0].user_id == today_alloc.user_id


# ── (f) NAV / mark_positions reflects the credited position ─────────────

def test_credited_position_marks_to_live_price(db) -> None:
    """After credit, mark_positions + compute_account_nav reflect the
    live price -> the listing gain is observable on the Paper dashboard."""
    from backend.paper.valuation import compute_account_nav, mark_positions

    alloc = _seed_allotted_allocation(db, email="p31f@example.com")
    fill = credit_listed_allotment(db, alloc)
    assert fill is not None

    # Stub the price function to a +20% listing gain.
    gain_price = alloc.issue_price * Decimal("1.20")

    def _price_fn(_sym: str) -> Decimal:
        return Decimal(gain_price)

    acct = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == alloc.user_id)
        .one()
    )
    refreshed = mark_positions(db, acct.id, price_fn=_price_fn)
    assert refreshed == 1

    # compute_account_nav returns a dict of Decimal money values plus a
    # bool is_stale (see backend/paper/valuation.py).
    nav = compute_account_nav(db, acct, price_fn=_price_fn)
    assert nav["unrealized_pnl"] > Decimal("0"), (
        f"position at +20% must show positive unrealized P&L, got {nav}"
    )


# ── (g) Serializer surfaces the P3.1 fields ─────────────────────────────

def test_serializer_includes_p31_fields(db) -> None:
    alloc = _seed_allotted_allocation(db, email="p31g@example.com")
    fill = credit_listed_allotment(db, alloc)
    assert fill is not None

    payload = serialize_paper_ipo_allocation(alloc)
    # Required P3.1 keys are PRESENT (even on a credited row, listing
    # price may legitimately be None if marks resolver returned None —
    # so we assert the keys exist, not their non-None-ness).
    assert payload["book_credited"] is True
    assert payload["paper_fill_id"] == fill.id
    assert "listing_price" in payload
    assert "simulated_pnl" in payload
    assert "book_note" in payload
