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
    place_order,
)
# Entry + GTT placements route through the paper broker (by account mode);
# squareoff_* / cancel_orders keep using the kite helpers above because
# they size from kite get_positions / get_orders (paper reads land in P4).
from backend.paper.routing import (
    paper_position_qty,
    should_use_paper,
    submit_gtt,
    submit_order,
)
from backend.models import StepStatus, WorkflowApproval
from backend.workflows.engine import _AwaitingApproval
from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    ActionAllocateNotionalConfig,
    ActionArmIpoIntentConfig,
    ActionCancelOrdersConfig,
    ActionPlaceOrderConfig,
    ActionAllocateBasketConfig,
    ActionSetStoplossConfig,
    ActionSetTakeprofitConfig,
    ActionSquareoffAllConfig,
    ActionSquareoffAllIntradayConfig,
    ActionSquareoffSymbolConfig,
    ActionUpdateWatchlistConfig,
)


def _paper_squareoff(ctx: Any, *, symbol_filter: Optional[str] = None) -> dict[str, Any]:
    """Square off paper positions (P4): read the PAPER book, place opposite-
    side MARKET orders through the paper broker so they fill into the same
    book. Paper is CNC long-only, so we flatten the CNC legs."""
    from backend.paper.positions import paper_positions_kite_shape

    positions = paper_positions_kite_shape(ctx.db, int(ctx.workflow.user_id))
    legs = _build_squareoff_legs(
        positions, product_filter="CNC", symbol_filter=symbol_filter,
    )
    placed: list[dict] = []
    skipped: list[dict] = []
    for leg in legs:
        try:
            r = submit_order(
                ctx,
                tradingsymbol=leg["symbol"],
                transaction_type=leg["transaction_type"],
                quantity=leg["quantity"],
                order_type="MARKET",
                exchange=leg["exchange"],
                product="CNC",
                # Retry-stable per-symbol key (one position per symbol per
                # pass) so a re-fire of the step dedups instead of re-selling.
                leg_key=leg["symbol"],
            )
            placed.append({
                "symbol": leg["symbol"], "side": leg["transaction_type"],
                "qty": leg["quantity"], "order_id": str(r.get("order_id", "")),
                "status": str(r.get("status", "")),
            })
        except Exception as e:
            skipped.append({"symbol": leg["symbol"], "reason": str(e)[:160]})

    n_filled = sum(
        1 for o in placed if o.get("status") not in ("REJECTED", "failed")
    )
    # Cancel the now-orphaned resting SELL guards (stop-loss / take-profit /
    # GTT) for each flattened symbol, so they can't re-arm against a LATER
    # position the user re-opens (silent unwanted exit otherwise).
    cancelled = _paper_cancel_protective_sells(
        ctx, {leg["symbol"] for leg in legs},
    )
    # If legs were expected but NONE filled, don't report success — raise so
    # the engine's retry fires and a persistent failure surfaces as a failed
    # step instead of a silent square-off-that-didn't.
    if legs and n_filled == 0:
        raise RuntimeError(
            f"paper squareoff placed no fills ({len(skipped)} skipped)"
        )
    return {
        "orders": placed,
        "skipped": skipped,
        "n_filled": n_filled,
        "n_skipped": len(skipped),
        "cancelled_guards": cancelled,
        "scope": "paper",
    }


def _paper_cancel_protective_sells(ctx: Any, symbols: set) -> list[str]:
    """Cancel resting SELL orders (SL/TP/GTT) for the given symbols — used
    after a squareoff so an orphaned protective order can't fire against a
    later re-opened position. Returns the cancelled order ids."""
    if not symbols:
        return []
    from backend.models import PaperOrder
    from backend.paper.fills import cancel_resting_order
    from backend.paper.positions import paper_open_orders_kite_shape

    cancelled: list[str] = []
    for o in paper_open_orders_kite_shape(ctx.db, int(ctx.workflow.user_id)):
        if str(o.get("transaction_type", "")).upper() != "SELL":
            continue
        if str(o.get("tradingsymbol", "")).upper() not in symbols:
            continue
        po = ctx.db.get(PaperOrder, o.get("order_id"))
        if po is not None and str(po.status) == "resting":
            cancel_resting_order(ctx.db, po)
            cancelled.append(po.id)
    return cancelled


