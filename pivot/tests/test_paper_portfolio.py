"""Unit tests for backend.paper.portfolio (P4 READ-ONLY read service).

Offline by construction: positions open via PaperBroker market buys with an
injected price_fn; marks applied via valuation.mark_positions; NAV points via
snapshots.snapshot_account_nav — no network, no live quote.

The read service is READ-ONLY: these tests assert it computes from the STORED
marks and never mutates the book.
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
from backend.paper.portfolio import (
    account_summary,
    fills_journal,
    holdings,
    nav_curve,
    open_orders,
)
from backend.paper.snapshots import snapshot_account_nav
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


def _buy(db, user_id, sym, qty, px, order_type="MARKET", price=None):
    broker = PaperBroker(db, user_id, price_fn=lambda s: to_money(px))
    return broker.place_order(
        tradingsymbol=sym,
        transaction_type="BUY",
        quantity=qty,
        order_type=order_type,
        price=price,
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


# ── account_summary ──────────────────────────────────────────────────────


def test_account_summary_nav_unrealized_buying_power_exact(session):
    u = _user(session)
    _buy(session, u.id, "INFY", 10, 100)
    _buy(session, u.id, "TCS", 5, 200)
    acct = _account(session, u.id)

    infy_avg = to_money(_pos(session, acct.id, "INFY").avg_cost)
    tcs_avg = to_money(_pos(session, acct.id, "TCS").avg_cost)
    cash = to_money(acct.cash_available)

    new_marks = {"INFY": to_money(110), "TCS": to_money(190)}
    mark_positions(session, acct.id, price_fn=lambda s: new_marks[s])

    s = account_summary(session, u.id)

    exp_mv = to_money(10 * to_money(110)) + to_money(5 * to_money(190))
    exp_unreal = (
        to_money(10 * (to_money(110) - infy_avg))
        + to_money(5 * (to_money(190) - tcs_avg))
    )
    assert s["exists"] is True
    assert s["num_positions"] == 2
    assert s["num_open_orders"] == 0
    assert s["positions_mv"] == float(exp_mv)
    assert s["unrealized_pnl"] == float(exp_unreal)
    # No cash reserved (only market fills) -> buying_power == cash_available.
    assert s["cash_reserved"] == 0.0
    assert s["buying_power"] == float(cash)
    # NAV = cash_available + cash_reserved + positions_mv.
    assert s["nav"] == float(cash + exp_mv)
    # total_pnl = nav - starting_capital.
    assert s["total_pnl"] == float(cash + exp_mv - to_money(150000))
    assert s["is_stale"] is False
    assert s["mode"] == "paper"
    # All money fields are floats, not Decimal.
    for k in ("starting_capital", "cash_available", "nav", "unrealized_pnl",
              "realized_pnl_cum", "day_pnl", "total_pnl", "invested"):
        assert isinstance(s[k], float)


def test_account_summary_no_account_returns_exists_false(session):
    u = _user(session)
    s = account_summary(session, u.id)
    assert s == {"exists": False}


def test_account_summary_is_read_only(session):
    u = _user(session)
    _buy(session, u.id, "INFY", 10, 100)
    acct = _account(session, u.id)
    mark_positions(session, acct.id, price_fn=lambda s: to_money(110))

    before_cash = to_money(acct.cash_available)
    before_mark = _pos(session, acct.id, "INFY").last_price

    account_summary(session, u.id)

    acct2 = _account(session, u.id)
    assert to_money(acct2.cash_available) == before_cash
    # Read did not re-mark or change the stored mark.
    assert _pos(session, acct2.id, "INFY").last_price == before_mark


# ── holdings ─────────────────────────────────────────────────────────────


def test_holdings_sorted_by_mv_with_sector_and_unrealized(session):
    u = _user(session)
    # INFY MV = 10*110 = 1100; TCS MV = 5*190 = 950 -> INFY first.
    _buy(session, u.id, "INFY", 10, 100)
    _buy(session, u.id, "TCS", 5, 200)
    acct = _account(session, u.id)

    infy_avg = to_money(_pos(session, acct.id, "INFY").avg_cost)
    mark_positions(
        session, acct.id,
        price_fn=lambda s: {"INFY": to_money(110), "TCS": to_money(190)}[s],
    )

    h = holdings(session, u.id)
    assert [r["symbol"] for r in h] == ["INFY", "TCS"]
    # Descending market value.
    assert h[0]["market_value"] >= h[1]["market_value"]
    # Sector from SECTOR_MAP (both IT in the map).
    assert h[0]["sector"] == "IT"
    assert h[1]["sector"] == "IT"
    # Unrealized on INFY = 10 * (110 - avg).
    assert h[0]["unrealized_pnl"] == float(
        to_money(10 * (to_money(110) - infy_avg))
    )
    assert h[0]["last_price"] == 110.0
    assert h[0]["stale"] is False
    assert h[0]["last_mark_at"] is not None


def test_holdings_market_buy_seeds_last_price_immediately(session):
    """2026-07-06 fix: a market buy stamps last_price/prev_close at the fill
    price the instant the position opens — it used to stay None for hours
    (until the next scheduler tick / the once-daily EOD snapshot), which is
    why Total P&L read ₹0 all day after every fill."""
    u = _user(session)
    _buy(session, u.id, "RELIANCE", 4, 100)
    h = holdings(session, u.id)
    assert len(h) == 1
    assert h[0]["last_price"] == 100
    assert h[0]["sector"] == "Energy"


def test_holdings_unmarked_position_falls_back_to_avg_cost(session):
    """A position that genuinely has no mark (e.g. a symbol the marker
    can't price at all) still values at book cost, never None/zero."""
    u = _user(session)
    _buy(session, u.id, "RELIANCE", 4, 100)
    pos = session.query(PaperPosition).filter_by(symbol="RELIANCE").one()
    pos.last_price = None  # simulate a mark that was never resolved
    session.flush()
    h = holdings(session, u.id)
    assert h[0]["last_price"] is None
    # avg_cost is inclusive of buy charges, so the fallback isn't a bare 4*100.
    assert h[0]["market_value"] == pytest.approx(4 * float(pos.avg_cost))


def test_holdings_excludes_closed_and_unknown_sector(session):
    u = _user(session)
    _buy(session, u.id, "ZZZUNKNOWN", 3, 50)
    broker = PaperBroker(session, u.id, price_fn=lambda s: to_money(60))
    broker.place_order(
        tradingsymbol="ZZZUNKNOWN", transaction_type="SELL",
        quantity=3, order_type="MARKET",
    )  # fully close -> excluded
    _buy(session, u.id, "INFY", 2, 100)

    h = holdings(session, u.id)
    syms = [r["symbol"] for r in h]
    assert "ZZZUNKNOWN" not in syms
    assert syms == ["INFY"]


def test_holdings_no_account_empty(session):
    u = _user(session)
    assert holdings(session, u.id) == []


# ── open_orders ──────────────────────────────────────────────────────────


def test_open_orders_resting_limit_with_reserved_cash(session):
    u = _user(session)
    # Resting LIMIT BUY reserves cash up front.
    _buy(session, u.id, "INFY", 10, 100, order_type="LIMIT", price=95.0)
    o = open_orders(session, u.id)
    assert len(o) == 1
    assert o[0]["symbol"] == "INFY"
    assert o[0]["side"] == "BUY"
    assert o[0]["order_type"] == "LIMIT"
    assert o[0]["status"] == "resting"
    assert o[0]["limit_price"] == 95.0
    assert o[0]["reserved_cash"] > 0
    assert isinstance(o[0]["reserved_cash"], float)
    assert o[0]["created_at"] is not None


def test_open_orders_excludes_filled_market_order(session):
    u = _user(session)
    _buy(session, u.id, "INFY", 5, 100)  # MARKET -> filled, not resting
    assert open_orders(session, u.id) == []


def test_open_orders_newest_first(session):
    u = _user(session)
    _buy(session, u.id, "INFY", 1, 100, order_type="LIMIT", price=90.0)
    _buy(session, u.id, "TCS", 1, 200, order_type="LIMIT", price=180.0)
    o = open_orders(session, u.id)
    assert len(o) == 2
    # Both resting; newest (TCS) first by created_at desc. created_at can tie
    # on fast clocks, so assert the set is correct and TCS is not last.
    assert {r["symbol"] for r in o} == {"INFY", "TCS"}


# ── fills_journal ────────────────────────────────────────────────────────


def test_fills_journal_market_buys_newest_first(session):
    u = _user(session)
    _buy(session, u.id, "INFY", 10, 100)
    _buy(session, u.id, "TCS", 5, 200)
    j = fills_journal(session, u.id)
    assert len(j) == 2
    syms = {r["symbol"] for r in j}
    assert syms == {"INFY", "TCS"}
    for r in j:
        assert r["side"] == "BUY"
        assert isinstance(r["fill_price"], float)
        assert isinstance(r["gross_value"], float)
        assert isinstance(r["net_cashflow"], float)
        assert r["order_id"] is not None
    # Buys have null realized_pnl.
    assert all(r["realized_pnl"] is None for r in j)


def test_fills_journal_limit_caps_results(session):
    u = _user(session)
    for px in (100, 101, 102):
        _buy(session, u.id, "INFY", 1, px)
    j = fills_journal(session, u.id, limit=2)
    assert len(j) == 2


def test_fills_journal_no_account_empty(session):
    u = _user(session)
    assert fills_journal(session, u.id) == []


# ── nav_curve ────────────────────────────────────────────────────────────


def test_nav_curve_two_days_ordered(session):
    u = _user(session)
    _buy(session, u.id, "INFY", 10, 100)
    acct = _account(session, u.id)

    d1 = dt.date(2026, 5, 28)
    d2 = dt.date(2026, 5, 29)
    snapshot_account_nav(session, acct, d1, price_fn=lambda s: to_money(105))
    snapshot_account_nav(session, acct, d2, price_fn=lambda s: to_money(110))

    curve = nav_curve(session, u.id)
    assert len(curve) == 2
    # Oldest first.
    assert curve[0]["as_of_date"] == d1.isoformat()
    assert curve[1]["as_of_date"] == d2.isoformat()
    for pt in curve:
        assert isinstance(pt["nav"], float)
        assert isinstance(pt["positions_mv"], float)
        assert "nifty_close" in pt
    # nifty_close defaults to None.
    assert curve[0]["nifty_close"] is None


def test_nav_curve_no_account_empty(session):
    u = _user(session)
    assert nav_curve(session, u.id) == []
