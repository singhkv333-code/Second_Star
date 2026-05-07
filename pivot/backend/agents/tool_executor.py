"""
backend/agents/tool_executor.py

Routes Sarvam tool_call to the right backend function.
Builds LogicCard dict for every execution-type tool.
Returns: { success, data, logiccard, error }
"""

import logging

from backend.agents.tools import get_tool_defaults
from backend.safety import validate_order_value

logger = logging.getLogger(__name__)


async def execute_tool(tool_name: str, arguments: dict,
                       kite_token: str, db, user_id: int) -> dict:
    handlers = {
        "place_market_order":         _place_market_order,
        "place_limit_order":          _place_limit_order,
        "create_gtt_order":           _create_gtt_order,
        "create_sl_order":            _create_sl_order,
        "create_oco_order":           _create_oco_order,
        "create_dip_buy":             _create_dip_buy,
        "place_basket_order":         _place_basket_order,
        "cancel_order":               _cancel_order,
        "modify_order":               _generic_confirm,
        "squareoff_all_intraday":     _squareoff_all_intraday,
        "squareoff_symbol":           _squareoff_symbol,
        "list_pending_orders":        _list_pending_orders,
        "list_gtt_orders":            _list_gtt_orders,
        "cancel_gtt":                 _cancel_gtt,
        "place_futures_order":        _generic_confirm,
        "place_options_order":        _generic_confirm,
        "place_multileg_options":     _generic_confirm,
        "roll_futures_position":      _generic_confirm,
        "get_option_chain":           _generic_confirm,
        "get_option_greeks":          _generic_confirm,
        "get_margin_required":        _generic_confirm,
        "create_sip":                 _create_sip,
        "list_sips":                  _list_sips,
        "pause_sip":                  _pause_sip,
        "resume_sip":                 _resume_sip,
        "delete_sip":                 _delete_sip,
        "pause_all_sips":             _pause_all_sips,
        "create_strategy":            _create_strategy,
        "propose_workflow":           _propose_workflow,
        "propose_scheduled_order":    _propose_scheduled_order,
        "propose_threshold_order":    _propose_threshold_order,
        "propose_basket_allocation":  _propose_basket_allocation,
        "propose_holding_action":     _propose_holding_action,
        "create_cash_sweep":          _generic_confirm,
        "create_rebalancing_rule":    _generic_confirm,
        "create_drawdown_protection": _generic_confirm,
        "list_strategies":            _list_strategies,
        "pause_strategy":             _pause_strategy,
        "resume_strategy":            _resume_strategy,
        "delete_strategy":            _delete_strategy,
        "get_portfolio_summary":      _get_portfolio_summary,
        "get_holdings":               _get_holdings,
        "get_sector_breakdown":       _get_sector_breakdown,
        "get_holding_detail":         _get_holding_detail,
        "get_tax_summary":            _get_tax_summary,
        "get_active_products":        _get_active_products,
        "get_live_price":             _get_live_price,
        "get_index_level":            _get_index_level,
        "get_ohlc":                   _get_ohlc,
        "get_52wk_range":             _generic_confirm,
        "get_market_status":          _get_market_status,
        "get_upcoming_events":        _get_upcoming_events,
        "get_top_movers":             _get_top_movers,
        # /core/ analytics bridge
        "get_indicator":              _get_indicator,
        "get_multiple_indicators":    _get_multiple_indicators,
        "get_performance_metrics":    _get_performance_metrics,
        "compare_performance":        _compare_performance,
        "get_correlation_matrix":     _get_correlation_matrix,
        "get_returns":                _get_returns,
        "compare_yields":             _compare_yields,
        "get_yield_recommendation":   _get_yield_recommendation,
        "calculate_order_qty":        _calculate_order_qty,
        "calculate_tax_impact":       _calculate_tax_impact,
        "calculate_sl_price":         _calculate_sl_price,
        "calculate_dip_price":        _calculate_dip_price,
        "calculate_margin":           _calculate_margin,
        "run_backtest":               _run_backtest,
        "get_scheduler_status":       _get_scheduler_status,
        "list_upcoming_jobs":         _list_upcoming_jobs,
    }
    handler = handlers.get(tool_name)
    if not handler:
        return {"success": False, "error": f"Unknown tool: {tool_name}",
                "data": {}, "logiccard": None}
    # Merge declarative defaults — user-supplied values win.
    merged = {**get_tool_defaults(tool_name), **(arguments or {})}
    try:
        return await handler(merged, kite_token, db, user_id)
    except Exception as e:
        logger.error(f"Tool {tool_name} failed: {e}")
        return {"success": False, "error": str(e), "data": {}, "logiccard": None}


def _lc(type_, action, symbol, details, explanation, *, register_payload=None):
    """Build a LogicCard for the chat UI.

    `register_payload` is the machine-readable form of the same intent —
    when the user clicks "Confirm & register" in chat, this payload is
    POSTed to `/orders/register` (or the matching endpoint for the card
    type) and a row gets written to TradeLog. Tools never persist on
    their own; the confirm click is the commit point. Option A in the
    v1 plan.
    """
    card = {
        "type": type_,
        "action": action,
        "symbol": symbol,
        "details": details,
        "explanation": explanation,
        "disclaimer": "This is automation of your instructions, not financial advice.",
        "requires_confirmation": True,
    }
    if register_payload is not None:
        card["register_payload"] = register_payload
    return card


