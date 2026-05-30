"""Unit tests for backend.paper.snapshots (P3 daily NAV equity curve).

Offline by construction: positions are opened via PaperBroker market buys
with an injected price_fn, and snapshots are taken with injected price_fns —
no network, no live quote ever reached.
"""
import datetime as dt

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import (  # noqa: F401 — registers tables on Base.metadata
    PaperAccount,
    PaperFill,
    PaperLedgerEntry,
    PaperNavSnapshot,
    PaperOrder,
    PaperPosition,
    User,
)
from backend.paper.broker import PaperBroker
from backend.paper.money import to_money
from backend.paper.snapshots import (
    latest_nav,
    nav_series,
    snapshot_account_nav,
)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _fk(c, _):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()
    engine.dispose()


def _user(db):
    u = User(email=f"x{id(db)}@e.com", hashed_password="x")
    db.add(u)
    db.flush()
    return u


def _buy(db, user_id, sym, qty, px):
    """Open/add to a position via a real market BUY at price ``px``."""
    broker = PaperBroker(db, user_id, price_fn=lambda s: to_money(px))
    return broker.place_order(
        tradingsymbol=sym,
        transaction_type="BUY",
        quantity=qty,
        order_type="MARKET",
    )


def _pos(db, account_id, sym):
    return (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == sym,
        )
        .first()
    )


def _account(db, user_id):
    return (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id)
        .first()
    )


def _snapshot_rows(db, account_id):
    return (
        db.query(PaperNavSnapshot)
        .filter(PaperNavSnapshot.account_id == account_id)
        .all()
    )


# ── (a) upsert by (account_id, as_of_date) ──────────────────────────────


def test_snapshot_creates_row_with_exact_nav(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)
    cash = to_money(acct.cash_available)

    d1 = dt.date(2026, 5, 28)
    row = snapshot_account_nav(
        session, acct, d1, price_fn=lambda s: to_money(100)
    )

    exp_mv = to_money(10 * to_money(100))
    assert row.positions_mv == exp_mv
    assert row.nav == to_money(cash + exp_mv)
    assert row.cash_available == cash
    assert row.user_id == acct.user_id
    assert row.as_of_date == d1
    assert row.is_stale is False
    assert len(_snapshot_rows(session, acct.id)) == 1


def test_re_snapshot_same_date_upserts_single_row(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)
    cash = to_money(acct.cash_available)
    d1 = dt.date(2026, 5, 28)

    row1 = snapshot_account_nav(
        session, acct, d1, price_fn=lambda s: to_money(100)
    )
    id1 = row1.id
    nav_at_100 = row1.nav

    # Re-snapshot the SAME date at a higher mark.
    row2 = snapshot_account_nav(
        session, acct, d1, price_fn=lambda s: to_money(110)
    )

    # Still exactly one row (UNIQUE(account_id, as_of_date) holds), same PK.
    assert len(_snapshot_rows(session, acct.id)) == 1
    assert row2.id == id1
    # NAV updated to the new mark.
    exp_mv = to_money(10 * to_money(110))
    assert row2.positions_mv == exp_mv
    assert row2.nav == to_money(cash + exp_mv)
    assert row2.nav != nav_at_100


def test_nifty_close_written_as_float(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 1, 100)
    acct = _account(session, u.id)
    d1 = dt.date(2026, 5, 28)

    row = snapshot_account_nav(
        session, acct, d1, price_fn=lambda s: to_money(100),
        nifty_close=22500.5,
    )
    assert row.nifty_close == 22500.5
    assert isinstance(row.nifty_close, float)

    # None stays None.
    row2 = snapshot_account_nav(
        session, acct, dt.date(2026, 5, 29),
        price_fn=lambda s: to_money(100),
    )
    assert row2.nifty_close is None


# ── (b) series + latest across dates ────────────────────────────────────


def test_nav_series_and_latest_across_two_dates(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)

    d1 = dt.date(2026, 5, 28)
    d2 = dt.date(2026, 5, 29)
    snapshot_account_nav(session, acct, d1, price_fn=lambda s: to_money(100))
    snapshot_account_nav(session, acct, d2, price_fn=lambda s: to_money(120))

    series = nav_series(session, acct.id)
    assert len(series) == 2
    # Ordered ascending by as_of_date.
    assert [r.as_of_date for r in series] == [d1, d2]

    latest = latest_nav(session, acct.id)
    assert latest.as_of_date == d2

    # Date filters are inclusive.
    only_d2 = nav_series(session, acct.id, start=d2)
    assert [r.as_of_date for r in only_d2] == [d2]
    only_d1 = nav_series(session, acct.id, end=d1)
    assert [r.as_of_date for r in only_d1] == [d1]


def test_latest_nav_none_when_no_snapshots(session):
    # No account/snapshots yet -> latest returns None for an unknown id.
    assert latest_nav(session, "no-such-account") is None
    assert nav_series(session, "no-such-account") == []


# ── (c) prev_close rolled to the marked price ───────────────────────────


def test_snapshot_rolls_prev_close_to_marked_price(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)

    a = _pos(session, acct.id, "AAA")
    assert a.prev_close is None  # not yet rolled

    d1 = dt.date(2026, 5, 28)
    snapshot_account_nav(session, acct, d1, price_fn=lambda s: to_money(115))

    a = _pos(session, acct.id, "AAA")
    # last_price stamped by the mark, and prev_close rolled to it.
    assert a.last_price == 115.0
    assert a.prev_close == 115.0


def test_prev_close_not_rolled_for_unmarked_stale_position(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)

    d1 = dt.date(2026, 5, 28)
    # price_fn None -> position never marked (last_price stays None) + stale.
    snapshot_account_nav(session, acct, d1, price_fn=lambda s: None)

    a = _pos(session, acct.id, "AAA")
    assert a.last_price is None
    # No close to carry forward.
    assert a.prev_close is None
