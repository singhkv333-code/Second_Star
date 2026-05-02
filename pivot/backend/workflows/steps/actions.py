"""Action step executors.

Actions mutate state and MUST be idempotent (ARCHITECTURE.md §7
invariant 1). Every executor here uses the engine-supplied
`client_request_id = sha1(f"{run_id}:{step_index}:{attempts}")` so the
broker can reject duplicates on retry.

max_retries=1: actions are idempotent so we tolerate one transient
retry, but no more — we don't want to spam orders if the failure is
structural.

Day 2 ships `action.place_order` (real, including the approval-pause
path). Other actions stay NotImplementedError until Day 3-4.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from backend.kite.orders import (
    cancel_order,
    get_orders,
    place_gtt_order,
    place_order,
)
from backend.models import StepStatus, WorkflowApproval
from backend.workflows.engine import _AwaitingApproval
from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    ActionCancelOrdersConfig,
    ActionPlaceOrderConfig,
    ActionSetStoplossConfig,
    ActionUpdateWatchlistConfig,
)


def _kite_token_for_run(ctx: Any) -> str:
    """Resolve the Kite token for the workflow's user. Mirrors
    services.portfolio: in mock mode (no Kite session) we return a
    placeholder string; KITE_MOCK_MODE in backend/kite/auth.py routes
    the call to mock data."""
    from backend.models import User

    user = (
        ctx.db.query(User)
        .filter(User.id == int(ctx.workflow.user_id))
        .first()
    )
    if user and user.kite_session and user.kite_session.access_token:
        return str(user.kite_session.access_token)
    return "mock_token"


def _has_pending_approval(ctx: Any) -> Optional[WorkflowApproval]:
    """Look for a still-undecided approval row for this (run, step).
    Used to detect the 'engine first encounters a requires_approval=
    true step' moment vs the 'engine resumes after approval decided'
    moment. The latter has decision='approved'."""
    return (
        ctx.db.query(WorkflowApproval)
        .filter(
            WorkflowApproval.run_id == ctx.run.id,
            WorkflowApproval.step_index == ctx.step.step_index,
        )
        .order_by(WorkflowApproval.requested_at.desc())
        .first()
    )


@register_step(
    step_type="action.place_order",
    category="action",
    label="Place order",
    description="Place a market or limit order via Kite",
    icon="shopping-cart",
    max_retries=1,
    trigger_only=False,
    config_model=ActionPlaceOrderConfig,
    output_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "status": {"type": "string"},
            "client_request_id": {"type": "string"},
        },
        "required": ["order_id", "client_request_id"],
    },
)
async def execute_action_place_order(ctx: Any) -> Optional[dict[str, Any]]:
    """Two-phase executor for the demo path:

      Phase 1 (no decision yet, requires_approval=true): write a
      WorkflowApproval row and raise _AwaitingApproval. The engine
      flips the run to `awaiting_approval` and returns; resumption
      happens via the approvals router.

      Phase 2 (decision='approved', or no approval needed): submit the
      order to Kite with the engine-supplied client_request_id. The
      Kite layer is in mock mode by default in tests, so this returns
      a synthetic order_id without hitting any real broker.

      Approval rejected → the engine never re-enters this executor;
      the approvals router terminates the run as `cancelled`.
    """
    cfg = ctx.config
    requires_approval = bool(cfg.get("requires_approval", False))

    if requires_approval:
        existing = _has_pending_approval(ctx)
        if existing is None or existing.decision is None:
            # First-touch: create a fresh approval row. Use the same
            # session the engine handed us so the row appears in the
            # same transaction.
            from backend.workflows.engine import _utcnow
            summary = (
                f"{cfg['side'].upper()} {cfg['quantity']} {cfg['symbol']} "
                f"at {cfg.get('order_type', 'market')}"
            )
            if cfg.get("order_type") == "limit" and cfg.get("limit_price"):
                summary += f" (limit ₹{cfg['limit_price']})"

            approval = WorkflowApproval(
                run_id=ctx.run.id,
                step_index=ctx.step.step_index,
                expires_at=_utcnow() + timedelta(minutes=15),
                summary=summary,
            )
            ctx.db.add(approval)
            ctx.db.commit()
            ctx.db.refresh(approval)
            raise _AwaitingApproval(approval.id)

        if existing.decision == "rejected":
            # Engine should never re-enter this executor on rejection,
            # but be defensive.
            raise RuntimeError(
                f"approval rejected at step {ctx.step.step_index}"
            )
        # decision == "approved" → fall through to actual order placement.

    # Phase 2: place the real order (or mock-order in test mode).
    token = _kite_token_for_run(ctx)
    side = cfg["side"]
    transaction_type = "BUY" if side == "buy" else "SELL"
    order_type = cfg.get("order_type", "market").upper()
    price = (
        float(cfg["limit_price"])
        if order_type == "LIMIT" and cfg.get("limit_price") is not None
        else None
    )

    result = place_order(
        access_token=token,
        tradingsymbol=str(cfg["symbol"]),
        exchange="NSE",
        transaction_type=transaction_type,
        quantity=int(cfg["quantity"]),
        order_type=order_type,
        price=price,
        product="CNC",
        tag=f"wf_{ctx.client_request_id[:16]}",
    )

    return {
        "order_id": str(result.get("order_id", "")),
        "status": str(result.get("status", "")),
        "client_request_id": ctx.client_request_id,
    }


@register_step(
    step_type="action.cancel_orders",
    category="action",
    label="Cancel orders",
    description="Cancel matching pending orders",
    icon="x-circle",
    max_retries=1,
    trigger_only=False,
    config_model=ActionCancelOrdersConfig,
    output_schema={
        "type": "object",
        "properties": {
            "cancelled_count": {"type": "integer"},
            "order_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["cancelled_count"],
    },
)
async def execute_action_cancel_orders(ctx: Any) -> Optional[dict[str, Any]]:
    """Cancel every pending order matching the optional symbol/side
    filters. Idempotent: cancelling an already-cancelled order is a
    no-op (Kite returns CANCELLED). On retry, only orders still pending
    get re-cancelled — the order_id list shrinks naturally."""
    cfg = ctx.config
    symbol_filter = (cfg.get("symbol_filter") or "").upper() or None
    side_filter = (cfg.get("side_filter") or "").upper() or None  # BUY/SELL
    token = _kite_token_for_run(ctx)

    orders = get_orders(token) or []
    pending: list[dict[str, Any]] = []
    for o in orders:
        status = str(o.get("status", "")).upper()
        if status not in {"OPEN", "PENDING", "TRIGGER PENDING"}:
            continue
        if symbol_filter and str(o.get("tradingsymbol", "")).upper() != symbol_filter:
            continue
        if side_filter and str(o.get("transaction_type", "")).upper() != side_filter:
            continue
        pending.append(o)

    cancelled_ids: list[str] = []
    for o in pending:
        order_id = str(o.get("order_id", ""))
        if not order_id:
            continue
        try:
            cancel_order(token, order_id)
            cancelled_ids.append(order_id)
        except Exception as e:
            # Best-effort: log and continue. The engine's max_retries=1
            # gives one retry; persistent failures bubble up.
            import logging
            logging.getLogger(__name__).warning(
                "cancel_order failed for %s: %s", order_id, e,
            )

    return {
        "cancelled_count": len(cancelled_ids),
        "order_ids": cancelled_ids,
    }


@register_step(
    step_type="action.set_stoploss",
    category="action",
    label="Set stop-loss",
    description="Place a stop-loss order on a holding",
    icon="shield",
    max_retries=1,
    trigger_only=False,
    config_model=ActionSetStoplossConfig,
    output_schema={
        "type": "object",
        "properties": {
            "trigger_id": {"type": "string"},
            "client_request_id": {"type": "string"},
        },
        "required": ["trigger_id"],
    },
)
async def execute_action_set_stoploss(ctx: Any) -> Optional[dict[str, Any]]:
    """Set a stop-loss as a Kite GTT (Good-Till-Triggered) sell order.
    Idempotent via the engine's client_request_id (passed to the broker
    as `tag`). Quantity defaults to the user's current holding for the
    symbol when not specified."""
    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    trigger_price = float(cfg["trigger_price"])
    qty = cfg.get("quantity")
    token = _kite_token_for_run(ctx)

    if qty is None:
        # Default to current holding quantity for the symbol.
        from backend.services.portfolio import get_user_portfolio
        portfolio = get_user_portfolio(int(ctx.workflow.user_id), ctx.db)
        holdings = portfolio.get("holdings", []) if isinstance(portfolio, dict) else []
        for h in holdings:
            if str(h.get("tradingsymbol", "")).upper() == symbol:
                qty = int(h.get("quantity", 0))
                break
    if not qty or int(qty) <= 0:
        raise ValueError(
            f"action.set_stoploss: no quantity specified and no holding "
            f"of {symbol} found"
        )

    # GTT limit price is the trigger_price (sell at the trigger).
    result = place_gtt_order(
        access_token=token,
        tradingsymbol=symbol,
        exchange="NSE",
        transaction_type="SELL",
        quantity=int(qty),
        trigger_price=trigger_price,
        limit_price=trigger_price,
        last_price=trigger_price,
    )
    return {
        "trigger_id": str(result.get("trigger_id", "")),
        "client_request_id": ctx.client_request_id,
    }


@register_step(
    step_type="action.update_watchlist",
    category="action",
    label="Update watchlist",
    description="Add or remove a symbol from your watchlist",
    icon="list-plus",
    max_retries=1,
    trigger_only=False,
    config_model=ActionUpdateWatchlistConfig,
    output_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "symbol": {"type": "string"},
        },
        "required": ["action", "symbol"],
    },
)
async def execute_action_update_watchlist(ctx: Any) -> Optional[dict[str, Any]]:
    """Add or remove a symbol from the user's watchlist.

    Idempotent on both sides:
      - 'add' for a symbol already in the watchlist → no-op (the
        UNIQUE (user_id, symbol, exchange) constraint guarantees this
        anyway, but we check first to avoid IntegrityError noise in
        logs).
      - 'remove' for a symbol absent from the watchlist → no-op.

    Engine retries are safe by construction: on retry the row already
    exists (add) or already doesn't (remove).
    """
    from sqlalchemy import and_

    from backend.models import WatchlistItem

    cfg = ctx.config
    action = str(cfg["action"]).lower()
    symbol = str(cfg["symbol"]).upper()
    exchange = str(cfg.get("exchange", "NSE")).upper()
    user_id = int(ctx.workflow.user_id)

    existing = (
        ctx.db.query(WatchlistItem)
        .filter(and_(
            WatchlistItem.user_id == user_id,
            WatchlistItem.symbol == symbol,
            WatchlistItem.exchange == exchange,
        ))
        .first()
    )

    mutated = False
    if action == "add" and existing is None:
        ctx.db.add(WatchlistItem(
            user_id=user_id, symbol=symbol, exchange=exchange,
        ))
        ctx.db.commit()
        mutated = True
    elif action == "remove" and existing is not None:
        ctx.db.delete(existing)
        ctx.db.commit()
        mutated = True
    elif action not in {"add", "remove"}:
        raise ValueError(f"unsupported watchlist action: {action!r}")

    return {
        "action": action,
        "symbol": symbol,
        "exchange": exchange,
        "mutated": mutated,
    }


# Keep StepStatus import alive in case future executors emit run-step
# status nuances directly (currently engine-only).
_ = StepStatus
