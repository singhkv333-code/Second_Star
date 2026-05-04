"""
backend/agents/tools.py

All Pivot tool definitions in OpenAI function calling format.
Sarvam reads these at call time to understand available actions.

TOOL SUBSETS — intent classifier returns a subset name.
Main call only receives tools in that subset.
Prevents model seeing 50+ tools simultaneously.
"""

TOOL_SUBSETS = {
    "ORDER_IMMEDIATE":   ["place_market_order", "place_limit_order", "get_live_price"],
    "ORDER_CONDITIONAL": ["create_gtt_order", "create_sl_order", "create_oco_order", "create_dip_buy", "get_live_price"],
    "ORDER_RECURRING":   ["create_sip", "list_sips", "pause_sip", "resume_sip", "delete_sip", "pause_all_sips"],
    "ORDER_BASKET":      ["place_basket_order", "get_live_price"],
    "ORDER_FNO":         ["place_futures_order", "place_options_order", "place_multileg_options", "roll_futures_position", "get_option_chain", "get_option_greeks", "get_margin_required"],
    "ORDER_MANAGE":      ["cancel_order", "modify_order", "list_pending_orders", "list_gtt_orders", "cancel_gtt", "squareoff_all_intraday", "squareoff_symbol"],
    "PORTFOLIO_QUERY":   ["get_portfolio_summary", "get_holdings", "get_sector_breakdown", "get_holding_detail", "get_tax_summary", "get_active_products"],
    "MARKET_QUERY":      ["get_live_price", "get_index_level", "get_ohlc", "get_52wk_range", "get_market_status", "get_upcoming_events", "get_option_chain"],
    "AUTOMATION_CREATE": ["create_strategy", "create_cash_sweep", "create_rebalancing_rule", "create_drawdown_protection", "propose_workflow"],
    "AUTOMATION_MANAGE": ["list_strategies", "pause_strategy", "resume_strategy", "delete_strategy"],
    "WORKFLOW_PROPOSE":  ["propose_workflow"],
    "SIP_MANAGE":        ["list_sips", "pause_sip", "resume_sip", "delete_sip", "pause_all_sips"],
    "YIELD_QUERY":       ["compare_yields", "get_yield_recommendation"],
    "CALCULATION":       ["calculate_order_qty", "calculate_tax_impact", "calculate_sl_price", "calculate_dip_price", "calculate_margin"],
    "BACKTEST":          ["run_backtest"],
    "SCHEDULER":         ["get_scheduler_status", "list_upcoming_jobs"],
    "GENERAL":           [],
}

ALL_TOOLS = {}

# Declarative defaults registry — single source of truth for optional-field
# values. The chat LLM only fills REQUIRED fields; optional fields get merged
# in by `tool_executor.execute_tool` and `services.tool_registry.execute`
# right before the handler runs. User-supplied values always win.
_TOOL_DEFAULTS: dict[str, dict] = {}


def tool(name, description, properties, required, defaults=None):
    defn = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            }
        }
    }
    ALL_TOOLS[name] = defn
    if defaults:
        _TOOL_DEFAULTS[name] = dict(defaults)
    return defn


def get_tool_defaults(name: str) -> dict:
    """Returns a copy of the documented defaults for a tool, or {}.

    Used by the executors to auto-fill optional fields (exchange, product,
    order_type, etc.) before dispatching to the handler. Always returns a
    fresh dict — callers may mutate it freely.
    """
    return dict(_TOOL_DEFAULTS.get(name, {}))


# ── ORDER EXECUTION ──────────────────────────────────────────────────────────

tool("place_market_order",
     "Places an immediate market order via Zerodha Kite. "
     "Use when user wants to buy or sell RIGHT NOW at current price. "
     "Do NOT use for conditional orders (use create_gtt_order). "
     "Do NOT use for recurring orders (use create_sip). "
     "Always requires user confirmation before execution.",
     {
         "symbol":           {"type": "string", "description": "NSE ticker uppercase e.g. INFY"},
         "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]},
         "quantity":         {"type": "integer", "minimum": 1},
         "exchange":         {"type": "string", "enum": ["NSE", "BSE"], "default": "NSE"},
         "product":          {"type": "string", "enum": ["CNC", "MIS"], "default": "CNC",
                              "description": "CNC=delivery, MIS=intraday"},
     },
     ["symbol", "transaction_type", "quantity"],
     defaults={"exchange": "NSE", "product": "CNC", "order_type": "MARKET"})

tool("place_limit_order",
     "Places a limit order that only executes at the specified price or better. "
     "Use when user specifies a price: 'buy INFY at 1450'. "
     "Do NOT use for GTT (trigger-based) orders.",
     {
         "symbol":           {"type": "string"},
         "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]},
         "quantity":         {"type": "integer", "minimum": 1},
         "price":            {"type": "number", "description": "Limit price in INR"},
         "exchange":         {"type": "string", "enum": ["NSE", "BSE"], "default": "NSE"},
         "product":          {"type": "string", "enum": ["CNC", "MIS"], "default": "CNC"},
     },
     ["symbol", "transaction_type", "quantity", "price"],
     defaults={"exchange": "NSE", "product": "CNC", "order_type": "LIMIT"})

tool("create_gtt_order",
     "Creates a GTT order that fires when a price condition is met. Zerodha monitors it. "
     "Use for: 'buy if it falls to X', 'sell if it hits X'. One-time conditional order. "
     "Do NOT use for recurring orders. Do NOT use for immediate execution.",
     {
         "symbol":           {"type": "string"},
         "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]},
         "quantity":         {"type": "integer", "minimum": 1},
         "trigger_price":    {"type": "number", "description": "Price that activates the order"},
         "limit_price":      {"type": "number", "description": "Execution price after trigger"},
         "exchange":         {"type": "string", "enum": ["NSE", "BSE"], "default": "NSE"},
     },
     ["symbol", "transaction_type", "quantity", "trigger_price", "limit_price"],
     defaults={"exchange": "NSE", "product": "CNC"})