def _cached(symbol):
    """Return the latest known price for `symbol`, in INR.

    Lookup order:
      1. Redis price cache (Kite tick stream populates this when a
         user has an open session).
      2. yfinance live quote — sourced from `market.yfinance_service`.
         Cached internally for 1h to avoid hammering yfinance on
         every order draft.
      3. 0 — caller decides how to render an unknown price (we no
         longer fall back to a 100 stub; that produced the misleading
         "Est. Value ₹1,000" cards reported in the PDF).
    """
    from backend.agents.context_injector import _cached_price
    d = _cached_price(symbol)
    if d:
        try:
            ltp = float(d.get("ltp", 0))
            if ltp > 0:
                return ltp
        except (TypeError, ValueError):
            pass
    # Redis miss → yfinance. This is the source of truth per user's
    # 2026-05-05 directive: any price-fetching path tied to an order
    # execution goes through yfinance.
    try:
        from backend.market.yfinance_service import (
            fetch_price_history, resolve_symbol,
        )
        # 5-day daily history is plenty for a "latest close" signal,
        # cheap, and already Redis-cached inside the helper. Falls
        # through to a bare-symbol retry if .NS comes back empty.
        records = fetch_price_history(symbol, period="5d", interval="1d")
        if records:
            last_close = records[-1].get("close")
            if last_close:
                try:
                    return float(last_close)
                except (TypeError, ValueError):
                    pass
        logger.debug("yfinance returned no rows for %s (resolved=%s)",
                     symbol, resolve_symbol(symbol))
    except Exception as e:
        logger.warning("yfinance price lookup failed for %s: %s", symbol, e)
    return 0


# ── ORDERS ───────────────────────────────────────────────────────────────────

async def _place_market_order(a, kt, db, uid):
    sym, qty, txn = a["symbol"].upper(), a["quantity"], a["transaction_type"]
    # Live price comes from yfinance via _cached() (Redis tick cache
    # first, yfinance fallback). 0 means we genuinely don't have a
    # quote — we render that as "—" rather than the old ₹100 stub
    # that produced the misleading "Est. Value ₹1,000" card.
    est = _cached(sym)
    ok, err = validate_order_value(qty, est or 1)
    if not ok:
        return {"success": False, "error": err, "data": {}, "logiccard": None}
    product = a.get("product", "CNC")
    est_value = f"₹{qty * est:,.0f}" if est else "—"
    explanation = (
        f"{'Buy' if txn == 'BUY' else 'Sell'} {qty} {sym} immediately at ~₹{est:,.2f}."
        if est else
        f"{'Buy' if txn == 'BUY' else 'Sell'} {qty} {sym} immediately at market "
        "(live price unavailable — order will fill at the prevailing market price)."
    )
    lc = _lc("market_order", txn, sym,
             [{"label": "Quantity", "value": str(qty)},
              {"label": "Order Type", "value": "MARKET"},
              {"label": "Product", "value": product},
              {"label": "Est. Value", "value": est_value}],
             explanation,
             register_payload={
                 "symbol": sym, "exchange": a.get("exchange", "NSE"),
                 "transaction_type": txn, "order_type": "MARKET",
                 "quantity": int(qty),
                 "price": float(est) if est else 0.0,
                 "product": product,
             })
    return {"success": True, "data": {}, "logiccard": lc}


async def _place_limit_order(a, kt, db, uid):
    sym, qty, price, txn = a["symbol"].upper(), a["quantity"], a["price"], a["transaction_type"]
    lc = _lc("limit_order", txn, sym,
             [{"label": "Quantity", "value": str(qty)},
              {"label": "Limit Price", "value": f"₹{price:,.2f}"},
              {"label": "Est. Value", "value": f"₹{qty * price:,.0f}"}],
             f"{txn.title()} {qty} {sym} at ₹{price:,.2f} or better.",
             register_payload={
                 "symbol": sym, "exchange": a.get("exchange", "NSE"),
                 "transaction_type": txn, "order_type": "LIMIT",
                 "quantity": int(qty), "price": float(price),
                 "product": a.get("product", "CNC"),
             })
    return {"success": True, "data": {}, "logiccard": lc}


