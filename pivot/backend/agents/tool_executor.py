"""
backend/agents/tool_executor.py

Routes the LLM's tool_call to the right backend function.
Builds LogicCard dict for every execution-type tool.
Returns: { success, data, logiccard, error }
"""

import logging
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from backend.agents.tools import get_tool_defaults
from backend.safety import validate_order_value

logger = logging.getLogger(__name__)

# Generic, user-safe failure for any DB-layer error while registering a
# workflow. The full exception is logged server-side (stack + SQL); the chat
# reply must NEVER carry a raw psycopg2/SQLAlchemy string — the register path
# interpolates `error` verbatim into the user-facing text.
_REGISTER_DB_ERROR = {
    "success": False,
    "error": (
        "couldn't save the agent just now — a temporary issue on our end. "
        "Nothing was armed; try registering it again in a moment."
    ),
    "data": {},
    "logiccard": None,
}


async def execute_tool(tool_name: str, arguments: dict,
                       kite_token: str, db, user_id: int) -> dict:
    handler = HANDLERS.get(tool_name)
    if not handler:
        return {"success": False, "error": f"Unknown tool: {tool_name}",
                "data": {}, "logiccard": None}
    # Late-bind through module globals so monkeypatching
    # `tool_executor._foo` keeps working (HANDLERS captured the original
    # references at import — a Phase-0 regression caught by
    # tests/test_tool_defaults.py). Handlers defined in other modules
    # (consolidated_handlers) fall back to the stored reference.
    handler = globals().get(handler.__name__, handler)
    # Merge declarative defaults — user-supplied values win.
    merged = {**get_tool_defaults(tool_name), **(arguments or {})}
    try:
        return await handler(merged, kite_token, db, user_id)
    except Exception as e:
        logger.error(f"Tool {tool_name} failed: {e}")
        return {"success": False, "error": str(e), "data": {}, "logiccard": None}


def _build_handlers() -> dict:
    """Single name → handler map for every legacy chat tool.

    Module-level (via ``HANDLERS`` below) so the registry can DERIVE the
    LLM-visible tool set from it instead of hand-maintaining a parallel
    list: a tool is real iff its handler is not the ``_generic_confirm``
    stub (see ``STUB_TOOLS``). Implementing a real handler makes the tool
    visible automatically; no second list to keep aligned.
    """
    return {
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
        # F&O P1 — real options surface (chain / suggest / build /
        # critique / portfolio greeks). Strategy REGISTRATION happens on
        # the card's POST /option-strategies, never through a chat tool.
        "get_option_chain":           _get_option_chain,
        "suggest_option_strategy":    _suggest_option_strategy,
        "build_option_strategy":      _build_option_strategy,
        "critique_option_strategy":   _critique_option_strategy,
        # Track C #3: roll/adjust an existing option leg (close + reopen
        # on a later expiry / different strike) — register-not-execute.
        "roll_option_position":       _roll_option_position,
        "get_portfolio_greeks":       _get_portfolio_greeks,
        # Track C #1: chat-side workflow arming + armed-state readback.
        "register_workflow":          _register_workflow,
        "get_workflow_status":        _get_workflow_status,
        "get_margin_required":        _generic_confirm,
        "create_sip":                 _create_sip,
        "list_sips":                  _list_sips,
        "pause_sip":                  _pause_sip,
        "resume_sip":                 _resume_sip,
        "delete_sip":                 _delete_sip,
        "pause_all_sips":             _pause_all_sips,
        "create_strategy":            _create_strategy,
        "propose_workflow":           _propose_workflow,
        "backtest_workflow":          _backtest_workflow,
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
        "compute":                    _compute,
        # retail capability tools (2026-05-29): fundamental screen,
        # single-stock fundamentals + news, IPO feed
        "screen_fundamentals":        _screen_fundamentals,
        "fetch_fundamentals":         _fetch_fundamentals,
        "query_financials":           _query_financials,
        "get_symbol_news":            _get_symbol_news,
        # Strategy builder + dynamic clarifying questions (Workstreams A & B).
        # build_strategy emits a strategy_builder_card; ask_user_dynamic runs
        # the VOI question engine and pauses the turn via needs_clarification.
        "build_strategy":             _build_strategy,
        "ask_user_dynamic":           _ask_user_dynamic,
        "ask_agent_clarify":          _ask_agent_clarify,
        "list_upcoming_ipos":         _list_upcoming_ipos,
        "get_ipo_details":            _get_ipo_details,
        "get_ipo_listing":            _get_ipo_listing,
        "propose_ipo_application":    _propose_ipo_application,
        "propose_ipo_automation":     _propose_ipo_automation,
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
        "get_scheduler_status":       _get_scheduler_status,
        "list_upcoming_jobs":         _list_upcoming_jobs,
    }


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

import re as _re_sip

# Off-exchange mutual-fund phrasing. Direct-plan MFs are bought via the
# AMC/RTA, NOT the exchange — Pivot can only SIP NSE/BSE-listed
# instruments (ETFs, equities). Detect these so _create_sip fails CLOSED
# instead of persisting a fabricated ticker like "PARAGPAREKHFLEXICAP".
_MF_PHRASE_RE = _re_sip.compile(
    r"\b(?:flexi[\s-]?cap|flexicap|direct[\s-]?(?:plan|growth)|"
    r"regular[\s-]?(?:plan|growth)|mutual[\s-]?fund|"
    r"\bMF\b|index[\s-]?fund|elss|liquid[\s-]?fund|debt[\s-]?fund|"
    r"parag[\s-]?parikh|parag[\s-]?parekh|mirae|axis[\s-]?(?:blue|small|mid)|"
    r"hdfc[\s-]?(?:flexi|index|top)|sbi[\s-]?(?:bluechip|small|magnum)|"
    r"icici[\s-]?(?:pru|prudential)|nippon[\s-]?india[\s-]?(?:small|growth)|"
    r"kotak[\s-]?(?:flexi|emerging)|quant[\s-]?(?:small|active)|"
    r"uti[\s-]?(?:flexi|nifty[\s-]?index))\b",
    _re_sip.IGNORECASE,
)
# Recognized listed ETF/index proxies — used as a positive whitelist so
# a long-but-real ETF ticker isn't mistaken for a fabricated MF name.
_LISTED_ETF_PROXIES = {
    "NIFTYBEES", "GOLDBEES", "SILVERBEES", "BANKBEES", "JUNIORBEES",
    "MON100", "MAFANG", "MASPTOP50", "ITBEES", "LIQUIDBEES",
    "SETFNIF50", "SETFNN50", "ICICIB22", "CPSEETF", "MOM100",
    "HDFCSML250", "MOM30IETF", "MOM50",
}


def _looks_like_offexchange_mf(symbol: str, raw: str) -> bool:
    """True when the SIP target is an off-exchange mutual fund (direct-plan
    AMC fund) or a fabricated MF-shaped ticker that isn't a listed ETF."""
    s = (symbol or "").strip().upper()
    if s in _LISTED_ETF_PROXIES:
        return False
    if _MF_PHRASE_RE.search(raw or "") or _MF_PHRASE_RE.search(symbol or ""):
        return True
    # Fabricated-ticker heuristic: an unrecognized, overlong all-caps blob
    # (the LLM concatenating a fund name) that's not a known ETF and ends
    # in a fund-name fragment.
    if (len(s) > 12 and s.isalpha() and s not in _LISTED_ETF_PROXIES
            and _re_sip.search(r"(FLEXI|CAP|FUND|GROWTH|DIRECT|BLUECHIP|SMALLCAP|MIDCAP)$", s)):
        return True
    return False


async def _create_sip(a, kt, db, uid):
    from backend.routers.sip import compute_next_execution
    from backend.utils.time_utils import format_ist
    from backend.market.yfinance_service import canonical_symbol
    # Canonicalize so bare "gold"/"silver"/"nifty" map to the tradeable
    # ETF (GOLDBEES / SILVERBEES / NIFTYBEES) instead of persisting a
    # dead symbol — create_sip is the right home for recurring ETF buys.
    raw_symbol = str(a.get("symbol") or "")
    sym = canonical_symbol(a["symbol"])

    # ── Fail-closed: off-exchange mutual fund → do NOT create. ────────
    # Direct-plan MFs are bought via the AMC/RTA, not the exchange. Return
    # the honest boundary and pre-fill the nearest listed broad-market /
    # flexicap ETF as a draft so the user has a real next step — NEVER
    # persist a fabricated ticker or narrate "SIP is set".
    if _looks_like_offexchange_mf(sym, raw_symbol):
        proxy = "NIFTYBEES"  # nearest listed broad-market / flexicap proxy
        amt = a.get("amount_inr") or 5000
        freq = a.get("frequency") or "monthly"
        nxt = compute_next_execution(freq, a.get("day_of_month"), a.get("day_of_week"))
        nxt_str = format_ist(nxt, include_seconds=False)
        lc = _lc("sip_create", "CREATE SIP", proxy,
                 [{"label": "Amount", "value": f"₹{amt:,.0f}"},
                  {"label": "Frequency", "value": str(freq).title()},
                  {"label": "First Run", "value": nxt_str},
                  {"label": "Executes at", "value": "09:15 IST"}],
                 f"Draft SIP into {proxy} (nearest listed proxy) — confirm on "
                 f"the card to register.")
        return {
            "success": True,
            "data": {
                "boundary": (
                    "Direct-plan mutual funds are bought via the AMC/RTA, "
                    "not the exchange — Pivot can only SIP NSE/BSE-listed "
                    "instruments (ETFs and equities). I can't register that "
                    f"fund. Nearest listed proxy: {proxy} (broad-market ETF). "
                    "I've drafted a SIP into it — confirm on the card to "
                    "register, or name another listed ETF."
                ),
                "_render_hint": "logic_card",
            },
            "logiccard": lc,
        }

    amt, freq = a["amount_inr"], a["frequency"]
    nxt = compute_next_execution(freq, a.get("day_of_month"), a.get("day_of_week"))
    nxt_str = format_ist(nxt, include_seconds=False)
    lc = _lc("sip_create", "CREATE SIP", sym,
             [{"label": "Amount", "value": f"₹{amt:,.0f}"},
              {"label": "Frequency", "value": freq.title()},
              {"label": "First Run", "value": nxt_str},
              {"label": "Executes at", "value": "09:15 IST"}],
             f"{freq.title()} SIP of ₹{amt:,.0f} in {sym} — drafted; confirm on "
             f"the card to register. First run: {nxt_str}. "
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
                                 SIPSchedule.is_active).update({"is_active": False})
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


