"""Unit tests for backend.paper.valuation (P3 mark-to-market + NAV).

Offline by construction: positions are opened via PaperBroker market buys
with an injected price_fn, and marks are applied via injected price_fns —
no network, no live quote ever reached.
"""
from decimal import Decimal

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
from backend.paper.valuation import (
    compute_account_nav,
    mark_positions,
    position_day_pnl,
    position_market_value,
    position_unrealized_pnl,
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


def _sell(db, user_id, sym, qty, px):
    broker = PaperBroker(db, user_id, price_fn=lambda s: to_money(px))
    return broker.place_order(
        tradingsymbol=sym,
        transaction_type="SELL",
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


# ── mark_positions ──────────────────────────────────────────────────────


def test_mark_positions_refreshes_open_positions(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    _buy(session, u.id, "BBB", 5, 200)
    acct = _account(session, u.id)

    marks = {"AAA": to_money(110), "BBB": to_money(190)}
    n = mark_positions(session, acct.id, price_fn=lambda s: marks[s])
    assert n == 2

    a = _pos(session, acct.id, "AAA")
    b = _pos(session, acct.id, "BBB")
    assert a.last_price == 110.0
    assert b.last_price == 190.0
    assert a.last_mark_at is not None
    assert a.stale is False and b.stale is False


def test_mark_positions_none_price_sets_stale_and_keeps_last_price(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)

    # First mark gives a real price.
    mark_positions(session, acct.id, price_fn=lambda s: to_money(120))
    a = _pos(session, acct.id, "AAA")
    assert a.last_price == 120.0
    assert a.stale is False

    # Next mark returns None -> stale True, last_price unchanged.
    n = mark_positions(session, acct.id, price_fn=lambda s: None)
    assert n == 0
    a = _pos(session, acct.id, "AAA")
    assert a.stale is True
    assert a.last_price == 120.0  # preserved


def test_mark_positions_nonpositive_price_sets_stale(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 3, 50)
    acct = _account(session, u.id)
    n = mark_positions(session, acct.id, price_fn=lambda s: to_money(0))
    assert n == 0
    a = _pos(session, acct.id, "AAA")
    assert a.stale is True


def test_mark_positions_skips_closed_positions(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 4, 100)
    _sell(session, u.id, "AAA", 4, 110)  # fully close
    acct = _account(session, u.id)

    # Closed position (quantity 0) is not marked.
    n = mark_positions(session, acct.id, price_fn=lambda s: to_money(999))
    assert n == 0
    a = _pos(session, acct.id, "AAA")
    assert a.quantity == 0
    assert a.last_price is None  # never marked


# ── position_market_value ───────────────────────────────────────────────


def test_position_market_value_uses_last_price(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)
    mark_positions(session, acct.id, price_fn=lambda s: to_money(123))
    a = _pos(session, acct.id, "AAA")
    assert position_market_value(a) == to_money(10 * 123)
    assert isinstance(position_market_value(a), Decimal)


def test_position_market_value_falls_back_to_avg_cost(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)
    a = _pos(session, acct.id, "AAA")
    # Never marked: value = qty * avg_cost (avg_cost includes buy charges).
    assert a.last_price is None
    assert position_market_value(a) == to_money(10 * to_money(a.avg_cost))


# ── position_unrealized_pnl ─────────────────────────────────────────────


def test_position_unrealized_pnl(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)
    a = _pos(session, acct.id, "AAA")
    avg = to_money(a.avg_cost)
    mark_positions(session, acct.id, price_fn=lambda s: to_money(150))
    a = _pos(session, acct.id, "AAA")
    assert position_unrealized_pnl(a) == to_money(10 * (to_money(150) - avg))


def test_position_unrealized_pnl_zero_when_unmarked(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)
    a = _pos(session, acct.id, "AAA")
    assert position_unrealized_pnl(a) == to_money(0)


# ── position_day_pnl ────────────────────────────────────────────────────


def test_position_day_pnl_requires_both_marks(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)
    a = _pos(session, acct.id, "AAA")

    # Neither set -> 0.
    assert position_day_pnl(a) == to_money(0)

    # last_price only -> still 0 (no prev_close).
    mark_positions(session, acct.id, price_fn=lambda s: to_money(150))
    a = _pos(session, acct.id, "AAA")
    assert position_day_pnl(a) == to_money(0)

    # Both set -> qty * (last - prev_close).
    a.prev_close = 140.0
    session.flush()
    assert position_day_pnl(a) == to_money(10 * (to_money(150) - to_money(140)))


# ── compute_account_nav ─────────────────────────────────────────────────


def test_compute_account_nav_two_positions_exact(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    _buy(session, u.id, "BBB", 5, 200)
    acct = _account(session, u.id)

    # Capture avg costs (include buy charges) before marking.
    a_avg = to_money(_pos(session, acct.id, "AAA").avg_cost)
    b_avg = to_money(_pos(session, acct.id, "BBB").avg_cost)
    cash = to_money(acct.cash_available)

    new_marks = {"AAA": to_money(110), "BBB": to_money(190)}
    nav = compute_account_nav(session, acct, price_fn=lambda s: new_marks[s])

    exp_mv = to_money(10 * to_money(110)) + to_money(5 * to_money(190))
    exp_unreal = (
        to_money(10 * (to_money(110) - a_avg))
        + to_money(5 * (to_money(190) - b_avg))
    )
    assert nav["positions_mv"] == exp_mv
    assert nav["unrealized_pnl"] == exp_unreal
    assert nav["cash_available"] == cash
    assert nav["nav"] == to_money(cash + exp_mv)
    assert nav["is_stale"] is False
    assert all(isinstance(nav[k], Decimal) for k in (
        "cash_available", "cash_settled", "positions_mv", "nav",
        "realized_pnl_cum", "unrealized_pnl",
    ))


def test_compute_account_nav_stale_falls_back_to_avg_cost(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)
    a_avg = to_money(_pos(session, acct.id, "AAA").avg_cost)
    cash = to_money(acct.cash_available)

    # price_fn returns None: position is stale and falls back to avg_cost.
    nav = compute_account_nav(session, acct, price_fn=lambda s: None)
    assert nav["is_stale"] is True
    assert nav["positions_mv"] == to_money(10 * a_avg)
    assert nav["unrealized_pnl"] == to_money(0)  # never marked
    assert nav["nav"] == to_money(cash + to_money(10 * a_avg))


def test_compute_account_nav_realized_includes_closed_position(session):
    u = _user(session)
    # Open then fully close AAA -> realized P&L retained on the closed row.
    _buy(session, u.id, "AAA", 10, 100)
    _sell(session, u.id, "AAA", 10, 130)
    # Keep an open position so NAV has live MV too.
    _buy(session, u.id, "BBB", 5, 200)
    acct = _account(session, u.id)

    closed = _pos(session, acct.id, "AAA")
    assert closed.quantity == 0
    realized = to_money(closed.realized_pnl)
    assert realized != to_money(0)  # a real realized number was booked

    nav = compute_account_nav(session, acct, price_fn=lambda s: to_money(210))
    # realized_pnl_cum spans ALL positions incl. the closed one.
    assert nav["realized_pnl_cum"] == realized
    # Open BBB contributes to MV; closed AAA does not.
    assert nav["positions_mv"] == to_money(5 * to_money(210))


def test_nav_includes_reserved_cash(session):
    # A resting LIMIT BUY reserves cash; that cash is still OWNED, so NAV
    # must not dip when the order rests (else the equity curve drops on
    # placement and jumps on fill). Review fix.
    user = _user(session)
    PaperBroker(session, user.id, price_fn=lambda s: to_money(100)).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="LIMIT", price=95.0,
    )
    acct = _account(session, user.id)
    assert acct.cash_reserved > to_money(0)
    nav = compute_account_nav(session, acct, price_fn=lambda s: to_money(100))
    assert nav["nav"] == to_money(150000)              # full seed, not dipped
    assert nav["cash_reserved"] == acct.cash_reserved
    assert nav["positions_mv"] == to_money(0)          # no open position yet