async def _create_gtt_order(a, kt, db, uid):
    sym = a["symbol"].upper()
    txn, qty = a["transaction_type"], a["quantity"]
    tp, lp = a["trigger_price"], a["limit_price"]
    cur = _cached(sym)
    lc = _lc("gtt_order", txn, sym,
             [{"label": "Trigger Price", "value": f"₹{tp:,.2f}"},
              {"label": "Limit Price", "value": f"₹{lp:,.2f}"},
              {"label": "Quantity", "value": str(qty)},
              {"label": "Current Price", "value": f"₹{cur:,.2f}" if cur else "—"},
              {"label": "Est. Spend", "value": f"₹{qty * tp:,.0f}"}],
             f"GTT: {txn.lower()} {qty} {sym} when price hits ₹{tp:,.2f}. "
             f"Zerodha monitors this automatically.",
             register_payload={
                 "symbol": sym, "exchange": a.get("exchange", "NSE"),
                 "transaction_type": txn, "order_type": "GTT",
                 "quantity": int(qty), "price": float(lp),
                 "trigger_price": float(tp), "product": "CNC",
             })
    return {"success": True, "data": {}, "logiccard": lc}


async def _create_sl_order(a, kt, db, uid):
    sym, qty = a["symbol"].upper(), a["quantity"]
    sp = a.get("stop_price")
    if not sp and a.get("stop_pct") and a.get("entry_price"):
        sp = round(a["entry_price"] * (1 - a["stop_pct"] / 100), 2)
    lc = _lc("sl_order", "SELL", sym,
             [{"label": "Quantity", "value": str(qty)},
              {"label": "Stop Price", "value": f"₹{sp:,.2f}" if sp else "—"}],
             f"Stop-loss: sell {qty} {sym} if price falls to ₹{sp:,.2f}." if sp
             else f"Stop-loss requested for {sym} but stop price could not be determined.",
             register_payload={
                 "symbol": sym, "exchange": a.get("exchange", "NSE"),
                 "transaction_type": "SELL", "order_type": "SL",
                 "quantity": int(qty),
                 "trigger_price": float(sp) if sp else None,
                 "price": float(sp) if sp else None,
                 "product": "CNC",
             } if sp else None)
    return {"success": True, "data": {}, "logiccard": lc}


async def _create_oco_order(a, kt, db, uid):
    sym, qty = a["symbol"].upper(), a["quantity"]
    tgt, stp = a["target_price"], a["stop_price"]
    lc = _lc("oco_order", "OCO", sym,
             [{"label": "Quantity", "value": str(qty)},
              {"label": "Target (Sell)", "value": f"₹{tgt:,.2f}"},
              {"label": "Stop (Sell)", "value": f"₹{stp:,.2f}"}],
             f"OCO on {qty} {sym}: sell at ₹{tgt:,.2f} target OR ₹{stp:,.2f} stop.",
             register_payload={
                 "symbol": sym, "exchange": a.get("exchange", "NSE"),
                 "transaction_type": "SELL", "order_type": "OCO",
                 "quantity": int(qty), "price": float(tgt),
                 "trigger_price": float(stp), "product": "CNC",
             })
    return {"success": True, "data": {}, "logiccard": lc}


async def _create_dip_buy(a, kt, db, uid):
    sym = a["symbol"].upper()
    dip, budget = a["dip_pct"], a["budget_inr"]
    cur = _cached(sym)
    tp = round(cur * (1 - dip / 100), 2) if cur else None
    qty = int(budget / tp) if tp else None
    lc = _lc("dip_buy", "BUY", sym,
             [{"label": "Current Price", "value": f"₹{cur:,.2f}" if cur else "—"},
              {"label": "Dip %", "value": f"{dip}%"},
              {"label": "Trigger Price", "value": f"₹{tp:,.2f}" if tp else "—"},
              {"label": "Quantity", "value": str(qty) if qty else "—"},
              {"label": "Budget", "value": f"₹{budget:,.0f}"}],
             f"Dip buy: purchase {qty or '?'} {sym} when price falls {dip}% to ₹{tp or '?'}.",
             register_payload={
                 "symbol": sym, "exchange": a.get("exchange", "NSE"),
                 "transaction_type": "BUY", "order_type": "GTT",
                 "quantity": int(qty), "price": float(tp),
                 "trigger_price": float(tp), "product": "CNC",
             } if (tp and qty) else None)
    return {"success": True, "data": {}, "logiccard": lc}


async def _place_basket_order(a, kt, db, uid):
    legs = a["legs"]
    lc = _lc("basket_order", "BASKET",
             ", ".join(l["symbol"] for l in legs),
             [{"label": f"{l['transaction_type']} {l['symbol']}",
               "value": f"{l['quantity']} @ {l.get('order_type', 'MARKET')}"}
              for l in legs],
             f"Basket: {len(legs)} orders execute simultaneously.",
             register_payload={
                 "basket": True,
                 "legs": [
                     {
                         "symbol": l["symbol"].upper(),
                         "exchange": l.get("exchange", "NSE"),
                         "transaction_type": l["transaction_type"],
                         "order_type": l.get("order_type", "MARKET"),
                         "quantity": int(l["quantity"]),
                         "price": float(l.get("price")) if l.get("price") is not None else None,
                         "product": l.get("product", "CNC"),
                     }
                     for l in legs
                 ],
             })
    return {"success": True, "data": {}, "logiccard": lc}


async def _cancel_order(a, kt, db, uid):
    from backend.kite.orders import cancel_order
    r = cancel_order(kt, a["order_id"])
    return {"success": True, "data": r, "logiccard": None}


