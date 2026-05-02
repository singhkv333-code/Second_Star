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


def tool(name, description, properties, required):
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
    return defn


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
     ["symbol", "transaction_type", "quantity"])

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
     ["symbol", "transaction_type", "quantity", "price"])

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
     ["symbol", "transaction_type", "quantity", "trigger_price", "limit_price"])

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
     ["symbol", "quantity"])

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
     ["symbol", "quantity", "target_price", "stop_price"])

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
     ["symbol", "dip_pct", "budget_inr"])

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
     ["legs"])

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
     ["symbol", "amount_inr", "frequency"])

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
     ["name", "trigger_type", "trigger_params", "action"])

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
     ["symbol"])

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
     ["symbol"])

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
     ["symbol"])

tool("get_52wk_range",
     "Returns 52-week high and low for a stock.",
     {"symbol": {"type": "string"}},
     ["symbol"])

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

tool("propose_workflow",
     "Translates a natural-language trading strategy into a structured "
     "Pivot workflow draft (an 'Agent'). Use when the user describes a "
     "multi-step automation: 'every weekday at 3 PM if my buying power "
     "is over 50K buy 10 RELIANCE and email me'. Returns a WorkflowDraft "
     "with name, description, ordered steps (trigger -> fetch -> condition "
     "-> action -> notify), and rationale. Does NOT activate or persist — "
     "the user reviews and activates from the editor panel. Prefer this "
     "over create_strategy when the request describes a multi-step "
     "'agent' rather than a single price-trigger rule.",
     {
         "user_intent": {
             "type": "string",
             "description": "The user's verbatim strategy description.",
         },
     },
     ["user_intent"])


def get_tools_for_subset(subset_name: str) -> list:
    """Returns tool definition list for a given subset name."""
    names = TOOL_SUBSETS.get(subset_name, [])
    return [ALL_TOOLS[n] for n in names if n in ALL_TOOLS]
