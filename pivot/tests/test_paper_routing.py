"""P2 tests: order routing (paper vs Kite) by flag + account mode.

Covers the workflow action seam (submit_order/submit_gtt with a StepContext)
and the chat seam (submit_order_for_user), the retry-stable idempotency
key, and the fallbacks to the Kite mock path.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend.models import (
    PaperAccount,
    PaperFill,
    PaperOrder,
    RunStatus,
    User,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from backend.paper import (
    get_or_create_account,
    submit_gtt,
    submit_order,
    submit_order_for_user,
)
from backend.paper.broker import PaperBroker
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


@pytest.fixture(autouse=True)
def _paper_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests are pinned paper-OFF in conftest; this module opts back IN.
    # (test_flag_off_routes_to_kite overrides this with its own setattr.)
    monkeypatch.setattr("backend.config.settings.paper_trading_enabled", True)


@pytest.fixture()
def fixed_price(monkeypatch: pytest.MonkeyPatch) -> None:
    # The broker resolves a mark via get_mark_price; pin it offline.
    monkeypatch.setattr(
        "backend.paper.broker.get_mark_price",
        lambda symbol, token="mock_token": to_money(100.0),
    )


def _user(db: Session) -> User:
    u = User(email="p2@example.com", hashed_password="x")
    db.add(u)
    db.flush()
    return u


def _ctx(db: Session, user_id: int, *, step_index: int = 2):
    """Real Workflow/Step/Run rows so the order's attribution FKs resolve
    under FK enforcement. Returns a StepContext-shaped stub over them."""
    wf = Workflow(user_id=user_id, name="t", status=WorkflowStatus.active)
    db.add(wf)
    db.flush()
    step = WorkflowStep(
        workflow_id=wf.id, step_index=step_index,
        step_type="action.place_order", config={},
    )
    run = WorkflowRun(
        workflow_id=wf.id, workflow_version=1, triggered_by="manual",
        status=RunStatus.running,
    )
    db.add_all([step, run])
    db.flush()
    return SimpleNamespace(
        db=db, workflow=wf, run=run, step=step,
        attempts=1, client_request_id="sha-ignored",
    )


# ── workflow routing ─────────────────────────────────────────────────────

def test_workflow_market_order_routes_to_paper(
    session: Session, fixed_price: None,
) -> None:
    user = _user(session)
    ctx = _ctx(session, user.id)
    res = submit_order(
        ctx, access_token="mock_token", tradingsymbol="RELIANCE",
        exchange="NSE", transaction_type="BUY", quantity=10,
        order_type="MARKET", price=None, product="CNC",
        tag="wf_ignored",
    )
    assert res["status"] == "COMPLETE" and res["paper_status"] == "filled"
    order = session.query(PaperOrder).one()
    assert order.origin_kind == "workflow"
    assert order.workflow_id == ctx.workflow.id
    assert order.workflow_run_id == ctx.run.id
    # retry-stable crid: run:step:side:symbol (NO attempts)
    assert order.client_request_id == f"wf:{ctx.run.id}:{ctx.step.step_index}:BUY:RELIANCE"
    assert session.query(PaperFill).count() == 1
    assert session.query(PaperAccount).filter_by(user_id=user.id).one()


def test_workflow_retry_is_idempotent(
    session: Session, fixed_price: None,
) -> None:
    user = _user(session)
    # Two firings with the SAME ctx (same run/step) — i.e. an engine retry.
    # The crid excludes attempts, so the second dedups vs double-filling.
    ctx = _ctx(session, user.id)
    r1 = submit_order(
        ctx, tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="MARKET",
    )
    r2 = submit_order(
        ctx, tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="MARKET",
    )
    assert r2["order_id"] == r1["order_id"]
    assert r2["idempotent_replay"] is True
    assert session.query(PaperOrder).count() == 1
    assert session.query(PaperFill).count() == 1


def test_basket_legs_get_distinct_crids(
    session: Session, fixed_price: None,
) -> None:
    user = _user(session)
    ctx = _ctx(session, user.id)
    for sym in ("RELIANCE", "INFY", "TCS"):
        submit_order(
            ctx, tradingsymbol=sym, transaction_type="BUY", quantity=1,
            order_type="MARKET",
        )
    crids = {o.client_request_id for o in session.query(PaperOrder).all()}
    base = f"wf:{ctx.run.id}:{ctx.step.step_index}:BUY"
    assert crids == {f"{base}:RELIANCE", f"{base}:INFY", f"{base}:TCS"}
    assert session.query(PaperFill).count() == 3


def test_workflow_gtt_routes_to_paper(session: Session) -> None:
    user = _user(session)
    ctx = _ctx(session, user.id)
    res = submit_gtt(
        ctx, access_token="mock_token", tradingsymbol="RELIANCE",
        exchange="NSE", transaction_type="SELL", quantity=10,
        trigger_price=120.0, limit_price=118.0, last_price=100.0,
    )
    assert res["trigger_id"] == res["order_id"]
    assert res["status"] == "active"
    order = session.query(PaperOrder).filter_by(order_type="GTT").one()
    assert order.workflow_run_id == ctx.run.id
    assert order.limit_price == 118.0
    assert order.client_request_id == f"wf:{ctx.run.id}:{ctx.step.step_index}:GTT:RELIANCE"


# ── fallbacks to Kite ────────────────────────────────────────────────────

def test_flag_off_routes_to_kite(
    session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.config.settings.paper_trading_enabled", False)
    user = _user(session)
    ctx = _ctx(session, user.id)
    res = submit_order(
        ctx, access_token="mock_token", tradingsymbol="RELIANCE",
        transaction_type="BUY", quantity=10, order_type="MARKET",
    )
    assert str(res["order_id"]).startswith("MOCK")  # kite mock path
    assert session.query(PaperOrder).count() == 0
    assert session.query(PaperAccount).count() == 0  # no account side-effect


def test_account_mode_live_routes_to_kite(
    session: Session, fixed_price: None,
) -> None:
    user = _user(session)
    acct = get_or_create_account(session, user.id)
    acct.mode = "live"
    session.flush()
    ctx = _ctx(session, user.id)
    res = submit_order(
        ctx, access_token="mock_token", tradingsymbol="RELIANCE",
        transaction_type="BUY", quantity=10, order_type="MARKET",
    )
    assert str(res["order_id"]).startswith("MOCK")
    assert session.query(PaperOrder).count() == 0  # nothing landed in paper


# ── chat routing ─────────────────────────────────────────────────────────

def test_chat_confirm_routes_to_paper(
    session: Session, fixed_price: None,
) -> None:
    user = _user(session)
    res = submit_order_for_user(
        session, user.id, access_token="mock_token",
        tradingsymbol="RELIANCE", exchange="NSE", transaction_type="BUY",
        quantity=5, order_type="MARKET", price=None, product="CNC",
        client_request_id="chat-confirm:prev_42", source="chat",
    )
    assert res["status"] == "COMPLETE"
    order = session.query(PaperOrder).one()
    assert order.origin_kind == "chat"
    assert order.client_request_id == "chat-confirm:prev_42"
    # re-confirming the same preview is idempotent (no double fill)
    res2 = submit_order_for_user(
        session, user.id, access_token="mock_token",
        tradingsymbol="RELIANCE", exchange="NSE", transaction_type="BUY",
        quantity=5, order_type="MARKET", client_request_id="chat-confirm:prev_42",
    )
    assert res2["idempotent_replay"] is True
    assert session.query(PaperFill).count() == 1


# ── end-to-end: real engine executor -> paper fill ───────────────────────

def test_engine_executor_fills_paper(
    session: Session, fixed_price: None,
) -> None:
    # Drives the ACTUAL action.place_order executor (not just submit_order)
    # with the engine's real _ExecutorContext, proving the seam wires the
    # executor -> routing -> PaperBroker end to end.
    from backend.workflows.engine import _ExecutorContext
    from backend.workflows.steps.actions import execute_action_place_order

    user = _user(session)
    stub = _ctx(session, user.id)
    exec_ctx = _ExecutorContext(
        run=stub.run,
        step=stub.step,
        workflow=stub.workflow,
        config={
            "symbol": "RELIANCE", "side": "buy",
            "quantity": 10, "order_type": "market",
        },
        attempts=1,
        client_request_id="engine-crid",
        db=session,
    )
    result = asyncio.run(execute_action_place_order(exec_ctx))
    assert result["order_id"]
    order = session.query(PaperOrder).one()
    assert order.symbol == "RELIANCE"
    assert order.origin_kind == "workflow"
    assert order.workflow_run_id == stub.run.id
    assert order.client_request_id == f"wf:{stub.run.id}:{stub.step.step_index}:BUY:RELIANCE"
    assert session.query(PaperFill).count() == 1


# ── P2 review fixes: squareoff guard, SL paper sizing, leg-key dedup ──────

def _exec_ctx(stub, config: dict):
    from backend.workflows.engine import _ExecutorContext
    return _ExecutorContext(
        run=stub.run, step=stub.step, workflow=stub.workflow, config=config,
        attempts=1, client_request_id="x", db=stub.db,
    )


def test_squareoff_empty_when_no_paper_positions(session: Session) -> None:
    # P4: squareoff now reads the PAPER book. With no positions it's a clean
    # no-op (no phantom kite-mock fills).
    from backend.workflows.steps.actions import execute_action_squareoff_all

    user = _user(session)
    get_or_create_account(session, user.id)  # mode=paper
    out = asyncio.run(execute_action_squareoff_all(_exec_ctx(_ctx(session, user.id), {})))
    assert out["n_filled"] == 0
    assert out["orders"] == []
    assert out["scope"] == "paper"


def test_squareoff_flattens_paper_position(
    session: Session, fixed_price: None,
) -> None:
    # P4 re-wire: squareoff reads the paper book and SELLs the open lot
    # through the paper broker (fills into the same book).
    from backend.models import PaperFill, PaperPosition
    from backend.workflows.steps.actions import execute_action_squareoff_all

    user = _user(session)
    PaperBroker(session, user.id, price_fn=lambda _s: to_money(100)).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="MARKET",
    )
    out = asyncio.run(execute_action_squareoff_all(_exec_ctx(_ctx(session, user.id), {})))
    assert out["n_filled"] == 1
    pos = session.query(PaperPosition).filter_by(symbol="RELIANCE").one()
    assert pos.quantity == 0  # flattened
    assert session.query(PaperFill).filter_by(transaction_type="SELL").count() == 1


def test_cancel_orders_cancels_paper_resting(
    session: Session, fixed_price: None,
) -> None:
    # P4 re-wire: cancel_orders cancels paper resting orders + releases the
    # reserved cash.
    from backend.workflows.steps.actions import execute_action_cancel_orders

    user = _user(session)
    PaperBroker(session, user.id, price_fn=lambda _s: to_money(100)).place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
        order_type="LIMIT", price=95.0,
    )
    assert session.query(PaperOrder).filter_by(status="resting").count() == 1
    out = asyncio.run(execute_action_cancel_orders(_exec_ctx(_ctx(session, user.id), {})))
    assert out["cancelled_count"] == 1
    assert session.query(PaperOrder).filter_by(status="cancelled").count() == 1
    acct = session.query(PaperAccount).filter_by(user_id=user.id).one()
    assert acct.cash_reserved == to_money(0)  # reserve released on cancel


def test_set_stoploss_sizes_from_paper_position(
    session: Session, fixed_price: None,
) -> None:
    from backend.models import PaperPosition
    from backend.workflows.steps.actions import execute_action_set_stoploss

    user = _user(session)
    acct = get_or_create_account(session, user.id)
    # paper holds 17 INFY (a kite mock holding would be a different qty)
    session.add(PaperPosition(
        account_id=acct.id, user_id=user.id, symbol="INFY", quantity=17,
        avg_cost=to_money(100), realized_pnl=to_money(0),
    ))
    session.flush()
    out = asyncio.run(execute_action_set_stoploss(
        _exec_ctx(_ctx(session, user.id), {"symbol": "INFY", "trigger_price": 90.0})
    ))
    assert out["trigger_id"]
    gtt = session.query(PaperOrder).filter_by(order_type="GTT").one()
    assert gtt.quantity == 17  # sized from the PAPER position, not kite


def test_same_symbol_legs_distinct_with_leg_key(
    session: Session, fixed_price: None,
) -> None:
    user = _user(session)
    ctx = _ctx(session, user.id)
    # Two legs of the same symbol+side in one step, distinguished by leg_key
    # -> both fill, aggregating into one position (full notional deployed).
    submit_order(ctx, tradingsymbol="RELIANCE", transaction_type="BUY",
                 quantity=5, order_type="MARKET", leg_key="0")
    submit_order(ctx, tradingsymbol="RELIANCE", transaction_type="BUY",
                 quantity=5, order_type="MARKET", leg_key="1")
    assert session.query(PaperOrder).count() == 2
    assert session.query(PaperFill).count() == 2
    from backend.models import PaperPosition
    assert session.query(PaperPosition).filter_by(symbol="RELIANCE").one().quantity == 10


def test_same_symbol_legs_collapse_without_leg_key(
    session: Session, fixed_price: None,
) -> None:
    # Documents WHY the allocate loops pass leg_key: without it, a repeated
    # symbol+side in one step collapses to one order (silent under-fill).
    user = _user(session)
    ctx = _ctx(session, user.id)
    submit_order(ctx, tradingsymbol="RELIANCE", transaction_type="BUY",
                 quantity=5, order_type="MARKET")
    submit_order(ctx, tradingsymbol="RELIANCE", transaction_type="BUY",
                 quantity=5, order_type="MARKET")
    assert session.query(PaperOrder).count() == 1  # collapsed (same crid)


# ── HTTP: /orders/gtt must COMMIT the paper GTT (the critic's blocker) ────

def test_http_gtt_commits_paper_order(client, auth_headers, db, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.paper.broker.get_mark_price",
        lambda symbol, token="mock_token": to_money(100.0),
    )
    r = client.post("/orders/gtt", headers=auth_headers, json={
        "tradingsymbol": "RELIANCE", "transaction_type": "SELL",
        "quantity": 10, "trigger_price": 120.0, "limit_price": 118.0,
        "last_price": 100.0, "is_confirmed": True,
    })
    assert r.status_code == 200, r.text
    assert r.json().get("trigger_id")
    # The bug: the endpoint never committed, so the paper GTT was rolled
    # back on request close. Assert it actually persisted.
    assert db.query(PaperOrder).filter_by(order_type="GTT").count() == 1


# ── P4 review-fix regressions ─────────────────────────────────────────────

def test_buying_power_not_double_counted_with_reserve(
    session: Session, fixed_price: None,
) -> None:
    # buying_power must equal cash_available (the reserve already left it);
    # the old `available - reserved` double-counted and went negative, AND
    # the same flawed gate rejected legit orders.
    from backend.paper.portfolio import account_summary

    user = _user(session)
    b = PaperBroker(session, user.id, price_fn=lambda _s: to_money(100))
    # resting LIMIT BUY reserves ~70k of the 150k book
    b.place_order(tradingsymbol="RELIANCE", transaction_type="BUY",
                  quantity=700, order_type="LIMIT", price=100.0)
    acct = session.query(PaperAccount).filter_by(user_id=user.id).one()
    assert acct.cash_reserved > to_money(70000)
    summary = account_summary(session, user.id)
    assert summary["buying_power"] == float(acct.cash_available)
    assert summary["buying_power"] > 0  # not negative
    # a MARKET buy that fits within the free cash is ACCEPTED (was rejected)
    res = b.place_order(tradingsymbol="INFY", transaction_type="BUY",
                        quantity=500, order_type="MARKET")
    assert res["status"] == "COMPLETE"


def test_squareoff_cancels_orphaned_protective_sell(
    session: Session, fixed_price: None,
) -> None:
    # After flattening, the resting SELL stop/GTT that guarded the lot must
    # be cancelled so it can't re-arm against a future position.
    from backend.models import PaperPosition
    from backend.workflows.steps.actions import execute_action_squareoff_all

    user = _user(session)
    b = PaperBroker(session, user.id, price_fn=lambda _s: to_money(100))
    b.place_order(tradingsymbol="RELIANCE", transaction_type="BUY",
                  quantity=10, order_type="MARKET")
    b.place_gtt_order(tradingsymbol="RELIANCE", transaction_type="SELL",
                      quantity=10, trigger_price=90.0, limit_price=90.0,
                      last_price=100.0)
    assert session.query(PaperOrder).filter_by(
        order_type="GTT", status="resting").count() == 1

    out = asyncio.run(execute_action_squareoff_all(_exec_ctx(_ctx(session, user.id), {})))
    assert session.query(PaperPosition).filter_by(symbol="RELIANCE").one().quantity == 0
    assert session.query(PaperOrder).filter_by(
        order_type="GTT", status="cancelled").count() == 1
    assert out["cancelled_guards"]  # the orphaned SELL was cancelled
