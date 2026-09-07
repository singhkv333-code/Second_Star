"""Tests for demo seeding on first registration.

Confirms a fresh user lands on a populated Agents tab + Portfolio order
history, and that re-running the seeder is a no-op (idempotent).

Conftest disables seeding for the rest of the suite via
DEMO_SEED_ON_REGISTER=0; this file opts back in at module level so the
seeder actually runs during these tests.
"""
import os
import uuid

import pytest

from backend.models import PaperAccount, PaperPosition, TradeLog, Workflow, WorkflowStatus
from backend.services.demo_seeder import _seed_paper_account, seed_demo_data


@pytest.fixture(autouse=True)
def _enable_seeder(monkeypatch):
    """Re-enable the demo seeder for this test module only."""
    monkeypatch.setenv("DEMO_SEED_ON_REGISTER", "1")
    yield


def test_register_seeds_demo_workflows_and_trades(client, db):
    email = f"u_{uuid.uuid4().hex[:8]}@pivot.com"
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "full_name": "Demo"},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["user_id"]

    workflows = (
        db.query(Workflow).filter(Workflow.user_id == user_id).all()
    )
    assert len(workflows) == 3
    names = {wf.name for wf in workflows}
    assert "RELIANCE 3:15 PM weekday buy" in names
    assert "INFY weekly dip-buy" in names
    assert "TCS monthly SIP" in names
    # All three should be activated
    assert all(wf.status == WorkflowStatus.active for wf in workflows)
    assert all(wf.activated_at is not None for wf in workflows)

    trades = (
        db.query(TradeLog).filter(TradeLog.user_id == user_id).all()
    )
    assert len(trades) == 6
    assert all(t.status == "registered" for t in trades)
    assert all(t.source == "demo-seed" for t in trades)
    assert all(t.kite_order_id is None for t in trades)


def test_seed_workflow_steps_have_valid_step_types(client, db):
    email = f"u_{uuid.uuid4().hex[:8]}@pivot.com"
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "full_name": "Demo"},
    )
    user_id = r.json()["user_id"]
    workflows = (
        db.query(Workflow).filter(Workflow.user_id == user_id).all()
    )
    canonical = next(
        wf for wf in workflows if wf.name == "RELIANCE 3:15 PM weekday buy"
    )
    step_types = [s.step_type for s in sorted(canonical.steps, key=lambda x: x.step_index)]
    assert step_types == [
        "trigger.schedule",
        "fetch.portfolio",
        "condition.numeric",
        "action.place_order",
        "notify.message",
    ]


def test_demo_seeder_is_idempotent(client, db):
    """Calling seed_demo_data twice for the same user is a no-op."""
    email = f"u_{uuid.uuid4().hex[:8]}@pivot.com"
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "full_name": "Demo"},
    )
    user_id = r.json()["user_id"]

    initial_workflows = (
        db.query(Workflow).filter(Workflow.user_id == user_id).count()
    )
    initial_trades = (
        db.query(TradeLog).filter(TradeLog.user_id == user_id).count()
    )

    # Run the seeder again — should skip.
    second = seed_demo_data(db, user_id)
    assert second["skipped"] is True
    assert second["workflows"] == 0
    assert second["trades"] == 0

    assert (
        db.query(Workflow).filter(Workflow.user_id == user_id).count()
        == initial_workflows
    )
    assert (
        db.query(TradeLog).filter(TradeLog.user_id == user_id).count()
        == initial_trades
    )


def test_seeded_trades_visible_in_order_history(client, auth_headers):
    """The seeded TradeLog rows should appear on GET /orders/history."""
    r = client.get("/orders/history", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 6
    symbols = {row["symbol"] for row in rows}
    # At least the demo set should be present
    assert {"RELIANCE", "INFY", "HDFCBANK", "TCS"}.issubset(symbols)


def test_seeded_workflows_visible_via_workflows_api(client, auth_headers):
    """Seeded workflows should appear on GET /api/workflows."""
    r = client.get("/api/workflows", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    items = body.get("items", body)
    names = {item["name"] for item in items}
    assert "RELIANCE 3:15 PM weekday buy" in names
    assert "INFY weekly dip-buy" in names
    assert "TCS monthly SIP" in names


def test_register_seeds_paper_account_and_holdings(client, db):
    email = f"u_{uuid.uuid4().hex[:8]}@pivot.com"
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "full_name": "Demo"},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["user_id"]

    account = db.query(PaperAccount).filter(PaperAccount.user_id == user_id).first()
    assert account is not None
    assert float(account.starting_capital) == 500000.0
    assert float(account.cash_available) < 500000.0  # some spent on holdings

    positions = (
        db.query(PaperPosition).filter(PaperPosition.account_id == account.id).all()
    )
    assert len(positions) == 6
    symbols = {p.symbol for p in positions}
    assert symbols == {"HDFCBANK", "TCS", "RELIANCE", "INFY", "ITC", "SBIN"}
    assert all(p.quantity > 0 for p in positions)


def test_seed_paper_account_is_idempotent(client, db):
    email = f"u_{uuid.uuid4().hex[:8]}@pivot.com"
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "full_name": "Demo"},
    )
    user_id = r.json()["user_id"]

    account = db.query(PaperAccount).filter(PaperAccount.user_id == user_id).first()
    initial_positions = (
        db.query(PaperPosition).filter(PaperPosition.account_id == account.id).count()
    )
    initial_cash = account.cash_available

    result = _seed_paper_account(db, user_id)
    assert result["paper_seeded"] is True
    assert result["holdings"] == 0  # already seeded, nothing new bought

    db.refresh(account)
    assert (
        db.query(PaperPosition).filter(PaperPosition.account_id == account.id).count()
        == initial_positions
    )
    assert account.cash_available == initial_cash


def test_login_does_not_re_seed(client, db):
    """Only registration seeds. Login is unchanged."""
    email = f"u_{uuid.uuid4().hex[:8]}@pivot.com"
    client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "full_name": "Demo"},
    )
    # Capture state after registration
    user = (
        db.query(__import__("backend.models", fromlist=["User"]).User)
        .filter_by(email=email).first()
    )
    pre_workflows = (
        db.query(Workflow).filter(Workflow.user_id == user.id).count()
    )

    # Login again
    r = client.post(
        "/auth/login",
        json={"email": email, "password": "password123"},
    )
    assert r.status_code == 200

    post_workflows = (
        db.query(Workflow).filter(Workflow.user_id == user.id).count()
    )
    assert post_workflows == pre_workflows