async def _squareoff_all_intraday(a, kt, db, uid):
    lc = _lc("squareoff", "SQUARE OFF ALL", "ALL MIS",
             [{"label": "Scope", "value": "All open intraday (MIS) positions"}],
             "Closes all open intraday positions immediately.")
    return {"success": True, "data": {}, "logiccard": lc}


async def _squareoff_symbol(a, kt, db, uid):
    sym = a["symbol"].upper()
    lc = _lc("squareoff", "EXIT", sym,
             [{"label": "Symbol", "value": sym}],
             f"Exits all open positions in {sym}.")
    return {"success": True, "data": {}, "logiccard": lc}


async def _list_pending_orders(a, kt, db, uid):
    from backend.kite.orders import get_orders
    orders = get_orders(kt)
    pending = [o for o in orders if o.get("status") in ("OPEN", "TRIGGER PENDING")]
    return {"success": True, "data": {"orders": pending}, "logiccard": None}


async def _list_gtt_orders(a, kt, db, uid):
    return {"success": True, "data": {"message": "Active GTT orders"}, "logiccard": None}


async def _cancel_gtt(a, kt, db, uid):
    return {"success": True, "data": {"trigger_id": a["trigger_id"]}, "logiccard": None}


# ── SIP ──────────────────────────────────────────────────────────────────────

async def _create_sip(a, kt, db, uid):
    from backend.routers.sip import compute_next_execution
    from backend.utils.time_utils import format_ist
    sym = a["symbol"].upper()
    amt, freq = a["amount_inr"], a["frequency"]
    nxt = compute_next_execution(freq, a.get("day_of_month"), a.get("day_of_week"))
    nxt_str = format_ist(nxt, include_seconds=False)
    lc = _lc("sip_create", "CREATE SIP", sym,
             [{"label": "Amount", "value": f"₹{amt:,.0f}"},
              {"label": "Frequency", "value": freq.title()},
              {"label": "First Run", "value": nxt_str},
              {"label": "Executes at", "value": "09:15 IST"}],
             f"{freq.title()} SIP of ₹{amt:,.0f} in {sym}. First run: {nxt_str}. "
             f"Quantity calculated from live price at execution.")
    return {"success": True, "data": {}, "logiccard": lc}


async def _list_sips(a, kt, db, uid):
    from backend.models import SIPSchedule
    from backend.utils.time_utils import format_ist
    sips = db.query(SIPSchedule).filter(SIPSchedule.user_id == uid).all()
    return {"success": True, "data": {"sips": [
        {"id": s.id, "symbol": s.symbol, "amount": s.amount,
         "frequency": s.frequency, "is_active": s.is_active,
         "next_run": format_ist(s.next_execution_at, include_seconds=False)
                      if s.next_execution_at else "—",
         "total_invested": s.total_invested}
        for s in sips
    ]}, "logiccard": None}


async def _pause_sip(a, kt, db, uid):
    from backend.models import SIPSchedule
    s = db.query(SIPSchedule).filter(SIPSchedule.id == a["sip_id"],
                                     SIPSchedule.user_id == uid).first()
    if not s:
        return {"success": False, "error": "SIP not found", "data": {}, "logiccard": None}
    s.is_active = False
    db.commit()
    return {"success": True, "data": {"id": a["sip_id"], "status": "paused"}, "logiccard": None}


async def _resume_sip(a, kt, db, uid):
    from backend.models import SIPSchedule
    from backend.routers.sip import compute_next_execution
    from backend.utils.time_utils import format_ist
    s = db.query(SIPSchedule).filter(SIPSchedule.id == a["sip_id"],
                                     SIPSchedule.user_id == uid).first()
    if not s:
        return {"success": False, "error": "SIP not found", "data": {}, "logiccard": None}
    s.is_active = True
    s.next_execution_at = compute_next_execution(s.frequency, s.day_of_month, s.day_of_week)
    db.commit()
    return {"success": True, "data": {
        "id": a["sip_id"], "status": "active",
        "next_run": format_ist(s.next_execution_at, include_seconds=False),
    }, "logiccard": None}


async def _delete_sip(a, kt, db, uid):
    from backend.models import SIPSchedule
    s = db.query(SIPSchedule).filter(SIPSchedule.id == a["sip_id"],
                                     SIPSchedule.user_id == uid).first()
    if not s:
        return {"success": False, "error": "SIP not found", "data": {}, "logiccard": None}
    db.delete(s)
    db.commit()
    return {"success": True, "data": {"id": a["sip_id"], "status": "deleted"}, "logiccard": None}


async def _pause_all_sips(a, kt, db, uid):
    from backend.models import SIPSchedule
    db.query(SIPSchedule).filter(SIPSchedule.user_id == uid,
                                 SIPSchedule.is_active == True).update({"is_active": False})
    db.commit()
    return {"success": True, "data": {"message": "All SIPs paused"}, "logiccard": None}


# ── STRATEGIES ───────────────────────────────────────────────────────────────