def _steps_have_indicator_trigger(steps) -> bool:
    """True when a workflow draft's steps arm a technical-indicator trigger —
    either a ``trigger.indicator`` step or a ``trigger.compound`` whose tree
    contains an IndicatorNode. Used to enforce the always-ask-timeframe rule
    on the general propose_workflow builder (the timeframe lives inside the
    step tree, not a top-level arg)."""
    if not isinstance(steps, list):
        return False

    def _tree_has_indicator(node) -> bool:
        if isinstance(node, dict):
            if node.get("type") == "indicator":
                return True
            return any(_tree_has_indicator(v) for v in node.values())
        if isinstance(node, list):
            return any(_tree_has_indicator(x) for x in node)
        return False

    for step in steps:
        if not isinstance(step, dict):
            continue
        st = step.get("step_type") or step.get("type")
        if st == "trigger.indicator":
            return True
        if st == "trigger.compound" and _tree_has_indicator(step.get("config")):
            return True
    return False


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
        _ensure_step_labels,
        propose_workflow_async,
        validate_draft_against_registry,
    )

    a = a or {}

    # Indicator timeframe: DEFAULT to daily when the user didn't name one,
    # rather than refusing to build. The bar-interval is the lowest-priority
    # clarify with a safe standard default (system_core.md's clarify
    # priority) — force-asking it here preempted the real gap and over-asked
    # fully-specified drafts. The engine already treats an unset indicator
    # timeframe as daily, so we let the draft build; the reply states the
    # daily assumption and the user can amend to any interval before
    # activating (register-not-execute — nothing fires silently). The flag is
    # popped so it never leaks into the draft schema. Default True so REST /
    # legacy callers (no flag) are unaffected.
    a.pop("_user_named_timeframe", True)

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
        # Defense-in-depth: ensure every step carries a human label so
        # the FE chat card never shows a raw step_type id like
        # "trigger.compound" / "action.place_order". The validator above
        # already calls this; the second call is a no-op when labels
        # are present and a cheap safety net for any future caller that
        # constructs a draft outside the validator path.
        _ensure_step_labels(draft)
        payload = draft.model_dump()
        payload["_render_hint"] = "workflow_draft_card"
        # R4a: pre-flight check Mustache refs against the backtester's
        # resolvable set. When False, the FE hides the Backtest button
        # and the chat layer surfaces `backtest_blockers` upfront
        # instead of the user discovering it via a runtime float-cast
        # error.
        try:
            from backend.services.backtest_resolvability import (
                check_draft, check_live_fireable,
            )
            _steps = payload.get("steps") or []
            bt_ok, bt_blockers = check_draft(_steps)
            payload["backtestable"] = bool(bt_ok)
            payload["backtest_blockers"] = bt_blockers
            payload["live_warnings"] = check_live_fireable(_steps)
        except Exception:
            # Conservative default: assume backtestable rather than
            # falsely hiding the button for an unrelated bug here.
            payload["backtestable"] = True
            payload["backtest_blockers"] = []
        # R4b: translate the LLM's valid_until (YYYY-MM-DD) into the
        # row-level expires_at (ISO timestamp at 23:59 IST end-of-day)
        # so the FE can POST it directly to /workflows without doing
        # the date arithmetic. Engine deactivates past this instant.
        try:
            _stamp_expires_at(payload)
        except Exception:
            pass
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
    try:
        from backend.services.backtest_resolvability import (
            check_draft, check_live_fireable,
        )
        _steps = payload.get("steps") or []
        bt_ok, bt_blockers = check_draft(_steps)
        payload["backtestable"] = bool(bt_ok)
        payload["backtest_blockers"] = bt_blockers
        payload["live_warnings"] = check_live_fireable(_steps)
    except Exception:
        payload["backtestable"] = True
        payload["backtest_blockers"] = []
    try:
        _stamp_expires_at(payload)
    except Exception:
        pass
    return {"success": True, "data": payload, "logiccard": None}


def _stamp_expires_at(payload: dict) -> None:
    """R4b: derive ``expires_at`` (timestamp) from ``valid_until``
    (YYYY-MM-DD) so the FE can POST it as-is on /workflows. Sets the
    moment to 23:59 IST end-of-day on the named date — keeps "valid
    till 30 June" intuitive for the user.

    No-op when ``valid_until`` is missing or already a full ISO
    timestamp. Failure is silently absorbed by the caller — the
    workflow remains perpetual rather than blocking the draft.
    """
    raw = payload.get("valid_until")
    if not raw or not isinstance(raw, str):
        return
    if payload.get("expires_at"):
        return
    from datetime import datetime, timedelta, timezone
    # YYYY-MM-DD only — anything else is best left to the user/FE.
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        return
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return
    # 23:59 IST end-of-day → 18:29 UTC. IST = UTC+5:30.
    ist = timezone(timedelta(hours=5, minutes=30))
    eod_ist = dt.replace(hour=23, minute=59, second=0, tzinfo=ist)
    payload["expires_at"] = eod_ist.astimezone(timezone.utc).isoformat()


async def _backtest_workflow(a, kt, db, uid):
    """Validate a workflow draft AND simulate it on historical bars.

    Mirrors `_propose_workflow`'s arg shape (the LLM emits the same
    `name + steps[]` structure) but, instead of returning a draft card
    for activation, runs the steps through
    ``backend.services.workflow_backtester.backtest_workflow`` and
    returns the IndicatorBacktestResult chart payload.

    On eligibility failure (unsupported step type, fundamentals fetch,
    etc.) we surface the backtester's reason verbatim — the chat hop
    can then ask the user to restate.
    """
    import asyncio

    from backend.services.turn_context import trial_group_for
    from backend.services.workflow_backtester import (
        backtest_workflow as run_workflow_bt,
    )
    from backend.workflows.propose import (
        ProposalValidationError,
        validate_draft_against_registry,
    )

    a = a or {}
    steps = a.get("steps")
    if not isinstance(steps, list) or not steps:
        return {
            "success": False,
            "error": (
                "backtest_workflow needs `steps[]` — emit the same shape "
                "you'd give propose_workflow."
            ),
            "data": {},
            "logiccard": None,
        }

    # Validate the draft so we surface a clean error for malformed
    # configs (missing fields, bad enum values) rather than crashing
    # mid-simulation. The validator returns a WorkflowDraft; we only
    # need the validated step dicts the backtester consumes.
    try:
        draft = validate_draft_against_registry(a)
    except ProposalValidationError as e:
        return {
            "success": False,
            "error": str(e)[:300],
            "data": {},
            "logiccard": None,
        }

    name = a.get("name") or draft.name or "Workflow"
    period = str(a.get("period") or "5y")
    start_date = a.get("start_date") or None
    end_date = a.get("end_date") or None
    benchmark_symbol = a.get("benchmark_symbol") or None
    interval = str(a.get("interval") or "1d")
    starting_capital = a.get("starting_capital")

    validated_steps = [s.model_dump() for s in draft.steps]
    try:
        result = await asyncio.to_thread(
            run_workflow_bt,
            validated_steps,
            period=period,
            name=name,
            start_date=start_date,
            end_date=end_date,
            benchmark_symbol=benchmark_symbol,
            interval=interval,
            starting_capital=(
                float(starting_capital) if starting_capital else None
            ),
            # Group this CONVERSATION's backtests (falls back to user) so the
            # Deflated Sharpe deflates for how many variants were tried in this
            # session — tuning one idea deflates together; unrelated chats stay
            # independent (selection-bias guard).
            trial_group=trial_group_for(uid),
        )
    except ValueError as e:
        return {
            "success": False,
            "error": f"backtest failed: {e}",
            "data": {},
            "logiccard": None,
        }
    except Exception as e:
        logger.error("backtest_workflow exec failed: %s", e)
        return {
            "success": False,
            "error": f"backtest failed: {str(e)[:200]}",
            "data": {},
            "logiccard": None,
        }

    return {
        "success": True,
        "data": {
            "_render_hint": "indicator_backtest_chart",
            "symbol": result.symbol,
            "indicator": result.indicator,
            "indicator_period": result.indicator_period,
            "operator": result.operator,
            "threshold": result.threshold,
            "period_label": result.period_label,
            "price_curve": result.price_curve,
            "equity_curve": result.equity_curve,
            "indicator_curve": result.indicator_curve,
            "signals": result.signals,
            "metrics": result.metrics,
            "bench_buy_hold_return_pct": result.bench_buy_hold_return_pct,
            "benchmark_label": getattr(result, "benchmark_label", None),
            "methodology": result.methodology,
            "summary_text": result.summary_text,
            "strategy_kind": getattr(result, "strategy_kind", "indicator"),
            "display_title": getattr(result, "display_title", None),
            "display_subtitle": getattr(result, "display_subtitle", None),
            "window_start": getattr(result, "window_start", None),
            "window_end": getattr(result, "window_end", None),
            "n_bars": getattr(result, "n_bars", 0),
            "bar_interval": getattr(result, "bar_interval", "1d"),
        },
        "logiccard": None,
    }


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


