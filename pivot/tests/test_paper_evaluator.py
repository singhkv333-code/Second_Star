"""P3 tests for the resting-order fill evaluator (backend.paper.evaluator).

should_fill is the pure decision; evaluate_resting_orders is the driver
that reads a live mark per order, captures the trigger reference on first
sighting, fills crossed orders via fill_resting_order, and cancels the OCO
sibling when one leg fills.

Self-contained in-memory SQLite (PRAGMA foreign_keys=ON), deterministic
price via an injected price_fn so nothing touches the network.
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
    PaperOrder,
    PaperPosition,
    User,
)
from backend.paper.accounts import get_or_create_account
from backend.paper.broker import PaperBroker
from backend.paper.evaluator import evaluate_resting_orders, should_fill
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


def _open_position(
    db: Session, user: User, *, symbol: str, qty: int, price: float,
) -> PaperAccount:
    """Open a real long via a MARKET BUY so a SELL can rest against it."""
    broker = PaperBroker(db, user.id, price_fn=lambda _s: to_money(price))
    res = broker.place_order(
        tradingsymbol=symbol,
        transaction_type="BUY",
        quantity=qty,
        order_type="MARKET",
    )
    assert res["paper_status"] == "filled"
    order = db.get(PaperOrder, res["order_id"])
    return db.get(PaperAccount, order.account_id)


def _resting_sell(
    db: Session,
    account: PaperAccount,
    user: User,
    *,
    symbol: str,
    qty: int,
    order_type: str,
    trigger: float | None = None,
    limit: float | None = None,
    oco: str | None = None,
    intended: float | None = None,
) -> PaperOrder:
    """Construct a resting SELL order row directly."""
    order = PaperOrder(
        account_id=account.id,
        user_id=user.id,
        symbol=symbol,
        exchange="NSE",
        transaction_type="SELL",
        order_type=order_type,
        quantity=qty,
        limit_price=limit,
        trigger_price=trigger,
        intended_price=intended,
        status="resting",
        gtt_oco_group=oco,
        reserved_cash=to_money(0),
    )
    db.add(order)
    db.flush()
    return order


# ── should_fill (pure) ───────────────────────────────────────────────────

def test_should_fill_limit_buy() -> None:
    o = PaperOrder(
        symbol="X", transaction_type="BUY", order_type="LIMIT",
        quantity=1, limit_price=95.0, status="resting",
    )
    assert should_fill(o, to_money(100)) is None          # above limit
    assert should_fill(o, to_money(95)) == to_money(95)   # at limit
    assert should_fill(o, to_money(94)) == to_money(94)   # below limit


def test_should_fill_limit_sell() -> None:
    o = PaperOrder(
        symbol="X", transaction_type="SELL", order_type="LIMIT",
        quantity=1, limit_price=110.0, status="resting",
    )
    assert should_fill(o, to_money(100)) is None
    assert should_fill(o, to_money(110)) == to_money(110)
    assert should_fill(o, to_money(115)) == to_money(115)


def test_should_fill_downside_trigger() -> None:
    # SELL stop-loss below entry (reference 100, trigger 90).
    o = PaperOrder(
        symbol="X", transaction_type="SELL", order_type="GTT",
        quantity=1, trigger_price=90.0, intended_price=100.0,
        status="resting",
    )
    assert should_fill(o, to_money(95)) is None
    assert should_fill(o, to_money(90)) == to_money(90)
    assert should_fill(o, to_money(89)) == to_money(89)


def test_should_fill_upside_trigger() -> None:
    # Take-profit above entry (reference 100, trigger 120).
    o = PaperOrder(
        symbol="X", transaction_type="SELL", order_type="GTT",
        quantity=1, trigger_price=120.0, intended_price=100.0,
        status="resting",
    )
    assert should_fill(o, to_money(115)) is None
    assert should_fill(o, to_money(120)) == to_money(120)
    assert should_fill(o, to_money(121)) == to_money(121)


def test_should_fill_market_returns_none() -> None:
    o = PaperOrder(
        symbol="X", transaction_type="BUY", order_type="MARKET",
        quantity=1, status="pending",
    )
    assert should_fill(o, to_money(100)) is None


# ── (a) resting LIMIT BUY crosses ────────────────────────────────────────

def test_evaluate_limit_buy_fills_on_drop(session: Session) -> None:
    user = _user(session)
    account = get_or_create_account(session, user.id)
    sym, qty, limit = "TCS", 10, 95.0

    # Place via the broker so cash is reserved on the resting LIMIT BUY.
    broker = PaperBroker(session, user.id, price_fn=lambda _s: to_money(100))
    res = broker.place_order(
        tradingsymbol=sym, transaction_type="BUY", quantity=qty,
        order_type="LIMIT", price=limit,
    )
    order = session.get(PaperOrder, res["order_id"])
    assert order.status == "resting"
    assert to_money(order.reserved_cash) == to_money(buy_cost(limit, qty)[0])

    # Mark 100 (> 95): does not fill.
    out = evaluate_resting_orders(
        session, account, price_fn=lambda _s: to_money(100),
    )
    assert out["evaluated"] == 1
    assert out["filled"] == []
    assert out["skipped"] == [order.id]
    session.refresh(order)
    assert order.status == "resting"

    # Mark drops to 94 (<= 95): fills at 94.
    out = evaluate_resting_orders(
        session, account, price_fn=lambda _s: to_money(94),
    )
    assert out["filled"] == [order.id]
    assert out["cancelled"] == []
    session.refresh(order)
    assert order.status == "filled"
    assert order.filled_quantity == qty

    fill = (
        session.query(PaperFill)
        .filter(PaperFill.order_id == order.id)
        .one()
    )
    assert to_money(fill.fill_price) == to_money(94)

    # Reserve fully released.
    session.refresh(account)
    assert to_money(account.cash_reserved) == to_money(0)
    assert to_money(order.reserved_cash) == to_money(0)

    # Reconciliation invariant holds.
    assert _ledger_sum(session, account.id) == to_money(account.cash_available)


# ── (b) SELL GTT stop-loss: first tick captures reference, then fires ────

def test_evaluate_gtt_stoploss_downside(session: Session) -> None:
    user = _user(session)
    sym, qty = "INFY", 5
    account = _open_position(session, user, symbol=sym, qty=qty, price=100.0)

    # GTT SELL stop-loss with trigger 90, no reference captured yet.
    order = _resting_sell(
        session, account, user, symbol=sym, qty=qty,
        order_type="GTT", trigger=90.0, intended=None,
    )
    assert order.intended_price is None

    # First evaluate at mark 100: captures reference 100, does NOT fill.
    out = evaluate_resting_orders(
        session, account, price_fn=lambda _s: to_money(100),
    )
    assert out["filled"] == []
    assert out["skipped"] == [order.id]
    session.refresh(order)
    assert order.intended_price == 100.0
    assert order.status == "resting"

    # Mark drops to 89 (<= 90): downside trigger fires.
    out = evaluate_resting_orders(
        session, account, price_fn=lambda _s: to_money(89),
    )
    assert out["filled"] == [order.id]
    session.refresh(order)
    assert order.status == "filled"

    fill = (
        session.query(PaperFill)
        .filter(PaperFill.order_id == order.id)
        .one()
    )
    assert to_money(fill.fill_price) == to_money(89)
    # Position closed out.
    pos = (
        session.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account.id,
            PaperPosition.symbol == sym,
        )
        .one()
    )
    assert pos.quantity == 0
    assert _ledger_sum(session, account.id) == to_money(account.cash_available)


# ── (c) OCO: TP fills, SL sibling cancels ────────────────────────────────

def test_evaluate_oco_tp_fills_sl_cancels(session: Session) -> None:
    user = _user(session)
    sym, qty = "RELIANCE", 4
    account = _open_position(session, user, symbol=sym, qty=qty, price=100.0)

    # Two resting SELL legs sharing an OCO group, both with reference 100:
    # an SL at 90 (downside) and a TP at 120 (upside).
    sl = _resting_sell(
        session, account, user, symbol=sym, qty=qty,
        order_type="GTT", trigger=90.0, oco="g1", intended=100.0,
    )
    tp = _resting_sell(
        session, account, user, symbol=sym, qty=qty,
        order_type="GTT", trigger=120.0, oco="g1", intended=100.0,
    )

    # Mark 121 (>= 120): the TP fires; the SL must be cancelled.
    out = evaluate_resting_orders(
        session, account, price_fn=lambda _s: to_money(121),
    )
    assert out["evaluated"] == 2
    assert tp.id in out["filled"]
    assert sl.id in out["cancelled"]
    assert len(out["filled"]) == 1
    assert len(out["cancelled"]) == 1

    session.refresh(tp)
    session.refresh(sl)
    assert tp.status == "filled"
    assert sl.status == "cancelled"

    fill = (
        session.query(PaperFill)
        .filter(PaperFill.order_id == tp.id)
        .one()
    )
    assert to_money(fill.fill_price) == to_money(121)
    # Only one fill total (the SL never filled).
    assert session.query(PaperFill).count() == 2  # the entry BUY + the TP

    assert _ledger_sum(session, account.id) == to_money(account.cash_available)


# ── price-unavailable / non-positive marks skip ──────────────────────────

def test_evaluate_skips_when_no_price(session: Session) -> None:
    user = _user(session)
    account = get_or_create_account(session, user.id)
    broker = PaperBroker(session, user.id, price_fn=lambda _s: to_money(100))
    res = broker.place_order(
        tradingsymbol="WIPRO", transaction_type="BUY", quantity=2,
        order_type="LIMIT", price=90.0,
    )
    order = session.get(PaperOrder, res["order_id"])

    out_none = evaluate_resting_orders(session, account, price_fn=lambda _s: None)
    assert out_none["skipped"] == [order.id]
    assert out_none["filled"] == []

    out_zero = evaluate_resting_orders(
        session, account, price_fn=lambda _s: to_money(0),
    )
    assert out_zero["skipped"] == [order.id]
    session.refresh(order)
    assert order.status == "resting"


# ── P3 review-fix regressions ─────────────────────────────────────────────

def test_rejected_resting_fill_not_marked_filled(session: Session) -> None:
    # A resting SELL that oversells the held position must reject at fill and
    # be reported in 'rejected', NOT 'filled' (the evaluator must honour
    # fill_resting_order's None return).
    user = _user(session)
    PaperBroker(session, user.id, price_fn=lambda _s: to_money(100)).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="MARKET",
    )
    acct = session.query(PaperAccount).filter_by(user_id=user.id).one()
    over = PaperOrder(
        account_id=acct.id, user_id=user.id, symbol="RELIANCE",
        transaction_type="SELL", order_type="LIMIT", quantity=100,
        limit_price=90.0, status="resting",
    )
    session.add(over)
    session.flush()
    out = evaluate_resting_orders(session, acct, price_fn=lambda _s: to_money(95))
    session.refresh(over)
    assert over.status == "rejected"
    assert out["rejected"] == [over.id]
    assert out["filled"] == []
    assert session.query(PaperFill).count() == 1  # only the initial market buy


def test_rejected_oco_leg_does_not_cancel_sibling(session: Session) -> None:
    # THE BLOCKER: a rejected fill must not cancel its OCO sibling.
    user = _user(session)
    PaperBroker(session, user.id, price_fn=lambda _s: to_money(100)).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="MARKET",
    )
    acct = session.query(PaperAccount).filter_by(user_id=user.id).one()
    t0 = dt.datetime(2026, 5, 30, 9, 0, 0, tzinfo=dt.timezone.utc)
    a = PaperOrder(  # evaluated first; oversells -> rejects
        account_id=acct.id, user_id=user.id, symbol="RELIANCE",
        transaction_type="SELL", order_type="LIMIT", quantity=100,
        limit_price=90.0, status="resting", gtt_oco_group="g1", created_at=t0,
    )
    b = PaperOrder(  # sibling that does NOT cross on its own (limit 200 vs
        # mark 95) — so the only thing that could change its status is a
        # WRONG OCO-cancel by A's reject. It must stay resting.
        account_id=acct.id, user_id=user.id, symbol="RELIANCE",
        transaction_type="SELL", order_type="LIMIT", quantity=5,
        limit_price=200.0, status="resting", gtt_oco_group="g1",
        created_at=t0 + dt.timedelta(seconds=1),
    )
    session.add_all([a, b])
    session.flush()
    out = evaluate_resting_orders(session, acct, price_fn=lambda _s: to_money(95))
    session.refresh(a)
    session.refresh(b)
    assert a.status == "rejected" and a.id in out["rejected"]
    assert a.id not in out["filled"]
    # The fix: A's reject did NOT cancel its OCO sibling; B is untouched.
    assert b.status == "resting" and b.id not in out["cancelled"]


def test_gtt_trigger_direction_uses_placement_reference(session: Session) -> None:
    # intended_price is captured at PLACEMENT (not the first tick), so a
    # stop placed above the trigger fires on the way DOWN.
    user = _user(session)
    broker = PaperBroker(session, user.id, price_fn=lambda _s: to_money(100))
    broker.place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="MARKET",
    )
    broker.place_gtt_order(
        tradingsymbol="RELIANCE", transaction_type="SELL", quantity=10,
        trigger_price=90.0, limit_price=90.0, last_price=100.0,
    )
    acct = session.query(PaperAccount).filter_by(user_id=user.id).one()
    gtt = session.query(PaperOrder).filter_by(order_type="GTT").one()
    assert gtt.intended_price == 100.0  # reference captured at placement
    # price falls to 89 (<= trigger 90, below the 100 reference) -> stop fires
    out = evaluate_resting_orders(session, acct, price_fn=lambda _s: to_money(89))
    assert gtt.id in out["filled"]