async def _create_strategy(a, kt, db, uid):
    import json as j
    from backend.models import Strategy, StrategyStatus
    s = Strategy(user_id=uid, name=a["name"], strategy_type=a["trigger_type"],
                 trigger_symbol=a.get("trigger_symbol"),
                 trigger_condition=j.dumps(a.get("trigger_params", {})),
                 action_config=j.dumps(a.get("action", {})),
                 max_budget=min(a.get("max_budget_inr", 50000), 200000),
                 status=StrategyStatus.active)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"success": True, "data": {"id": s.id, "name": s.name}, "logiccard": None}


async def _generic_confirm(a, kt, db, uid):
    return {"success": True, "data": {"message": "Created", "args": a}, "logiccard": None}


async def _propose_workflow(a, kt, db, uid):
    """Validate a workflow draft emitted by the chat hop.

    The chat hop now produces the full WorkflowDraft (name, description,
    steps[], rationale) as the tool arguments — there is no nested LLM
    call here. The executor's job is to validate against the step
    registry and return the draft for the UI to render.

    Backwards compat: if the model only sent ``user_intent`` (older
    prompts, REST callers), fall back to the original two-LLM-call
    planner. New traffic should never hit that path.

    Returns a tool result whose `data` is the WorkflowDraft dict +
    a `_render_hint` that tells the chat UI to show an inline
    "Open in editor" card. Does NOT persist to DB — frontend POSTs
    to /api/workflows when the user activates from the editor.
    """
    from backend.workflows.propose import (
        ProposalValidationError,
        propose_workflow_async,
        validate_draft_against_registry,
    )

    a = a or {}

    # New path — chat hop emits the structured draft directly.
    if isinstance(a.get("steps"), list):
        try:
            draft = validate_draft_against_registry(a)
        except ProposalValidationError as e:
            logger.info("propose_workflow validation failed: %s", e)
            # Surface as a tool error so the agentic loop's next hop
            # can self-correct (e.g. fix a bad step_type, drop an
            # extra trigger, supply the missing required field).
            return {
                "success": False,
                "error": str(e)[:300],
                "data": {},
                "logiccard": None,
            }
        payload = draft.model_dump()
        payload["_render_hint"] = "workflow_draft_card"
        return {"success": True, "data": payload, "logiccard": None}

    # Legacy fallback — only `user_intent` provided. Runs the inner
    # planner LLM. Kept for the `/api/workflows/propose-workflow`
    # REST endpoint and any older prompt that still reaches here.
    user_intent = a.get("user_intent", "")
    if not user_intent:
        return {
            "success": False,
            "error": (
                "propose_workflow needs structured arguments "
                "(name + steps[]) — emit the draft directly."
            ),
            "data": {},
            "logiccard": None,
        }
    try:
        draft = await propose_workflow_async(user_intent)
    except ProposalValidationError as e:
        return {
            "success": False,
            "error": str(e)[:300],
            "data": {},
            "logiccard": None,
        }
    payload = draft.model_dump()
    payload["_render_hint"] = "workflow_draft_card"
    return {"success": True, "data": payload, "logiccard": None}


# ── Macro tool executors ────────────────────────────────────────────
#
# Each executor delegates to a single hydration function in
# `services/workflow_macros.py`. The model emits 5-15 small typed
# fields; the hydrator returns the same WorkflowDraft shape that
# `_propose_workflow` produces. Output decode time on the LLM side
# drops from ~7s to ~0.2s.

async def _run_macro(name: str, a: dict) -> dict:
    """Shared body for macro executors.

    Calls workflow_macros.hydrate_and_validate; on ValueError surfaces
    the message as a tool error so the agentic loop can ask the user
    for the missing/invalid field. On success returns the draft as
    `data` with `_render_hint='workflow_draft_card'` so the FE
    renders the same card as full propose_workflow."""
    from backend.services.workflow_macros import hydrate_and_validate

    try:
        draft = hydrate_and_validate(name, a or {})
    except (ValueError, TypeError) as e:
        logger.info("macro %s rejected: %s", name, e)
        return {
            "success": False,
            "error": str(e)[:300],
            "data": {},
            "logiccard": None,
        }
    return {"success": True, "data": draft, "logiccard": None}


async def _propose_scheduled_order(a, kt, db, uid):
    return await _run_macro("scheduled_order", a)


async def _propose_threshold_order(a, kt, db, uid):
    return await _run_macro("threshold_order", a)


async def _propose_basket_allocation(a, kt, db, uid):
    return await _run_macro("basket_allocation", a)


async def _propose_holding_action(a, kt, db, uid):
    return await _run_macro("holding_action", a)


async def _list_strategies(a, kt, db, uid):
    from backend.models import Strategy
    from backend.utils.time_utils import format_ist
    strategies = db.query(Strategy).filter(Strategy.user_id == uid).all()
    return {"success": True, "data": {"strategies": [
        {"id": s.id, "name": s.name, "type": s.strategy_type,
         "status": getattr(s.status, "value", s.status),
         "last_triggered": format_ist(s.last_triggered_at) if s.last_triggered_at else "Never"}
        for s in strategies
    ]}, "logiccard": None}


