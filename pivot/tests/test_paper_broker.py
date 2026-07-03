"""P1 tests for the paper broker — synchronous MARKET path, idempotency,
cash/position/ledger accrual, resting orders, and money precision.

Self-contained in-memory SQLite (PRAGMA foreign_keys=ON), deterministic
price via an injected price_fn so nothing touches the network.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend import models
from backend.kite import orders as kite_orders
from backend.models import (
    PaperAccount,
    PaperFill,
    PaperLedgerEntry,
    PaperOrder,
    PaperPosition,
    User,
    WatchlistItem,
)
from backend.paper import PaperBroker, get_or_create_account
from backend.paper.money import to_money
from backend.services.trading_costs import buy_cost, sell_cost


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
    u = User(email="p1@example.com", hashed_password="x")
    db.add(u)
    db.flush()
    return u


def _broker(db: Session, user: User, price: float) -> PaperBroker:
    # Fixed-price provider for determinism.
    return PaperBroker(db, user.id, price_fn=lambda _sym: to_money(price))


# ── seeding ────────────────────────────────────────────────────────────

def test_account_auto_seeded_on_first_order(session: Session) -> None:
    user = _user(session)
    assert session.query(PaperAccount).count() == 0
    _broker(session, user, 100.0).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=1,
    )
    acct = session.query(PaperAccount).one()
    assert acct.starting_capital == Decimal("150000.0000")
    # seed ledger row exists
    seed = session.query(PaperLedgerEntry).filter_by(kind="seed").one()
    assert seed.amount == Decimal("150000.0000")


def test_explicit_starting_capital(session: Session) -> None:
    user = _user(session)
    acct = get_or_create_account(session, user.id, starting_capital=1_000_000)
    assert acct.cash_available == Decimal("1000000.0000")


# ── market buy ───────────────────────────────────────────────────────────

def test_market_buy_fills_position_and_debits_cash(session: Session) -> None:
    user = _user(session)
    res = _broker(session, user, 100.0).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        client_request_id="crid-buy-1",
    )
    assert res["status"] == "COMPLETE" and res["paper_status"] == "filled"
    assert res["average_price"] == 100.0
    assert res["filled_quantity"] == 10

    net_debit, charges = buy_cost(100.0, 10)
    net_debit, charges = to_money(net_debit), to_money(charges)

    acct = session.query(PaperAccount).one()
    assert acct.cash_available == to_money(Decimal("150000") - net_debit)
    assert acct.cash_settled == acct.cash_available  # P1 simplified settlement

    pos = session.query(PaperPosition).one()
    assert pos.quantity == 10
    assert pos.avg_cost == to_money(net_debit / 10)  # incl. buy charges

    fill = session.query(PaperFill).one()
    assert fill.transaction_type == "BUY"
    assert fill.charges == charges
    assert fill.net_cashflow == -net_debit
    assert fill.realized_pnl is None
    assert isinstance(fill.gross_value, Decimal)


def test_ledger_reconciles_cash_by_replay(session: Session) -> None:
    user = _user(session)
    b = _broker(session, user, 100.0)
    b.place_order(tradingsymbol="INFY", transaction_type="BUY", quantity=5)
    b.place_order(tradingsymbol="TCS", transaction_type="BUY", quantity=3)
    acct = session.query(PaperAccount).one()
    total = sum(
        (e.amount for e in session.query(PaperLedgerEntry).all()), Decimal("0"),
    )
    assert to_money(total) == acct.cash_available


# ── idempotency ──────────────────────────────────────────────────────────

def test_idempotent_replay_does_not_double_fill(session: Session) -> None:
    user = _user(session)
    b = _broker(session, user, 100.0)
    r1 = b.place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        client_request_id="dup-1",
    )
    cash_after_first = session.query(PaperAccount).one().cash_available
    r2 = b.place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        client_request_id="dup-1",
    )
    assert r2["order_id"] == r1["order_id"]
    assert r2["idempotent_replay"] is True
    assert session.query(PaperOrder).count() == 1
    assert session.query(PaperFill).count() == 1
    assert session.query(PaperAccount).one().cash_available == cash_after_first


# ── market sell ──────────────────────────────────────────────────────────

def test_sell_realizes_pnl_and_credits_cash(session: Session) -> None:
    user = _user(session)
    b = _broker(session, user, 100.0)
    b.place_order(tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10)
    net_debit, _ = buy_cost(100.0, 10)
    avg_cost = to_money(to_money(net_debit) / 10)

    # Sell all 10 at a higher price.
    b2 = PaperBroker(session, user.id, price_fn=lambda _s: to_money(110.0))
    res = b2.place_order(
        tradingsymbol="RELIANCE", transaction_type="SELL", quantity=10,
    )
    assert res["status"] == "COMPLETE"
    net_credit, _ = sell_cost(110.0, 10)
    net_credit = to_money(net_credit)
    expected_realized = to_money(net_credit - avg_cost * 10)

    fill = (
        session.query(PaperFill)
        .filter(PaperFill.transaction_type == "SELL").one()
    )
    assert fill.realized_pnl == expected_realized
    assert fill.settles_at is not None  # T+1 stamped on the sell
    pos = session.query(PaperPosition).one()
    assert pos.quantity == 0
    assert pos.avg_cost == Decimal("0.0000")
    assert pos.realized_pnl == expected_realized


# ── rejects ──────────────────────────────────────────────────────────────

def test_insufficient_buying_power_rejects(session: Session) -> None:
    user = _user(session)
    res = _broker(session, user, 100.0).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=100_000,
    )
    assert res["status"] == "REJECTED"
    assert res["reject_reason"] == "insufficient_buying_power"
    assert session.query(PaperFill).count() == 0
    assert session.query(PaperPosition).count() == 0
    # cash untouched (still full seed)
    assert session.query(PaperAccount).one().cash_available == Decimal("150000.0000")


def test_oversell_rejects(session: Session) -> None:
    user = _user(session)
    b = _broker(session, user, 100.0)
    b.place_order(tradingsymbol="RELIANCE", transaction_type="BUY", quantity=5)
    res = b.place_order(
        tradingsymbol="RELIANCE", transaction_type="SELL", quantity=10,
    )
    assert res["status"] == "REJECTED"
    assert res["reject_reason"] == "insufficient_position"
    assert session.query(PaperPosition).one().quantity == 5  # unchanged


def test_price_unavailable_rejects(session: Session) -> None:
    user = _user(session)
    b = PaperBroker(session, user.id, price_fn=lambda _s: None)
    res = b.place_order(
        tradingsymbol="NOSUCH", transaction_type="BUY", quantity=1,
    )
    assert res["status"] == "REJECTED"
    assert res["reject_reason"] == "price_unavailable"
    assert session.query(PaperFill).count() == 0


# ── resting orders ───────────────────────────────────────────────────────

def test_limit_buy_rests_and_reserves_cash(session: Session) -> None:
    user = _user(session)
    res = _broker(session, user, 100.0).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="LIMIT", price=95.0,
    )
    assert res["status"] == "OPEN" and res["paper_status"] == "resting"
    assert session.query(PaperFill).count() == 0  # not filled yet
    acct = session.query(PaperAccount).one()
    reserve = to_money(buy_cost(95.0, 10)[0])
    assert acct.cash_reserved == reserve
    assert acct.cash_available == to_money(Decimal("150000") - reserve)
    order = session.query(PaperOrder).one()
    assert order.reserved_cash == reserve
    # reserve ledger row
    assert session.query(PaperLedgerEntry).filter_by(kind="reserve").count() == 1


def test_gtt_rests(session: Session) -> None:
    user = _user(session)
    res = _broker(session, user, 100.0).place_gtt_order(
        tradingsymbol="RELIANCE", transaction_type="SELL", quantity=10,
        trigger_price=120.0, limit_price=120.0,
    )
    assert res["paper_status"] == "resting"
    order = session.query(PaperOrder).one()
    assert order.order_type == "GTT" and order.trigger_price == 120.0


# ── attribution ──────────────────────────────────────────────────────────

def test_attribution_is_recorded(session: Session) -> None:
    user = _user(session)
    # forward_ideas FK: create one to reference.
    idea = models.ForwardIdea(
        user_id=user.id,
        account_id=get_or_create_account(session, user.id).id,
        origin_kind="workflow", label="x",
    )
    session.add(idea)
    session.flush()
    _broker(session, user, 100.0).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=1,
        origin_kind="workflow", source="workflow", idea_id=idea.id,
    )
    order = session.query(PaperOrder).one()
    assert order.idea_id == idea.id and order.origin_kind == "workflow"
    fill = session.query(PaperFill).one()
    assert fill.idea_id == idea.id  # copied from the order


# ── TypeError fix on kite.orders.place_order ─────────────────────────────

# ── input guards (from the P1 adversarial review) ───────────────────────

def test_zero_quantity_rejected_without_crash(session: Session) -> None:
    user = _user(session)
    res = _broker(session, user, 100.0).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=0,
    )
    assert res["status"] == "REJECTED" and res["reject_reason"] == "invalid_quantity"
    # malformed input persists nothing
    assert session.query(PaperOrder).count() == 0
    assert session.query(PaperFill).count() == 0


def test_negative_quantity_rejected(session: Session) -> None:
    user = _user(session)
    b = _broker(session, user, 100.0)
    for side in ("BUY", "SELL"):
        res = b.place_order(
            tradingsymbol="RELIANCE", transaction_type=side, quantity=-5,
        )
        assert res["reject_reason"] == "invalid_quantity"
    assert session.query(PaperPosition).count() == 0
    assert session.query(PaperAccount).count() == 0  # never even seeded


@pytest.mark.parametrize("bad_price", [0.0, -50.0])
def test_nonpositive_mark_rejected(session: Session, bad_price: float) -> None:
    user = _user(session)
    b = PaperBroker(session, user.id, price_fn=lambda _s: to_money(bad_price))
    res = b.place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=5,
    )
    assert res["status"] == "REJECTED" and res["reject_reason"] == "price_unavailable"
    assert session.query(PaperFill).count() == 0
    assert session.query(PaperAccount).one().cash_available == Decimal("150000.0000")


def test_resting_limit_overdraft_rejected(session: Session) -> None:
    user = _user(session)
    res = _broker(session, user, 100.0).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10_000,
        order_type="LIMIT", price=95.0,  # reserve 950,000 > 150,000 seed
    )
    assert res["status"] == "REJECTED"
    assert res["reject_reason"] == "insufficient_buying_power"
    acct = session.query(PaperAccount).one()
    assert acct.cash_available == Decimal("150000.0000")
    assert acct.cash_reserved == Decimal("0.0000")
    assert session.query(PaperLedgerEntry).filter_by(kind="reserve").count() == 0


def test_settled_equals_available_plus_reserved(session: Session) -> None:
    user = _user(session)
    _broker(session, user, 100.0).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="LIMIT", price=95.0,
    )
    acct = session.query(PaperAccount).one()
    # reserve moves available -> reserved; settled is total owned.
    assert acct.cash_settled == acct.cash_available + acct.cash_reserved
    assert acct.cash_reserved == to_money(buy_cost(95.0, 10)[0])


# ── multi-leg sequences ──────────────────────────────────────────────────

def test_multibuy_partialsell_reconciles(session: Session) -> None:
    user = _user(session)
    b = _broker(session, user, 100.0)
    b.place_order(tradingsymbol="X", transaction_type="BUY", quantity=10)
    b2 = PaperBroker(session, user.id, price_fn=lambda _s: to_money(120.0))
    b2.place_order(tradingsymbol="X", transaction_type="BUY", quantity=10)

    nd1, _ = buy_cost(100.0, 10)
    nd2, _ = buy_cost(120.0, 10)
    exact_avg = to_money((to_money(nd1) + to_money(nd2)) / 20)
    pos = session.query(PaperPosition).one()
    assert abs(pos.avg_cost - exact_avg) <= Decimal("0.0001")  # bounded drift

    avg_before = pos.avg_cost
    b3 = PaperBroker(session, user.id, price_fn=lambda _s: to_money(130.0))
    b3.place_order(tradingsymbol="X", transaction_type="SELL", quantity=5)
    session.refresh(pos)
    assert pos.quantity == 15
    assert pos.avg_cost == avg_before  # partial sell does NOT move avg_cost

    b3.place_order(tradingsymbol="X", transaction_type="SELL", quantity=15)
    session.refresh(pos)
    assert pos.quantity == 0
    assert pos.avg_cost == Decimal("0.0000")

    # cash reconciles EXACTLY by ledger replay
    acct = session.query(PaperAccount).one()
    total = sum((e.amount for e in session.query(PaperLedgerEntry).all()), Decimal("0"))
    assert to_money(total) == acct.cash_available

    # cumulative realized within the documented sub-2-paise bound of the
    # true economic P&L (sell credits - buy debits, once flat).
    nc1, _ = sell_cost(130.0, 5)
    nc2, _ = sell_cost(130.0, 15)
    economic = (to_money(nc1) + to_money(nc2)) - (to_money(nd1) + to_money(nd2))
    assert abs(pos.realized_pnl - economic) <= Decimal("0.02")


def test_sell_to_zero_then_rebuy_resets_avg(session: Session) -> None:
    user = _user(session)
    _broker(session, user, 100.0).place_order(
        tradingsymbol="X", transaction_type="BUY", quantity=10,
    )
    PaperBroker(session, user.id, price_fn=lambda _s: to_money(110.0)).place_order(
        tradingsymbol="X", transaction_type="SELL", quantity=10,
    )
    PaperBroker(session, user.id, price_fn=lambda _s: to_money(200.0)).place_order(
        tradingsymbol="X", transaction_type="BUY", quantity=5,
    )
    pos = session.query(PaperPosition).one()
    nd, _ = buy_cost(200.0, 5)
    assert pos.quantity == 5
    assert pos.avg_cost == to_money(to_money(nd) / 5)  # basis reset cleanly


# ── GTT + Kite-interface parity (P2 drop-in readiness) ───────────────────

def test_gtt_returns_trigger_id_and_persists_limit(session: Session) -> None:
    user = _user(session)
    res = _broker(session, user, 100.0).place_gtt_order(
        tradingsymbol="RELIANCE", transaction_type="SELL", quantity=10,
        trigger_price=120.0, limit_price=118.0, last_price=100.0,
        exchange="NSE", access_token="ignored-token",
    )
    assert res["trigger_id"] == res["order_id"]  # GTT contract
    assert res["status"] == "active"
    order = session.query(PaperOrder).one()
    assert order.order_type == "GTT"
    assert order.limit_price == 118.0  # GTT now carries its limit
    assert order.trigger_price == 120.0


def test_place_order_accepts_kite_kwargs(session: Session) -> None:
    # The P2 shim forwards the Kite kwargs; the broker must accept (and
    # ignore) access_token + tag rather than TypeError.
    user = _user(session)
    res = _broker(session, user, 100.0).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=1,
        access_token="some-token", tag="wf_abc123", product="CNC",
    )
    assert res["status"] == "COMPLETE"


# ── idempotency edge cases ───────────────────────────────────────────────

def test_replay_of_rejected_order(session: Session) -> None:
    user = _user(session)
    b = _broker(session, user, 100.0)
    r1 = b.place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=100_000,
        client_request_id="rej-1",
    )
    assert r1["status"] == "REJECTED"
    # same crid replays the SAME rejected order (at-most-once placement).
    r2 = b.place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=100_000,
        client_request_id="rej-1",
    )
    assert r2["idempotent_replay"] is True
    assert r2["order_id"] == r1["order_id"]
    assert session.query(PaperOrder).count() == 1


def test_idempotency_user_scoped_and_savepoint_preserves_caller_work(
    session: Session,
) -> None:
    # user99 already owns crid "shared" (a different user's order).
    u99 = User(email="u99@example.com", hashed_password="x")
    session.add(u99)
    session.flush()
    a99 = get_or_create_account(session, u99.id)
    session.add(PaperOrder(
        account_id=a99.id, user_id=u99.id, client_request_id="shared",
        symbol="X", transaction_type="BUY", order_type="MARKET", quantity=1,
        status="filled",
    ))
    session.flush()

    u1 = _user(session)
    # The caller's OTHER uncommitted work in the same session.
    sibling = WatchlistItem(user_id=u1.id, symbol="SIBLING", exchange="NSE")
    session.add(sibling)
    session.flush()
    sibling_id = sibling.id

    # u1 reuses u99's crid. The user-scoped fast-path SELECT misses (it's
    # another user's order), so we insert -> the global unique index
    # collides at flush -> IntegrityError. The SAVEPOINT must roll back
    # ONLY that insert, leaving the caller's sibling row intact.
    b = PaperBroker(session, u1.id, price_fn=lambda _s: to_money(100.0))
    with pytest.raises(IntegrityError):
        b.place_order(
            tradingsymbol="Y", transaction_type="BUY", quantity=1,
            client_request_id="shared",
        )
    assert (
        session.query(WatchlistItem).filter_by(id=sibling_id).first() is not None
    ), "SAVEPOINT must not discard the caller's other uncommitted work"


def test_kite_place_order_accepts_client_request_id() -> None:
    # Regression: actions.py squareoff legs pass client_request_id=...,
    # which previously raised TypeError. In mock mode this must return a
    # dict echoing the id (no broker call).
    res = kite_orders.place_order(
        access_token="mock_token", tradingsymbol="RELIANCE", exchange="NSE",
        transaction_type="SELL", quantity=5, order_type="MARKET",
        client_request_id="sqoff_sym:RELIANCE:run:0:1:leg0:RELIANCE",
    )
    assert res["status"] == "COMPLETE"
    assert res["client_request_id"] == "sqoff_sym:RELIANCE:run:0:1:leg0:RELIANCE"