tool("create_sl_order",
     "Creates a stop-loss GTT order to protect a holding. "
     "Use for: 'put a stop loss on INFY', 'exit if it falls 5%'. "
     "Accepts stop_price OR stop_pct. If stop_pct given, needs entry_price to calculate.",
     {
         "symbol":       {"type": "string"},
         "quantity":     {"type": "integer", "minimum": 1},
         "stop_price":   {"type": "number", "description": "Absolute exit price. Provide this OR stop_pct."},
         "stop_pct":     {"type": "number", "description": "% below current price e.g. 5 means 5% drop"},
         "entry_price":  {"type": "number", "description": "Buy price, used to calculate stop from stop_pct"},
     },
     ["symbol", "quantity"],
     defaults={"exchange": "NSE", "product": "CNC"})

tool("create_oco_order",
     "Creates OCO (One Cancels Other): target sell + stop-loss sell. "
     "When one triggers the other cancels. "
     "Use for: 'set target 1600 and stop 1400 on INFY'.",
     {
         "symbol":       {"type": "string"},
         "quantity":     {"type": "integer", "minimum": 1},
         "target_price": {"type": "number", "description": "Sell if price RISES to this"},
         "stop_price":   {"type": "number", "description": "Sell if price FALLS to this"},
     },
     ["symbol", "quantity", "target_price", "stop_price"],
     defaults={"exchange": "NSE", "product": "CNC"})

tool("create_dip_buy",
     "Creates a GTT buy at a price calculated from a dip percentage. "
     "Use for: 'buy INFY if it dips 5%', 'buy on a 3% correction'. "
     "Calculates: trigger = current * (1 - dip_pct/100). qty = floor(budget/trigger). "
     "Always ask for budget_inr if not provided.",
     {
         "symbol":     {"type": "string"},
         "dip_pct":    {"type": "number", "description": "Percentage dip from current price"},
         "budget_inr": {"type": "number", "description": "Total INR to invest when triggered"},
     },
     ["symbol", "dip_pct", "budget_inr"],
     defaults={"exchange": "NSE", "product": "CNC"})

tool("place_basket_order",
     "Places simultaneous orders for multiple stocks. "
     "Use for: 'buy INFY, TCS, and WIPRO together'. All legs execute at once.",
     {
         "legs": {
             "type": "array", "minItems": 2,
             "items": {
                 "type": "object",
                 "properties": {
                     "symbol":           {"type": "string"},
                     "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]},
                     "quantity":         {"type": "integer"},
                     "order_type":       {"type": "string", "enum": ["MARKET", "LIMIT"]},
                     "price":            {"type": "number"},
                 },
                 "required": ["symbol", "transaction_type", "quantity", "order_type"]
             }
         }
     },
     ["legs"],
     defaults={"exchange": "NSE", "product": "CNC"})

tool("cancel_order",
     "Cancels a pending regular or limit order by order_id.",
     {"order_id": {"type": "string", "description": "Kite order ID"}},
     ["order_id"])

tool("modify_order",
     "Modifies price or quantity of a pending limit order. "
     "Use for: 'change my INFY order to 1430'.",
     {
         "order_id":     {"type": "string"},
         "new_price":    {"type": "number"},
         "new_quantity": {"type": "integer", "minimum": 1},
     },
     ["order_id"])

tool("squareoff_all_intraday",
     "Closes all open MIS (intraday) positions immediately. "
     "Use for: 'close all intraday', 'square off everything'.",
     {}, [])

tool("squareoff_symbol",
     "Closes all positions in a specific symbol. "
     "Use for: 'exit all my INFY positions'.",
     {"symbol": {"type": "string"}},
     ["symbol"])

tool("list_pending_orders",
     "Returns all pending/open orders for today.",
     {}, [])

tool("list_gtt_orders",
     "Returns all active GTT orders.",
     {}, [])

tool("cancel_gtt",
     "Cancels an active GTT order by trigger_id.",
     {"trigger_id": {"type": "string"}},
     ["trigger_id"])

# ── F&O ──────────────────────────────────────────────────────────────────────

tool("place_futures_order",
     "Places a futures order on NSE NFO. Always NRML product type. Quantity in lots. "
     "Use for: 'buy 1 lot NIFTY futures', 'sell BankNifty'.",
     {
         "underlying":       {"type": "string", "description": "e.g. NIFTY, BANKNIFTY, RELIANCE"},
         "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]},
         "lots":             {"type": "integer", "minimum": 1},
         "expiry":           {"type": "string", "default": "current", "description": "current, next, or date"},
         "order_type":       {"type": "string", "enum": ["MARKET", "LIMIT"], "default": "MARKET"},
         "price":            {"type": "number"},
     },
     ["underlying", "transaction_type", "lots"])

tool("place_options_order",
     "Places a single-leg options order (buy or write). "
     "For straddles/strangles/spreads use place_multileg_options.",
     {
         "underlying":       {"type": "string"},
         "option_type":      {"type": "string", "enum": ["CE", "PE"]},
         "strike":           {"type": "number"},
         "expiry":           {"type": "string", "default": "current_week"},
         "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]},
         "lots":             {"type": "integer", "minimum": 1},
         "order_type":       {"type": "string", "enum": ["MARKET", "LIMIT"], "default": "MARKET"},
         "price":            {"type": "number"},
     },
     ["underlying", "option_type", "strike", "transaction_type", "lots"])

tool("place_multileg_options",
     "Places multiple options legs simultaneously: straddles, strangles, spreads, covered calls. "
     "Use for: 'buy ATM straddle', 'covered call on my RELIANCE'.",
     {
         "underlying": {"type": "string"},
         "expiry":     {"type": "string", "default": "current_week"},
         "strategy":   {"type": "string",
                        "enum": ["straddle","strangle","call_spread","put_spread",
                                 "covered_call","protective_put","custom"]},
         "lots":       {"type": "integer", "minimum": 1, "default": 1},
         "legs":       {"type": "array", "description": "Required only when strategy=custom"},
     },
     ["underlying", "strategy"])