async def _pause_strategy(a, kt, db, uid):
    from backend.models import Strategy, StrategyStatus
    s = db.query(Strategy).filter(Strategy.id == a["strategy_id"],
                                  Strategy.user_id == uid).first()
    if not s:
        return {"success": False, "error": "Not found", "data": {}, "logiccard": None}
    s.status = StrategyStatus.paused
    db.commit()
    return {"success": True, "data": {"id": a["strategy_id"], "status": "paused"}, "logiccard": None}


async def _resume_strategy(a, kt, db, uid):
    from backend.models import Strategy, StrategyStatus
    s = db.query(Strategy).filter(Strategy.id == a["strategy_id"],
                                  Strategy.user_id == uid).first()
    if not s:
        return {"success": False, "error": "Not found", "data": {}, "logiccard": None}
    s.status = StrategyStatus.active
    db.commit()
    return {"success": True, "data": {"id": a["strategy_id"], "status": "active"}, "logiccard": None}


async def _delete_strategy(a, kt, db, uid):
    from backend.models import Strategy, StrategyStatus
    s = db.query(Strategy).filter(Strategy.id == a["strategy_id"],
                                  Strategy.user_id == uid).first()
    if not s:
        return {"success": False, "error": "Not found", "data": {}, "logiccard": None}
    s.status = StrategyStatus.completed
    db.commit()
    return {"success": True, "data": {"id": a["strategy_id"], "status": "deleted"}, "logiccard": None}


# ── PORTFOLIO ────────────────────────────────────────────────────────────────

async def _get_portfolio_summary(a, kt, db, uid):
    # WHY cached: chat sessions ask portfolio questions in bursts; the
    # 30s TTL collapses 3-5 broker round-trips into 1 within a thought.
    from backend.services.portfolio_cache import get_summary_cached
    return {"success": True, "data": get_summary_cached(uid, kt), "logiccard": None}


async def _get_holdings(a, kt, db, uid):
    from backend.services.portfolio_cache import get_holdings_cached
    return {"success": True, "data": {"holdings": get_holdings_cached(uid, kt)}, "logiccard": None}


async def _get_sector_breakdown(a, kt, db, uid):
    from backend.routers.portfolio import SECTOR_MAP
    from backend.services.portfolio_cache import get_holdings_cached
    holdings = get_holdings_cached(uid, kt)
    totals = {}
    total = 0
    for h in holdings:
        sec = SECTOR_MAP.get(h["tradingsymbol"], "Other")
        val = h["last_price"] * h["quantity"]
        totals[sec] = totals.get(sec, 0) + val
        total += val
    breakdown = [{"sector": s, "value": round(v, 2),
                  "pct": round(v / total * 100, 1) if total else 0}
                 for s, v in sorted(totals.items(), key=lambda x: -x[1])]
    return {"success": True, "data": {"sectors": breakdown, "total_value": total},
            "logiccard": None}


async def _get_holding_detail(a, kt, db, uid):
    from backend.services.portfolio_cache import get_holdings_cached
    sym = a["symbol"].upper()
    holdings = get_holdings_cached(uid, kt)
    h = next((x for x in holdings if x["tradingsymbol"] == sym), None)
    return {"success": True, "data": h or {"error": f"{sym} not in portfolio"},
            "logiccard": None}


async def _get_tax_summary(a, kt, db, uid):
    from backend.services.portfolio_cache import get_holdings_cached
    holdings = get_holdings_cached(uid, kt)
    candidates = [{"symbol": h["tradingsymbol"], "unrealised_loss": h["pnl"]}
                  for h in holdings if h.get("pnl", 0) < 0]
    return {"success": True, "data": {"loss_harvest_candidates": candidates}, "logiccard": None}


async def _get_active_products(a, kt, db, uid):
    from backend.models import ProductPosition
    from backend.utils.time_utils import format_ist
    products = db.query(ProductPosition).filter(
        ProductPosition.user_id == uid, ProductPosition.status == "active").all()
    return {"success": True, "data": {"products": [
        {"id": p.id, "type": p.product_type, "capital": p.capital_deployed,
         "maturity": format_ist(p.maturity_date) if p.maturity_date else "—"}
        for p in products
    ]}, "logiccard": None}


# ── MARKET DATA ──────────────────────────────────────────────────────────────

async def _get_live_price(a, kt, db, uid):
    """Live price via Kite cache; falls back to yfinance when Kite isn't connected.

    Without the fallback, every chat user without a Kite session sees
    "no price available" even though the data is publicly fetchable.
    """
    from backend.agents.context_injector import _cached_price
    sym = a["symbol"].upper()
    pd = _cached_price(sym)
    if pd and pd.get("ltp"):
        return {"success": True,
                "data": {"symbol": sym, "ltp": pd.get("ltp"),
                         "change_pct": pd.get("change_pct", 0),
                         "source": "kite"},
                "logiccard": None}

    # Fallback: yfinance for the NSE listing.
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{sym}.NS")
        info = ticker.fast_info
        last = float(info.last_price) if info.last_price is not None else None
        prev = float(info.previous_close) if info.previous_close is not None else None
        if last is None:
            return {"success": False, "data": {"error": f"No price for {sym}"},
                    "logiccard": None}
        change_pct = ((last - prev) / prev * 100) if prev else 0.0
        return {"success": True,
                "data": {"symbol": sym, "ltp": round(last, 2),
                         "change_pct": round(change_pct, 2),
                         "source": "yfinance"},
                "logiccard": None}
    except Exception as e:
        return {"success": False, "data": {"error": f"price fetch failed: {e}"},
                "logiccard": None}


