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
    ActionAllocateNotionalConfig,
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


def _resolve_entry_price_for_sl(ctx: Any, symbol: str, token: str) -> float:
    """Find the entry fill price for a percentage-based stop-loss.

    Walks the run context for the most recent action.place_order whose
    output covers ``symbol`` and returns its ``executed_price``. If no
    prior fill is found in this run (e.g. SL is being placed standalone
    on an existing holding), falls back to the live LTP from Kite.
    Raises ValueError when neither source produces a price.
    """
    run_ctx = getattr(ctx.run, "context", None) or {}
    steps_ctx = run_ctx.get("steps") if isinstance(run_ctx, dict) else None
    if isinstance(steps_ctx, dict):
        # Most recent step first.
        for _, step_out in sorted(
            steps_ctx.items(), key=lambda kv: int(kv[0]), reverse=True,
        ):
            if not isinstance(step_out, dict):
                continue
            fill_sym = str(step_out.get("symbol", "")).upper()
            fill_price = step_out.get("executed_price") or step_out.get("price")
            if fill_sym == symbol and fill_price:
                return float(fill_price)

    # Fallback: live LTP from Kite. The engine treats this as the
    # entry-equivalent for standalone SLs (e.g. user adds a 2% SL on a
    # holding they already own).
    from backend.kite.market_data import get_live_quote
    try:
        quotes = get_live_quote(token, [f"NSE:{symbol}"])
        for v in quotes.values():
            ltp = v.get("last_price") or v.get("ltp") if isinstance(v, dict) else None
            if ltp:
                return float(ltp)
    except Exception:
        pass
    raise ValueError(
        f"action.set_stoploss: trigger_offset_pct given for {symbol} "
        f"but no prior fill in this run and live quote unavailable"
    )


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
            "symbol": {"type": "string"},
            "side": {"type": "string"},
            "executed_price": {"type": ["number", "null"]},
            "quantity": {"type": "integer"},
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

    # Resolve quantity. Two sources, schema-validated to be XOR:
    #   - cfg["quantity"]: literal integer or pre-resolved ref
    #   - cfg["notional_inr"]: rupee amount; we fetch live price and
    #     compute floor(notional / price).
    qty_field = cfg.get("quantity")
    notional = cfg.get("notional_inr")
    if qty_field is not None:
        quantity = int(qty_field)
    elif notional is not None:
        # Notional path: fetch live LTP for the symbol and convert.
        # Use the same kite_token + helper as set_stoploss's fallback.
        from backend.kite.market_data import get_live_quote
        symbol = str(cfg["symbol"]).upper()
        instrument = f"NSE:{symbol}"
        try:
            quotes = get_live_quote(token, [instrument]) or {}
            ltp = float((quotes.get(instrument) or {}).get("last_price", 0) or 0)
        except Exception:
            ltp = 0.0
        if ltp <= 0:
            raise ValueError(
                f"action.place_order: notional_inr given for {symbol} "
                f"but live price unavailable; cannot convert to shares"
            )
        quantity = int(float(notional) // ltp)
        if quantity <= 0:
            raise ValueError(
                f"action.place_order: notional_inr={notional} too small "
                f"for {symbol} at ₹{ltp:.2f} — would buy 0 shares"
            )
    else:
        # Schema validator should have caught this; defensive.
        raise ValueError(
            "action.place_order: neither quantity nor notional_inr provided"
        )

    result = place_order(
        access_token=token,
        tradingsymbol=str(cfg["symbol"]),
        exchange="NSE",
        transaction_type=transaction_type,
        quantity=quantity,
        order_type=order_type,
        price=price,
        product="CNC",
        tag=f"wf_{ctx.client_request_id[:16]}",
    )

    # Expose enough detail that a downstream action.set_stoploss can
    # resolve trigger_offset_pct against this fill — without round-trip
    # to the broker. In mock mode `result` carries an `average_price`;
    # in live mode the broker returns it on the order resource.
    executed_price = (
        result.get("average_price")
        or result.get("price")
        or (price if order_type == "LIMIT" else None)
    )
    return {
        "order_id": str(result.get("order_id", "")),
        "status": str(result.get("status", "")),
        "client_request_id": ctx.client_request_id,
        "symbol": str(cfg["symbol"]).upper(),
        "side": side,
        "executed_price": float(executed_price) if executed_price else None,
        "quantity": quantity,
        "notional_inr_used": (
            float(notional) if notional is not None else None
        ),
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
    symbol when not specified.

    Trigger price resolution:
      - ``trigger_price`` (absolute) → used as-is.
      - ``trigger_offset_pct`` (percentage) → resolved at execution time
        as ``preceding_fill_price * (1 - pct/100)``. The preceding fill
        is the most recent successful action.place_order in the run's
        context for this symbol. Falls back to live LTP when no prior
        fill exists in this run.
    """
    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    qty = cfg.get("quantity")
    token = _kite_token_for_run(ctx)

    trigger_price = cfg.get("trigger_price")
    if trigger_price is None and cfg.get("trigger_offset_pct") is not None:
        pct = float(cfg["trigger_offset_pct"])
        entry = _resolve_entry_price_for_sl(ctx, symbol, token)
        trigger_price = round(entry * (1 - pct / 100.0), 2)
    elif trigger_price is None:
        raise ValueError(
            "action.set_stoploss: neither trigger_price nor "
            "trigger_offset_pct supplied"
        )
    trigger_price = float(trigger_price)

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


# ── Notional basket allocator ────────────────────────────────────────


@register_step(
    step_type="action.allocate_notional",
    category="action",
    label="Allocate budget across basket",
    description=(
        "Split a ₹ budget across a list of symbols and place each as "
        "an order. Replaces N copies of action.place_order for a "
        "portfolio buy/sell."
    ),
    icon="layout-grid",
    max_retries=1,
    trigger_only=False,
    config_model=ActionAllocateNotionalConfig,
    output_schema={
        "type": "object",
        "properties": {
            "orders": {"type": "array"},
            "total_deployed_inr": {"type": "number"},
            "residual_inr": {"type": "number"},
            "n_filled": {"type": "integer"},
            "n_skipped": {"type": "integer"},
        },
        "required": ["orders", "total_deployed_inr"],
    },
)
async def execute_action_allocate_notional(
    ctx: Any,
) -> Optional[dict[str, Any]]:
    """Equal or mcap-weighted basket order under one client_request_id.

    Steps:
      1. Resolve `symbols` (list literal OR ref to a list of dicts /
         strings — fetch.screener returns ranked dicts; we accept both).
      2. Pull live LTPs for every symbol (single Kite quote round-trip).
      3. Compute INR slice per symbol per `strategy`. equal = total/N.
         mcap_weighted = total * (mcap_i / sum(mcap)).
      4. Convert each slice → integer share count = floor(slice / ltp).
         Symbols whose slice is too small for one share get logged as
         skipped — the engine surfaces this on the run card.
      5. Place each order via the same kite.orders.place_order helper
         the single-symbol path uses. Tag each with a per-leg
         client_request_id derived from the parent so retries are
         broker-side idempotent (each leg's tag is unique to its
         (run, symbol) pair).
      6. Return a list of fills + the deployed total.
    """
    from backend.kite.market_data import get_live_quote

    cfg = ctx.config
    side = str(cfg["side"])
    txn_type = "BUY" if side == "buy" else "SELL"
    order_type = str(cfg.get("order_type", "market")).upper()
    strategy = str(cfg.get("strategy", "equal"))
    total_inr = float(cfg["total_inr"])

    # ── 1. Resolve symbols ──
    raw_syms = cfg["symbols"]
    if isinstance(raw_syms, str):
        # Refs were already resolved by the engine before us, so a
        # bare string at this point means the user typed a comma-sep
        # list. Split + strip.
        symbol_rows = [
            {"symbol": s.strip().upper()}
            for s in raw_syms.split(",") if s.strip()
        ]
    elif isinstance(raw_syms, list):
        symbol_rows = []
        for item in raw_syms:
            if isinstance(item, str):
                symbol_rows.append({"symbol": item.strip().upper()})
            elif isinstance(item, dict) and item.get("symbol"):
                # Accept the fetch.screener row shape directly so the
                # mcap_weighted strategy has the cap data.
                symbol_rows.append({
                    "symbol": str(item["symbol"]).upper(),
                    "mcap_cr": item.get("mcap_cr"),
                })
    else:
        raise ValueError(
            f"action.allocate_notional: symbols must be a list or "
            f"comma-separated string; got {type(raw_syms).__name__}"
        )
    if not symbol_rows:
        raise ValueError(
            "action.allocate_notional: symbols list is empty after "
            "resolution"
        )

    n = len(symbol_rows)

    # ── 2. Live quotes (one round-trip) ──
    token = _kite_token_for_run(ctx)
    instruments = [f"NSE:{r['symbol']}" for r in symbol_rows]
    try:
        quotes = get_live_quote(token, instruments) or {}
    except Exception as e:
        raise ValueError(
            f"action.allocate_notional: live quotes unavailable ({e}); "
            "cannot convert notional to share counts"
        ) from None
    for r in symbol_rows:
        q = quotes.get(f"NSE:{r['symbol']}") or {}
        r["ltp"] = float(q.get("last_price", 0) or 0)

    missing_ltp = [r["symbol"] for r in symbol_rows if r["ltp"] <= 0]
    if missing_ltp:
        raise ValueError(
            f"action.allocate_notional: no live price for "
            f"{', '.join(missing_ltp)}; aborting basket"
        )

    # ── 3. Compute INR slices ──
    if strategy == "mcap_weighted" and all(
        r.get("mcap_cr") for r in symbol_rows
    ):
        total_mcap = sum(int(r["mcap_cr"]) for r in symbol_rows)
        for r in symbol_rows:
            r["slice_inr"] = total_inr * (
                int(r["mcap_cr"]) / total_mcap
            )
    else:
        # 'equal' or fallback when caps missing
        slice_inr = total_inr / n
        for r in symbol_rows:
            r["slice_inr"] = slice_inr

    # ── 4. Slice → integer shares ──
    skipped: list[dict[str, Any]] = []
    for r in symbol_rows:
        qty = int(r["slice_inr"] // r["ltp"])
        if qty <= 0:
            skipped.append({
                "symbol": r["symbol"],
                "reason": (
                    f"slice ₹{r['slice_inr']:.0f} too small for one "
                    f"share at ₹{r['ltp']:.2f}"
                ),
                "slice_inr": round(r["slice_inr"], 2),
                "ltp": r["ltp"],
            })
        r["qty"] = qty

    # ── 5. Place each leg ──
    orders: list[dict[str, Any]] = []
    deployed = 0.0
    parent_req = ctx.client_request_id
    for r in symbol_rows:
        if r["qty"] <= 0:
            continue
        leg_tag = f"wf_{parent_req[:10]}_{r['symbol'][:10]}"
        try:
            result = place_order(
                access_token=token,
                tradingsymbol=r["symbol"],
                exchange="NSE",
                transaction_type=txn_type,
                quantity=r["qty"],
                order_type=order_type,
                price=None,
                product="CNC",
                tag=leg_tag,
            )
        except Exception as e:
            # Continue with the rest of the basket; don't let one
            # broker hiccup tank the whole agent.
            orders.append({
                "symbol": r["symbol"],
                "qty": r["qty"],
                "status": "failed",
                "error": str(e)[:200],
            })
            continue
        fill_price = float(
            result.get("average_price") or r["ltp"]
        )
        deployed += fill_price * r["qty"]
        orders.append({
            "symbol": r["symbol"],
            "qty": r["qty"],
            "ltp_at_compute": r["ltp"],
            "fill_price": fill_price,
            "slice_inr": round(r["slice_inr"], 2),
            "order_id": str(result.get("order_id", "")),
            "status": str(result.get("status", "")),
        })

    return {
        "orders": orders,
        "skipped": skipped,
        "n_filled": sum(1 for o in orders if o.get("status") not in ("failed",)),
        "n_skipped": len(skipped),
        "total_deployed_inr": round(deployed, 2),
        "residual_inr": round(total_inr - deployed, 2),
        "strategy": strategy,
        "side": side,
        "client_request_id": parent_req,
    }


# Keep StepStatus import alive in case future executors emit run-step
# status nuances directly (currently engine-only).
_ = StepStatus