def _derive_threshold_presets(current_yes: float, direction: str) -> list[float]:
    """Three preset chips anchored to current YES price.

    For non-extreme markets, the chips bracket "modest move", "notable
    move", and "doubled odds". For deep-OOM markets (current < 0.05),
    relative moves are too noisy — fall back to round-number presets.

    Returns dedup'd + sorted floats in the appropriate direction.
    """
    cur = max(0.0, min(1.0, current_yes))
    deep_oom = cur < 0.05 or cur > 0.95

    def _round_to(x: float, step: float = 0.05) -> float:
        return round(x / step) * step

    if direction == "below":
        if deep_oom and cur > 0.95:
            raw = [0.90, 0.75, 0.50]
        elif deep_oom:
            raw = [0.50, 0.25, 0.10]  # markets at 1-5%, alert on substantial drop is trivially true
        else:
            raw = [
                max(0.01, cur - 0.10),
                max(0.01, cur - 0.20),
                max(0.05, _round_to(cur / 2)),
            ]
    else:  # "above"
        if deep_oom and cur < 0.05:
            raw = [0.10, 0.25, 0.50]
        elif deep_oom:
            raw = [0.99, 0.97, 0.95]
        else:
            raw = [
                min(0.99, cur + 0.10),
                min(0.99, cur + 0.20),
                min(0.95, _round_to(min(cur * 2, 0.95))),
            ]
    # Round, dedupe, sort. Sort ascending; FE picks middle as default.
    rounded = sorted({round(x, 2) for x in raw if 0.0 < x < 1.0})
    if direction == "below":
        rounded = list(reversed(rounded))  # higher = closer to current = more frequent
    return rounded


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
    sym = (a.get("symbol") or "").upper().strip()
    # Reject obvious non-tickers — "show ME the option chain" was extracting
    # ME as a symbol and rendering a bogus "no quote for ME.NSE" snapshot
    # card. Fail with a clean nudge so the model re-routes (e.g. to the
    # option chain) instead of pricing an English stopword.
    _NOT_A_TICKER = {
        "ME", "SHOW", "THE", "MY", "A", "AN", "IT", "US", "TO", "OF", "IN",
        "ON", "FOR", "AND", "OR", "IS", "AT", "BE", "DO", "GO", "SO", "UP",
        "WE", "YOU", "PLS", "PLEASE", "HEY", "HI", "OK", "OPTION", "OPTIONS",
        "CHAIN", "PUT", "CALL", "STOCK", "PRICE", "QUOTE", "CHART",
    }
    if not sym or len(sym) < 2 or sym in _NOT_A_TICKER:
        return {
            "success": False,
            "data": {"error": (
                f"'{a.get('symbol')}' isn't a stock symbol I can price. "
                "Name a specific ticker (e.g. RELIANCE, NIFTY) — or if you "
                "want the option chain, ask for that."
            )},
            "logiccard": None,
        }
    pd = _cached_price(sym)
    if pd and pd.get("ltp"):
        return {"success": True,
                "data": {"symbol": sym, "ltp": pd.get("ltp"),
                         "change_pct": pd.get("change_pct", 0),
                         "source": "kite"},
                "logiccard": None}

    # Kite REST tier — a live quote when the WS ticker isn't running or this
    # symbol isn't in its universe. Works on cloud IPs where the yfinance
    # fallback below hangs (Yahoo drops datacenter egress).
    try:
        from backend.kite.live_quote import get_kite_quote
        kq = get_kite_quote(sym, "NSE")
        if kq and kq.get("last_price"):
            ltp = float(kq["last_price"])
            prev = kq.get("prev_close") or ltp
            change_pct = ((ltp - prev) / prev * 100) if prev else 0.0
            return {"success": True,
                    "data": {"symbol": sym, "ltp": round(ltp, 2),
                             "change_pct": round(change_pct, 2),
                             "source": "kite"},
                    "logiccard": None}
    except Exception:  # noqa: BLE001 — fall through to yfinance
        pass

    # Last-resort fallback: yfinance for the NSE listing. `fast_info` has no
    # timeout arg and hangs on a cloud IP, so run it under a hard wall-clock
    # bound — fail fast with a clean message instead of a gateway timeout.
    try:
        import yfinance as yf
        from backend.market.net_timeout import call_bounded

        def _yf_price() -> tuple[float | None, float | None]:
            info = yf.Ticker(f"{sym}.NS").fast_info
            last = float(info.last_price) if info.last_price is not None else None
            prev = float(info.previous_close) if info.previous_close is not None else None
            return last, prev

        res = call_bounded(_yf_price, timeout=6, default=None, label=f"yf.fast_info {sym}")
        last, prev = res if res is not None else (None, None)
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

    # Fast path: Kite tick cache (unchanged when the ticker is running).
    key = idx.replace("50", " 50") if "NIFTY50" in idx else idx
    d = _cached_price(key)
    if d and d.get("ltp"):
        return {"success": True,
                "data": {"index": idx, "level": d.get("ltp"),
                         "change_pct": d.get("change_pct", 0),
                         "source": "kite"},
                "logiccard": None}

    # Kite REST tier — the index instruments are quotable directly, so this
    # works on cloud IPs where the yfinance fallback is throttled.
    _KITE_INDEX_KEY = {
        "NIFTY50": "NSE:NIFTY 50", "NIFTY": "NSE:NIFTY 50",
        "NIFTY 50": "NSE:NIFTY 50",
        "SENSEX": "BSE:SENSEX",
        "BANKNIFTY": "NSE:NIFTY BANK", "BANK NIFTY": "NSE:NIFTY BANK",
        "NIFTYBANK": "NSE:NIFTY BANK",
        "MIDCAP": "NSE:NIFTY MIDCAP 100", "NIFTYMIDCAP": "NSE:NIFTY MIDCAP 100",
        "NIFTY MIDCAP 100": "NSE:NIFTY MIDCAP 100",
    }
    kkey = _KITE_INDEX_KEY.get(str(idx).upper().strip())
    if kkey:
        try:
            from backend.kite.live_quote import get_kite_quotes
            kq = get_kite_quotes([kkey]).get(kkey)
            if kq and kq.get("last_price"):
                level = float(kq["last_price"])
                prev = kq.get("prev_close") or level
                change_pct = ((level - prev) / prev * 100) if prev else 0.0
                return {"success": True,
                        "data": {"index": idx, "level": round(level, 2),
                                 "change_pct": round(change_pct, 2),
                                 "source": "kite"},
                        "logiccard": None}
        except Exception:  # noqa: BLE001 — fall through to yfinance
            pass

    # Fallback: yfinance via the same resolver the quote path uses
    # (NIFTY50->^NSEI, SENSEX->^BSESN, BANKNIFTY->^NSEBANK). Without this
    # every chat user without a live Kite session saw level=None and
    # couldn't even confirm the day's move ("why is nifty down today").
    # fetch_price_history is Redis-cached (1h), so no yfinance hammering.
    try:
        from backend.market.yfinance_service import fetch_price_history
        records = fetch_price_history(idx, period="5d", interval="1d")
        if records:
            last_close = records[-1].get("close")
            prev_close = records[-2].get("close") if len(records) >= 2 else None
            if last_close is not None:
                change_pct = (((last_close - prev_close) / prev_close) * 100
                              if prev_close else 0.0)
                return {"success": True,
                        "data": {"index": idx,
                                 "level": round(float(last_close), 2),
                                 "change_pct": round(change_pct, 2),
                                 "source": "yfinance"},
                        "logiccard": None}
    except Exception as e:
        return {"success": False,
                "data": {"index": idx, "level": None,
                         "error": f"index level fetch failed: {e}"},
                "logiccard": None}

    return {"success": False,
            "data": {"index": idx, "level": None,
                     "error": f"no level available for {idx}"},
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
        interval=a.get("interval", "1d"),
    )
    success = "error" not in data
    return {"success": success, "data": data, "logiccard": None}