async def _get_index_level(a, kt, db, uid):
    from backend.agents.context_injector import _cached_price
    idx = a.get("index", "NIFTY50")
    key = idx.replace("50", " 50") if "NIFTY50" in idx else idx
    d = _cached_price(key)
    return {"success": True, "data": {"index": idx, "level": d.get("ltp") if d else None},
            "logiccard": None}


async def _get_ohlc(a, kt, db, uid):
    from backend.kite.market_data import get_historical_ohlcv
    return {"success": True,
            "data": {"ohlcv": get_historical_ohlcv(a["symbol"], period=a.get("period", "1d"))[-5:]},
            "logiccard": None}


async def _get_market_status(a, kt, db, uid):
    from backend.utils.time_utils import now_ist, format_ist
    from backend.safety import is_market_open
    return {"success": True,
            "data": {"open": is_market_open(), "current_time_ist": format_ist(now_ist())},
            "logiccard": None}


async def _get_upcoming_events(a, kt, db, uid):
    return {"success": True,
            "data": {"message": "Connect TrueData for live event calendar"},
            "logiccard": None}


# ── /core/ analytics bridge ──────────────────────────────────────────


async def _get_indicator(a, kt, db, uid):
    from backend.core.tools.strategy_tools import get_indicator
    data = get_indicator(
        symbol=a.get("symbol", ""),
        indicator=a.get("indicator", ""),
        period=int(a.get("period", 14)),
        history_period=a.get("history_period", "6mo"),
    )
    success = "error" not in data
    return {"success": success, "data": data, "logiccard": None}


async def _get_multiple_indicators(a, kt, db, uid):
    from backend.core.tools.strategy_tools import get_multiple_indicators
    data = get_multiple_indicators(
        symbol=a.get("symbol", ""),
        indicators=a.get("indicators", []),
        history_period=a.get("history_period", "6mo"),
    )
    success = "error" not in data
    return {"success": success, "data": data, "logiccard": None}


async def _get_performance_metrics(a, kt, db, uid):
    from backend.core.tools.strategy_tools import get_performance_metrics
    data = get_performance_metrics(
        symbol=a.get("symbol", ""),
        period=a.get("period", "1y"),
        metrics=a.get("metrics") or None,
    )
    success = "error" not in data
    return {"success": success, "data": data, "logiccard": None}


async def _compare_performance(a, kt, db, uid):
    from backend.core.tools.strategy_tools import compare_performance
    data = compare_performance(
        symbols=a.get("symbols", []),
        period=a.get("period", "1y"),
        metric=a.get("metric", "sharpe"),
    )
    success = "error" not in data
    return {"success": success, "data": data, "logiccard": None}


async def _get_correlation_matrix(a, kt, db, uid):
    from backend.core.tools.strategy_tools import get_correlation_matrix
    data = get_correlation_matrix(
        symbols=a.get("symbols", []),
        period=a.get("period", "6mo"),
    )
    success = "error" not in data
    return {"success": success, "data": data, "logiccard": None}


async def _get_returns(a, kt, db, uid):
    from backend.core.tools.strategy_tools import get_returns
    data = get_returns(
        symbol=a.get("symbol", ""),
        period=a.get("period", "1y"),
        cumulative=bool(a.get("cumulative", False)),
    )
    success = "error" not in data
    return {"success": success, "data": data, "logiccard": None}


async def _get_top_movers(a, kt, db, uid):
    # WHY synchronous service call inside async tool: yfinance.download
    # blocks but the existing fetch.quote pattern does the same — engine
    # awaits us, the network round-trip is the bottleneck. The Redis
    # cache absorbs subsequent calls within 60s.
    from backend.services.top_movers import get_top_movers
    rows = get_top_movers(
        direction=a.get("direction", "gainers"),
        limit=int(a.get("limit", 5)),
    )
    seeded = bool(rows and rows[0].get("seed"))
    return {
        "success": True,
        "data": {
            "direction": a.get("direction", "gainers"),
            "rows": rows,
            "n": len(rows),
            "seeded": seeded,
            "note": (
                "Note: yfinance unavailable — these are seeded values."
                if seeded else None
            ),
        },
        "logiccard": None,
    }


# ── YIELDS ───────────────────────────────────────────────────────────────────