def _paper_cancel_orders(ctx: Any) -> dict[str, Any]:
    """Cancel matching paper resting orders (LIMIT/SL/GTT) — the paper-mode
    equivalent of action.cancel_orders (P4). Releases any reserved cash."""
    from backend.models import PaperOrder
    from backend.paper.fills import cancel_resting_order
    from backend.paper.positions import paper_open_orders_kite_shape

    cfg = ctx.config
    symbol_filter = (cfg.get("symbol_filter") or "").upper() or None
    side_filter = (cfg.get("side_filter") or "").upper() or None
    cancelled: list[str] = []
    for o in paper_open_orders_kite_shape(ctx.db, int(ctx.workflow.user_id)):
        if symbol_filter and str(o.get("tradingsymbol", "")).upper() != symbol_filter:
            continue
        if side_filter and str(o.get("transaction_type", "")).upper() != side_filter:
            continue
        po = ctx.db.get(PaperOrder, o.get("order_id"))
        if po is not None and str(po.status) == "resting":
            cancel_resting_order(ctx.db, po)
            cancelled.append(po.id)
    return {"cancelled_count": len(cancelled), "order_ids": cancelled}


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
        from backend.kite.auth import read_kite_access_token
        token = read_kite_access_token(user.kite_session)
        if token:
            return token
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
            "executed_value_inr": {"type": ["number", "null"]},
            "notional_inr_used": {"type": ["number", "null"]},
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

    result = submit_order(
        ctx,
        access_token=token,
        tradingsymbol=str(cfg["symbol"]),
        exchange="NSE",
        transaction_type=transaction_type,
        quantity=quantity,
        order_type=order_type,
        price=price,
        product=str(cfg.get("product", "CNC")).upper(),
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
    executed_value = (
        float(executed_price) * int(quantity)
        if (executed_price and quantity) else None
    )
    return {
        "order_id": str(result.get("order_id", "")),
        "status": str(result.get("status", "")),
        "client_request_id": ctx.client_request_id,
        "symbol": str(cfg["symbol"]).upper(),
        "side": side,
        "executed_price": float(executed_price) if executed_price else None,
        "quantity": quantity,
        "executed_value_inr": executed_value,
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
    if should_use_paper(ctx.db, int(ctx.workflow.user_id)):
        return _paper_cancel_orders(ctx)
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
        # Default to the current holding qty — sized from the PAPER
        # position when this account fills in paper, else the kite holding.
        if should_use_paper(ctx.db, int(ctx.workflow.user_id)):
            qty = paper_position_qty(ctx.db, int(ctx.workflow.user_id), symbol)
        else:
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
    result = submit_gtt(
        ctx,
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
    step_type="action.set_takeprofit",
    category="action",
    label="Set take-profit",
    description="Place a take-profit sell order on a holding",
    icon="target",
    max_retries=1,
    trigger_only=False,
    config_model=ActionSetTakeprofitConfig,
    output_schema={
        "type": "object",
        "properties": {
            "trigger_id": {"type": "string"},
            "client_request_id": {"type": "string"},
        },
        "required": ["trigger_id"],
    },
)
async def execute_action_set_takeprofit(ctx: Any) -> Optional[dict[str, Any]]:
    """Live executor: places a GTT sell at the trigger_price (Kite has no
    distinct take-profit type — same primitive as set_stoploss).

    Resolution mirrors set_stoploss but ABOVE the entry:
      - ``trigger_price`` (absolute) → as-is.
      - ``trigger_offset_pct`` → entry_fill * (1 + pct/100).

    Backtest sim: handled by workflow_backtester._execute_branch which
    registers a take-profit alongside any stoploss; on each subsequent
    bar, if HIGH ≥ trigger_price the position fills at trigger.
    """
    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    qty = cfg.get("quantity")
    token = _kite_token_for_run(ctx)

    trigger_price = cfg.get("trigger_price")
    if trigger_price is None and cfg.get("trigger_offset_pct") is not None:
        pct = float(cfg["trigger_offset_pct"])
        entry = _resolve_entry_price_for_sl(ctx, symbol, token)
        trigger_price = round(entry * (1 + pct / 100.0), 2)
    elif trigger_price is None:
        raise ValueError(
            "action.set_takeprofit: neither trigger_price nor "
            "trigger_offset_pct supplied"
        )
    trigger_price = float(trigger_price)

    if qty is None:
        if should_use_paper(ctx.db, int(ctx.workflow.user_id)):
            qty = paper_position_qty(ctx.db, int(ctx.workflow.user_id), symbol)
        else:
            from backend.services.portfolio import get_user_portfolio
            portfolio = get_user_portfolio(int(ctx.workflow.user_id), ctx.db)
            holdings = portfolio.get("holdings", []) if isinstance(portfolio, dict) else []
            for h in holdings:
                if str(h.get("tradingsymbol", "")).upper() == symbol:
                    qty = int(h.get("quantity", 0))
                    break
    if not qty or int(qty) <= 0:
        raise ValueError(
            f"action.set_takeprofit: no quantity specified and no "
            f"holding of {symbol} found"
        )

    result = submit_gtt(
        ctx,
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
    step_type="action.allocate_basket",
    category="action",
    label="Open weighted basket",
    description=(
        "Open a weighted basket of long and/or short positions in one "
        "step (synthetic-security pattern)."
    ),
    icon="layers",
    max_retries=1,
    trigger_only=False,
    config_model=ActionAllocateBasketConfig,
    output_schema={
        "type": "object",
        "properties": {
            "legs": {"type": "array"},
            "total_deployed_inr": {"type": "number"},
            "n_filled": {"type": "integer"},
        },
        "required": ["legs", "n_filled"],
    },
)
async def execute_action_allocate_basket(
    ctx: Any,
) -> Optional[dict[str, Any]]:
    """Live executor for weighted baskets.

    Splits ``total_inr`` across legs by ``weight``, fetches each leg's
    LTP in a single Kite quote round-trip, converts to share counts,
    and places one order per leg under per-leg client_request_ids.

    Short legs raise NotImplementedError on the live path — Pivot v1
    doesn't broker live shorts on equities. The backtester DOES simulate
    them; for live trading the workflow validator should refuse to
    activate any draft with a short leg.
    """
    from backend.kite.market_data import get_live_quote

    cfg = ctx.config
    legs_cfg = cfg["legs"]
    total_inr = float(cfg["total_inr"])
    order_type = str(cfg.get("order_type", "market")).upper()
    token = _kite_token_for_run(ctx)

    # Reject shorts on the live path.
    short_legs = [
        leg for leg in legs_cfg if str(leg.get("side", "long")) == "short"
    ]
    if short_legs:
        raise NotImplementedError(
            "action.allocate_basket: live shorts not yet supported "
            f"(short legs: {[l['symbol'] for l in short_legs]}). "
            "Backtest is fine; activate without short legs to go live."
        )

    # Normalise weights so they sum to 1.
    weights_sum = sum(float(leg["weight"]) for leg in legs_cfg) or 1.0
    instruments = [f"NSE:{leg['symbol'].upper()}" for leg in legs_cfg]
    quotes = get_live_quote(token, instruments) or {}

    placed: list[dict[str, Any]] = []
    deployed = 0.0
    parent_req = ctx.client_request_id
    for _leg_i, leg in enumerate(legs_cfg):
        sym = str(leg["symbol"]).upper()
        weight = float(leg["weight"]) / weights_sum
        slice_inr = total_inr * weight
        ltp = float((quotes.get(f"NSE:{sym}") or {}).get("last_price", 0) or 0)
        if ltp <= 0:
            placed.append({"symbol": sym, "status": "no_price"})
            continue
        qty = int(slice_inr // ltp)
        if qty <= 0:
            placed.append({"symbol": sym, "status": "slice_too_small"})
            continue
        leg_tag = f"basket_{parent_req[:10]}_{sym[:10]}"
        try:
            r = submit_order(
                ctx,
                access_token=token,
                tradingsymbol=sym,
                exchange="NSE",
                transaction_type="BUY",
                quantity=qty,
                order_type=order_type,
                price=None,
                product="CNC",
                tag=leg_tag,
                leg_key=str(_leg_i),
            )
        except Exception as e:
            placed.append({"symbol": sym, "status": "failed", "error": str(e)[:200]})
            continue
        fill = float(r.get("average_price") or ltp)
        deployed += fill * qty
        placed.append({
            "symbol": sym, "side": "long", "qty": qty,
            "weight": weight, "slice_inr": round(slice_inr, 2),
            "fill_price": fill, "status": str(r.get("status", "")),
            "order_id": str(r.get("order_id", "")),
        })

    return {
        "legs": placed,
        "n_filled": sum(1 for o in placed if o.get("status") not in {"failed", "no_price", "slice_too_small"}),
        "total_deployed_inr": round(deployed, 2),
        "client_request_id": parent_req,
    }


@register_step(
    step_type="action.squareoff_all",
    category="action",
    label="Square off everything",
    description=(
        "Close every open position — long AND short — at the trigger "
        "bar's close. Companion exit step for action.allocate_basket."
    ),
    icon="x-circle",
    max_retries=1,
    trigger_only=False,
    config_model=ActionSquareoffAllConfig,
    output_schema={
        "type": "object",
        "properties": {
            "orders": {"type": "array"},
            "n_filled": {"type": "integer"},
        },
        "required": ["orders", "n_filled"],
    },
)
async def execute_action_squareoff_all(
    ctx: Any,
) -> Optional[dict[str, Any]]:
    """Close every open position. Walks Kite positions (both products),
    sends opposite-side market orders to flatten each. Backtest fills
    at close (consistent with squareoff_all_intraday); live executor
    matches the broker's standard squareoff path."""
    if should_use_paper(ctx.db, int(ctx.workflow.user_id)):
        return _paper_squareoff(ctx)
    from backend.kite.portfolio import get_positions

    token = _kite_token_for_run(ctx)
    positions = get_positions(token)
    legs = _build_squareoff_legs(
        positions, product_filter="MIS", symbol_filter=None,
    ) + _build_squareoff_legs(
        positions, product_filter="CNC", symbol_filter=None,
    )
    parent_req = (
        f"sqoff_all:{ctx.run.id}:{ctx.step.step_index}:{ctx.attempts}"
    )
    placed: list[dict] = []
    skipped: list[dict] = []
    for i, leg in enumerate(legs):
        leg_req = f"{parent_req}:leg{i}:{leg['symbol']}"
        try:
            r = place_order(
                access_token=token,
                tradingsymbol=leg["symbol"],
                exchange=leg["exchange"],
                transaction_type=leg["transaction_type"],
                quantity=leg["quantity"],
                product=leg.get("product", "CNC"),
                order_type="MARKET",
                client_request_id=leg_req,
            )
            placed.append({
                "symbol": leg["symbol"],
                "side": leg["transaction_type"],
                "qty": leg["quantity"],
                "order_id": str(r.get("order_id", "")),
            })
        except Exception as e:
            skipped.append({"symbol": leg["symbol"], "reason": str(e)[:160]})
    return {
        "orders": placed,
        "skipped": skipped,
        "n_filled": len(placed),
        "scope": "all",
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
    for _leg_i, r in enumerate(symbol_rows):
        if r["qty"] <= 0:
            continue
        leg_tag = f"wf_{parent_req[:10]}_{r['symbol'][:10]}"
        try:
            result = submit_order(
                ctx,
                access_token=token,
                tradingsymbol=r["symbol"],
                exchange="NSE",
                transaction_type=txn_type,
                quantity=r["qty"],
                order_type=order_type,
                leg_key=str(_leg_i),
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


# ── Squareoff actions ─────────────────────────────────────────────────


def _build_squareoff_legs(
    positions: dict, *, product_filter: str, symbol_filter: Optional[str],
) -> list[dict]:
    """Filter Kite positions into closeable legs.

    Kite returns positions with positive (long) and negative (short)
    `quantity`. To exit, we send the OPPOSITE side: long → SELL, short
    → BUY. Zero-qty rows are net-flat and skipped.
    """
    legs: list[dict] = []
    rows = positions.get("net") if isinstance(positions, dict) else None
    if not isinstance(rows, list):
        return legs
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("product", "")).upper() != product_filter:
            continue
        sym = str(r.get("tradingsymbol", "")).upper()
        if symbol_filter and sym != symbol_filter:
            continue
        qty = int(r.get("quantity", 0) or 0)
        if qty == 0:
            continue
        legs.append({
            "symbol": sym,
            "exchange": str(r.get("exchange", "NSE")),
            "transaction_type": "SELL" if qty > 0 else "BUY",
            "quantity": abs(qty),
        })
    return legs


def _place_squareoff_legs(
    legs: list[dict], token: str, *, product: str, parent_req: str,
) -> tuple[list[dict], list[dict]]:
    """Run the same place_order helper that action.place_order uses."""
    placed: list[dict] = []
    skipped: list[dict] = []
    for i, leg in enumerate(legs):
        leg_req = f"{parent_req}:leg{i}:{leg['symbol']}"
        try:
            result = place_order(
                access_token=token,
                tradingsymbol=leg["symbol"],
                exchange=leg["exchange"],
                transaction_type=leg["transaction_type"],
                quantity=leg["quantity"],
                product=product,
                order_type="MARKET",
                client_request_id=leg_req,
            )
            placed.append({
                "symbol": leg["symbol"],
                "side": leg["transaction_type"],
                "quantity": leg["quantity"],
                "order_id": str(result.get("order_id", "")),
                "status": str(result.get("status", "")),
            })
        except Exception as e:
            skipped.append({"symbol": leg["symbol"], "reason": str(e)[:160]})
    return placed, skipped


@register_step(
    step_type="action.squareoff_all_intraday",
    category="action",
    label="Square off all intraday",
    description=(
        "Place market exits on every open MIS position. Pair with "
        "fetch.intraday_pnl + condition.numeric for P&L-gated exits."
    ),
    icon="x-circle",
    max_retries=1,
    trigger_only=False,
    config_model=ActionSquareoffAllIntradayConfig,
    output_schema={
        "type": "object",
        "properties": {
            "orders": {"type": "array"},
            "skipped": {"type": "array"},
            "n_filled": {"type": "integer"},
            "n_skipped": {"type": "integer"},
        },
        "required": ["orders", "n_filled"],
    },
)
async def execute_action_squareoff_all_intraday(
    ctx: Any,
) -> Optional[dict[str, Any]]:
    from backend.kite.portfolio import get_positions

    if should_use_paper(ctx.db, int(ctx.workflow.user_id)):
        # Paper is CNC delivery-only — there are no intraday (MIS) positions
        # to flatten, so this is a clean no-op.
        return {
            "orders": [], "skipped": [], "n_filled": 0, "n_skipped": 0,
            "scope": "intraday",
            "note": "paper has no intraday (MIS) positions",
        }
    token = _kite_token_for_run(ctx)
    positions = get_positions(token)
    legs = _build_squareoff_legs(
        positions, product_filter="MIS", symbol_filter=None,
    )
    parent_req = (
        f"sqoff_all:{ctx.run.id}:{ctx.step.step_index}:{ctx.attempts}"
    )
    orders, skipped = _place_squareoff_legs(
        legs, token, product="MIS", parent_req=parent_req,
    )
    return {
        "orders": orders,
        "skipped": skipped,
        "n_filled": sum(1 for o in orders if o.get("status") not in ("failed",)),
        "n_skipped": len(skipped),
        "scope": "intraday",
        "client_request_id": parent_req,
    }


@register_step(
    step_type="action.squareoff_symbol",
    category="action",
    label="Square off symbol",
    description="Exit a single symbol's open lot at market.",
    icon="x-circle",
    max_retries=1,
    trigger_only=False,
    config_model=ActionSquareoffSymbolConfig,
    output_schema={
        "type": "object",
        "properties": {
            "orders": {"type": "array"},
            "n_filled": {"type": "integer"},
        },
        "required": ["orders", "n_filled"],
    },
)
async def execute_action_squareoff_symbol(
    ctx: Any,
) -> Optional[dict[str, Any]]:
    from backend.kite.portfolio import get_positions

    if should_use_paper(ctx.db, int(ctx.workflow.user_id)):
        return _paper_squareoff(
            ctx, symbol_filter=str(ctx.config["symbol"]).upper(),
        )
    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    product = str(cfg.get("product", "MIS")).upper()
    token = _kite_token_for_run(ctx)
    positions = get_positions(token)
    legs = _build_squareoff_legs(
        positions, product_filter=product, symbol_filter=symbol,
    )
    parent_req = (
        f"sqoff_sym:{symbol}:{ctx.run.id}:{ctx.step.step_index}:{ctx.attempts}"
    )
    orders, skipped = _place_squareoff_legs(
        legs, token, product=product, parent_req=parent_req,
    )
    return {
        "orders": orders,
        "skipped": skipped,
        "n_filled": sum(1 for o in orders if o.get("status") not in ("failed",)),
        "n_skipped": len(skipped),
        "symbol": symbol,
        "product": product,
        "client_request_id": parent_req,
    }


# ── IPO arm-intent (P2 — register-not-execute) ───────────────────────


@register_step(
    step_type="action.arm_ipo_intent",
    category="action",
    label="Arm IPO intent + reminder",
    description=(
        "Record an IPO intent and hand off to the user (no broker call, "
        "never submits a bid). Pivot has NOT applied — you must apply "
        "and approve the UPI mandate yourself in your broker app."
    ),
    icon="file-check",
    max_retries=2,
    trigger_only=False,
    config_model=ActionArmIpoIntentConfig,
    output_schema={
        "type": "object",
        "properties": {
            "ipo_symbol": {"type": "string"},
            "ipo_name": {"type": ["string", "null"]},
            "ipo_type": {"type": "string"},
            "status": {"type": "string"},
            "amount_estimate": {"type": ["number", "null"]},
            "applied": {"type": "boolean"},
            "stale": {"type": "boolean"},
            # P3: present (string uuid) when the user was in paper mode and
            # the labelled-simulation row was written; null otherwise.
            "paper_allocation_id": {"type": ["string", "null"]},
        },
        "required": ["ipo_symbol", "status", "applied"],
    },
)
async def execute_action_arm_ipo_intent(
    ctx: Any,
) -> Optional[dict[str, Any]]:
    """Write an `intent_armed` row to ``ipo_applications``. NO broker call.

    Flow:
      1. Read cfg + user_id from ctx.
      2. Re-validate the IPO via ``ipo_feed.get_ipo_details`` so we
         catch type/lot_size/band changes vs draft time.
      3. Compute amount_estimate from the live band + lot size using
         ``compute_amount_estimate`` (the same helper the REST register
         path uses). If lot_size or band is missing we skip the math
         (store 0/None honestly) rather than fabricate.
      4. On feed-unreachable, still arm with ``stale=True`` — the
         autonomous path can't block on NSE flaking. But we never
         invent a band: amount_estimate is None when unverifiable.
      5. Persist via ``persist_ipo_application(..., status="intent_armed",
         autonomous=True, source="workflow-arm")``. Pivot's verb is
         "arm" / "remind", never "apply".

    Hard rule: NEVER calls ``backend.kite.orders.place_order`` or any
    broker / paper / UPI-mandate entry point. The companion notify step
    in the same workflow tells the user "Pivot has NOT applied".
    """
    from backend.services.ipo_application_service import (
        compute_amount_estimate,
        persist_ipo_application,
    )
    from backend.services.ipo_feed import get_ipo_details, parse_price_band

    cfg = ctx.config
    user_id = int(ctx.workflow.user_id)
    symbol = str(cfg["ipo_symbol"]).strip().upper()
    quantity_lots = int(cfg["quantity_lots"])
    category = str(cfg["category"])
    bid_price_mode = str(cfg["bid_price_mode"])
    bid_price_raw = cfg.get("bid_price")
    bid_price: Optional[float] = (
        float(bid_price_raw) if bid_price_raw is not None else None
    )

    # workflow_id is a UUID string in this schema; the soft-FK column on
    # ipo_applications is Integer (mirrors paper_orders' soft-ref pattern).
    # If we can't safely coerce, skip the link rather than blow up.
    workflow_id_int: Optional[int]
    try:
        workflow_id_int = int(ctx.workflow.id)
    except (TypeError, ValueError):
        workflow_id_int = None

    feed = get_ipo_details(symbol)
    stale = False
    ipo_name: Optional[str] = None
    ipo_type: str = "mainboard"
    lot_size: Optional[int] = None
    price_band: Optional[dict[str, Any]] = None

    if feed.get("source") == "unreachable":
        # Autonomous path must not block on NSE flaking. Arm stale.
        stale = True
    elif feed.get("found"):
        ipo = feed.get("ipo") or {}
        ipo_name = ipo.get("name")
        ipo_type = "sme" if ipo.get("type") == "sme" else "mainboard"
        raw_lot = ipo.get("lot_size")
        try:
            if raw_lot is None or raw_lot == "":
                lot_size = None
            else:
                lot_size = int(raw_lot)
        except (TypeError, ValueError):
            lot_size = None
        price_band = parse_price_band(ipo.get("price_band"))
    else:
        # Honest: feed reachable but the IPO is no longer in the live
        # window. Still arm (the user explicitly wanted the reminder),
        # but mark stale + skip amount.
        stale = True

    # Compute amount_estimate ONLY when we have honest inputs.
    amount_estimate: Optional[float]
    if (
        lot_size is not None and lot_size > 0
        and price_band is not None
        and price_band.get("max") is not None
    ):
        try:
            amount_estimate = compute_amount_estimate(
                quantity_lots=quantity_lots,
                lot_size=lot_size,
                bid_price_mode=bid_price_mode,
                bid_price=bid_price,
                price_band_max=float(price_band["max"]),
            )
        except ValueError:
            # cfg disagrees with feed (e.g. fixed mode but no bid_price).
            # Don't fabricate a number; persist None.
            amount_estimate = None
    else:
        amount_estimate = None

    # persist_ipo_application requires a positive lot_size (Integer col).
    # When we couldn't honestly compute one, store 0 — paired with
    # amount_estimate=None this is the honest "lot data unavailable"
    # marker the FE can render distinctly.
    lot_size_for_row = lot_size if (lot_size and lot_size > 0) else 0
    amount_estimate_for_row = (
        amount_estimate if amount_estimate is not None else 0.0
    )

    # P3: when the user is in paper mode, the IPOApplication row records
    # paper_mode=True so the audit trail is clear (the parallel
    # PaperIpoAllocation row written below carries the simulated outcome).
    paper = should_use_paper(ctx.db, user_id)
    row = persist_ipo_application(
        ctx.db, user_id,
        ipo_symbol=symbol,
        ipo_name=ipo_name,
        ipo_type=ipo_type,
        category=category,
        quantity_lots=quantity_lots,
        lot_size=lot_size_for_row,
        bid_price_mode=bid_price_mode,
        bid_price=bid_price,
        amount_estimate=amount_estimate_for_row,
        upi_id_masked=None,
        conversation_id=None,
        workflow_id=workflow_id_int,
        source="workflow-arm",
        stale=stale,
        autonomous=True,
        paper_mode=paper,
        status="intent_armed",
    )
    ctx.db.commit()
    ctx.db.refresh(row)

    # P3: paper-mode parallel-ledger write. NEVER mutates cash/positions
    # /NAV — see backend/paper/ipo_sim.py's module header for the
    # invariants. Only writes when the user is in paper mode AND the
    # IPOApplication row has a valid id (committed/refreshed above).
    paper_allocation_id: Optional[str] = None
    if paper:
        from backend.paper.ipo_sim import simulate_paper_ipo_allocation

        ipo_record_for_sim = (
            feed.get("ipo") if (feed and feed.get("found")) else None
        )
        alloc = simulate_paper_ipo_allocation(
            ctx.db, user_id,
            app_row=row,
            ipo_record=ipo_record_for_sim,
            source="workflow-arm",
        )
        ctx.db.commit()
        ctx.db.refresh(alloc)
        paper_allocation_id = str(alloc.id)

    return {
        "ipo_symbol": symbol,
        "ipo_name": ipo_name,
        "ipo_type": ipo_type,
        "status": "intent_armed",
        "amount_estimate": amount_estimate,
        "applied": False,  # Pivot has NOT applied — load-bearing flag.
        "stale": stale,
        "paper_allocation_id": paper_allocation_id,
    }


# Keep StepStatus import alive in case future executors emit run-step
# status nuances directly (currently engine-only).
_ = StepStatus