tool("roll_futures_position",
     "Rolls a futures position from current to next expiry. "
     "Use for: 'roll my NIFTY futures', 'roll to next month'.",
     {
         "underlying": {"type": "string"},
         "lots":       {"type": "integer", "minimum": 1},
     },
     ["underlying", "lots"])

tool("get_option_chain",
     "Returns full option chain with bid/ask, OI, IV for all strikes. "
     "Use when user asks about option premiums or wants to pick a strike.",
     {
         "underlying": {"type": "string"},
         "expiry":     {"type": "string", "default": "current_week"},
     },
     ["underlying"])

tool("get_option_greeks",
     "Returns Greeks (Delta, Theta, Vega, Gamma, IV) for an option.",
     {
         "underlying":  {"type": "string"},
         "option_type": {"type": "string", "enum": ["CE", "PE"]},
         "strike":      {"type": "number"},
         "expiry":      {"type": "string"},
     },
     ["underlying", "option_type", "strike"])

tool("get_margin_required",
     "Calculates margin required for an F&O position before placing it.",
     {
         "underlying":       {"type": "string"},
         "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]},
         "instrument_type":  {"type": "string", "enum": ["futures", "options"]},
         "lots":             {"type": "integer"},
     },
     ["underlying", "transaction_type", "instrument_type", "lots"])

# ── SIP ──────────────────────────────────────────────────────────────────────

tool("create_sip",
     "Creates a recurring investment schedule. Executes at 09:15 IST on chosen schedule. "
     "Quantity calculated from live price at execution — user specifies INR amount not shares. "
     "Use for: 'invest 5000 monthly in NIFTYBEES', 'weekly SIP in TCS'.",
     {
         "symbol":          {"type": "string"},
         "amount_inr":      {"type": "number", "minimum": 100, "description": "INR per execution"},
         "frequency":       {"type": "string", "enum": ["daily", "weekly", "monthly"]},
         "day_of_month":    {"type": "integer", "minimum": 1, "maximum": 28,
                             "description": "Required for monthly"},
         "day_of_week":     {"type": "integer", "minimum": 0, "maximum": 4,
                             "description": "0=Mon 4=Fri. Required for weekly."},
         "instrument_type": {"type": "string", "enum": ["etf","stock","mutual_fund"], "default": "etf"},
         "name":            {"type": "string"},
     },
     ["symbol", "amount_inr", "frequency"],
     defaults={"exchange": "NSE", "product": "CNC", "order_type": "MARKET"})

tool("list_sips",   "Returns all SIPs with next execution time in IST.", {}, [])
tool("pause_sip",   "Pauses a SIP. Stops execution until resumed.",
     {"sip_id": {"type": "integer"}}, ["sip_id"])
tool("resume_sip",  "Resumes a paused SIP. Next run recalculated in IST.",
     {"sip_id": {"type": "integer"}}, ["sip_id"])
tool("delete_sip",  "Permanently deletes a SIP.",
     {"sip_id": {"type": "integer"}}, ["sip_id"])
tool("pause_all_sips", "Pauses ALL active SIPs. Use for: 'stop all recurring investments'.",
     {}, [])

# ── STRATEGIES ───────────────────────────────────────────────────────────────

tool("create_strategy",
     "Creates an automation rule that monitors a condition and fires an order. "
     "Runs every 60 seconds during market hours IST. "
     "Use for RSI, price crossover, moving average signals. "
     "For cash management use create_cash_sweep. For rebalancing use create_rebalancing_rule.",
     {
         "name":           {"type": "string"},
         "trigger_type":   {"type": "string",
                            "enum": ["price_drop","price_cross","rsi","macd","ma_crossover","scheduled"]},
         "trigger_symbol": {"type": "string", "description": "Stock or index to watch"},
         "trigger_params": {"type": "object",
                            "description": "price_drop:{threshold_pct,reference_price} "
                            "price_cross:{target_price,direction:above|below} "
                            "rsi:{threshold,period,direction:above|below} "
                            "scheduled:{day_of_week,time_ist}"},
         "action":         {"type": "object",
                            "description": "{transaction_type,symbol,quantity_or_amount,order_type}"},
         "max_budget_inr": {"type": "number", "maximum": 200000},
     },
     ["name", "trigger_type", "trigger_params", "action"],
     defaults={"exchange": "NSE"})

tool("create_cash_sweep",
     "Creates a rule to automatically move idle cash above/below a threshold. "
     "Use for: 'invest excess cash above 1 lakh', 'auto-replenish cash when balance falls below 20k'.",
     {
         "direction":           {"type": "string", "enum": ["sweep_in", "sweep_out"]},
         "threshold_inr":       {"type": "number"},
         "target_instrument":   {"type": "string", "description": "Where to park or withdraw from"},
         "sweep_amount_or_pct": {"type": "number"},
     },
     ["direction", "threshold_inr", "target_instrument"])

tool("create_rebalancing_rule",
     "Creates a scheduled portfolio rebalancing rule. "
     "Use for: 'rebalance to 60/40 every month end'.",
     {
         "frequency":      {"type": "string", "enum": ["monthly","quarterly","on_breach"]},
         "target_weights": {"type": "array",
                            "items": {"type": "object",
                                      "properties": {"symbol": {"type": "string"},
                                                     "pct": {"type": "number"}}}},
         "tolerance_pct":  {"type": "number", "default": 5},
     },
     ["frequency", "target_weights"])

tool("create_drawdown_protection",
     "Creates portfolio circuit breaker. Moves to safe assets if portfolio drops X%. "
     "Use for: 'move to liquid fund if portfolio falls 15%'.",
     {
         "drawdown_pct": {"type": "number"},
         "action":       {"type": "string",
                          "enum": ["move_to_liquid","move_to_gsec","reduce_equity_pct"]},
         "reduce_pct":   {"type": "number"},
     },
     ["drawdown_pct", "action"])