async def _compare_yields(a, kt, db, uid):
    from backend.agents.yield_scanner import get_all_yields, calculate_after_tax_yield
    ts = a.get("tax_slab", 0.30)
    yields = await get_all_yields()
    result = sorted([{"instrument": k,
                      "gross_pct": round(v * 100, 2),
                      "after_tax_pct": round(calculate_after_tax_yield(v, k, ts) * 100, 2)}
                     for k, v in yields.items()],
                    key=lambda x: -x["after_tax_pct"])
    if result:
        result[0]["is_best"] = True
    return {"success": True, "data": {"yields": result}, "logiccard": None}


async def _get_yield_recommendation(a, kt, db, uid):
    from backend.agents.yield_scanner import get_all_yields, calculate_after_tax_yield
    ts = a.get("tax_slab", 0.30)
    yields = await get_all_yields()
    best = max(yields.items(), key=lambda x: calculate_after_tax_yield(x[1], x[0], ts))
    return {"success": True,
            "data": {"best": best[0],
                     "after_tax_pct": round(calculate_after_tax_yield(best[1], best[0], ts) * 100, 2)},
            "logiccard": None}


# ── CALCULATIONS ─────────────────────────────────────────────────────────────

async def _calculate_order_qty(a, kt, db, uid):
    budget = a["budget_inr"]
    price = a.get("price")
    if not price and a.get("symbol"):
        from backend.agents.context_injector import _cached_price
        d = _cached_price(a["symbol"].upper())
        price = d.get("ltp") if d else None
    if not price:
        return {"success": False, "data": {"error": "Cannot determine price"}, "logiccard": None}
    qty = int(budget / price)
    return {"success": True, "data": {"quantity": qty, "price": price, "total": qty * price},
            "logiccard": None}


async def _calculate_tax_impact(a, kt, db, uid):
    return {"success": True,
            "data": {"note": "Full tax calc requires holding history. Connect Kite."},
            "logiccard": None}


async def _calculate_sl_price(a, kt, db, uid):
    entry, pct = a["entry_price"], a["stop_pct"]
    sl = round(entry * (1 - pct / 100), 2)
    return {"success": True, "data": {"stop_loss_price": sl, "entry": entry, "pct": pct},
            "logiccard": None}


async def _calculate_dip_price(a, kt, db, uid):
    sym = a["symbol"].upper()
    dip, budget = a["dip_pct"], a["budget_inr"]
    cur = _cached(sym)
    if not cur:
        return {"success": False, "data": {"error": f"No price for {sym}"}, "logiccard": None}
    tp = round(cur * (1 - dip / 100), 2)
    qty = int(budget / tp)
    return {"success": True,
            "data": {"current": cur, "trigger": tp, "quantity": qty, "total": qty * tp},
            "logiccard": None}


async def _calculate_margin(a, kt, db, uid):
    return {"success": True,
            "data": {"note": "Margin calculation requires live Kite session"},
            "logiccard": None}


# ── BACKTEST ─────────────────────────────────────────────────────────────────

async def _run_backtest(a, kt, db, uid):
    from backend.kite.market_data import get_historical_ohlcv
    symbol = a["symbol"]
    history = get_historical_ohlcv(symbol, period=a.get("period", "1y"))
    if len(history) < 10:
        return {"success": False, "data": {"error": f"Insufficient data for {symbol}"},
                "logiccard": None}
    starting_capital = a.get("starting_capital", 100000)
    capital = starting_capital
    FRICTION = 0.001
    position = 0
    trades = 0
    prices = [d["close"] for d in history]
    for i, day in enumerate(history):
        price = day["close"]
        triggered = False
        st = a.get("trigger_condition", {})
        if a["strategy_type"] == "sip" and i % 30 == 0:
            triggered = True
        elif a["strategy_type"] == "price_drop" and i > 0:
            drop = st.get("drop_pct", 5) / 100
            if (prices[i - 1] - price) / prices[i - 1] >= drop:
                triggered = True
        if triggered and capital > 1000:
            qty = int(capital * 0.1 / price)
            if qty > 0:
                capital -= qty * price * (1 + FRICTION)
                position += qty
                trades += 1
    final = capital + position * prices[-1] * (1 - FRICTION)
    ret = (final - starting_capital) / starting_capital * 100
    return {"success": True, "data": {
        "total_return_pct": round(ret, 2), "total_trades": trades,
        "final_value": round(final, 2),
        "disclaimer": "Past performance does not guarantee future results."
    }, "logiccard": None}


# ── SCHEDULER ────────────────────────────────────────────────────────────────

async def _get_scheduler_status(a, kt, db, uid):
    from backend.utils.time_utils import format_ist, now_ist
    try:
        from backend.scheduler import scheduler
    except Exception:
        scheduler = None
    if not scheduler or not getattr(scheduler, "running", False):
        return {"success": True,
                "data": {"running": False, "current_time_ist": format_ist(now_ist())},
                "logiccard": None}
    jobs = [{"name": j.name, "next_run": format_ist(j.next_run_time)}
            for j in scheduler.get_jobs() if j.next_run_time]
    return {"success": True,
            "data": {"running": True, "current_time_ist": format_ist(now_ist()), "jobs": jobs},
            "logiccard": None}


async def _list_upcoming_jobs(a, kt, db, uid):
    return await _get_scheduler_status(a, kt, db, uid)
