"""P3 integration: the scheduler orchestrators stitch the modules together —
resting order -> evaluator fills -> position -> NAV snapshot (equity curve).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend.models import (
    PaperAccount,
    PaperFill,
    PaperLedgerEntry,
    PaperNavSnapshot,
    PaperOrder,
    PaperPosition,
    User,
)
from backend.paper.broker import PaperBroker
from backend.paper.jobs import mark_open_positions, snapshot_all_navs, tick_paper_accounts
from backend.paper.money import to_money
from backend.services.trading_costs import buy_cost


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _fk(c, _):  # noqa: ANN001
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _user(db: Session, email: str = "p3@example.com") -> User:
    u = User(email=email, hashed_password="x")
    db.add(u)
    db.flush()
    return u


def _broker(db: Session, user: User, price: float) -> PaperBroker:
    return PaperBroker(db, user.id, price_fn=lambda _s: to_money(price))


def test_resting_limit_fills_on_tick_then_nav_curve_grows(session: Session) -> None:
    user = _user(session)
    # Resting LIMIT BUY 10 RELIANCE @ 95 while mark is 100 -> rests + reserves
    res = _broker(session, user, 100.0).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="LIMIT", price=95.0,
    )
    assert res["paper_status"] == "resting"
    acct = session.query(PaperAccount).filter_by(user_id=user.id).one()
    assert acct.cash_reserved == to_money(buy_cost(95.0, 10)[0])  # charges-inclusive reserve
    assert session.query(PaperFill).count() == 0

    # Tick with the mark still above the limit -> no fill.
    tick_paper_accounts(session, price_fn=lambda _s: to_money(98))
    assert session.query(PaperFill).count() == 0
    assert session.query(PaperOrder).filter_by(status="resting").count() == 1

    # Mark drops to 94 (crosses the 95 limit) -> the tick fills it.
    out = tick_paper_accounts(session, price_fn=lambda _s: to_money(94))
    assert len(out["filled"]) == 1
    pos = session.query(PaperPosition).filter_by(symbol="RELIANCE").one()
    assert pos.quantity == 10
    session.refresh(acct)
    assert acct.cash_reserved == to_money(0)  # reserve released on fill
    # the release ledger row was written and cash reconciles
    assert session.query(PaperLedgerEntry).filter_by(kind="release").count() == 1
    total = sum((e.amount for e in session.query(PaperLedgerEntry).all()), Decimal("0"))
    assert to_money(total) == acct.cash_available

    # EOD NAV snapshot at mark 110.
    n = snapshot_all_navs(session, as_of_date=dt.date(2026, 5, 30),
                          price_fn=lambda _s: to_money(110), nifty_close=24000.0)
    assert n == 1
    s1 = session.query(PaperNavSnapshot).filter_by(as_of_date=dt.date(2026, 5, 30)).one()
    assert s1.positions_mv == to_money(1100)          # 10 * 110
    assert s1.nav == to_money(acct.cash_available + to_money(1100))
    assert s1.nifty_close == 24000.0

    # Next day at mark 120 -> a second point; the equity curve grew.
    snapshot_all_navs(session, as_of_date=dt.date(2026, 5, 31),
                      price_fn=lambda _s: to_money(120))
    assert session.query(PaperNavSnapshot).count() == 2
    s2 = session.query(PaperNavSnapshot).filter_by(as_of_date=dt.date(2026, 5, 31)).one()
    assert s2.nav > s1.nav  # 10 * (120-110) = +100 unrealized


def test_eod_snapshot_is_idempotent_per_day(session: Session) -> None:
    user = _user(session)
    _broker(session, user, 100.0).place_order(
        tradingsymbol="INFY", transaction_type="BUY", quantity=5,
        order_type="MARKET",
    )
    d = dt.date(2026, 5, 30)
    snapshot_all_navs(session, as_of_date=d, price_fn=lambda _s: to_money(100))
    snapshot_all_navs(session, as_of_date=d, price_fn=lambda _s: to_money(150))
    # one row per (account, day) — the re-run upserts, doesn't duplicate
    assert session.query(PaperNavSnapshot).filter_by(as_of_date=d).count() == 1
    row = session.query(PaperNavSnapshot).filter_by(as_of_date=d).one()
    assert row.positions_mv == to_money(750)  # reflects the latest mark (150)


def test_tick_skips_accounts_without_resting_orders(session: Session) -> None:
    # An account with only a filled MARKET order has no resting work.
    user = _user(session)
    _broker(session, user, 100.0).place_order(
        tradingsymbol="TCS", transaction_type="BUY", quantity=1,
        order_type="MARKET",
    )
    out = tick_paper_accounts(session, price_fn=lambda _s: to_money(100))
    assert out["accounts"] == 0
    assert out["filled"] == []


# ── mark_open_positions (intraday marking; 2026-07-06 live-test regression) ──
#
# Before this job existed, a position's last_price/day-P&L were frozen at
# their fill-time value for the whole trading day — mark_positions only ran
# once, from the 15:37 EOD NAV snapshot.


def test_mark_open_positions_refreshes_last_price(session: Session) -> None:
    user = _user(session)
    _broker(session, user, 100.0).place_order(
        tradingsymbol="INFY", transaction_type="BUY", quantity=10,
        order_type="MARKET",
    )
    pos = session.query(PaperPosition).one()
    assert pos.last_price == 100.0  # seeded at fill

    out = mark_open_positions(session, price_fn=lambda _s: to_money(107.5))
    session.commit()
    session.refresh(pos)
    assert out["accounts"] == 1
    assert out["positions_marked"] == 1
    assert pos.last_price == 107.5


def test_mark_open_positions_skips_accounts_with_no_open_positions(
    session: Session,
) -> None:
    user = _user(session)
    _broker(session, user, 100.0).place_order(
        tradingsymbol="INFY", transaction_type="BUY", quantity=1,
        order_type="MARKET",
    )
    # Fully exit the position -> quantity 0, no longer "open".
    _broker(session, user, 120.0).place_order(
        tradingsymbol="INFY", transaction_type="SELL", quantity=1,
        order_type="MARKET",
    )
    out = mark_open_positions(session, price_fn=lambda _s: to_money(999))
    assert out["accounts"] == 0
    assert out["positions_marked"] == 0