tool("list_strategies", "Returns all strategies with status and last triggered time in IST.", {}, [])
tool("pause_strategy",  "Pauses a strategy by ID.",
     {"strategy_id": {"type": "integer"}}, ["strategy_id"])
tool("resume_strategy", "Resumes a paused strategy.",
     {"strategy_id": {"type": "integer"}}, ["strategy_id"])
tool("delete_strategy", "Permanently deletes a strategy.",
     {"strategy_id": {"type": "integer"}}, ["strategy_id"])

# ── PORTFOLIO ────────────────────────────────────────────────────────────────

tool("get_portfolio_summary",
     "Returns total portfolio value, day P&L, total P&L, holdings count. "
     "Use for: 'show my portfolio', 'how am I doing today'.",
     {}, [])

tool("get_holdings",
     "Returns all holdings with live prices, quantities, P&L. "
     "Use for: 'show all my stocks', 'what do I hold'.",
     {"sort_by": {"type": "string", "enum": ["value","pnl","day_change","symbol"],
                  "default": "value"}},
     [])

tool("get_sector_breakdown",
     "Returns portfolio allocation by sector. Flags concentrated sectors. "
     "Use for: 'show sector breakdown', 'am I too concentrated in IT'.",
     {}, [])

tool("get_holding_detail",
     "Returns detailed info on one holding: avg price, all-time P&L, holding period, STCG/LTCG status. "
     "Use for: 'how much have I made on INFY', 'when did I buy TCS'.",
     {"symbol": {"type": "string"}},
     ["symbol"],
     defaults={"exchange": "NSE"})

tool("get_tax_summary",
     "Returns STCG vs LTCG breakdown and tax-loss harvesting candidates. "
     "Use for: 'show my tax situation', 'what can I sell for TLH'.",
     {}, [])

tool("get_active_products",
     "Returns active synthetic investment products with maturity dates in IST.",
     {}, [])

# ── MARKET DATA ──────────────────────────────────────────────────────────────

tool("get_live_price",
     "Returns current live price for any NSE stock or ETF.",
     {"symbol": {"type": "string"}},
     ["symbol"],
     defaults={"exchange": "NSE"})

tool("get_index_level",
     "Returns current level of Nifty 50, BankNifty, or Sensex.",
     {"index": {"type": "string", "enum": ["NIFTY50","BANKNIFTY","SENSEX"], "default": "NIFTY50"}},
     [])

tool("get_ohlc",
     "Returns Open, High, Low, Close for a stock.",
     {
         "symbol": {"type": "string"},
         "period": {"type": "string", "enum": ["today","1w","1m","3m","1y"], "default": "today"},
     },
     ["symbol"],
     defaults={"exchange": "NSE"})

tool("get_52wk_range",
     "Returns 52-week high and low for a stock.",
     {"symbol": {"type": "string"}},
     ["symbol"],
     defaults={"exchange": "NSE"})

tool("get_market_status",
     "Returns whether NSE is open, current time in IST, time until open/close, upcoming holidays.",
     {}, [])

tool("get_upcoming_events",
     "Returns upcoming earnings, RBI meeting dates, ex-dividend dates, F&O expiry dates.",
     {}, [])

# ── YIELDS ───────────────────────────────────────────────────────────────────

tool("compare_yields",
     "Returns after-tax yield comparison across savings account, FD, liquid fund, "
     "overnight fund, arbitrage fund, G-Sec. "
     "Use for: 'where should I park my cash', 'compare yields'.",
     {"tax_slab": {"type": "number", "enum": [0.05, 0.20, 0.30], "default": 0.30}},
     [])

tool("get_yield_recommendation",
     "Returns the single best instrument to park idle cash right now. "
     "Use for: 'what should I do with idle cash', 'best FD alternative'.",
     {
         "amount_inr":   {"type": "number"},
         "horizon_days": {"type": "integer"},
         "tax_slab":     {"type": "number", "default": 0.30},
     },
     [])

# ── CALCULATIONS ─────────────────────────────────────────────────────────────

tool("calculate_order_qty",
     "Calculates shares to buy from a rupee budget and price. "
     "Use before placing when user specifies INR amount not share count.",
     {
         "budget_inr": {"type": "number"},
         "price":      {"type": "number", "description": "Uses live price if not given"},
         "symbol":     {"type": "string", "description": "Required if price not given"},
     },
     ["budget_inr"])

tool("calculate_tax_impact",
     "Estimates STCG or LTCG tax on a proposed sale. "
     "Shows whether holding qualifies for LTCG (held > 1 year).",
     {
         "symbol":   {"type": "string"},
         "quantity": {"type": "integer"},
         "tax_slab": {"type": "number", "default": 0.30},
     },
     ["symbol", "quantity"])

tool("calculate_sl_price",
     "Calculates stop-loss price from a percentage. e.g. 5% stop on entry 1500 = stop at 1425.",
     {
         "entry_price": {"type": "number"},
         "stop_pct":    {"type": "number"},
     },
     ["entry_price", "stop_pct"])

tool("calculate_dip_price",
     "Calculates what price represents a dip% from current, and how many shares budget buys.",
     {
         "symbol":     {"type": "string"},
         "dip_pct":    {"type": "number"},
         "budget_inr": {"type": "number"},
     },
     ["symbol", "dip_pct", "budget_inr"])

tool("calculate_margin",
     "Calculates margin needed for an order before placement.",
     {
         "symbol":   {"type": "string"},
         "quantity": {"type": "integer"},
         "product":  {"type": "string", "enum": ["CNC","MIS","NRML"]},
     },
     ["symbol", "quantity", "product"])

# ── BACKTEST ─────────────────────────────────────────────────────────────────