async def _get_multiple_indicators(a, kt, db, uid):
    from backend.core.tools.strategy_tools import get_multiple_indicators
    data = get_multiple_indicators(
        symbol=a.get("symbol", ""),
        indicators=a.get("indicators", []),
        history_period=a.get("history_period", "6mo"),
        interval=a.get("interval", "1d"),
        period=a.get("period"),
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
        include=a.get("include") or None,
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


# ── Retail capability tools (2026-05-29) ──────────────────────────────────────
# Sync service call inside an async tool — the established pattern here
# (see _get_top_movers): the DB/network round-trip is the cost and the
# engine awaits us. No exotic _render_hint: the FE has no card for these
# yet, so the LLM consumes the data and answers in prose (no FE change
# needed). Honesty fields (note / found / source) flow through so the
# model never fabricates when data is sparse or a feed is unreachable.

async def _screen_fundamentals(a, kt, db, uid):
    from backend.services.fundamentals_screen import screen_by_fundamentals
    out = screen_by_fundamentals(
        filters=a.get("filters") or [],
        sector=a.get("sector"),
        sort_by=a.get("sort_by"),
        limit=int(a.get("limit", 15)),
        market_cap_tier=a.get("market_cap_tier"),
        custom_ratios=a.get("custom_ratios") or None,
        exclude=a.get("exclude") or None,
        growth_years=a.get("growth_years"),
        title=(a.get("title") or "").strip() or None,
    )
    return {"success": True, "data": out, "logiccard": None}


async def _fetch_fundamentals(a, kt, db, uid):
    from backend.services.analysis_chat_tools import (
        fetch_fundamentals, public_fundamentals_view,
    )
    return {"success": True,
            "data": public_fundamentals_view(fetch_fundamentals(str(a.get("symbol", "")))),
            "logiccard": None}


async def _query_financials(a, kt, db, uid):
    """Resolve an arbitrary financial term for one company (semantic
    translation + fuzzy line-item match + live price ratios)."""
    import asyncio
    from backend.market.financials_db import resolve_financial_query
    sym = str(a.get("symbol", "")).strip().upper()
    metric = str(a.get("metric", "")).strip()
    basis = str(a.get("basis", "consolidated")).strip() or "consolidated"
    try:
        history = int(a.get("history", 0) or 0)
    except (TypeError, ValueError):
        history = 0
    if not sym or not metric:
        return {"success": False, "error": "symbol and metric are required",
                "data": {}, "logiccard": None}
    res = await asyncio.to_thread(
        resolve_financial_query, sym, metric, basis=basis, history=history,
    )
    return {"success": True, "data": res, "logiccard": None}


async def _get_symbol_news(a, kt, db, uid):
    from backend.services.analysis_chat_tools import get_symbol_news
    return {"success": True,
            "data": get_symbol_news(str(a.get("symbol", "")), int(a.get("limit", 5))),
            "logiccard": None}


# ── STRATEGY BUILDER + DYNAMIC CLARIFYING QUESTIONS (Workstreams A & B) ───────
#
# These two executors back the `build_strategy` + `ask_user_dynamic` tools.
# The model passes the REQUEST CONTEXT (and, for the builder, the slot-state);
# the construction lives in the engines (services/strategy_builder.py +
# services/clarify_engine.py), never in the LLM args. Wire shapes + render
# hints come from services/strategy_contracts.py (the single source of truth).
#
# `_ask_user_dynamic` runs the VOI question engine and emits a clarify_card.
# It does NOT execute anything — it returns a `needs_clarification` marker so
# validation_handler/chat_service pause the turn (mirrors the ASK_USER
# intercept). `_build_strategy` runs the §3a pipeline and emits the
# strategy_builder_card the FE renders.


def _slot_state_from_args(a: dict):
    """Map the LLM's build_strategy args → a typed SlotState.

    Anything the model supplied is taken as explicit and its ``assumed`` flag
    is cleared; anything omitted keeps the contract default and stays flagged
    ``assumed`` so the card surfaces "(assumed …)" (plan §2f / §3c). Tolerant of
    partial / malformed args — a bad sub-field falls back to its default rather
    than failing the build (honest boundary over a hard error).
    """
    from backend.services.strategy_contracts import (
        AssetPrefs,
        MetricFilter,
        SlotState,
        ViewSlot,
    )

    slots = SlotState()
    cleared: list[str] = []

    # User-stated hard constraints. Each is parsed independently and a
    # malformed one is skipped rather than failing the build — but a VALID one
    # is never dropped: these are the user's own words, not our preferences.
    filters_in = a.get("filters")
    if isinstance(filters_in, (list, tuple)):
        for f in filters_in:
            if not isinstance(f, dict):
                continue
            try:
                slots.filters.append(MetricFilter(
                    field=str(f.get("field") or "").strip().lower(),
                    op=str(f.get("op") or "").strip(),
                    value=float(f.get("value")),
                ))
            except Exception:
                continue

    mn_in = a.get("max_names")
    if mn_in is not None:
        try:
            slots.max_names = max(1, min(20, int(mn_in)))
        except (TypeError, ValueError):
            slots.max_names = None

    band_in = a.get("mcap_band")
    if isinstance(band_in, str) and band_in.strip().lower() in ("large", "mid", "small"):
        slots.mcap_band = band_in.strip().lower()  # type: ignore[assignment]

    wb_in = a.get("weight_by")
    if isinstance(wb_in, str) and wb_in.strip():
        try:
            slots.weight_by = wb_in.strip().lower()  # type: ignore[assignment]
            SlotState.model_validate(slots.model_dump())  # enum check
        except Exception:
            slots.weight_by = None

    gp_in = a.get("gold_pct")
    if gp_in is not None:
        try:
            slots.gold_pct = float(gp_in)
            # A stated split is an explicit gold ask — the sleeve heuristic
            # must not get a second vote on whether gold "earns its place".
            slots.asset_prefs.gold_requested = True
        except (TypeError, ValueError):
            slots.gold_pct = None

    view_in = a.get("view")
    if isinstance(view_in, dict) and view_in:
        try:
            slots.view = ViewSlot(**{
                k: v for k, v in view_in.items()
                if k in ViewSlot.model_fields and v is not None
            })
            cleared.append("view")
        except Exception:
            pass

    risk_in = a.get("risk")
    if isinstance(risk_in, str) and risk_in.strip():
        try:
            slots.risk = risk_in.strip().lower()  # type: ignore[assignment]
            SlotState.model_validate(slots.model_dump())  # enum check
            cleared.append("risk")
        except Exception:
            slots.risk = "balanced"

    horizon_in = a.get("horizon")
    if isinstance(horizon_in, str) and horizon_in.strip():
        try:
            slots.horizon = horizon_in.strip().lower()  # type: ignore[assignment]
            SlotState.model_validate(slots.model_dump())
            cleared.append("horizon")
        except Exception:
            slots.horizon = "medium"

    cap_in = a.get("capital_inr")
    if cap_in is not None:
        try:
            slots.capital_inr = float(cap_in)
            cleared.append("capital_inr")
        except (TypeError, ValueError):
            pass

    prefs_in = a.get("asset_prefs")
    if isinstance(prefs_in, dict) and prefs_in:
        try:
            slots.asset_prefs = AssetPrefs(**{
                k: v for k, v in prefs_in.items()
                if k in AssetPrefs.model_fields and v is not None
            })
            cleared.append("asset_prefs")
        except Exception:
            pass

    theme_in = a.get("theme")
    if isinstance(theme_in, str) and theme_in.strip():
        slots.theme = theme_in.strip()
        cleared.append("theme")

    # Explicit constituent allow-list (B1): the vetted winners the model pins the
    # universe to. Normalised (strip .NS, upper, de-dup) and carried in-band on
    # the slot-state so a clarify round-trip preserves the pin.
    syms_in = a.get("symbols")
    if isinstance(syms_in, (list, tuple)):
        cleaned: list[str] = []
        seen: set[str] = set()
        for s in syms_in:
            t = str(s or "").replace(".NS", "").strip().upper()
            if t and t not in seen:
                seen.add(t)
                cleaned.append(t)
        if cleaned:
            slots.symbols = cleaned

    # (2026-07-17) The deterministic thematic seed — code pinning frozen
    # thematic_map winners when the model left `symbols` empty — was
    # REMOVED: the model now reasons out the beneficiaries itself and pins
    # them via `symbols` + `symbol_reasons` (thematic.md carries the
    # reasoning pattern with two worked examples). Exclusion re-application
    # and the fundamentals vet still run on whatever the model pins.

    # Re-validate the whole thing once; on any enum slip fall back to a clean
    # default state so the builder always receives a valid SlotState.
    try:
        slots = SlotState.model_validate(slots.model_dump())
    except Exception:
        slots = SlotState()
        cleared = []

    if cleared:
        slots.mark_assumed(*cleared, value=False)
    return slots


async def _build_strategy(a, kt, db, uid):
    """Run the §3a equity+gold construction pipeline → strategy_builder_card.

    Emits ``data = {"_render_hint": "strategy_builder_card", ...card}`` on the
    normal tool-success path; chat_service stashes it under
    ``raw_data["build_strategy"]`` and the chat router hoists the render hint to
    the top level (so the FE's StrategyBuilderCard renders). Register-not-
    execute + the not-advice disclaimer are carried inside the card."""
    from backend.services.strategy_builder import build_strategy
    from backend.services.strategy_contracts import RENDER_HINT_STRATEGY_BUILDER

    request = str(a.get("request") or "").strip()
    slots = _slot_state_from_args(a)
    # ctx is loose-typed in the builder; hand it the per-turn DB session so it
    # can reuse an open session (it otherwise opens its own read-only ones).
    # `symbols` (the pinned allow-list) is also carried on slots.symbols; passing
    # it explicitly keeps the direct-call path (Wave C thematic flow) unambiguous.
    # Per-leg WHY strings are MODEL-authored (`symbol_reasons` arg) — the
    # frozen thematic_map WHY injection went with the seed above. Sanitize
    # to a plain upper-key str→str map; builder falls back to its quality/
    # conviction templates for any leg without a reason.
    reasons: dict[str, str] = {}
    _sr = a.get("symbol_reasons")
    if isinstance(_sr, dict):
        for k, v in _sr.items():
            if str(k).strip() and str(v).strip():
                reasons[str(k).strip().upper()] = str(v).strip()[:220]
    wov = a.get("weight_overrides")
    if isinstance(wov, dict) and wov:
        try:
            wov = {str(k): float(v) for k, v in wov.items()}
        except (TypeError, ValueError):
            wov = None
    else:
        wov = None
    card = build_strategy(
        request, slots, ctx=db, symbols=slots.symbols,
        constituent_reasons=reasons or None,
        weight_overrides=wov,
    )
    payload = {"_render_hint": RENDER_HINT_STRATEGY_BUILDER, **card.model_dump()}
    return {"success": True, "data": payload, "logiccard": None}


async def _ask_user_dynamic(a, kt, db, uid):
    """Generate the VOI-ranked clarify card → needs_clarification marker.

    Calls the engine's :func:`clarify_engine.generate_clarify_card`. When the
    skip-entirely gate fires (or nothing clears τ_q) the engine returns
    ``None`` — we surface a ``needs_clarification=False`` marker so the chat
    loop knows to build directly instead of pausing. Otherwise we return the
    clarify_card payload + a ``needs_clarification`` marker that
    validation_handler maps onto a paused turn (mirrors ASK_USER).

    The card is emitted under ``data`` with ``_render_hint='clarify_card'`` so
    the chat layer can surface it as raw_data on the paused turn."""
    from backend.services.clarify_engine import generate_clarify_card
    from backend.services.strategy_contracts import RENDER_HINT_CLARIFY

    request = str(a.get("request") or "").strip()
    slots = _slot_state_from_args(a)
    card = await generate_clarify_card(request, slots, ctx=db)
    if card is None:
        # Nothing worth asking — tell the caller to build directly. No card,
        # no pause; this is NOT an error.
        return {
            "success": True,
            "data": {"_clarify_skip": True},
            "logiccard": None,
            "needs_clarification": False,
        }
    first = card.questions[0] if card.questions else None
    return {
        "success": True,
        "data": {
            "_render_hint": RENDER_HINT_CLARIFY,
            "clarify": card.model_dump(),
        },
        "logiccard": None,
        # Markers consumed by validation_handler to pause the turn and surface
        # the first question's prompt as the assistant reply.
        "needs_clarification": True,
        "question": (first.prompt if first is not None else ""),
    }


async def _ask_agent_clarify(a, kt, db, uid):
    """Generate the structured clarify card for an UNDER-SPECIFIED agent build.

    Makes ONE fast LLM call (≤6s hard timeout) to frame intent-aware questions
    (exposure / structure / capital) for the actual request, with a deterministic
    action+size fallback on any error or timeout — so the hop is bounded and
    never breaks the clarify turn. When the ask is specific enough to build,
    ``generate_agent_clarify_card`` returns ``None`` and we surface
    ``needs_clarification=False`` so the chat loop proceeds to propose_workflow.
    Otherwise we emit a clarify_card tagged
    ``_clarify_kind='agent'`` / ``_build_tool='propose_workflow'`` so the resume
    path folds answers into an enriched intent and builds via propose_workflow
    (not the portfolio build_strategy)."""
    from backend.services.agent_clarify import generate_agent_clarify_card
    from backend.services.strategy_contracts import RENDER_HINT_CLARIFY

    request = str(a.get("request") or "").strip()
    sym = str(a.get("symbol") or "").strip()
    if sym and sym.upper() not in request.upper():
        # Fold an explicitly-passed symbol into the request so the engine
        # grounds the chips on it even when the model didn't echo it verbatim.
        request = f"{request} {sym}".strip()
    card = await generate_agent_clarify_card(request)
    if card is None:
        return {
            "success": True,
            "data": {"_clarify_skip": True},
            "logiccard": None,
            "needs_clarification": False,
        }
    questions = card.get("questions") or []
    first = questions[0] if questions else None
    return {
        "success": True,
        "data": {
            "_render_hint": RENDER_HINT_CLARIFY,
            "clarify": card,
            "_clarify_kind": "agent",
            "_build_tool": "propose_workflow",
        },
        "logiccard": None,
        "needs_clarification": True,
        "question": (first.get("prompt") if isinstance(first, dict) else ""),
    }


async def _list_upcoming_ipos(a, kt, db, uid):
    from backend.services.ipo_feed import list_upcoming_ipos
    data = list_upcoming_ipos()
    # Attach the FE render hint on EVERY outcome (open list / empty-but-
    # reachable / unreachable) so the chat surface renders the interactive
    # IpoListCard with the right empty/unreachable state instead of falling
    # back to plain text. Hint string is byte-identical to the FE
    # discriminator in ChatDemo.resolveStreamingMessage.
    return {"success": data.get("source") != "unreachable",
            "data": {**data, "_render_hint": "ipo_list_card"},
            "logiccard": None}


async def _get_ipo_details(a, kt, db, uid):
    from backend.services.ipo_feed import get_ipo_details
    data = get_ipo_details(str(a.get("name_or_symbol", "")))
    return {"success": bool(data.get("found")), "data": data, "logiccard": None}


def _listed_current_price(symbol: str) -> float | None:
    """Honest current-price fetch for a LISTED equity.

    Mirrors the path ``_get_live_price`` uses: Kite tick cache first
    (``context_injector._cached_price``), then yfinance's ``fast_info``
    (``last_price`` / ``previous_close``). Returns ``None`` when neither
    yields a positive value — the caller surfaces that honestly rather
    than fabricating a number or substituting the previous close.

    Kept narrow (only `symbol -> float|None`) so the IPO listing handler
    doesn't drag in the larger live-price response envelope.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    try:
        from backend.agents.context_injector import _cached_price
        pd = _cached_price(sym)
        if pd:
            ltp = pd.get("ltp")
            if ltp is not None:
                try:
                    val = float(ltp)
                    if val > 0:
                        return val
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001 — Kite cache is best-effort
        logger.debug("listed price kite cache lookup failed for %s: %s", sym, e)
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{sym}.NS")
        info = ticker.fast_info
        last = info.last_price if info.last_price is not None else None
        if last is None:
            return None
        try:
            val = float(last)
        except (TypeError, ValueError):
            return None
        return val if val > 0 else None
    except Exception as e:  # noqa: BLE001 — yfinance is best-effort
        logger.debug("listed price yfinance lookup failed for %s: %s", sym, e)
        return None


async def _get_ipo_listing(a, kt, db, uid):
    """Post-listing performance card: issue price vs current price + gain %.

    Reads the NSE past-issues feed via ``fetch_listed_ipo`` (a listed IPO
    has dropped off the upcoming/current endpoints, so ``get_ipo_details``
    would 404 here) and pairs it with the same live-price path
    ``_get_live_price`` uses. Honest-on-failure:

      * not-found / unreachable → ``success: False`` + honest note,
        NO card hint (the chat surface renders the note as plain text).
      * found, no current price → ``current_price: None`` +
        ``listing_gain_pct: None`` + note "listing data pending …".
      * found, no issue price   → same shape, note "issue price unavailable".
      * found + both prices     → computes gain%; caller renders the card.

    NEVER fabricates the current price, the listing gain, the issue
    price, or the listing date.
    """
    from backend.services.ipo_feed import fetch_listed_ipo

    query = str(a.get("name_or_symbol", "")).strip()
    if not query:
        return {
            "success": False,
            "data": {
                "found": False,
                "note": (
                    "Provide an IPO name or symbol — e.g. 'TIKONA' or "
                    "'Tikona Infinet' — to look up the listing outcome."
                ),
            },
            "logiccard": None,
        }

    rec = fetch_listed_ipo(query)
    if not rec.get("found"):
        # Honest miss / unreachable — surface the note without a card hint
        # so the chat renders it as plain text.
        return {
            "success": False,
            "data": rec,
            "logiccard": None,
        }

    symbol = (rec.get("symbol") or "").strip().upper() or None
    name = rec.get("name")
    ipo_type = rec.get("type") if rec.get("type") in {"sme", "mainboard"} else "mainboard"
    issue_price_raw = rec.get("issue_price")
    issue_price: float | None
    try:
        issue_price = float(issue_price_raw) if issue_price_raw is not None else None
    except (TypeError, ValueError):
        issue_price = None
    listing_date = rec.get("listing_date")

    current_price: float | None = (
        _listed_current_price(symbol) if symbol else None
    )

    # Trendlyne-published performance (issue→listing-day pop, and issue→LTP
    # current return) carried through fetch_listed_ipo. Used to (a) surface
    # the listing-day pop the live computation can't reconstruct, and (b)
    # fall back to a real current-return when we have no live symbol price.
    tl_listing_gain = rec.get("listing_gain_pct")   # issue → listing-day open
    tl_current_return = rec.get("current_return_pct")  # issue → current LTP

    listing_gain_pct: float | None
    source = "nse"
    if (
        issue_price is not None
        and current_price is not None
        and issue_price > 0
    ):
        # Live current return (issue → live LTP).
        listing_gain_pct = round(
            (current_price - issue_price) / issue_price * 100.0, 2
        )
    elif tl_current_return is not None:
        # No live price (e.g. Trendlyne-only, no NSE symbol) — use Trendlyne's
        # published current return rather than showing nothing.
        listing_gain_pct = float(tl_current_return)
        source = "trendlyne"
    else:
        listing_gain_pct = None

    # Honest notes — order matters: missing issue price is the more
    # fundamental gap (we can't compute the gain at all).
    note: str | None = None
    if issue_price is None:
        note = "issue price unavailable"
    elif current_price is None and listing_gain_pct is None:
        note = "listing data pending — no live price yet"

    if "trendlyne" in (rec.get("sources") or []) and source == "nse":
        source = "nse+trendlyne"

    payload: dict = {
        "_render_hint": "ipo_listed_card",
        "symbol": symbol,
        "name": name,
        "type": ipo_type,
        "issue_price": issue_price,
        "listing_date": listing_date,
        "current_price": current_price,
        "listing_gain_pct": listing_gain_pct,
        # New: the listing-day pop (issue → first-day open), distinct from the
        # current return above. Surfaced when Trendlyne carries it.
        "listing_day_gain_pct": (
            float(tl_listing_gain) if tl_listing_gain is not None else None
        ),
        "subscription": rec.get("subscription"),
        "source": source,
        "note": note,
    }
    return {"success": True, "data": payload, "logiccard": None}


# ── F&O P1 tool handlers ─────────────────────────────────────────────
#
# All four card-producing handlers are honest-on-failure (note + matches,
# never fabricated chains/strikes) and registration-free: the strategy
# card's Register button POSTs to /option-strategies — chat never places
# or registers an option order by itself.


def _normalize_expiry_arg(raw) -> tuple[str | None, str | None]:
    """LLMs pass 'nearest'/'current'/'current_week'/'next'/'monthly' as
    expiry. Returns (iso_or_none, mode) where mode is one of
    {None, "next_weekly", "next_monthly"} when iso is None.

    'monthly'/'next_month' used to be folded into the same bucket as
    'next'/'next_week' and resolved to the SECOND listed expiry — which
    is normally the next WEEKLY, not the monthly one (list_expiries
    tags each entry with its real "kind"). A user asking for an
    "iron condor expiring next month" would silently get the nearer
    weekly expiry instead. Kept as a distinct mode so the caller can
    filter by kind=="monthly"."""
    val = str(raw or "").strip().lower()
    if not val or val in ("nearest", "current", "current_week", "this_week", "weekly"):
        return None, None
    if val in ("next", "next_week", "next_expiry"):
        return None, "next_weekly"
    if val in ("monthly", "next_month", "month", "monthly_expiry"):
        return None, "next_monthly"
    return str(raw)[:10], None


def _resolve_expiry_for_tool(db, underlying: str, raw) -> str | None:
    from backend.market.instrument_master import list_expiries

    iso, mode = _normalize_expiry_arg(raw)
    if iso:
        return iso
    if mode == "next_weekly":
        expiries = list_expiries(db, underlying)
        if len(expiries) > 1:
            return expiries[1]["expiry"]
    elif mode == "next_monthly":
        expiries = list_expiries(db, underlying)
        monthly = [e for e in expiries if e.get("kind") == "monthly"]
        if monthly:
            return monthly[0]["expiry"]
        # No monthly-tagged row for this underlying (e.g. weeklies-only) —
        # fall back to the furthest listed expiry rather than silently
        # handing back the nearest one, which is the opposite of "monthly".
        if expiries:
            return expiries[-1]["expiry"]
    return None  # nearest


async def _get_option_chain(a, kt, db, uid):
    """ATM-centered chain slice → ``option_chain_card``."""
    from backend.market.option_chain import get_chain
    from backend.services.option_strategies import SEBI_DISCLOSURE

    underlying = str(a.get("underlying", "")).strip().upper()
    if not underlying:
        return {
            "success": False,
            "data": {"note": "Provide an underlying — e.g. NIFTY, BANKNIFTY, RELIANCE."},
            "logiccard": None,
        }
    expiry = _resolve_expiry_for_tool(db, underlying, a.get("expiry"))
    width = max(1, min(int(a.get("width") or 8), 20))
    chain = get_chain(db, underlying, expiry, width=width)
    if chain is None:
        return {
            "success": False,
            "data": {
                "note": (
                    f"No option chain found for '{underlying}'"
                    + (f" expiry {expiry}" if expiry else "")
                    + " — it may not be in the F&O segment, or the "
                    "requested expiry isn't listed."
                ),
            },
            "logiccard": None,
        }
    payload = {
        "_render_hint": "option_chain_card",
        **chain,
        "disclosure": SEBI_DISCLOSURE,
    }
    # 51-sweep: the mock/stale feed printed a spot ~700pts off the live
    # index with no visible flag. When the chain isn't Kite-live, say so
    # LOUDLY — the model must relay this line and every strategy built
    # on it inherits the caveat.
    if (chain.get("source") or "").lower() != "kite":
        payload = {
            "data_status": "mock",
            "stale_note": (
                "OPTION DATA IS MOCK/NOT LIVE (no active Kite session): "
                "strikes, premiums and the spot may be far from the real "
                "market. Tell the user this explicitly before any numbers."
            ),
            **payload,
        }
    return {"success": True, "data": payload, "logiccard": None}


def _stale_options_note(db) -> Optional[str]:
    """51-sweep: when there is no live Kite session, every option
    strategy is priced off the MOCK chain (observed ~700pts off the
    live index). Return the loud caveat, or None when live."""
    try:
        from backend.market.option_chain import get_system_kite
        if get_system_kite(db) is not None:
            return None
    except Exception:
        pass
    return (
        "OPTION DATA IS MOCK/NOT LIVE (no active Kite session): strikes, "
        "premiums, spot and payoff numbers may be far from the real "
        "market. State this to the user BEFORE any numbers, and advise "
        "re-checking once the live feed is connected."
    )


def _strategy_card_payload(payload: dict) -> dict:
    """Shape a resolved strategy into the card dict. ``summary`` leads
    the dict so the (6000-char-truncated) LLM view keeps the decision
    quad even when the payoff array gets cut."""
    computed = payload["computed"]
    quad = {
        "max_loss": computed["max_loss"],
        "max_profit": computed["max_profit"],
        "pop": computed["pop"],
        "capital_required": computed["capital_required"],
        "net_premium": computed["net_premium"],
        "breakevens": computed["breakevens"],
    }
    return {
        "_render_hint": "option_strategy_card",
        "summary": {
            "template": payload["editable"]["template"],
            "underlying": payload["locked"]["underlying"],
            "expiry": payload["locked"]["expiry"],
            "legs": [
                {"option_type": l["option_type"], "side": l["side"],
                 "strike": l["strike"], "mid": l["mid"]}
                for l in payload["editable"]["legs"]
            ],
            **quad,
            "critique_verdict": payload["critique"]["verdict"],
            "critique_summary": payload["critique"]["summary"],
            # Engine-anchored numbers the chat layer MUST quote verbatim so
            # prose can't drift (e.g. calling a bounded short put
            # "unlimited loss"). digest carries the risk-shape + POP +
            # breakevens; comparison is the 2-row current-vs-alternative.
            "critique_digest": payload["critique"].get("digest"),
            "critique_comparison": payload["critique"].get("comparison", []),
        },
        "locked": payload["locked"],
        "editable": payload["editable"],
        "validation": payload["validation"],
        "critique": payload["critique"],
        "candidates": payload.get("candidates", []),
        "computed": computed,
    }


async def _suggest_option_strategy(a, kt, db, uid):
    """View → 2-3 risk-tagged candidates → editable strategy card."""
    from backend.services.option_strategies import (
        StrategyResolutionError,
        suggest_strategies,
    )

    underlying = str(a.get("underlying", "")).strip().upper()
    view = str(a.get("view", "")).strip().lower()
    if not underlying or not view:
        return {
            "success": False,
            "data": {"note": "Need the underlying and a view (bullish/bearish/neutral/volatile)."},
            "logiccard": None,
        }
    try:
        payload = suggest_strategies(
            db, underlying, view,
            expiry=_resolve_expiry_for_tool(db, underlying, a.get("expiry")),
            risk=a.get("risk"),
            qty_lots=int(a.get("qty_lots") or 1),
        )
    except StrategyResolutionError as exc:
        return {"success": False, "data": {"note": str(exc)}, "logiccard": None}
    card = _strategy_card_payload(payload)
    _stale = _stale_options_note(db)
    if _stale:
        # PREPEND the stale fields: _summarise_tool_result truncates the
        # tool-result JSON at 6000 chars, and option cards are big — a
        # note appended last never reached the model (live-observed).
        card = {"data_status": "mock", "stale_note": _stale, **card}
        if isinstance(card.get("summary"), str):
            card["summary"] = "[MOCK DATA — not live] " + card["summary"]
    return {"success": True, "data": card, "logiccard": None}


async def _build_option_strategy(a, kt, db, uid):
    """One named template → editable strategy card."""
    from backend.services.option_strategies import (
        TEMPLATES,
        StrategyResolutionError,
        resolve_strategy,
    )

    underlying = str(a.get("underlying", "")).strip().upper()
    template = str(a.get("template", "")).strip().lower()
    if not underlying or not template:
        return {
            "success": False,
            "data": {"note": "Need the underlying and a strategy template name."},
            "logiccard": None,
        }
    explicit_legs = None
    strikes = a.get("strikes")
    if strikes and template in TEMPLATES:
        spec_legs = TEMPLATES[template].legs
        if len(strikes) == len(spec_legs):
            # Models sometimes pass strikes=[null] when they have no
            # level in mind. A None/garbage strike must NOT kill the
            # build — fall back to the template's delta/ATM defaults.
            try:
                explicit_legs = [
                    {"option_type": s.option_type, "side": s.side,
                     "strike": float(k)}
                    for s, k in zip(spec_legs, strikes)
                ]
            except (TypeError, ValueError):
                explicit_legs = None
    try:
        payload = resolve_strategy(
            db, underlying, template,
            expiry=_resolve_expiry_for_tool(db, underlying, a.get("expiry")),
            qty_lots=int(a.get("qty_lots") or 1),
            explicit_legs=explicit_legs,
        )
    except StrategyResolutionError as exc:
        return {"success": False, "data": {"note": str(exc)}, "logiccard": None}
    card = _strategy_card_payload(payload)
    _stale = _stale_options_note(db)
    if _stale:
        # PREPEND the stale fields: _summarise_tool_result truncates the
        # tool-result JSON at 6000 chars, and option cards are big — a
        # note appended last never reached the model (live-observed).
        card = {"data_status": "mock", "stale_note": _stale, **card}
        if isinstance(card.get("summary"), str):
            card["summary"] = "[MOCK DATA — not live] " + card["summary"]
    return {"success": True, "data": card, "logiccard": None}


async def _critique_option_strategy(a, kt, db, uid):
    """Copilot pre-trade critique of explicit legs → strategy card
    (read-only intent, but the card lets the user register if happy).

    Silent-default: when a leg names only option_type + side and no
    strike (e.g. "is a naked put on RELIANCE smart?"), synthesize a
    sensible default strike from the live chain — ATM for a long leg,
    ~ the nearest liquid OTM strike for a short premium-selling leg —
    so the critique (and its screaming-risk warning) renders instead of
    collapsing to an ask_user for inputs the user shouldn't have to
    supply. The card stays editable; the user can move the strike."""
    from backend.market.option_chain import get_chain
    from backend.services.option_strategies import (
        StrategyResolutionError,
        resolve_strategy,
    )

    underlying = str(a.get("underlying", "")).strip().upper()
    legs = a.get("legs") or []
    if not underlying or not legs:
        return {
            "success": False,
            "data": {"note": "Need the underlying and at least one leg to critique."},
            "logiccard": None,
        }
    expiry = _resolve_expiry_for_tool(db, underlying, a.get("expiry"))

    # Pre-fetch the chain once so we can both default missing strikes and
    # pass it through to resolve_strategy (avoids a second fetch).
    chain = get_chain(db, underlying, expiry, width=15)

    def _default_strike(option_type: str, side: str) -> Optional[float]:
        if not chain:
            return None
        rows = chain.get("rows") or []
        atm = float(chain.get("atm_strike") or 0.0)
        if not rows or atm <= 0:
            return None
        ot = option_type.upper()
        sd = side.upper()
        # Short premium legs default ~1 step OTM (call above / put below
        # ATM); long legs default ATM. Walk to the nearest quotable row.
        strikes = sorted(r["strike"] for r in rows)
        if sd == "SELL":
            cands = [s for s in strikes if (s > atm if ot == "CE" else s < atm)]
            if cands:
                return cands[0] if ot == "PE" else cands[0]
        return min(strikes, key=lambda s: abs(s - atm))

    try:
        explicit = []
        for l in legs:
            ot = str(l.get("option_type", "")).upper()
            sd = str(l.get("side", "")).upper()
            strike = float(l.get("strike") or 0.0)
            if strike <= 0.0:
                defaulted = _default_strike(ot, sd)
                if defaulted is None:
                    raise StrategyResolutionError(
                        f"The {underlying} chain is too thin to default a "
                        f"{ot} strike — try a more liquid expiry or name a strike."
                    )
                strike = defaulted
            explicit.append({"option_type": ot, "side": sd, "strike": strike})
        payload = resolve_strategy(
            db, underlying, "custom",
            expiry=expiry,
            qty_lots=int(a.get("qty_lots") or 1),
            explicit_legs=explicit,
            chain=chain,
        )
    except (StrategyResolutionError, ValueError) as exc:
        return {"success": False, "data": {"note": str(exc)}, "logiccard": None}
    card = _strategy_card_payload(payload)
    _stale = _stale_options_note(db)
    if _stale:
        # PREPEND the stale fields: _summarise_tool_result truncates the
        # tool-result JSON at 6000 chars, and option cards are big — a
        # note appended last never reached the model (live-observed).
        card = {"data_status": "mock", "stale_note": _stale, **card}
        if isinstance(card.get("summary"), str):
            card["summary"] = "[MOCK DATA — not live] " + card["summary"]
    return {"success": True, "data": card, "logiccard": None}


async def _roll_option_position(a, kt, db, uid):
    """Track C #3: price a roll of an existing option leg → 2-leg
    option_strategy_card (close old + open new) with roll net
    credit/debit and the go-forward position's econ quad."""
    from backend.services.option_strategies import (
        StrategyResolutionError,
        roll_option_position,
    )

    underlying = str(a.get("underlying", "")).strip().upper()
    try:
        strike = float(a.get("strike") or 0.0)
    except (TypeError, ValueError):
        strike = 0.0
    option_type = str(a.get("option_type", "")).strip().upper()
    if not underlying or strike <= 0 or option_type not in ("CE", "PE"):
        return {
            "success": False,
            "data": {"note": (
                "To roll I need the existing leg: underlying, strike and "
                "CE/PE (e.g. 'roll my short 24000 NIFTY call to next expiry')."
            )},
            "logiccard": None,
        }
    try:
        offset_raw = a.get("strike_offset")
        payload = roll_option_position(
            db, underlying,
            strike=strike,
            option_type=option_type,
            side=str(a.get("side") or "SELL").upper(),
            from_expiry=a.get("from_expiry"),
            to_expiry=a.get("to_expiry") or "next",
            new_strike=(
                float(a["new_strike"])
                if a.get("new_strike") not in (None, "", 0) else None
            ),
            strike_offset=int(offset_raw or 0),
            qty_lots=int(a.get("qty_lots") or 1),
        )
    except StrategyResolutionError as exc:
        return {"success": False, "data": {"note": str(exc)}, "logiccard": None}
    except (TypeError, ValueError) as exc:
        return {"success": False, "data": {"note": f"Bad roll parameters: {exc}"},
                "logiccard": None}
    card = _strategy_card_payload(payload)
    # Surface the roll block at top level so the chat layer leads with
    # the switch economics (net credit/debit of the roll itself).
    card["roll"] = payload.get("roll")
    card["summary"]["roll"] = payload.get("roll")
    return {"success": True, "data": card, "logiccard": None}


# ── Track C #1: chat-side workflow registration + status ────────────────


def _watcher_cadence_line() -> str:
    from backend.workflows.scheduler import _WATCHER_INTERVAL_SECONDS
    return (
        f"checked ~every {_WATCHER_INTERVAL_SECONDS}s during NSE market "
        "hours (09:15–15:30 IST, trading days)"
    )


def _trigger_summary_for_step(step_type: str, cfg: dict) -> str:
    """One human line per trigger step — grounded in the step's real
    config + the watcher's real cadence."""
    cfg = cfg or {}
    if step_type == "trigger.indicator":
        tf = str(cfg.get("timeframe") or "daily").lower()
        tf_label = ", weekly closes" if tf == "weekly" else ""
        return (
            f"{str(cfg.get('indicator', '')).upper()}"
            f"({cfg.get('period')}{tf_label}) on {cfg.get('symbol')} "
            f"{cfg.get('operator')} {cfg.get('value')} — "
            + _watcher_cadence_line()
        )
    if step_type == "trigger.price":
        return (
            f"price of {cfg.get('symbol')} {cfg.get('operator')} "
            f"₹{cfg.get('value')} — " + _watcher_cadence_line()
        )
    if step_type in ("trigger.compound", "trigger.exit_compound"):
        return (
            f"condition tree ({step_type.split('.')[1]}) — "
            + _watcher_cadence_line()
        )
    if step_type == "trigger.schedule":
        return (
            f"cron '{cfg.get('cron')}' ({cfg.get('timezone', 'UTC')}) — "
            "fires at the cron time (poller resolution ~30s)"
        )
    if step_type == "trigger.market_relative_time":
        return (
            f"{cfg.get('anchor')} {cfg.get('offset_minutes', 0):+d}min — "
            "fires at the resolved time (poller resolution ~30s)"
        )
    return f"{step_type} — evaluated by the workflow engine"


_ON_FIRE_LINE = (
    "On a fire it REGISTERS the order for your confirmation in your "
    "broker app — Pivot never auto-executes (register-not-execute)."
)


async def _register_workflow(a, kt, db, uid):
    """Persist a workflow draft and flip it ACTIVE — the same service
    path the FE 'Save & activate' button drives. Never executes
    anything; arming only."""
    from backend.models import Workflow, WorkflowStatus
    from backend.routers.workflows import (
        _register_armed_idea,
        _replace_steps,
        _validate_steps,
    )
    from backend.workflows.scheduler import (
        InvalidCronError,
        upsert_workflow_schedule,
    )

    a = a or {}
    wf = None
    workflow_id = str(a.get("workflow_id") or "").strip()
    if workflow_id:
        wf = (
            db.query(Workflow)
            .filter(Workflow.id == workflow_id, Workflow.user_id == uid)
            .first()
        )
        if wf is None:
            # Honest boundary: the anchored agent is gone / not owned. Do NOT
            # silently fall back to creating a duplicate — fail clearly.
            return {"success": False,
                    "error": f"workflow {workflow_id} not found",
                    "data": {}, "logiccard": None}
        if wf.status == WorkflowStatus.archived:
            return {"success": False,
                    "error": "cannot activate an archived workflow",
                    "data": {}, "logiccard": None}
        # "Edit with chat" amendment: when the caller also supplies steps, the
        # draft was edited — UPDATE this workflow in place (replace
        # steps/name/description, bump version) before activating, mirroring
        # PATCH /api/workflows/{id}. Without steps we just (re)activate.
        amended_steps = a.get("steps")
        has_amended_steps = isinstance(amended_steps, list) and bool(amended_steps)
        if has_amended_steps:
            steps_in = [
                {
                    "step_type": s.get("step_type"),
                    "config": s.get("config") or {},
                    "label": s.get("label"),
                }
                for s in amended_steps if isinstance(s, dict)
            ]
            try:
                _validate_steps(steps_in)
            except Exception as exc:  # HTTPException from validation_error
                detail = getattr(exc, "detail", None)
                msg = (
                    detail.get("error", {}).get("message")
                    if isinstance(detail, dict) and isinstance(detail.get("error"), dict)
                    else str(detail or exc)
                )
                return {"success": False,
                        "error": f"draft failed validation: {str(msg)[:240]}",
                        "data": {}, "logiccard": None}
            new_name = a.get("name")
            if isinstance(new_name, str) and new_name.strip():
                wf.name = new_name[:120]
            new_desc = a.get("description")
            if isinstance(new_desc, str):
                wf.description = new_desc[:500] or None
            raw_exp = a.get("expires_at") or a.get("valid_until")
            if isinstance(raw_exp, str) and raw_exp:
                from datetime import datetime as _dt
                try:
                    wf.expires_at = _dt.fromisoformat(
                        raw_exp.replace("Z", "+00:00"),
                    )
                except ValueError:
                    pass
            _replace_steps(db, wf, steps_in)
            # Bump version per the PATCH contract (runs reference the version
            # at creation time, so old run rows keep their original steps).
            wf.version = int(wf.version) + 1
        elif wf.status == WorkflowStatus.active:
            return {
                "success": True,
                "data": {
                    "workflow_id": str(wf.id),
                    "status": "active",
                    "name": str(wf.name),
                    "note": "Already live — nothing to re-register.",
                },
                "logiccard": None,
            }
    else:
        steps_in = a.get("steps")
        if not isinstance(steps_in, list) or not steps_in:
            return {
                "success": False,
                "error": (
                    "register_workflow needs the draft's steps[] (or a "
                    "workflow_id). Re-send the active draft verbatim."
                ),
                "data": {}, "logiccard": None,
            }
        steps_in = [
            {
                "step_type": s.get("step_type"),
                "config": s.get("config") or {},
                "label": s.get("label"),
            }
            for s in steps_in if isinstance(s, dict)
        ]
        try:
            _validate_steps(steps_in)
        except Exception as exc:  # HTTPException from validation_error
            detail = getattr(exc, "detail", None)
            msg = (
                detail.get("error", {}).get("message")
                if isinstance(detail, dict) and isinstance(detail.get("error"), dict)
                else str(detail or exc)
            )
            return {"success": False,
                    "error": f"draft failed validation: {str(msg)[:240]}",
                    "data": {}, "logiccard": None}
        expires_at = None
        raw_exp = a.get("expires_at") or a.get("valid_until")
        if isinstance(raw_exp, str) and raw_exp:
            from datetime import datetime as _dt
            try:
                expires_at = _dt.fromisoformat(raw_exp.replace("Z", "+00:00"))
            except ValueError:
                expires_at = None
        wf = Workflow(
            user_id=uid,
            name=str(a.get("name") or "Chat-registered agent")[:120],
            description=str(a.get("description") or "")[:500] or None,
            single_instance=True,
            status=WorkflowStatus.draft,
            expires_at=expires_at,
        )
        db.add(wf)
        try:
            db.flush()
        except SQLAlchemyError:
            # Never leak a raw DB exception (e.g. a psycopg2
            # ForeignKeyViolation / IntegrityError string) into the
            # user-facing reply — the caller interpolates `error` verbatim.
            db.rollback()
            logger.exception("[register_workflow] persist (flush) failed")
            return dict(_REGISTER_DB_ERROR)
        _replace_steps(db, wf, steps_in)

    # Activate — identical sequence to POST /workflows/{id}/activate.
    from datetime import datetime, timezone
    wf.status = WorkflowStatus.active
    wf.activated_at = datetime.now(timezone.utc)
    try:
        upsert_workflow_schedule(db, wf)
    except InvalidCronError as exc:
        db.rollback()
        return {"success": False,
                "error": f"invalid schedule on the draft: {exc}",
                "data": {}, "logiccard": None}
    try:
        db.commit()
    except SQLAlchemyError:
        # Same guard on the commit path: a deferred constraint or a
        # step-insert failure surfaces here, and its raw text must not
        # reach the chat reply.
        db.rollback()
        logger.exception("[register_workflow] commit failed")
        return dict(_REGISTER_DB_ERROR)
    db.refresh(wf)
    try:
        _register_armed_idea(db, uid, wf)
    except Exception:  # noqa: BLE001 — never block arming on paper-idea
        logger.exception("[register_workflow] armed-idea registration failed")

    triggers = [
        {
            "step_index": int(s.step_index),
            "step_type": str(s.step_type),
            "summary": _trigger_summary_for_step(
                str(s.step_type), dict(s.config or {}),
            ),
        }
        for s in sorted(wf.steps, key=lambda s: int(s.step_index))
        if str(s.step_type).startswith("trigger.")
    ]
    data = {
        "_render_hint": "workflow_draft_card",
        "workflow_id": str(wf.id),
        "status": "active",
        "name": str(wf.name),
        "description": wf.description,
        "steps": [
            {
                "step_type": str(s.step_type),
                "config": dict(s.config or {}),
                "label": s.label,
            }
            for s in sorted(wf.steps, key=lambda s: int(s.step_index))
        ],
        "triggers": triggers,
        "next_run_at": (
            wf.next_run_at.isoformat() if wf.next_run_at else None
        ),
        "on_fire": _ON_FIRE_LINE,
        "registered": True,
    }
    return {"success": True, "data": data, "logiccard": None}


async def _get_workflow_status(a, kt, db, uid):
    """Grounded armed-state readback: persisted status + the watcher's
    real cadence + (best-effort) current indicator values."""
    import asyncio

    from backend.models import Workflow, WorkflowStatus

    a = a or {}
    workflow_id = str(a.get("workflow_id") or "").strip()
    q = db.query(Workflow).filter(Workflow.user_id == uid)
    if workflow_id:
        wf = q.filter(Workflow.id == workflow_id).first()
    else:
        wf = (
            q.filter(Workflow.status != WorkflowStatus.archived)
            .order_by(
                Workflow.activated_at.desc().nullslast(),
                Workflow.created_at.desc(),
            )
            .first()
        )
    if wf is None:
        return {
            "success": True,
            "data": {
                "note": (
                    "No workflow found — nothing is armed yet. A draft on "
                    "screen is NOT live until it's registered/activated."
                ),
                "armed": False,
            },
            "logiccard": None,
        }

    status = wf.status.value if hasattr(wf.status, "value") else str(wf.status)
    armed = status == "active"
    triggers = []
    for s in sorted(wf.steps, key=lambda s: int(s.step_index)):
        st = str(s.step_type)
        if not st.startswith("trigger."):
            continue
        cfg = dict(s.config or {})
        entry = {
            "step_index": int(s.step_index),
            "step_type": st,
            "summary": _trigger_summary_for_step(st, cfg),
        }
        if st == "trigger.indicator":
            # Best-effort live value so "current RSI 47.2 — waiting"
            # is grounded, never guessed.
            try:
                from backend.workflows.scheduler import _compute_indicator_sync
                value = await asyncio.to_thread(
                    _compute_indicator_sync,
                    str(cfg.get("symbol", "")).upper(),
                    str(cfg.get("indicator", "")).lower(),
                    int(cfg.get("period", 14)),
                    str(cfg.get("timeframe") or "daily"),
                )
            except Exception:  # noqa: BLE001
                value = None
            entry["current_value"] = (
                round(float(value), 2) if value is not None else None
            )
            if value is not None:
                try:
                    op = str(cfg.get("operator"))
                    thr = float(cfg.get("value", 0.0))
                    cur = float(value)
                    met = (cur > thr) if op in (">", "crosses_above") else (cur < thr)
                    entry["condition_met_now"] = bool(met)
                except (TypeError, ValueError):
                    pass
        triggers.append(entry)

    data = {
        "workflow_id": str(wf.id),
        "name": str(wf.name),
        "status": status,
        "armed": armed,
        "armed_line": (
            "Live." if armed else
            f"NOT live — status is '{status}'. Register/activate it to arm."
        ),
        "triggers": triggers,
        "last_run_at": wf.last_run_at.isoformat() if wf.last_run_at else None,
        "next_run_at": wf.next_run_at.isoformat() if wf.next_run_at else None,
        "expires_at": (
            wf.expires_at.isoformat()
            if getattr(wf, "expires_at", None) else None
        ),
        "on_fire": _ON_FIRE_LINE,
    }
    return {"success": True, "data": data, "logiccard": None}


async def _get_portfolio_greeks(a, kt, db, uid):
    """Net Greeks across the user's OPEN option positions, re-marked
    against the live chain (P2) — falls back to registration snapshots
    for registered-but-unfilled (live-book) strategies."""
    from backend.models import OptionStrategy
    from backend.services.portfolio_greeks import portfolio_greeks_card

    card = portfolio_greeks_card(db, uid)
    if card["position_count"] > 0:
        card["basis"] = "Live re-mark of open paper option positions."
        return {"success": True, "data": card, "logiccard": None}

    # No filled positions — aggregate registration-time snapshots of
    # still-registered strategies (live-book intents) so "what's my
    # delta" answers honestly instead of claiming flat.
    rows = (
        db.query(OptionStrategy)
        .filter(
            OptionStrategy.user_id == uid,
            OptionStrategy.status.in_(("registered", "intent_armed")),
        )
        .all()
    )
    if not rows:
        return {"success": True, "data": card, "logiccard": None}
    net = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    by_underlying: dict[str, dict] = {}
    for s in rows:
        greeks = s.net_greeks_json or {}
        bucket = by_underlying.setdefault(
            s.underlying,
            {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0,
             "positions": 0},
        )
        bucket["positions"] += 1
        for k in net:
            v = float(greeks.get(k) or 0.0)
            net[k] += v
            bucket[k] += v
    card.update({
        "net": {k: round(v, 4) for k, v in net.items()},
        "by_underlying": {
            u: {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in b.items()}
            for u, b in by_underlying.items()
        },
        "position_count": len(rows),
        "basis": (
            "Registration-time snapshot of registered (unfilled) "
            "strategies — live marks apply once legs fill."
        ),
    })
    card.pop("note", None)
    return {"success": True, "data": card, "logiccard": None}


async def _propose_ipo_application(a, kt, db, uid):
    """Build the editable IPO application card for the chat surface.

    Returns the structured payload the FE renders as ``ipo_application_card``.
    Honest on failure: distinguishes "no match" (success:false + matches[])
    from "feed unreachable" (success:false + note) — both relayed to the
    model so it can speak honestly back to the user. Never fabricates
    IPOs / dates / bands. Never claims Pivot places the bid.
    """
    from backend.services.ipo_feed import (
        IPO_GMP_ENABLED,
        detect_registrar,
        fetch_listed_ipo,
        fetch_subscription,
        get_ipo_details,
        gmp_payload,
        parse_price_band,
        resolve_listing_date,
        resolve_rhp,
    )

    query = str(a.get("name_or_symbol", "")).strip()
    if not query:
        return {
            "success": False,
            "data": {
                "note": (
                    "Provide an IPO name or symbol — e.g. 'TIKONA' or "
                    "'Tikona Infinet' — to build the application card."
                ),
            },
            "logiccard": None,
        }

    feed = get_ipo_details(query)
    if feed.get("source") == "unreachable":
        return {
            "success": False,
            "data": {
                "note": (
                    "Live IPO feed is unreachable right now — cannot build "
                    "an application card without verified IPO data."
                ),
                "source": "unreachable",
            },
            "logiccard": None,
        }
    if not feed.get("found"):
        # P4: a LISTED IPO has dropped off the upcoming/current feeds, so
        # the apply flow's "not found" branch hides a graceful answer. Try
        # the past-issues feed and, if the symbol has listed, return the
        # ipo_listed_card with a "this IPO has already listed —
        # applications are closed" note instead of a bare not-found.
        listed = fetch_listed_ipo(query)
        if listed.get("found"):
            sym = (listed.get("symbol") or "").strip().upper() or None
            iss_raw = listed.get("issue_price")
            try:
                iss_price: float | None = (
                    float(iss_raw) if iss_raw is not None else None
                )
            except (TypeError, ValueError):
                iss_price = None
            curr = _listed_current_price(sym) if sym else None
            gain_pct: float | None
            if iss_price is not None and curr is not None and iss_price > 0:
                gain_pct = round((curr - iss_price) / iss_price * 100.0, 2)
            else:
                gain_pct = None
            base_note = (
                "this IPO has already listed — applications are closed"
            )
            if iss_price is None:
                base_note += " (issue price unavailable)"
            elif curr is None:
                base_note += " (listing data pending — no live price yet)"
            listed_payload: dict = {
                "_render_hint": "ipo_listed_card",
                "symbol": sym,
                "name": listed.get("name"),
                "type": (
                    listed.get("type")
                    if listed.get("type") in {"sme", "mainboard"}
                    else "mainboard"
                ),
                "issue_price": iss_price,
                "listing_date": listed.get("listing_date"),
                "current_price": curr,
                "listing_gain_pct": gain_pct,
                "source": "nse",
                "note": base_note,
            }
            return {
                "success": True,
                "data": listed_payload,
                "logiccard": None,
            }
        return {
            "success": False,
            "data": {
                "note": (
                    f"No live IPO matches {query!r}. "
                    + (feed.get("note") or "")
                ),
                "matches": feed.get("matches") or [],
            },
            "logiccard": None,
        }

    ipo = feed.get("ipo") or {}
    name = ipo.get("name")
    symbol = (ipo.get("symbol") or "").upper() or query.upper()
    ipo_type = "sme" if ipo.get("type") == "sme" else "mainboard"
    status_ = (ipo.get("status") or "").lower() or "upcoming"

    # Coerce lot size.
    raw_lot = ipo.get("lot_size")
    lot_size: int | None
    try:
        lot_size = int(raw_lot) if raw_lot not in (None, "") else None
    except (TypeError, ValueError):
        lot_size = None

    band = parse_price_band(ipo.get("price_band"))

    raw_extra = feed.get("extra") or {}
    # Stitch the normalized ipo dict + its _raw NSE blob into a single
    # record shape the P1 helpers (resolve_*, detect_registrar) walk.
    enrichment_record: dict[str, object] = dict(ipo)
    if isinstance(raw_extra, dict):
        enrichment_record["_raw"] = raw_extra

    # Trendlyne enrichment now rides on the merged ipo record (see
    # ipo_feed._merge_trendlyne). Prefer the NSE-derived helpers, fall back to
    # the Trendlyne fields so the card fills even when NSE is sparse.
    rhp_url = resolve_rhp(enrichment_record) or ipo.get("rhp_url")
    registrar_name, allotment_deeplink = detect_registrar(enrichment_record)
    # Trendlyne carries a BSE allotment-status check link for listing-soon IPOs.
    if not allotment_deeplink and ipo.get("allotment_check_url"):
        allotment_deeplink = ipo.get("allotment_check_url")
    listing_date = resolve_listing_date(enrichment_record) or ipo.get("listing_date")

    # Subscription % is meaningful ONLY for currently-open issues; skip
    # the network call for upcoming / closed (NSE returns "Missing
    # Symbol" anyway). Keep the as_of stamp so the FE can render
    # "as of HH:MM" once data is available.
    subscription_block: dict[str, object] | None = None
    if status_ == "open":
        sub_body = fetch_subscription(symbol)
        sub_cats = sub_body.get("subscription")
        if isinstance(sub_cats, dict):
            subscription_block = {**sub_cats, "as_of": sub_body.get("as_of")}
    # Fall back to Trendlyne's subscription breakdown (total/retail/hni/qib)
    # when NSE gave nothing — map onto the card's category keys.
    if subscription_block is None:
        tl_sub = ipo.get("subscription")
        if isinstance(tl_sub, dict) and any(v is not None for v in tl_sub.values()):
            subscription_block = {
                "overall": tl_sub.get("total"),
                "rii": tl_sub.get("retail"),
                "nii": tl_sub.get("hni"),
                "qib": tl_sub.get("qib"),
                "employee": None,
                "shareholder": None,
                "as_of": None,
                "source": "trendlyne",
            }

    # FE-driven defaults. Mainboard min 1 lot, SME min 2 lots.
    min_lots = 2 if ipo_type == "sme" else 1
    default_lots = min_lots

    # Server-side amount estimate (at cut-off — band.max).
    amount_at_cutoff: float | None
    if band is not None and lot_size is not None and lot_size > 0:
        amount_at_cutoff = float(default_lots * lot_size * band["max"])
    else:
        amount_at_cutoff = None

    cutoff_allowed = (ipo_type != "sme")

    # Closed status = read-only variant. The FE disables Register; we still
    # surface the locked fields + any RHP / allotment deep links.
    locked: dict[str, object | None] = {
        "price_band": band,
        "lot_size": lot_size,
        "open_date": ipo.get("open_date"),
        "close_date": ipo.get("close_date"),
        "issue_size": ipo.get("issue_size"),
        "rhp_url": rhp_url,
        # P1: registrar + allotment deep-link from detect_registrar.
        # The live NSE feed does not carry the registrar name yet, so
        # these resolve to (None, None) for real records today — the FE
        # then shows "Allotment: check with your broker/registrar."
        "registrar": registrar_name,
        "allotment_deeplink": allotment_deeplink,
        # P1: per-category subscription multiples. Populated only when
        # status=="open"; otherwise None → FE renders
        # "Subscription not available."
        "subscription": subscription_block,
        # P1: listing date — populated on listed/past records, None on
        # upcoming/active. Honest-null, never fabricated.
        "listing_date": listing_date,
        # Trendlyne: allotment date + status for listing-soon IPOs (the
        # allotment_deeplink above is the BSE status-check link).
        "allotment_date": ipo.get("allotment_date"),
        "allotment_status": ipo.get("allotment_status"),
    }
    editable = {
        "category": "retail",
        "quantity_lots": default_lots,
        "bid_price_mode": "cutoff" if cutoff_allowed else "fixed",
        "bid_price": None,
        "upi_id": "",
    }
    validation = {
        "min_lots": min_lots,
        "lot_size": lot_size,
        "amount_estimate_at_cutoff": amount_at_cutoff,
        # Mainboard retail cap (₹2L). SME bypasses this cap intentionally.
        "retail_max_amount": 200000,
        "sme_bypasses_retail_cap": True,
        "upi_cap": 500000,
        "cutoff_allowed": cutoff_allowed,
        "price_band": band,
        "category_options": [
            "retail", "snii", "bnii", "shareholder", "employee",
        ],
    }

    payload: dict[str, object] = {
        "_render_hint": "ipo_application_card",
        "symbol": symbol,
        "name": name,
        "type": ipo_type,
        "status": status_ if status_ in {"upcoming", "open", "closed"} else "upcoming",
        "locked": locked,
        "editable": editable,
        # KYC OMITTED in P0 by design — the FE renders a single line about
        # broker-stored KYC. Never store / render fake PAN/demat data.
        "kyc": None,
        "validation": validation,
        # P2: trigger.ipo_open + action.arm_ipo_intent open-day reminder
        # workflow is now buildable via propose_ipo_automation.
        "automatable": True,
        "conversation_id": a.get("conversation_id"),
        # Honest provenance: which feeds populated this card (NSE skeleton +
        # Trendlyne enrichment). The FE renders a small "Data: …" line.
        "data_sources": ipo.get("sources") or ["nse"],
        "disclaimer": (
            "Pivot can't submit or fund this bid. This registers your "
            "intent only; YOU place and approve the mandate in your "
            "broker/UPI app by 5 PM on close day."
        ),
    }

    # GMP is fail-closed OFF in v1: only attach the "gmp" key when the
    # flag is on AND gmp_payload returns a value (which it doesn't in
    # v1 — no vendor wired). When omitted, the FE never renders the
    # chip; that's the intended behaviour.
    if IPO_GMP_ENABLED:
        gmp = gmp_payload(symbol)
        if gmp is not None:
            payload["gmp"] = gmp

    return {"success": True, "data": payload, "logiccard": None}


async def _propose_ipo_automation(a, kt, db, uid):
    """Build a workflow_draft_card for "set up open-day reminders for X IPO".

    Returns the same payload shape the WorkflowDraftCard already renders
    (the draft carries `_render_hint: "workflow_draft_card"`). The 3-step
    draft is:

      [0] trigger.ipo_open      { symbol }                      # fires once on open
      [1] action.arm_ipo_intent { ipo_symbol, lots, category,   # writes intent_armed
                                  bid_price_mode, bid_price? }
      [2] notify.message        { template: "<open-day handoff>" }

    Sensible defaults: 1 lot mainboard / 2 lots SME, retail category,
    cut-off mode for mainboard retail (allowed) else fixed (the user can
    edit lots / category / bid mode on the card before activating).
    Honest not-found / unreachable fallbacks — never fabricate.
    """
    from backend.services.ipo_feed import get_ipo_details
    from backend.services.workflow_macros import build_ipo_reminder_draft

    query = str(a.get("name_or_symbol", "")).strip()
    if not query:
        return {
            "success": False,
            "data": {
                "note": (
                    "Provide an IPO name or symbol — e.g. 'TIKONA' or "
                    "'Tikona Infinet' — to build the open-day reminder "
                    "workflow."
                ),
            },
            "logiccard": None,
        }

    feed = get_ipo_details(query)
    if feed.get("source") == "unreachable":
        return {
            "success": False,
            "data": {
                "note": (
                    "Live IPO feed is unreachable right now — cannot "
                    "build a reminder workflow without verified IPO "
                    "data. Try again in a minute."
                ),
                "source": "unreachable",
            },
            "logiccard": None,
        }
    if not feed.get("found"):
        return {
            "success": False,
            "data": {
                "note": (
                    f"No live IPO matches {query!r}. "
                    + (feed.get("note") or "")
                ),
                "matches": feed.get("matches") or [],
            },
            "logiccard": None,
        }

    ipo = feed.get("ipo") or {}
    symbol = (ipo.get("symbol") or query).strip().upper()
    ipo_type = "sme" if ipo.get("type") == "sme" else "mainboard"

    # Sensible defaults per IPO type. The user can edit before activating.
    quantity_lots = 2 if ipo_type == "sme" else 1
    category = "retail"
    bid_price_mode = "fixed" if ipo_type == "sme" else "cutoff"

    bid_price = None
    if bid_price_mode == "fixed":
        # For SME issues (no cut-off allowed), pin to band.max so the
        # arm action has an honest in-band default. Surface None when
        # the band is missing rather than fabricating a number.
        from backend.services.ipo_feed import parse_price_band
        band = parse_price_band(ipo.get("price_band"))
        if band is not None and band.get("max") is not None:
            bid_price = float(band["max"])

    try:
        draft = build_ipo_reminder_draft(
            symbol, ipo,
            quantity_lots=quantity_lots,
            category=category,
            bid_price_mode=bid_price_mode,
            bid_price=bid_price,
        )
    except ValueError as e:
        return {
            "success": False,
            "data": {
                "note": (
                    f"Couldn't build a reminder workflow for {symbol}: {e}. "
                    "Try setting up reminders manually via the IPO card."
                ),
            },
            "logiccard": None,
        }

    return {"success": True, "data": draft, "logiccard": None}


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


async def _compute(a, kt, db, uid):
    """COMPUTE lane — deterministic sandboxed math over in-context values.

    The subprocess spawn (~100ms) blocks; run it off the event loop. A
    failed validation/execution comes back success=False with a message
    written for the model, so the tool loop self-corrects in-turn instead
    of surfacing a generic apology."""
    import asyncio
    from backend.services.safe_compute import run_compute
    code = str(a.get("code") or "")
    res = await asyncio.to_thread(run_compute, code)
    if not res.ok:
        return {
            "success": False,
            "error": f"compute failed: {res.error}",
            "data": {},
            "logiccard": None,
        }
    return {
        "success": True,
        "data": {
            "result": res.result,
            "note": a.get("note"),
            # Nudge the reply to quote these exact values, not re-derive.
            "_guidance": "Present `result` values verbatim — do not re-do "
                         "the arithmetic in prose.",
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
    sym = (a.get("symbol") or "").upper()
    if not price and sym:
        from backend.agents.context_injector import _cached_price
        d = _cached_price(sym)
        price = d.get("ltp") if d else None
    # L34 fix: cache miss → yfinance fallback so the calc doesn't fail
    # silently and surface a raw provider error to the chat layer.
    if not price and sym:
        try:
            import yfinance as yf
            yf_sym = sym if sym.endswith(".NS") else f"{sym}.NS"
            hist = yf.Ticker(yf_sym).history(period="5d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
        except Exception:
            price = None
    if not price:
        return {"success": False,
                "data": {"error": f"Could not fetch a live price for {sym or 'the symbol'}"},
                "logiccard": None}
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


# ── Registry derivation surface ──────────────────────────────────────
# Built once at import (all handler defs above are resolved by now).
# STUB_TOOLS self-derives: anything still wired to `_generic_confirm`
# is a placeholder and must NOT be shown to the LLM. Swapping a stub
# for a real handler makes the tool visible with no other edit.
HANDLERS: dict = _build_handlers()
STUB_TOOLS: frozenset = frozenset(
    name for name, fn in HANDLERS.items() if fn is _generic_confirm
)

# Consolidated view-enum tools (chat-kernel Phase 1, 2026-07-10).
# Importing the module registers their schemas (tools.tool(...) at
# import time); merging their handlers here makes them visible via the
# registry derivation. The narrow tools they supersede stay in HANDLERS
# as dispatch targets but are hidden from the LLM by tool_registry's
# _HIDDEN_TOOLS (see SUPERSEDED_BY_CONSOLIDATION).
from backend.agents.consolidated_handlers import CONSOLIDATED_HANDLERS  # noqa: E402

HANDLERS.update(CONSOLIDATED_HANDLERS)
