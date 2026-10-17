"""Unit tests for backend.paper.positions (P4 Kite-shaped paper reads).

Offline by construction: positions are opened via PaperBroker market buys
with an injected price_fn, a GTT is placed via place_gtt_order, and marks
are applied via an injected price_fn — no network, no live quote ever
reached. Both read functions are asserted READ-ONLY by shape only (they
never write).
"""
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
from backend.paper.positions import (
    paper_open_orders_kite_shape,
    paper_positions_kite_shape,
)
from backend.paper.valuation import mark_positions


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
    broker = PaperBroker(db, user_id, price_fn=lambda s: to_money(px))
    return broker.place_order(
        tradingsymbol=sym,
        transaction_type="BUY",
        quantity=qty,
        order_type="MARKET",
    )


def _gtt(db, user_id, sym, side, qty, trigger, limit, px):
    broker = PaperBroker(db, user_id, price_fn=lambda s: to_money(px))
    return broker.place_gtt_order(
        tradingsymbol=sym,
        transaction_type=side,
        quantity=qty,
        trigger_price=trigger,
        limit_price=limit,
    )


def _account(db, user_id):
    return (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id)
        .first()
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


# ── paper_positions_kite_shape ──────────────────────────────────────────


def test_positions_kite_shape_two_longs(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    _buy(session, u.id, "BBB", 5, 200)
    acct = _account(session, u.id)

    # Mark so unrealized P&L != 0 and last_price != avg_cost.
    marks = {"AAA": to_money(110), "BBB": to_money(190)}
    mark_positions(session, acct.id, price_fn=lambda s: marks[s])

    res = paper_positions_kite_shape(session, u.id)
    assert set(res) == {"net", "day"}
    assert len(res["net"]) == 2

    by_sym = {e["tradingsymbol"]: e for e in res["net"]}
    assert set(by_sym) == {"AAA", "BBB"}

    # avg_cost is charges-inclusive (buy-side brokerage rolls into the book
    # cost), so assert against the STORED avg_cost, not the raw fill price.
    pa = _pos(session, acct.id, "AAA")
    pb = _pos(session, acct.id, "BBB")

    a = by_sym["AAA"]
    assert a["exchange"] == "NSE"
    assert a["product"] == "CNC"
    assert a["quantity"] == 10
    assert a["average_price"] == float(to_money(pa.avg_cost))
    assert a["last_price"] == 110.0
    # 10 * (110 - avg_cost): marked above book, so positive.
    assert a["pnl"] == float(to_money(10 * (to_money(110) - to_money(pa.avg_cost))))
    assert a["pnl"] > 0
    assert isinstance(a["average_price"], float)
    assert isinstance(a["last_price"], float)
    assert isinstance(a["pnl"], float)

    b = by_sym["BBB"]
    assert b["quantity"] == 5
    assert b["average_price"] == float(to_money(pb.avg_cost))
    assert b["last_price"] == 190.0
    # 5 * (190 - avg_cost): marked below book, so negative.
    assert b["pnl"] == float(to_money(5 * (to_money(190) - to_money(pb.avg_cost))))
    assert b["pnl"] < 0


def test_day_mirrors_net(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)

    res = paper_positions_kite_shape(session, u.id)
    assert res["day"] == res["net"]
    assert len(res["day"]) == 1


def test_positions_last_price_falls_back_to_avg_cost_when_unmarked(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    acct = _account(session, u.id)
    pos = _pos(session, acct.id, "AAA")
    assert pos.last_price is None  # never marked

    # No mark applied -> last_price is None -> use avg_cost.
    res = paper_positions_kite_shape(session, u.id)
    e = res["net"][0]
    avg = float(to_money(pos.avg_cost))
    assert e["last_price"] == avg
    assert e["average_price"] == avg
    # last_price == avg_cost -> unrealized pnl is exactly 0.
    assert e["pnl"] == 0.0


def test_positions_excludes_closed_lots(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)
    # Sell the whole lot -> quantity 0 row remains but must not show.
    broker = PaperBroker(session, u.id, price_fn=lambda s: to_money(120))
    broker.place_order(
        tradingsymbol="AAA",
        transaction_type="SELL",
        quantity=10,
        order_type="MARKET",
    )
    res = paper_positions_kite_shape(session, u.id)
    assert res["net"] == []
    assert res["day"] == []


def test_positions_no_account_returns_empty_shape(session):
    u = _user(session)  # user exists, but no paper account / no orders
    res = paper_positions_kite_shape(session, u.id)
    assert res == {"net": [], "day": []}


# ── paper_open_orders_kite_shape ────────────────────────────────────────


def test_open_orders_returns_resting_gtt_as_trigger_pending(session):
    u = _user(session)
    # An open long so the SELL GTT is a plausible exit; px=100 mark.
    _buy(session, u.id, "AAA", 10, 100)
    gtt = _gtt(session, u.id, "AAA", "SELL", 10, trigger=90, limit=89, px=100)

    orders = paper_open_orders_kite_shape(session, u.id)
    assert len(orders) == 1
    o = orders[0]
    assert o["order_id"] == gtt["order_id"]
    assert o["tradingsymbol"] == "AAA"
    assert o["exchange"] == "NSE"
    assert o["transaction_type"] == "SELL"
    assert o["quantity"] == 10
    assert o["order_type"] == "GTT"
    assert o["product"] == "CNC"
    assert o["status"] == "TRIGGER PENDING"


def test_open_orders_excludes_filled_market_orders(session):
    u = _user(session)
    # A filled MARKET buy must NOT appear in the resting blotter.
    _buy(session, u.id, "AAA", 10, 100)
    orders = paper_open_orders_kite_shape(session, u.id)
    assert orders == []


def test_open_orders_no_account_returns_empty_list(session):
    u = _user(session)
    assert paper_open_orders_kite_shape(session, u.id) == []