tool("run_backtest",
     "Runs a strategy simulation on historical data. "
     "Returns total return %, win rate %, max drawdown %, trade log. "
     "Always appends disclaimer about past performance.",
     {
         "symbol":            {"type": "string"},
         "strategy_type":     {"type": "string",
                               "enum": ["sip","price_drop","rsi","price_cross"]},
         "trigger_condition": {"type": "object"},
         "period":            {"type": "string",
                               "enum": ["1mo","3mo","6mo","1y","2y"], "default": "1y"},
         "starting_capital":  {"type": "number", "default": 100000},
     },
     ["symbol", "strategy_type", "trigger_condition"])

# ── SCHEDULER ────────────────────────────────────────────────────────────────

tool("get_scheduler_status",
     "Returns scheduler health and next job times in IST. "
     "Use for: 'is automation running', 'when is my next SIP'.",
     {}, [])

tool("list_upcoming_jobs",
     "Returns all upcoming scheduled SIP and strategy jobs with IST timestamps.",
     {}, [])

# ── AGENT SYSTEM (Workflows v1) ──────────────────────────────────────────────


def _build_propose_workflow_schema() -> tuple[dict, list[str]]:
    """Generate a discriminated-union schema for `steps[].items` from the
    workflow registry, plus a flat list of step_type names.

    Each registered step type contributes one `oneOf` branch with its
    Pydantic-derived config schema inlined as `properties.config`. This
    is what stops the model from emitting empty `config: {}` — the
    schema now requires per-step-type keys (e.g. `cron` for
    `trigger.schedule`, `symbol/side/quantity` for `action.place_order`).

    Returns (steps_array_schema, step_type_names).
    """
    from backend.workflows.registry import STEP_REGISTRY

    branches: list[dict] = []
    names: list[str] = []
    for step_type in sorted(STEP_REGISTRY.keys()):
        defn = STEP_REGISTRY[step_type]
        config_schema = dict(defn.config_schema or {"type": "object"})
        # Pydantic injects "$defs" + "properties.title"; the discriminated
        # union doesn't need either at branch-level.
        branches.append({
            "type": "object",
            "properties": {
                "step_type": {"const": step_type},
                "label": {
                    "type": "string",
                    "description": "Short human label for this step.",
                },
                "config": config_schema,
            },
            "required": ["step_type", "config"],
        })
        names.append(step_type)

    return {
        "type": "array",
        "minItems": 1,
        "description": "Ordered list of steps. First item MUST be a trigger.*.",
        "items": {"oneOf": branches},
    }, names


_PROPOSE_STEPS_SCHEMA, _PROPOSE_STEP_TYPES = _build_propose_workflow_schema()


