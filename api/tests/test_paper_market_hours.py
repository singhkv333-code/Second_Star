"""Market-hours-aware paper fills (owner test 2026-07-06).

When ``paper_respect_market_hours`` is on and the NSE is CLOSED, a MARKET order
rests ("queued for open") instead of filling at the stale close; the evaluator
fills it on the next market-hours tick. When the market is open it fills
immediately, as before.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend.models import PaperOrder, User
from backend.paper import PaperBroker, get_or_create_account
from backend.paper.evaluator import evaluate_resting_orders, should_fill
from backend.paper.jobs import tick_paper_accounts
from backend.paper.money import to_money


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
    u = User(email="mh@example.com", hashed_password="x")
    db.add(u)
    db.flush()
    return u


@pytest.fixture(autouse=True)
def _respect_hours_on(monkeypatch):
    """This module tests the market-hours behaviour, so turn the flag ON
    (conftest pins it OFF for the rest of the suite)."""
    from backend.config import settings
    monkeypatch.setattr(settings, "paper_respect_market_hours", True, raising=False)


def _mkt(monkeypatch, is_open: bool):
    monkeypatch.setattr("backend.paper.broker.is_market_open", lambda: is_open)
    monkeypatch.setattr("backend.paper.jobs.is_market_open", lambda: is_open)


def test_market_order_rests_when_market_closed(session, monkeypatch):
    _mkt(monkeypatch, False)
    u = _user(session)
    get_or_create_account(session, u.id)
    b = PaperBroker(session, u.id, price_fn=lambda _s: to_money(100.0))
    res = b.place_order(tradingsymbol="INFY", transaction_type="BUY", quantity=10)
    order = session.get(PaperOrder, res["order_id"])
    assert order.status == "resting"          # queued for open, NOT filled
    assert order.order_type == "MARKET"


def test_queued_market_fills_on_open_tick(session, monkeypatch):
    _mkt(monkeypatch, False)
    u = _user(session)
    get_or_create_account(session, u.id)
    b = PaperBroker(session, u.id, price_fn=lambda _s: to_money(100.0))
    res = b.place_order(tradingsymbol="INFY", transaction_type="BUY", quantity=10)
    oid = res["order_id"]
    assert session.get(PaperOrder, oid).status == "resting"

    # Market opens; the tick fills the queued MARKET at the live mark.
    _mkt(monkeypatch, True)
    summary = tick_paper_accounts(session, price_fn=lambda _s: to_money(105.0))
    session.commit()
    assert oid in summary["filled"]
    assert session.get(PaperOrder, oid).status == "filled"


def test_market_order_fills_immediately_when_open(session, monkeypatch):
    _mkt(monkeypatch, True)
    u = _user(session)
    get_or_create_account(session, u.id)
    b = PaperBroker(session, u.id, price_fn=lambda _s: to_money(100.0))
    res = b.place_order(tradingsymbol="INFY", transaction_type="BUY", quantity=10)
    assert session.get(PaperOrder, res["order_id"]).status == "filled"


def test_tick_skips_when_market_closed(session, monkeypatch):
    # Seed a resting MARKET while closed, then tick while STILL closed.
    _mkt(monkeypatch, False)
    u = _user(session)
    get_or_create_account(session, u.id)
    b = PaperBroker(session, u.id, price_fn=lambda _s: to_money(100.0))
    b.place_order(tradingsymbol="INFY", transaction_type="BUY", quantity=10)
    summary = tick_paper_accounts(session, price_fn=lambda _s: to_money(105.0))
    assert summary.get("skipped_market_closed") is True
    assert summary["filled"] == []


def test_should_fill_market_returns_mark():
    class _O:
        order_type = "MARKET"
        transaction_type = "BUY"
        limit_price = None
        trigger_price = None
        intended_price = None
    assert should_fill(_O(), Decimal("101.5")) == to_money(Decimal("101.5"))
