"""P0 schema tests for the paper-trading / forward-testing tables.

Self-contained: each test gets a fresh in-memory SQLite DB built from
Base.metadata.create_all (the same path the app's test conftest uses),
with PRAGMA foreign_keys=ON so FK + CASCADE behaviour is exercised the
way Postgres would enforce it. We assert:

  - account seeding defaults (₹1,50,000 single book, mode='paper')
  - client_request_id idempotency (UNIQUE)
  - one position per (account, symbol) (UNIQUE)
  - enum-like CHECK constraints reject bad values
  - the full attribution chain account -> idea -> order -> fill
  - ORM cascade delete tears down an account's children
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend import models
from backend.models import (
    FORWARD_IDEA_STATUSES,
    PAPER_ACCOUNT_MODES,
    PAPER_LEDGER_KINDS,
    PAPER_ORDER_STATUSES,
    ForwardIdea,
    PaperAccount,
    PaperFill,
    PaperIdeaNavSnapshot,
    PaperLedgerEntry,
    PaperNavSnapshot,
    PaperOrder,
    PaperPosition,
    User,
)


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
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _user(db: Session, email: str = "p0@example.com") -> User:
    u = User(email=email, hashed_password="x")
    db.add(u)
    db.flush()
    return u


def _account(db: Session, user: User) -> PaperAccount:
    acct = PaperAccount(user_id=user.id)
    db.add(acct)
    db.flush()
    return acct


# ── seeding defaults ──────────────────────────────────────────────────

def test_account_seed_defaults(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    session.refresh(acct)

    assert acct.id and len(acct.id) == 36
    assert acct.label == "default"
    assert acct.currency == "INR"
    assert acct.starting_capital == 150000.0
    assert acct.cash_settled == 150000.0
    assert acct.cash_available == 150000.0
    assert acct.cash_reserved == 0.0
    assert acct.mode == "paper"
    assert acct.is_active is True
    assert acct.created_at is not None
    # one-to-one back-reference resolves
    assert user.paper_account is acct


def test_one_account_per_user(session: Session) -> None:
    user = _user(session)
    _account(session, user)
    session.add(PaperAccount(user_id=user.id))
    with pytest.raises(IntegrityError):
        session.flush()


# ── idempotency + uniqueness ──────────────────────────────────────────

def test_client_request_id_is_unique(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    crid = "wf_run:0:1"
    session.add(PaperOrder(
        account_id=acct.id, user_id=user.id, client_request_id=crid,
        symbol="RELIANCE", transaction_type="BUY", order_type="MARKET",
        quantity=10,
    ))
    session.flush()
    session.add(PaperOrder(
        account_id=acct.id, user_id=user.id, client_request_id=crid,
        symbol="RELIANCE", transaction_type="BUY", order_type="MARKET",
        quantity=10,
    ))
    with pytest.raises(IntegrityError):
        session.flush()


def test_null_client_request_id_allowed_multiple(session: Session) -> None:
    # UNIQUE index must still permit many NULLs (manual orders w/o crid).
    user = _user(session)
    acct = _account(session, user)
    for _ in range(3):
        session.add(PaperOrder(
            account_id=acct.id, user_id=user.id, client_request_id=None,
            symbol="INFY", transaction_type="BUY", order_type="MARKET",
            quantity=1,
        ))
    session.flush()  # no IntegrityError


def test_one_position_per_account_symbol(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    session.add(PaperPosition(account_id=acct.id, user_id=user.id, symbol="TCS"))
    session.flush()
    session.add(PaperPosition(account_id=acct.id, user_id=user.id, symbol="TCS"))
    with pytest.raises(IntegrityError):
        session.flush()


# ── CHECK constraints ─────────────────────────────────────────────────

def test_order_status_check(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    session.add(PaperOrder(
        account_id=acct.id, user_id=user.id, symbol="SBIN",
        transaction_type="BUY", order_type="MARKET", quantity=1,
        status="not_a_status",
    ))
    with pytest.raises(IntegrityError):
        session.flush()


def test_account_mode_check(session: Session) -> None:
    user = _user(session)
    session.add(PaperAccount(user_id=user.id, mode="bogus"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_ledger_kind_check(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    session.add(PaperLedgerEntry(
        account_id=acct.id, kind="not_a_kind", amount=1.0, balance_after=1.0,
    ))
    with pytest.raises(IntegrityError):
        session.flush()


def test_frozensets_match_check_constraints(session: Session) -> None:
    # The exported frozensets the broker validates against must match the
    # CHECK constraint literals exactly (guards drift between the two).
    assert PAPER_ACCOUNT_MODES == {"paper", "live"}
    assert PAPER_ORDER_STATUSES == {
        "pending", "queued", "resting", "partially_filled",
        "filled", "cancelled", "rejected",
    }
    assert PAPER_LEDGER_KINDS == {
        "seed", "buy_debit", "sell_credit", "reserve", "release", "settlement",
    }
    assert FORWARD_IDEA_STATUSES == {"paper", "candidate", "promoted", "retired"}


# ── attribution chain + relationships ─────────────────────────────────

def test_attribution_chain(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    idea = ForwardIdea(
        user_id=user.id, account_id=acct.id, origin_kind="workflow",
        label="RELIANCE 3PM buy",
    )
    session.add(idea)
    session.flush()

    order = PaperOrder(
        account_id=acct.id, user_id=user.id, idea_id=idea.id,
        symbol="RELIANCE", transaction_type="BUY", order_type="MARKET",
        quantity=10, origin_kind="workflow", status="filled",
        filled_quantity=10,
    )
    session.add(order)
    session.flush()

    fill = PaperFill(
        order_id=order.id, account_id=acct.id, user_id=user.id,
        idea_id=idea.id, symbol="RELIANCE", transaction_type="BUY",
        quantity=10, fill_price=1450.0, gross_value=14500.0, charges=20.0,
        net_cashflow=-14520.0, filled_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add(fill)
    session.flush()

    session.refresh(acct)
    session.refresh(idea)
    session.refresh(order)
    assert [o.id for o in acct.orders] == [order.id]
    assert [f.id for f in acct.fills] == [fill.id]
    assert order.idea is idea
    assert [o.id for o in idea.orders] == [order.id]
    assert [f.id for f in idea.fills] == [fill.id]
    assert order.fills[0] is fill
    assert fill.idea is idea
    assert idea.status == "paper"
    assert idea.cohort_trial_count == 1


def test_bracket_parent_child(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    entry = PaperOrder(
        account_id=acct.id, user_id=user.id, symbol="HDFCBANK",
        transaction_type="BUY", order_type="MARKET", quantity=5,
    )
    session.add(entry)
    session.flush()
    sl = PaperOrder(
        account_id=acct.id, user_id=user.id, symbol="HDFCBANK",
        transaction_type="SELL", order_type="GTT", quantity=5,
        trigger_price=1500.0, parent_order_id=entry.id,
        gtt_oco_group="oco-1", status="resting",
    )
    session.add(sl)
    session.flush()
    session.refresh(entry)
    assert sl.parent is entry
    assert [c.id for c in entry.children] == [sl.id]


# ── cascade ───────────────────────────────────────────────────────────

def test_account_delete_cascades(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    idea = ForwardIdea(
        user_id=user.id, account_id=acct.id, origin_kind="chat", label="x",
    )
    session.add(idea)
    session.flush()
    order = PaperOrder(
        account_id=acct.id, user_id=user.id, idea_id=idea.id, symbol="ITC",
        transaction_type="BUY", order_type="MARKET", quantity=1,
    )
    session.add(order)
    session.flush()
    fill = PaperFill(
        order_id=order.id, account_id=acct.id, user_id=user.id,
        symbol="ITC", transaction_type="BUY", quantity=1, fill_price=400.0,
        gross_value=400.0, charges=1.0, net_cashflow=-401.0,
    )
    session.add(fill)
    session.add(PaperPosition(account_id=acct.id, user_id=user.id, symbol="ITC", quantity=1))
    session.flush()

    session.delete(acct)
    session.flush()

    assert session.query(PaperOrder).count() == 0
    assert session.query(PaperFill).count() == 0
    assert session.query(PaperPosition).count() == 0
    assert session.query(ForwardIdea).count() == 0
    assert session.query(PaperAccount).count() == 0


def test_db_level_cascade_on_raw_account_delete(session: Session) -> None:
    # The ORM cascade test above passes even with FK enforcement off
    # (delete-orphan issues the child DELETEs). This bypasses the ORM
    # unit-of-work with a raw DELETE so the migration's ondelete=CASCADE
    # is the ONLY thing that can remove the children — proving the DDL
    # clause prod relies on.
    user = _user(session)
    acct = _account(session, user)
    idea = ForwardIdea(
        user_id=user.id, account_id=acct.id, origin_kind="workflow", label="x",
    )
    session.add(idea)
    session.flush()
    order = PaperOrder(
        account_id=acct.id, user_id=user.id, idea_id=idea.id, symbol="ITC",
        transaction_type="BUY", order_type="MARKET", quantity=1,
    )
    session.add(order)
    session.flush()
    fill = PaperFill(
        order_id=order.id, account_id=acct.id, user_id=user.id, idea_id=idea.id,
        symbol="ITC", transaction_type="BUY", quantity=1, fill_price=400.0,
        gross_value=400.0, charges=1.0, net_cashflow=-401.0,
    )
    session.add(fill)
    session.add(PaperPosition(account_id=acct.id, user_id=user.id, symbol="ITC"))
    session.add(PaperLedgerEntry(
        account_id=acct.id, kind="seed", amount=150000.0, balance_after=150000.0,
    ))
    session.add(PaperNavSnapshot(
        account_id=acct.id, user_id=user.id, as_of_date=dt.date(2026, 5, 30),
        cash_available=1, cash_settled=1, positions_mv=0, nav=1,
        realized_pnl_cum=0, unrealized_pnl=0, is_stale=False,
    ))
    session.add(PaperIdeaNavSnapshot(
        idea_id=idea.id, account_id=acct.id, as_of_date=dt.date(2026, 5, 30),
        committed_capital=0, positions_mv=0, idea_nav=0, realized_pnl=0,
        unrealized_pnl=0,
    ))
    session.flush()
    session.expire_all()

    # Raw DELETE — no ORM cascade involved.
    session.execute(
        text("DELETE FROM paper_accounts WHERE id = :i"), {"i": acct.id},
    )

    for tbl in (
        "paper_orders", "paper_fills", "paper_positions", "paper_ledger",
        "forward_ideas", "paper_nav_snapshots", "paper_idea_nav_snapshots",
    ):
        n = session.execute(text(f"SELECT count(*) FROM {tbl}")).scalar()
        assert n == 0, f"{tbl} not cascaded by DB ondelete (count={n})"


def test_raw_idea_delete_cascades_idea_nav(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    idea = ForwardIdea(
        user_id=user.id, account_id=acct.id, origin_kind="chat", label="x",
    )
    session.add(idea)
    session.flush()
    session.add(PaperIdeaNavSnapshot(
        idea_id=idea.id, account_id=acct.id, as_of_date=dt.date(2026, 5, 30),
        committed_capital=0, positions_mv=0, idea_nav=0, realized_pnl=0,
        unrealized_pnl=0,
    ))
    session.flush()
    session.execute(
        text("DELETE FROM forward_ideas WHERE id = :i"), {"i": idea.id},
    )
    n = session.execute(
        text("SELECT count(*) FROM paper_idea_nav_snapshots"),
    ).scalar()
    assert n == 0


def test_pragma_foreign_keys_is_on(session: Session) -> None:
    # Guards the fixture: if a future refactor drops the connect listener,
    # every FK/cascade assertion above would silently no-op. Fail loudly.
    assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_nav_snapshot_unique_per_account_day(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    d = dt.date(2026, 5, 30)
    kw = dict(
        account_id=acct.id, user_id=user.id, as_of_date=d, cash_available=1,
        cash_settled=1, positions_mv=0, nav=1, realized_pnl_cum=0,
        unrealized_pnl=0, is_stale=False,
    )
    session.add(PaperNavSnapshot(**kw))
    session.flush()
    session.add(PaperNavSnapshot(**{**kw, "nav": 2}))
    with pytest.raises(IntegrityError):
        session.flush()


def test_idea_nav_snapshot_unique_per_idea_day(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    idea = ForwardIdea(
        user_id=user.id, account_id=acct.id, origin_kind="workflow", label="x",
    )
    session.add(idea)
    session.flush()
    d = dt.date(2026, 5, 30)
    kw = dict(
        idea_id=idea.id, account_id=acct.id, as_of_date=d, committed_capital=0,
        positions_mv=0, idea_nav=0, realized_pnl=0, unrealized_pnl=0,
    )
    session.add(PaperIdeaNavSnapshot(**kw))
    session.flush()
    session.add(PaperIdeaNavSnapshot(**{**kw, "idea_nav": 1}))
    with pytest.raises(IntegrityError):
        session.flush()


def test_scorecard_cache_json_roundtrip(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    payload = {
        "cum_return": 0.14, "sharpe": 0.7, "alpha": 0.02, "psr": 0.91,
        "mdd": -0.16, "series": [1, 2, 3],
    }
    idea = ForwardIdea(
        user_id=user.id, account_id=acct.id, origin_kind="workflow",
        label="x", scorecard_cache=payload,
    )
    session.add(idea)
    session.flush()
    session.expire_all()
    got = session.get(ForwardIdea, idea.id)
    assert isinstance(got.scorecard_cache, dict)
    assert got.scorecard_cache == payload


def test_backtest_run_id_is_soft_reference(session: Session) -> None:
    # Inserting a backtest_run_id with no matching dsl_backtest_runs row
    # must NOT raise — proving there is intentionally no FK. If someone
    # "fixes" it into a hard FK, this fails loudly (and would dangle vs
    # the test metadata, which is exactly why it's soft).
    user = _user(session)
    acct = _account(session, user)
    idea = ForwardIdea(
        user_id=user.id, account_id=acct.id, origin_kind="workflow",
        label="x", backtest_run_id="no-such-dsl-backtest-run",
    )
    session.add(idea)
    session.flush()
    assert idea.backtest_run_id == "no-such-dsl-backtest-run"


def test_money_columns_are_numeric_decimal(session: Session) -> None:
    # Reconciled-money columns must hydrate as Decimal (not float) so the
    # ledger replay is exact to paise. A cents-sensitive sum that would
    # drift under binary float stays exact under Numeric.
    user = _user(session)
    acct = _account(session, user)
    session.refresh(acct)
    assert isinstance(acct.cash_available, Decimal)
    assert acct.cash_available == Decimal("150000.0000")

    total = Decimal("0")
    for _ in range(30):  # 30 * 0.10 must equal exactly 3.00
        e = PaperLedgerEntry(
            account_id=acct.id, kind="buy_debit", amount=Decimal("0.10"),
            balance_after=0,
        )
        session.add(e)
        total += Decimal("0.10")
    session.flush()
    assert total == Decimal("3.00")
    rows = session.query(PaperLedgerEntry).all()
    assert sum((r.amount for r in rows), Decimal("0")) == Decimal("3.00")
    assert all(isinstance(r.amount, Decimal) for r in rows)


def test_worst_case_client_request_id_fits(session: Session) -> None:
    # The longest id actions.py builds: squareoff_symbol leg, symbol
    # twice. 36-char run uuid + a long F&O symbol must fit String(120).
    user = _user(session)
    acct = _account(session, user)
    run_uuid = "0" * 36
    symbol = "BANKNIFTY24OCT52000CE"  # 21 chars
    crid = f"sqoff_sym:{symbol}:{run_uuid}:3:1:leg0:{symbol}"
    assert len(crid) <= 120
    order = PaperOrder(
        account_id=acct.id, user_id=user.id, client_request_id=crid,
        symbol=symbol, transaction_type="SELL", order_type="MARKET",
        quantity=50, status="filled",
    )
    session.add(order)
    session.flush()
    session.refresh(order)
    assert order.client_request_id == crid  # not truncated


def test_manual_order_and_fill_allow_null_idea(session: Session) -> None:
    # idea_id NULL is a first-class case (manual orders), and settles_at
    # is nullable (only SELLs stamp T+1). Date columns hydrate as date.
    user = _user(session)
    acct = _account(session, user)
    order = PaperOrder(
        account_id=acct.id, user_id=user.id, idea_id=None, symbol="INFY",
        transaction_type="BUY", order_type="MARKET", quantity=1,
        origin_kind="manual",
    )
    session.add(order)
    session.flush()
    fill = PaperFill(
        order_id=order.id, account_id=acct.id, user_id=user.id, idea_id=None,
        symbol="INFY", transaction_type="BUY", quantity=1, fill_price=1500.0,
        gross_value=1500.0, charges=2.0, net_cashflow=-1502.0, settles_at=None,
    )
    session.add(fill)
    snap = PaperNavSnapshot(
        account_id=acct.id, user_id=user.id, as_of_date=dt.date(2026, 5, 30),
        cash_available=1, cash_settled=1, positions_mv=0, nav=1,
        realized_pnl_cum=0, unrealized_pnl=0, is_stale=False,
    )
    session.add(snap)
    session.flush()
    session.refresh(order)
    session.refresh(fill)
    session.refresh(snap)
    assert order.idea_id is None and fill.idea_id is None
    assert fill.settles_at is None
    assert isinstance(snap.as_of_date, dt.date)


def test_models_registered_in_metadata() -> None:
    expected = {
        "paper_accounts", "paper_orders", "paper_fills", "paper_positions",
        "paper_ledger", "forward_ideas", "paper_nav_snapshots",
        "paper_idea_nav_snapshots",
    }
    assert expected <= set(Base.metadata.tables)
    # keep `models` import meaningful for linters
    assert models.PaperAccount.__tablename__ == "paper_accounts"
