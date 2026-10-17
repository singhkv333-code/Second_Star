"""Tests for action.update_watchlist + WatchlistItem model.

Covers:
  - add a new symbol → row created, mutated=True
  - add an already-present symbol → no duplicate, mutated=False
  - remove an existing symbol → row deleted, mutated=True
  - remove an absent symbol → no-op, mutated=False
  - unsupported action raises ValueError
  - two users' watchlists are isolated
  - UNIQUE (user_id, symbol, exchange) enforced
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import User, WatchlistItem
from backend.workflows.steps.actions import execute_action_update_watchlist


class _Ctx:
    """Minimal ctx — only user_id (via workflow) and config + db."""
    def __init__(self, db: Session, user_id: int, config: dict[str, Any]) -> None:
        self.db = db
        self.config = config
        self.workflow = type("W", (), {"user_id": user_id})()


def _make_user(db: Session, email: str = "wl@example.com") -> User:
    u = User(email=email, hashed_password="x", full_name="Watchlist Test")
    db.add(u)
    db.flush()
    return u


@pytest.mark.asyncio
async def test_add_new_symbol(workflow_db: Session) -> None:
    user = _make_user(workflow_db)
    out = await execute_action_update_watchlist(_Ctx(
        workflow_db, user.id, {"action": "add", "symbol": "INFY"},
    ))
    assert out == {
        "action": "add", "symbol": "INFY", "exchange": "NSE", "mutated": True,
    }
    rows = workflow_db.query(WatchlistItem).filter_by(user_id=user.id).all()
    assert len(rows) == 1
    assert rows[0].symbol == "INFY"
    assert rows[0].exchange == "NSE"


@pytest.mark.asyncio
async def test_add_existing_symbol_is_noop(workflow_db: Session) -> None:
    user = _make_user(workflow_db, "wl2@example.com")
    workflow_db.add(WatchlistItem(user_id=user.id, symbol="TCS", exchange="NSE"))
    workflow_db.flush()
    out = await execute_action_update_watchlist(_Ctx(
        workflow_db, user.id, {"action": "add", "symbol": "TCS"},
    ))
    assert out["mutated"] is False
    # Still exactly 1 row (no duplicate).
    rows = workflow_db.query(WatchlistItem).filter_by(user_id=user.id).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_remove_existing_symbol(workflow_db: Session) -> None:
    user = _make_user(workflow_db, "wl3@example.com")
    workflow_db.add(WatchlistItem(user_id=user.id, symbol="HDFC", exchange="NSE"))
    workflow_db.flush()
    out = await execute_action_update_watchlist(_Ctx(
        workflow_db, user.id, {"action": "remove", "symbol": "HDFC"},
    ))
    assert out["mutated"] is True
    rows = workflow_db.query(WatchlistItem).filter_by(user_id=user.id).all()
    assert rows == []


@pytest.mark.asyncio
async def test_remove_absent_symbol_is_noop(workflow_db: Session) -> None:
    user = _make_user(workflow_db, "wl4@example.com")
    out = await execute_action_update_watchlist(_Ctx(
        workflow_db, user.id, {"action": "remove", "symbol": "RELIANCE"},
    ))
    assert out["mutated"] is False


@pytest.mark.asyncio
async def test_unsupported_action_raises(workflow_db: Session) -> None:
    user = _make_user(workflow_db, "wl5@example.com")
    with pytest.raises(ValueError, match="unsupported watchlist action"):
        await execute_action_update_watchlist(_Ctx(
            workflow_db, user.id, {"action": "toggle", "symbol": "INFY"},
        ))


@pytest.mark.asyncio
async def test_two_users_isolated(workflow_db: Session) -> None:
    """User A's watchlist must not affect user B."""
    user_a = _make_user(workflow_db, "a@example.com")
    user_b = _make_user(workflow_db, "b@example.com")
    await execute_action_update_watchlist(_Ctx(
        workflow_db, user_a.id, {"action": "add", "symbol": "INFY"},
    ))
    # B's add must succeed even though A also has INFY.
    out = await execute_action_update_watchlist(_Ctx(
        workflow_db, user_b.id, {"action": "add", "symbol": "INFY"},
    ))
    assert out["mutated"] is True

    a_rows = workflow_db.query(WatchlistItem).filter_by(user_id=user_a.id).all()
    b_rows = workflow_db.query(WatchlistItem).filter_by(user_id=user_b.id).all()
    assert len(a_rows) == 1 and len(b_rows) == 1


def test_unique_constraint_at_db_level(workflow_db: Session) -> None:
    """Direct double-insert (bypassing the executor) must IntegrityError."""
    user = _make_user(workflow_db, "uq@example.com")
    workflow_db.add(WatchlistItem(user_id=user.id, symbol="X", exchange="NSE"))
    workflow_db.flush()
    workflow_db.add(WatchlistItem(user_id=user.id, symbol="X", exchange="NSE"))
    with pytest.raises(IntegrityError):
        workflow_db.flush()
    workflow_db.rollback()


@pytest.mark.asyncio
async def test_add_with_explicit_exchange(workflow_db: Session) -> None:
    """exchange config defaults to NSE; explicit BSE works too."""
    user = _make_user(workflow_db, "bse@example.com")
    out = await execute_action_update_watchlist(_Ctx(
        workflow_db, user.id,
        {"action": "add", "symbol": "INFY", "exchange": "BSE"},
    ))
    assert out["exchange"] == "BSE"
    rows = workflow_db.query(WatchlistItem).filter_by(user_id=user.id).all()
    assert len(rows) == 1
    assert rows[0].exchange == "BSE"
