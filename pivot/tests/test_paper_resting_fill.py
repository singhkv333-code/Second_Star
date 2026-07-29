"""P3 tests for the resting-order fill/cancel path (backend.paper.fills).

A resting LIMIT BUY reserves cash on placement; fill_resting_order releases
that reserve and fills at the limit (reusing execute_market_fill), while
cancel_resting_order releases the reserve and marks the order cancelled with
no fill. Both paths must keep the ledger reconciliation invariant
(SUM(amount) == cash_available) intact.

Self-contained in-memory SQLite (PRAGMA foreign_keys=ON), deterministic price
via an injected price_fn so nothing touches the network.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend.models import (
    PaperAccount,
    PaperFill,
    PaperLedgerEntry,
    PaperOrder,
    PaperPosition,
    User,
)
from backend.paper.broker import PaperBroker
from backend.paper.fills import cancel_resting_order, fill_resting_order
from backend.paper.money import to_money
from backend.services.trading_costs import buy_cost


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _user(db: Session) -> User:
    u = User(email=f"x{id(db)}@e.com", hashed_password="x")
    db.add(u)
    db.flush()
    return u


def _ledger_sum(db: Session, account_id: str) -> Decimal:
    rows = (
        db.query(PaperLedgerEntry)
        .filter(PaperLedgerEntry.account_id == account_id)
        .all()
    )
    return sum((to_money(r.amount) for r in rows), to_money(0))


def _place_resting_limit_buy(
    db: Session, user: User, *, symbol: str, qty: int, limit: float, mark: float,
) -> PaperOrder:
    """Place a LIMIT BUY with limit below mark so it rests + reserves cash."""
    broker = PaperBroker(db, user.id, price_fn=lambda _s: to_money(mark))
    res = broker.place_order(
        tradingsymbol=symbol,
        transaction_type="BUY",
        quantity=qty,
        order_type="LIMIT",
        price=limit,
    )
    assert res["paper_status"] == "resting"
    return db.get(PaperOrder, res["order_id"])


# ── fill_resting_order ──────────────────────────────────────────────────

def test_fill_resting_order_releases_reserve_and_fills(session: Session) -> None:
    user = _user(session)
    sym, qty, limit, mark = "TCS", 10, 90.0, 100.0
    order = _place_resting_limit_buy(
        session, user, symbol=sym, qty=qty, limit=limit, mark=mark,
    )
    account = session.get(PaperAccount, order.account_id)

    # Cash was reserved on placement.
    reserve = to_money(buy_cost(limit, qty)[0])
    assert to_money(account.cash_reserved) == reserve
    assert to_money(account.cash_reserved) > 0
    assert to_money(order.reserved_cash) == reserve
    # Reconciliation holds while resting.
    assert _ledger_sum(session, account.id) == to_money(account.cash_available)

    fill = fill_resting_order(session, order, to_money(limit))
    session.flush()

    # Order filled, a PaperFill exists.
    assert fill is not None
    assert order.status == "filled"
    assert order.filled_quantity == qty
    fills = (
        session.query(PaperFill)
        .filter(PaperFill.order_id == order.id)
        .all()
    )
    assert len(fills) == 1
    assert fills[0].id == fill.id
    assert fills[0].quantity == qty

    # Reserve fully released back to zero.
    session.refresh(account)
    assert to_money(account.cash_reserved) == to_money(0)
    assert to_money(order.reserved_cash) == to_money(0)

    # A 'release' ledger row exists for the released reserve.
    releases = (
        session.query(PaperLedgerEntry)
        .filter(
            PaperLedgerEntry.account_id == account.id,
            PaperLedgerEntry.kind == "release",
        )
        .all()
    )
    assert len(releases) == 1
    assert to_money(releases[0].amount) == reserve

    # Position quantity is correct.
    pos = (
        session.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account.id,
            PaperPosition.symbol == sym,
        )
        .first()
    )
    assert pos is not None
    assert pos.quantity == qty

    # Reconciliation invariant holds after the fill.
    assert _ledger_sum(session, account.id) == to_money(account.cash_available)


def test_fill_resting_order_no_reserve_still_fills(session: Session) -> None:
    """A resting order with no reserved cash (e.g. SL-M-style) still fills
    and writes no spurious 'release' row."""
    user = _user(session)
    sym, qty, mark = "INFY", 5, 120.0
    # Build a resting order row directly with zero reserve.
    broker = PaperBroker(session, user.id, price_fn=lambda _s: to_money(mark))
    res = broker.place_order(
        tradingsymbol=sym,
        transaction_type="BUY",
        quantity=qty,
        order_type="SL-M",
        trigger_price=mark + 5,
    )
    order = session.get(PaperOrder, res["order_id"])
    assert order.status == "resting"
    assert to_money(order.reserved_cash or 0) == to_money(0)

    fill = fill_resting_order(session, order, to_money(mark))
    session.flush()

    assert fill is not None
    assert order.status == "filled"
    releases = (
        session.query(PaperLedgerEntry)
        .filter(
            PaperLedgerEntry.account_id == order.account_id,
            PaperLedgerEntry.kind == "release",
        )
        .all()
    )
    assert len(releases) == 0
    account = session.get(PaperAccount, order.account_id)
    assert _ledger_sum(session, account.id) == to_money(account.cash_available)


# ── cancel_resting_order ────────────────────────────────────────────────

def test_cancel_resting_order_releases_reserve_no_fill(session: Session) -> None:
    user = _user(session)
    sym, qty, limit, mark = "WIPRO", 8, 80.0, 100.0
    order = _place_resting_limit_buy(
        session, user, symbol=sym, qty=qty, limit=limit, mark=mark,
    )
    account = session.get(PaperAccount, order.account_id)
    reserve = to_money(buy_cost(limit, qty)[0])
    cash_before = to_money(account.cash_available)
    assert to_money(account.cash_reserved) == reserve

    result = cancel_resting_order(session, order)
    session.flush()

    assert result is None
    assert order.status == "cancelled"

    # Reserve released.
    session.refresh(account)
    assert to_money(account.cash_reserved) == to_money(0)
    assert to_money(order.reserved_cash) == to_money(0)
    assert to_money(account.cash_available) == cash_before + reserve

    # A 'release' ledger row exists.
    releases = (
        session.query(PaperLedgerEntry)
        .filter(
            PaperLedgerEntry.account_id == account.id,
            PaperLedgerEntry.kind == "release",
        )
        .all()
    )
    assert len(releases) == 1
    assert to_money(releases[0].amount) == reserve

    # No fill written.
    assert (
        session.query(PaperFill)
        .filter(PaperFill.order_id == order.id)
        .count()
        == 0
    )

    # Reconciliation invariant holds.
    assert _ledger_sum(session, account.id) == to_money(account.cash_available)
