"""Demo seeds for fresh users.

Runs once on first registration (via `auth/router.py::register`). Drops
3 ready-to-show workflows in `active` state (these are exactly the
"Pre-Built strategies" tiles shown on the home screen — HomeTab.tsx
matches them by name), ~6 historical `TradeLog` rows, and a ₹5,00,000
paper account (cash only — no starter positions). Idempotent: if the
user already has any workflows or trade logs, the whole thing is a
no-op (the paper account step has its own independent idempotency
check via `get_or_create_account`, since it runs after the commit
below).

What we seed (and why):
  - **RELIANCE 3:15 PM weekday buy** — the canonical demo workflow
    referenced throughout chat tests + docs (5 steps). Lets a new
    user immediately see what an agent looks like in the editor.
    3:15 PM IST, not 3:55 — NSE closes at 3:30 PM IST, so 3:55 was
    past close and would never actually fill.
  - **INFY weekly dip-buy** — 2 steps (schedule + action). Compact
    example, contrasts with the 5-step one.
  - **TCS monthly SIP** — 2 steps. Reinforces the SIP automation
    use case.
  - **6 TradeLog rows** — mix of BUY/SELL × MARKET/LIMIT/GTT,
    backdated 1-30 days, all `status="registered"` and
    `source="demo-seed"` so the order history tab isn't empty.
  - **Paper account seeded at ₹5,00,000** — cash only. A fresh signup
    starts with the full ₹5L as free cash and an EMPTY Portfolio; we no
    longer buy starter holdings, because a brand-new user shouldn't land
    with positions they never opened. (The frontend's default watchlist
    seed is separate — untouched here.)

Logging only — failures here never block registration.
"""
from __future__ import annotations

import logging
import random
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from backend.models import (
    TradeLog,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from backend.paper.accounts import get_or_create_account
from backend.utils.time_utils import now_ist

logger = logging.getLogger(__name__)

# New-user paper seed capital. Deliberately distinct from (and larger
# than) the platform default `settings.paper_seed_capital` (₹1,50,000,
# used when a user reaches paper endpoints without ever registering,
# e.g. pre-existing rows) — a fresh signup specifically gets ₹5L.
NEW_USER_PAPER_CAPITAL = 500_000

# Workflow recipes. Each step's `config` matches the Pydantic schema in
# backend/workflows/schemas.py — validated when the engine loads the
# workflow, but pre-validated by hand here.

_DEMO_WORKFLOWS: list[dict[str, Any]] = [
    {
        "name": "RELIANCE 3:15 PM weekday buy",
        "description": "Every weekday at 3:15 PM IST, buy 10 RELIANCE if buying power > ₹50,000.",
        "steps": [
            {
                "step_type": "trigger.schedule",
                "label": "Every weekday at 3:15 PM IST",
                "config": {"cron": "15 15 * * 1-5", "timezone": "Asia/Kolkata"},
            },
            {
                "step_type": "fetch.portfolio",
                "label": "Get portfolio",
                "config": {},
            },
            {
                "step_type": "condition.numeric",
                "label": "Buying power > ₹50,000",
                "config": {
                    "left": {"ref": "portfolio.cash"},
                    "operator": ">",
                    "right": 50000,
                },
            },
            {
                "step_type": "action.place_order",
                "label": "Buy 10 RELIANCE",
                "config": {
                    "symbol": "RELIANCE",
                    "side": "buy",
                    "quantity": 10,
                    "order_type": "market",
                    "requires_approval": False,
                },
            },
            {
                "step_type": "notify.message",
                "label": "Email confirmation",
                "config": {
                    "channel": "email",
                    "template": "order_confirmation",
                    "vars": {"symbol": "RELIANCE"},
                },
            },
        ],
    },
    {
        "name": "INFY weekly dip-buy",
        "description": "Every Monday morning, buy 5 INFY at limit if price < ₹1,400.",
        "steps": [
            {
                "step_type": "trigger.schedule",
                "label": "Every Monday at 9:30 AM IST",
                "config": {"cron": "30 9 * * 1", "timezone": "Asia/Kolkata"},
            },
            {
                "step_type": "action.place_order",
                "label": "Buy 5 INFY at ₹1,400",
                "config": {
                    "symbol": "INFY",
                    "side": "buy",
                    "quantity": 5,
                    "order_type": "limit",
                    "limit_price": 1400,
                    "requires_approval": False,
                },
            },
        ],
    },
    {
        "name": "TCS monthly SIP",
        "description": "On the 1st of every month, buy 2 TCS at market.",
        "steps": [
            {
                "step_type": "trigger.schedule",
                "label": "1st of every month at 9:30 AM IST",
                "config": {"cron": "30 9 1 * *", "timezone": "Asia/Kolkata"},
            },
            {
                "step_type": "action.place_order",
                "label": "Buy 2 TCS",
                "config": {
                    "symbol": "TCS",
                    "side": "buy",
                    "quantity": 2,
                    "order_type": "market",
                    "requires_approval": False,
                },
            },
        ],
    },
]


# TradeLog recipes — backdated relative to "now" so the history tab
# shows a believable spread without time-bomb dates that go stale.
_DEMO_TRADES: list[dict[str, Any]] = [
    {"symbol": "RELIANCE", "transaction_type": "BUY",  "order_type": "MARKET", "quantity": 10, "price": 2487.50, "trigger_price": None, "days_ago": 1},
    {"symbol": "INFY",     "transaction_type": "BUY",  "order_type": "LIMIT",  "quantity": 5,  "price": 1395.00, "trigger_price": None, "days_ago": 3},
    {"symbol": "HDFCBANK", "transaction_type": "BUY",  "order_type": "GTT",    "quantity": 3,  "price": 1500.00, "trigger_price": 1480.00, "days_ago": 7},
    {"symbol": "TCS",      "transaction_type": "BUY",  "order_type": "MARKET", "quantity": 2,  "price": 4115.30, "trigger_price": None, "days_ago": 12},
    {"symbol": "WIPRO",    "transaction_type": "SELL", "order_type": "LIMIT",  "quantity": 12, "price": 480.00,  "trigger_price": None, "days_ago": 18},
    {"symbol": "ITC",      "transaction_type": "BUY",  "order_type": "MARKET", "quantity": 25, "price": 442.10,  "trigger_price": None, "days_ago": 28},
]


def seed_demo_data(db: Session, user_id: int) -> dict[str, int]:
    """Seed demo workflows + trade logs for a freshly-registered user.

    Idempotent: if the user already has any workflow or trade_log row,
    we skip everything. Returns counts so the caller can log how much
    was created (useful in tests).
    """
    existing_workflows = (
        db.query(Workflow).filter(Workflow.user_id == user_id).count()
    )
    existing_trades = (
        db.query(TradeLog).filter(TradeLog.user_id == user_id).count()
    )
    if existing_workflows or existing_trades:
        return {"workflows": 0, "trades": 0, "skipped": True}

    workflows_created = _seed_workflows(db, user_id)
    trades_created = _seed_trade_logs(db, user_id)

    try:
        db.commit()
    except Exception as e:
        # Don't let seeding failures break registration. Roll back the
        # demo data and return 0s; the user gets an empty shell, which
        # is degraded but not broken.
        db.rollback()
        logger.warning("Demo seed commit failed for user %s: %s", user_id, e)
        return {"workflows": 0, "trades": 0, "skipped": False, "error": str(e)[:200]}

    # Paper account + starter holdings: a separate best-effort step,
    # committed independently so a failure here never rolls back the
    # workflows/trades that already landed above.
    paper_result = _seed_paper_account(db, user_id)

    return {
        "workflows": workflows_created,
        "trades": trades_created,
        "skipped": False,
        **paper_result,
    }


def _seed_workflows(db: Session, user_id: int) -> int:
    count = 0
    for recipe in _DEMO_WORKFLOWS:
        wf = Workflow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=recipe["name"],
            description=recipe["description"],
            # Seeded starter agents land as drafts, never live. A brand-new
            # account must not have agents running against the market before
            # the user has looked at them, let alone activated them.
            status=WorkflowStatus.draft,
            activated_at=None,
            single_instance=True,
            version=1,
        )
        db.add(wf)
        db.flush()                                # need wf.id for steps
        for idx, step in enumerate(recipe["steps"]):
            db.add(WorkflowStep(
                id=str(uuid.uuid4()),
                workflow_id=wf.id,
                step_index=idx,
                step_type=step["step_type"],
                label=step.get("label"),
                config=step["config"],
            ))
        count += 1
    return count