tool("propose_workflow",
     "FALLBACK workflow builder. Emit the full draft (name + steps[]) as "
     "structured arguments. Use this ONLY when none of the four macro "
     "tools fits the user's request:\n"
     "  - `propose_scheduled_order` → 'every weekday/Monday at HH:MM "
     "(buy|sell) N SYMBOL' patterns. ALWAYS prefer this for SIP-style.\n"
     "  - `propose_threshold_order` → '(buy|sell) N SYMBOL when "
     "(RSI|SMA|EMA|price) (<|>|crosses) X' patterns with absolute "
     "thresholds.\n"
     "  - `propose_basket_allocation` → 'invest ₹X across top N "
     "<sector> stocks' patterns.\n"
     "  - `propose_holding_action` → 'sell my SYMBOL when X' / 'set "
     "Y% SL on my SYMBOL' patterns.\n"
     "Use propose_workflow when the request needs runtime-relative "
     "thresholds ('5% below today's open'), multi-trigger workflows "
     "(two independent branches), explicit conditions on portfolio "
     "state ('if buying power > 50K'), or notify steps with custom "
     "templates. The macros emit ~20-30 tokens; this tool emits "
     "~1000. Always check macros first.\n\n"
     "EMITTING IS NOT ACTIVATING. The draft you return is rendered as a "
     "review card the user inspects and edits before clicking Activate. "
     "Calling propose_workflow does NOT place orders, does NOT persist, "
     "and does NOT need the user's prior confirmation. Do NOT call "
     "ASK_USER to ask 'do you confirm?' or 'should I proceed?' before "
     "emitting — the user confirms BY clicking Activate on the draft "
     "card. Just emit the draft.\n\n"
     "A workflow is an ordered list of steps grouped into BRANCHES. "
     "Step 0 MUST be a trigger.*. Additional trigger.* steps may "
     "appear at any later index — each new trigger starts a fresh "
     "branch. Steps after a trigger up to the NEXT trigger (or end of "
     "the workflow) belong to that branch. When any trigger fires, the "
     "engine runs ONLY that trigger's branch, not the whole workflow.\n\n"
     "A request like 'buy NIFTYBEES every Monday 9:15 AND sell it Monday "
     "close if RSI<30' is ONE workflow with two triggers / two branches "
     "— not two separate agents. Two adjacent trigger.* steps (an empty "
     "branch) is invalid; every trigger needs at least one action / "
     "condition / fetch step.\n\n"
     "Inter-step references use Mustache: {{ context.<idx>.<field> }} "
     "or {{ now }}. Times default to Asia/Kolkata. Indian stocks default "
     "to NSE / INR. Common configs:\n"
     "  - trigger.schedule: { cron: '15 9 * * 1', timezone: 'Asia/Kolkata' }\n"
     "  - trigger.price: { symbol, operator: '>'|'<'|'>='|'<=', value }\n"
     "  - trigger.indicator: { symbol, indicator: 'rsi'|'sma'|'ema', "
     "period: 14, operator, value }\n"
     "  - fetch.portfolio: {} (output: buying_power, holdings)\n"
     "  - fetch.indicator: { symbol, indicator, period }\n"
     "  - fetch.day_open: { symbol } "
     "(output: { value: today's open price, session_date })\n"
     "  - fetch.prior_close: { symbol, sessions_back?: 1 } "
     "(output: { value: prior close, session_date })\n"
     "  - fetch.relative_threshold: { symbol, "
     "reference: 'day_open'|'prior_close'|'prior_high'|'prior_low', "
     "offset_pct: number } "
     "(output: { value: ABSOLUTE price level, reference_value, "
     "reference_label })\n"
     "  - fetch.screener: { sector?, mcap_min_cr?, mcap_max_cr?, "
     "sort_by?: 'mcap'|'symbol', limit?: 10 } "
     "(output: { symbols: [...], ranked: [{symbol, name, sector, mcap_cr}], "
     "n }) — use this to resolve 'top N stocks in sector S'. Sectors: "
     "steel, metals, banking, psu_bank, private_bank, it, auto, pharma, "
     "fmcg, energy, cement, defence, telecom.\n"
     "  - condition.numeric: { left: '{{ context.1.buying_power }}', "
     "operator: '>', right: 50000 }\n"
     "  - action.place_order: { symbol, side: 'buy'|'sell', "
     "quantity OR notional_inr (ONE of them), "
     "order_type?: 'market'|'limit', requires_approval?: bool }. "
     "Use notional_inr when the user expressed size in INR ('buy ₹5K of "
     "RELIANCE'); the executor converts to integer shares at fire time.\n"
     "  - action.allocate_notional: { symbols: ref|list, "
     "side: 'buy'|'sell', total_inr, strategy?: 'equal'|'mcap_weighted', "
     "order_type? } — splits a ₹ budget across N symbols and places "
     "each as one order. Replaces N copies of action.place_order for a "
     "portfolio buy. The `symbols` field accepts either a literal list "
     "or a Mustache ref like `{{ context.4.ranked }}` (preferred — "
     "carries mcap data for mcap_weighted strategy).\n"
     "  - action.set_stoploss: { symbol, "
     "trigger_price OR trigger_offset_pct (% below entry, e.g. 2 for 2%), "
     "quantity? }\n"
     "  - notify.message: { channel: 'email'|'sms'|'push', template, "
     "vars?: {} }\n\n"
     "STOP-LOSS: when the user says '2% stop loss' / 'X% SL' / 'stop "
     "below entry', use action.set_stoploss with `trigger_offset_pct` "
     "(a number like 2). Use `trigger_price` only when the user gave "
     "an absolute price (e.g. '₹1,420' or 'stop at 1400'). Never both.\n\n"
     "RUNTIME-RELATIVE LEVELS (e.g. 'X% below today's open', 'above prior "
     "close', 'when price gaps down 5%'). Workflow triggers and "
     "condition.numeric only accept fixed numbers — they do NOT support "
     "arithmetic in Mustache refs (`{{ x * 0.95 }}` is invalid). To "
     "express a relative level, chain a `fetch.relative_threshold` step "
     "BEFORE the condition. Example for 'when RELIANCE drops 5% below "
     "today's open':\n"
     "  steps: [\n"
     "    { step_type: 'trigger.schedule', "
     "config: { cron: '*/5 9-15 * * 1-5', timezone: 'Asia/Kolkata' } },\n"
     "    { step_type: 'fetch.quote', config: { symbol: 'RELIANCE' } },\n"
     "    { step_type: 'fetch.relative_threshold', "
     "config: { symbol: 'RELIANCE', reference: 'day_open', "
     "offset_pct: -5 } },\n"
     "    { step_type: 'condition.numeric', "
     "config: { left: '{{ context.1.ltp }}', operator: '<=', "
     "right: '{{ context.2.value }}' } },\n"
     "    { step_type: 'action.set_stoploss', "
     "config: { symbol: 'RELIANCE', trigger_offset_pct: 2 } }\n"
     "  ]\n"
     "Never write `{{ x * 0.95 }}` directly — refs don't compute. Use "
     "fetch.relative_threshold and reference its `value` field.\n\n"
     "STAY LITERAL TO THE USER'S REQUEST. If the user only asked for "
     "an SL, do NOT add a buy step. If the user only asked for a buy, "
     "do NOT add an SL step. Add steps the user did not request only "
     "when the workflow is unworkable without them (e.g. fetch.portfolio "
     "before referencing holdings).\n\n"
     "EXAMPLES (emit shapes like these — adjust values to match the user):\n"
     "  // 'buy 5 NIFTYBEES every weekday at 09:15'\n"
     "  { name: 'Weekday NIFTYBEES SIP', description: 'Buy 5 NIFTYBEES every "
     "weekday at 09:15 IST', steps: ["
     "{ step_type: 'trigger.schedule', config: { cron: '15 9 * * 1-5', "
     "timezone: 'Asia/Kolkata' } },"
     "{ step_type: 'action.place_order', config: { symbol: 'NIFTYBEES', "
     "side: 'buy', quantity: 5, order_type: 'market', requires_approval: "
     "false } } ], rationale: 'Single weekly schedule trigger, market buy "
     "for SIP-style accumulation.' }\n\n"
     "  // SL-only example: 'when RELIANCE drops below 2700, set 2% stop loss "
     "on my holding'\n"
     "  { name: 'RELIANCE 2% SL on dip', steps: ["
     "{ step_type: 'trigger.price', config: { symbol: 'RELIANCE', "
     "operator: 'crosses_below', value: 2700 } },"
     "{ step_type: 'action.set_stoploss', config: { symbol: 'RELIANCE', "
     "trigger_offset_pct: 2 } } ], rationale: 'Trigger on price crossing "
     "below; place a percentage-based SL on the existing holding.' }\n\n"
     "  // Day-anchored runtime-relative SL example: 'if RELIANCE "
     "dips 5% on Monday set a 2% stop loss'. The 5% dip is RELATIVE "
     "to a reference price (prior close), so we need fetch.relative_threshold "
     "to compute the absolute level, fetch.quote for the current LTP, "
     "then condition.numeric to compare. The schedule runs every "
     "Monday during market hours; the SL fires when the LTP crosses "
     "below the threshold.\n"
     "  { name: 'RELIANCE 5% Monday dip → 2% SL', steps: ["
     "{ step_type: 'trigger.schedule', config: { cron: '*/15 9-15 * * 1', "
     "timezone: 'Asia/Kolkata' } },"
     "{ step_type: 'fetch.relative_threshold', config: { symbol: 'RELIANCE', "
     "reference: 'prior_close', offset_pct: -5 } },"
     "{ step_type: 'fetch.quote', config: { symbol: 'RELIANCE' } },"
     "{ step_type: 'condition.numeric', config: { left: '{{ context.2.ltp }}', "
     "operator: '<=', right: '{{ context.1.value }}' } },"
     "{ step_type: 'action.set_stoploss', config: { symbol: 'RELIANCE', "
     "trigger_offset_pct: 2 } } ], rationale: 'Polls RELIANCE every 15 "
     "minutes during Monday market hours; when LTP is at or below 5% of "
     "prior close, places a 2% stop-loss on the holding.' }\n\n"
     "  // Buy + SL example: 'watch HDFCBANK and buy 3 shares when price "
     "crosses below 1400, with a 2% stop loss after the buy'\n"
     "  { name: 'HDFCBANK dip buy with SL', steps: ["
     "{ step_type: 'trigger.price', config: { symbol: 'HDFCBANK', "
     "operator: 'crosses_below', value: 1400 } },"
     "{ step_type: 'action.place_order', config: { symbol: 'HDFCBANK', "
     "side: 'buy', quantity: 3, order_type: 'market', requires_approval: "
     "false } },"
     "{ step_type: 'action.set_stoploss', config: { symbol: 'HDFCBANK', "
     "trigger_offset_pct: 2 } } ], rationale: '...' }\n\n"
     "  // Portfolio basket example: 'invest ₹1,00,000 equally across "
     "the top 10 steel sector stocks when NIFTY opens above prev close'\n"
     "  { name: 'Steel basket on NIFTY gap-up', steps: ["
     "{ step_type: 'trigger.schedule', config: { cron: '20 9 * * 1-5', "
     "timezone: 'Asia/Kolkata' } },"
     "{ step_type: 'fetch.day_open', config: { symbol: 'NIFTY' } },"
     "{ step_type: 'fetch.prior_close', config: { symbol: 'NIFTY' } },"
     "{ step_type: 'condition.numeric', config: { left: "
     "'{{ context.1.value }}', operator: '>', right: "
     "'{{ context.2.value }}' } },"
     "{ step_type: 'fetch.screener', config: { sector: 'steel', "
     "sort_by: 'mcap', limit: 10 } },"
     "{ step_type: 'action.allocate_notional', config: { symbols: "
     "'{{ context.4.ranked }}', side: 'buy', total_inr: 100000, "
     "strategy: 'equal' } } ], rationale: 'Schedule shortly after open, "
     "compare day open to prior close, screen steel top 10, allocate "
     "₹1L equally across the basket.' }\n\n"
     "Output the draft as the tool arguments — do NOT pass the user's "
     "raw text. The user reviews and activates from the editor panel; "
     "do not persist.",
     {
         "name": {
             "type": "string",
             "description": "Short workflow title (e.g. 'Weekly NIFTYBEES buy').",
         },
         "description": {
             "type": "string",
             "description": "One-sentence summary in the user's own words.",
         },
         "steps": _PROPOSE_STEPS_SCHEMA,
         "rationale": {
             "type": "string",
             "description": "1-2 sentences explaining why these steps map to the user's request.",
         },
     },
     ["name", "steps"])