def _seed_trade_logs(db: Session, user_id: int) -> int:
    """Insert TradeLog rows backdated relative to today.

    `placed_at` defaults to CURRENT_TIMESTAMP via the column default —
    we override with explicit IST-aware datetimes so the history tab
    shows a believable time spread immediately. Random microseconds
    to keep ordering stable across rows.
    """
    now = now_ist()
    rng = random.Random(user_id)                  # deterministic per user
    count = 0
    for trade in _DEMO_TRADES:
        placed_at = now - timedelta(
            days=trade["days_ago"],
            hours=rng.randint(0, 6),
            minutes=rng.randint(0, 59),
        )
        db.add(TradeLog(
            user_id=user_id,
            kite_order_id=None,                   # never executed via broker
            symbol=trade["symbol"],
            exchange="NSE",
            transaction_type=trade["transaction_type"],
            order_type=trade["order_type"],
            quantity=trade["quantity"],
            price=trade["price"],
            trigger_price=trade["trigger_price"],
            status="registered",
            source="demo-seed",
            placed_at=placed_at,
        ))
        count += 1
    return count


def _seed_paper_account(db: Session, user_id: int) -> dict[str, Any]:
    """Seed a ₹5,00,000 paper account for a new user — cash only.

    A fresh signup starts with the full ₹5L as free cash and an EMPTY
    Portfolio; we intentionally do NOT buy starter holdings (a new user
    shouldn't land with positions they never opened). Idempotent:
    `get_or_create_account` no-ops (and ignores `starting_capital`) if
    the user already has a paper account. Runs in its own commit so a
    failure here can't roll back the workflows/trades already seeded.
    """
    try:
        get_or_create_account(
            db, user_id, starting_capital=NEW_USER_PAPER_CAPITAL,
        )
        db.commit()
        return {"paper_seeded": True, "holdings": 0}
    except Exception as e:
        db.rollback()
        logger.warning("Paper account seed failed for user %s: %s", user_id, e)
        return {"paper_seeded": False, "holdings": 0}