# ── MACRO WORKFLOW TOOLS ────────────────────────────────────────────
#
# Four narrow tools that hydrate the most common workflow shapes
# server-side. The model emits ~20-30 tokens of params; the executor
# in tool_executor.py expands these into a full WorkflowDraft and
# returns the same `_render_hint: "workflow_draft_card"` payload as
# `propose_workflow`. ~30× faster decode for the 80% of agent prompts
# that fit one of these shapes.
#
# When the prompt doesn't fit any macro, the model falls through to
# the full `propose_workflow` tool which is still in scope.


tool("propose_scheduled_order",
     "Build a workflow that places ONE order on a recurring schedule "
     "(SIP-style or weekly/Monday rules). PREFER this over "
     "propose_workflow for prompts shaped like: 'buy 5 NIFTYBEES every "
     "weekday at 09:15', 'every Monday 09:30 sell 2 INFY', 'put ₹500 "
     "into HDFCBANK every day at market open'. Server hydrates the full "
     "trigger.schedule + action.place_order (+ optional "
     "action.set_stoploss) draft. Pass exactly ONE of `quantity` or "
     "`notional_inr`.",
     {
         "symbol": {"type": "string"},
         "side": {"type": "string", "enum": ["buy", "sell"]},
         "quantity": {"type": "integer", "minimum": 1,
                      "description": "Integer share count. XOR with notional_inr."},
         "notional_inr": {"type": "number", "minimum": 1,
                          "description": "Rupee budget. XOR with quantity."},
         "days": {
             "type": "array",
             "items": {"type": "string"},
             "description": "Days to fire — list of mon|tue|wed|thu|fri|"
                            "weekday|daily. Use ['weekday'] for Mon-Fri.",
         },
         "time_ist": {
             "type": "string",
             "description": "Fire time, HH:MM in IST. Default 09:15.",
         },
         "sl_pct": {"type": "number", "minimum": 0.1, "maximum": 50,
                    "description": "Optional % stop-loss after the fill."},
         "requires_approval": {"type": "boolean",
                               "description": "Default false (auto-execute)."},
     },
     ["symbol", "side", "days"])


tool("propose_threshold_order",
     "Build a workflow that places ONE order when an indicator or "
     "price threshold fires. PREFER this over propose_workflow for "
     "prompts shaped like: 'buy 10 INFY when RSI < 30', 'sell 5 "
     "RELIANCE when price crosses above 2800', 'buy 3 HDFCBANK when "
     "price drops below 1400 with 2% SL'. Server hydrates the full "
     "trigger.{indicator|price} + action.place_order (+ optional "
     "action.set_stoploss) draft. Pass exactly ONE of `quantity` or "
     "`notional_inr`. Use this ONLY for absolute thresholds — for "
     "runtime-relative rules ('5% below today's open') use the full "
     "propose_workflow with fetch.relative_threshold instead.",
     {
         "symbol": {"type": "string"},
         "side": {"type": "string", "enum": ["buy", "sell"]},
         "quantity": {"type": "integer", "minimum": 1},
         "notional_inr": {"type": "number", "minimum": 1},
         "trigger_kind": {
             "type": "string", "enum": ["indicator", "price"],
             "description": "What kind of trigger. 'indicator' for RSI/SMA/EMA,"
                            " 'price' for absolute price levels.",
         },
         "operator": {
             "type": "string",
             "enum": ["<", ">", "crosses_above", "crosses_below"],
         },
         "threshold": {"type": "number"},
         "indicator": {
             "type": "string", "enum": ["rsi", "sma", "ema"],
             "description": "Required when trigger_kind='indicator'.",
         },
         "indicator_period": {"type": "integer", "minimum": 1, "maximum": 500,
                              "description": "Default 14 for RSI, 50 for SMA/EMA."},
         "sl_pct": {"type": "number", "minimum": 0.1, "maximum": 50},
         "requires_approval": {"type": "boolean"},
     },
     ["symbol", "side", "trigger_kind", "operator", "threshold"])


tool("propose_basket_allocation",
     "Build a workflow that allocates a ₹ budget across the top N "
     "stocks in a sector. PREFER this over propose_workflow for "
     "prompts shaped like: 'invest ₹1L equally across top 10 steel "
     "stocks', 'put ₹50K into top 5 banking stocks', 'allocate ₹2L "
     "across top 10 IT stocks when NIFTY gaps up'. Server hydrates "
     "trigger.schedule (+ optional gap-up/down gate on the index) + "
     "fetch.screener + action.allocate_notional + notify.message. "
     "Sectors recognised: steel, metals, banking, psu_bank, "
     "private_bank, it, auto, pharma, fmcg, energy, cement, defence, "
     "telecom.",
     {
         "sector": {"type": "string"},
         "total_inr": {"type": "number", "minimum": 1},
         "side": {"type": "string", "enum": ["buy", "sell"], "default": "buy"},
         "strategy": {
             "type": "string", "enum": ["equal", "mcap_weighted"],
             "default": "equal",
         },
         "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
         "schedule_time_ist": {
             "type": "string", "default": "09:20",
             "description": "Fire time, HH:MM IST. Default 09:20 (just after open).",
         },
         "days": {
             "type": "array", "items": {"type": "string"},
             "description": "Days to fire. Default ['weekday'].",
         },
         "gap_condition": {
             "type": "string",
             "enum": ["gap_up", "gap_down", "flat"],
             "description": "Optional gate: only fire when the index "
                            "opens above/below/flat vs prior close.",
         },
         "index_symbol": {"type": "string", "default": "NIFTY",
                          "description": "Index for gap_condition. Default NIFTY."},
         "requires_approval": {"type": "boolean"},
     },
     ["sector", "total_inr"])


tool("propose_holding_action",
     "Build a workflow that acts on the user's EXISTING holding of a "
     "symbol — sells it entirely or sets a stop-loss. PREFER this "
     "over propose_workflow for prompts shaped like: 'sell my INFY "
     "when RSI > 70', 'set 2% SL on my RELIANCE', 'exit TCS when it "
     "drops below 3500'. Two action shapes: 'sell' (entire holding "
     "via fetch.portfolio + place_order) or 'set_stoploss' (absolute "
     "price OR offset pct). Three trigger shapes: indicator, price, "
     "schedule.",
     {
         "symbol": {"type": "string"},
         "action_kind": {
             "type": "string", "enum": ["sell", "set_stoploss"],
         },
         "trigger_kind": {
             "type": "string",
             "enum": ["indicator", "price", "schedule", "manual"],
             "description": "When the user did not specify a trigger "
                            "(e.g. 'set 2% SL on my RELIANCE'), use "
                            "'manual' — the workflow only fires when "
                            "the user clicks Run now.",
         },
         "operator": {
             "type": "string",
             "enum": ["<", ">", "crosses_above", "crosses_below"],
             "description": "Required for trigger_kind in {indicator, price}.",
         },
         "threshold": {"type": "number",
                       "description": "Required for trigger_kind in {indicator, price}."},
         "indicator": {
             "type": "string", "enum": ["rsi", "sma", "ema"],
             "description": "Required when trigger_kind='indicator'.",
         },
         "indicator_period": {"type": "integer", "minimum": 1, "maximum": 500},
         "schedule_cron": {
             "type": "string",
             "description": "Required when trigger_kind='schedule'. e.g. '15 9 * * 1-5'.",
         },
         "sl_offset_pct": {
             "type": "number", "minimum": 0.1, "maximum": 50,
             "description": "For action_kind='set_stoploss'. % below current "
                            "price. XOR with sl_trigger_price.",
         },
         "sl_trigger_price": {
             "type": "number",
             "description": "For action_kind='set_stoploss'. Absolute price. "
                            "XOR with sl_offset_pct.",
         },
         "requires_approval": {"type": "boolean"},
     },
     ["symbol", "action_kind", "trigger_kind"])


def get_tools_for_subset(subset_name: str) -> list:
    """Returns tool definition list for a given subset name."""
    names = TOOL_SUBSETS.get(subset_name, [])
    return [ALL_TOOLS[n] for n in names if n in ALL_TOOLS]
