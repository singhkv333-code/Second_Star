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
    "MARKET_QUERY":      ["get_live_price", "get_index_level", "get_ohlc", "get_52wk_range", "get_market_status", "get_upcoming_events", "get_top_movers", "get_option_chain"],
    "AUTOMATION_CREATE": ["create_strategy", "create_cash_sweep", "create_rebalancing_rule", "create_drawdown_protection", "propose_workflow"],
    "AUTOMATION_MANAGE": ["list_strategies", "pause_strategy", "resume_strategy", "delete_strategy"],
    "WORKFLOW_PROPOSE":  ["propose_workflow"],
    "SIP_MANAGE":        ["list_sips", "pause_sip", "resume_sip", "delete_sip", "pause_all_sips"],
    "YIELD_QUERY":       ["compare_yields", "get_yield_recommendation"],
    "CALCULATION":       ["calculate_order_qty", "calculate_tax_impact", "calculate_sl_price", "calculate_dip_price", "calculate_margin"],
    "BACKTEST":          ["backtest_workflow"],
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
     "Places an immediate market order via Zerodha Kite. Use when user "
     "wants to buy or sell RIGHT NOW at current price. NOT for conditional "
     "orders (use create_gtt_order) or recurring (use create_sip). "
     "ASK_USER first when the company name is genuinely ambiguous (bare "
     "'Tata', 'M&M', 'HDFC', 'Adani'); otherwise infer the NSE ticker.",
     {
         "symbol":           {"type": "string", "description":
                              "NSE ticker, uppercase. Infer from company name: "
                              "Swiggy→SWIGGY, Zomato/Eternal→ETERNAL, Infosys→INFY, "
                              "HDFC Bank→HDFCBANK, SBI→SBIN, TCS→TCS, Wipro→WIPRO. "
                              "ASK_USER only for genuine ambiguity (bare 'Tata', "
                              "bare 'HDFC', bare 'Adani', 'M&M')."},
         "transaction_type": {"type": "string", "enum": ["BUY", "SELL"],
                              "description": "BUY or SELL — uppercase only."},
         "quantity":         {"type": "integer", "minimum": 1, "description":
                              "Number of shares as a positive integer. If the "
                              "user said '100 of X' without specifying shares vs "
                              "lots, ASK_USER for the unit (shares vs lots)."},
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
     "Do NOT use for recurring orders. Do NOT use for immediate execution. "
     "trigger_price is an ABSOLUTE rupee price — if the user expressed it "
     "as a percentage move ('5% below current'), call ASK_USER for the "
     "absolute price OR fetch the live quote first and compute it.",
     {
         "symbol":           {"type": "string", "description":
                              "NSE ticker, uppercase. ASK_USER if the user named "
                              "an ambiguous company (Tata, M&M, HDFC, Adani)."},
         "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]},
         "quantity":         {"type": "integer", "minimum": 1},
         "trigger_price":    {"type": "number", "description":
                              "Absolute price (INR) that activates the order. "
                              "Must be a number, NOT a percentage. If the user "
                              "only gave a percentage, ASK_USER for the absolute."},
         "limit_price":      {"type": "number", "description":
                              "Execution price after trigger fires (INR). For BUY, "
                              "set slightly above trigger to ensure fill; for SELL, "
                              "slightly below."},
         "exchange":         {"type": "string", "enum": ["NSE", "BSE"], "default": "NSE"},
     },
     ["symbol", "transaction_type", "quantity", "trigger_price", "limit_price"],
     defaults={"exchange": "NSE", "product": "CNC"})

tool("create_sl_order",
     "Creates a stop-loss GTT order to protect a holding. Accepts "
     "stop_price OR stop_pct (needs entry_price). ALWAYS prefer this over "
     "propose_workflow/propose_holding_action for plain stop-loss requests.",
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

tool("get_top_movers",
     "Today's top gainers or losers in NIFTY 50, ranked by intraday "
     "% change. Use for prompts like 'top gainers today', 'who's "
     "moving most', 'today's biggest losers'. Backed by yfinance with "
     "a 60s cache; falls back to a curated seed list when yfinance is "
     "unavailable (rows tagged `seed: true`). The universe is always "
     "NIFTY 50 — do NOT call ASK_USER to ask which universe or how "
     "many; the defaults are correct (gainers, limit=5). Only use "
     "ASK_USER if the user explicitly asks about a different index "
     "(e.g. 'NIFTY 100', 'Bank Nifty') which isn't supported in v1.",
     {
         "direction": {
             "type": "string", "enum": ["gainers", "losers"],
             "default": "gainers",
         },
         "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
     },
     [])

# ── ANALYTICS / INDICATORS / RISK / COMPARISON ──────────────────────────────
# Bridges to /core/ (indicator vault + calculations + data layer).

tool("get_indicator",
     "Compute a single technical indicator on an NSE-listed ticker over "
     "the last few months of daily candles. Use for: 'what's RELIANCE's "
     "RSI', 'TCS 50-day SMA', 'INFY MACD'. The `indicator` arg accepts "
     "rsi/sma/ema/wma/macd/adx/supertrend/atr/bollinger/donchian/keltner/"
     "obv/vwap/cci/mfi/stoch/williams_r/aroon/trix/roc/historical_vol. "
     "`period` is the lookback (e.g. 14 for RSI(14), 50 for SMA(50)). "
     "Output includes current_value, signal (bullish/bearish/neutral), "
     "and an interpretation string.",
     {
         "symbol":         {"type": "string"},
         "indicator":      {"type": "string"},
         "period":         {"type": "integer", "minimum": 2, "maximum": 250, "default": 14},
         "history_period": {"type": "string", "default": "6mo",
                            "description": "yfinance period: 1mo|3mo|6mo|1y|2y|5y"},
     },
     ["symbol", "indicator"])

tool("get_multiple_indicators",
     "Compute several indicators for one ticker in a single call — saves "
     "round-trips when the user asks for multiple ('RSI and MACD for "
     "INFY', 'show me Bollinger Bands and ATR for TCS').",
     {
         "symbol":         {"type": "string"},
         "indicators":     {"type": "array", "items": {"type": "string"}},
         "history_period": {"type": "string", "default": "6mo"},
     },
     ["symbol", "indicators"])

tool("get_performance_metrics",
     "Risk-adjusted performance summary for one ticker over a period. "
     "Returns a structured dict of total_return / annualised_return / "
     "volatility / sharpe / sortino / max_drawdown / VaR. Use for: "
     "'how risky is TCS', 'what's RELIANCE's Sharpe over 1 year', "
     "'INFY drawdown last 5 years'.",
     {
         "symbol":   {"type": "string"},
         "period":   {"type": "string", "default": "1y",
                      "description": "1mo|3mo|6mo|1y|2y|5y|max"},
         "metrics":  {"type": "array", "items": {"type": "string"},
                      "description": "Subset of total_return/annualised_return/volatility/sharpe/sortino/max_drawdown/var. Empty = all."},
     },
     ["symbol"])

tool("compare_performance",
     "Rank a list of tickers by a chosen metric. Use for: 'rank "
     "RELIANCE TCS INFY by Sharpe', 'which of these has best risk-"
     "adjusted return last year', 'compare these stocks'. Returns the "
     "full comparison table.",
     {
         "symbols":  {"type": "array", "items": {"type": "string"}},
         "period":   {"type": "string", "default": "1y"},
         "metric":   {"type": "string", "default": "sharpe",
                      "enum": ["sharpe", "total_return", "volatility", "max_drawdown"]},
     },
     ["symbols"])

tool("get_correlation_matrix",
     "Pairwise return correlations across a basket of tickers. Use for: "
     "'how correlated are TCS, INFY, WIPRO', 'diversification check on "
     "my portfolio', 'which pairs are most correlated'.",
     {
         "symbols": {"type": "array", "items": {"type": "string"}},
         "period":  {"type": "string", "default": "6mo"},
     },
     ["symbols"])

tool("get_returns",
     "Period return for one ticker. Use for: 'what's TCS up YTD', "
     "'how has RELIANCE done over 5 years', 'INFY return last quarter'. "
     "Set cumulative=true to also get the running cumulative-return curve.",
     {
         "symbol":     {"type": "string"},
         "period":     {"type": "string", "default": "1y"},
         "cumulative": {"type": "boolean", "default": False},
     },
     ["symbol"])

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
     "LEGACY single-indicator backtest. Use ONLY for the simplest "
     "RSI / price-cross-SMA / price-drop / SIP-style cases that match "
     "one of the four `strategy_type` enum values exactly. For ANY "
     "other backtest — multi-indicator, MACD, Supertrend, Bollinger, "
     "stoch, cross-asset, with stoploss/squareoff, calendar+condition "
     "combos — use `backtest_workflow` instead.\n\n"
     "PREFER `backtest_workflow` when the user names: macd, supertrend, "
     "bollinger / bb, stoch, mfi, cci, williams_r, atr, keltner, "
     "donchian, aroon, psar, roc, trix, obv, vwap, wma — or chains "
     "two conditions ('RSI < 30 AND volume spike') — or wants a "
     "stoploss / squareoff / time-of-day exit — or trades a different "
     "symbol than the trigger watches.\n\n"
     "Do NOT use for fundamentals expressions (PE < 15, ROE > 18) — "
     "those go through the deterministic `/expr-backtest` slash command.",
     {
         "symbol":            {"type": "string", "description":
                               "NSE ticker, uppercase. Single stock only — "
                               "this tool is one-symbol; use the fundamentals "
                               "backtest path for universe-level shapes."},
         "strategy_type":     {"type": "string",
                               "enum": ["sip","price_drop","rsi","price_cross"],
                               "description":
                               "EXACTLY one of: sip (recurring buy on a "
                               "schedule), price_drop (buy on % drop), rsi "
                               "(buy/sell when RSI crosses threshold), "
                               "price_cross (buy/sell when price crosses an "
                               "SMA/EMA). If the user's strategy doesn't fit "
                               "any of these, do NOT pick the closest one — "
                               "call ASK_USER instead."},
         "trigger_condition": {"type": "object", "description":
                               "Strategy-specific config. For rsi: "
                               "{'period': 14, 'op': '<', 'threshold': 30}. "
                               "For price_cross: {'period': 200, 'kind': 'sma', "
                               "'direction': 'above'}. For price_drop: "
                               "{'pct': 5}. For sip: {'cron': '...', "
                               "'amount_inr': N}."},
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


def _build_propose_workflow_schema() -> tuple[dict, list[str], str]:
    """LLM-facing step schema for propose_workflow / backtest_workflow.

    Collapses every step into ONE shape — ``{step_type: enum, label?:
    str, config: object}`` — instead of a 41-branch discriminated union.
    The LLM learns per-step required keys from the description text
    (which embeds the compact catalog this function also returns) and
    from ``prompts/agentic_examples.json``; server-side Pydantic models
    in ``workflows/schemas.py`` validate every config before
    activation, so collapsing the LLM-facing schema does NOT weaken the
    contract. Trade: ~33 KB schema → <1 KB.

    Returns ``(steps_array_schema, step_type_names, compact_catalog)``.
    The catalog string is a one-line-per-step-type rendering suitable
    for embedding directly in the tool description.
    """
    from backend.workflows.registry import STEP_REGISTRY

    names = sorted(STEP_REGISTRY.keys())
    catalog_lines: list[str] = []
    for st in names:
        defn = STEP_REGISTRY[st]
        try:
            schema = defn.config_model.model_json_schema()
            required = sorted(schema.get("required") or [])
        except Exception:
            required = []
        marker = "TRG" if defn.trigger_only else defn.category[:3].upper()
        req_summary = ",".join(required) if required else "—"
        catalog_lines.append(f"  {st} [{marker}] req: {req_summary}")
    catalog = "\n".join(catalog_lines)

    steps_schema = {
        "type": "array",
        "minItems": 1,
        "description": (
            "Ordered steps; step 0 MUST be a trigger.*. Each step's "
            "`config` must contain the required keys listed for its "
            "step_type in the CATALOG section of this tool's "
            "description. Server validates against registry Pydantic "
            "models — extra keys are allowed."
        ),
        "items": {
            "type": "object",
            "properties": {
                "step_type": {"type": "string", "enum": names},
                "label": {"type": "string"},
                "config": {"type": "object", "additionalProperties": True},
            },
            "required": ["step_type", "config"],
        },
    }
    return steps_schema, names, catalog


_PROPOSE_STEPS_SCHEMA, _PROPOSE_STEP_TYPES, _PROPOSE_CATALOG = _build_propose_workflow_schema()


tool("propose_workflow",
     "FALLBACK workflow builder. PREFER the four macros first: "
     "`propose_scheduled_order` (recurring HH:MM), "
     "`propose_threshold_order` (price/RSI/SMA/EMA absolute threshold), "
     "`propose_basket_allocation` (sector basket), "
     "`propose_holding_action` (sell/SL on existing holding). Use this "
     "tool only when none fits — runtime-relative thresholds ('5% below "
     "today's open'), multi-trigger / multi-action workflows, "
     "portfolio-state conditions. NOT for BACKTESTS (`backtest_workflow`) "
     "or single-action automation.\n\n"
     "EMITTING IS NOT ACTIVATING — emit the draft, do NOT ASK_USER to "
     "confirm. Step 0 MUST be a trigger.*. Extra trigger.* steps start "
     "new BRANCHES; two adjacent triggers is invalid. Inter-step refs "
     "use Mustache: `{{ context.<idx>.<field> }}` / `{{ now }}`. Indian "
     "stocks default to NSE/INR; times to Asia/Kolkata.\n\n"
     "CATALOG (step_type [category] required-config-keys; server "
     "validates configs against registry Pydantic models — extra keys "
     "permitted):\n"
     f"{_PROPOSE_CATALOG}\n\n"
     "STEP NOTES (only the tricky ones — see agentic_examples.json for "
     "full shapes):\n"
     "- trigger.market_relative_time: anchor∈{open,close,pre_open,"
     "post_close}, offset_minutes signed; PREFER over hardcoded "
     "09:15/15:30 cron.\n"
     "- trigger.indicator: indicator∈{rsi,sma,ema,wma,macd,adx,"
     "supertrend,bollinger,stoch,stoch_rsi,cci,mfi,williams_r,atr,"
     "keltner,donchian,aroon,psar,roc,trix,obv,vwap}; `value` is a "
     "FIXED level NEVER a second indicator. For indicator-vs-indicator "
     "(e.g. 50-EMA vs 200-EMA) use trigger.schedule + two "
     "fetch.indicator + condition.numeric.\n"
     "- fetch.indicator: only rsi|sma|ema|macd (macd returns "
     "histogram, NOT macd_line/macd_signal).\n"
     "- fetch.relative_threshold: reference∈{day_open,prior_close,"
     "prior_high,prior_low}, signed offset_pct. USE for runtime-"
     "relative levels — Mustache arithmetic is NOT supported.\n"
     "- fetch.intraday_pnl: total_pct is already a % (compare against "
     "-2 not -0.02).\n"
     "- fetch.portfolio: holdings entries are { quantity, avg_buy_price, "
     "last_price, current_value_inr, pnl_inr, pnl_pct } — no "
     "holding_days field.\n"
     "- fetch.screener sectors: steel, metals, banking, psu_bank, "
     "private_bank, it, auto, pharma, fmcg, energy, cement, defence, "
     "telecom.\n"
     "- action.place_order: quantity OR notional_inr; order_type∈"
     "{market,limit}.\n"
     "- action.set_stoploss: trigger_price OR trigger_offset_pct (% — "
     "e.g. 2 for 2%), never both.\n"
     "- notify.message: channel='push' (in-app only; email/SMS/WhatsApp "
     "NOT wired).\n\n"
     "HARD RULES:\n"
     "1. STAY LITERAL — only what the user asked for. No unprompted "
     "sell/SL/trim branches.\n"
     "2. Multi-condition buy = ONE branch with multiple "
     "condition.numeric in series (engine halts on first false).\n"
     "3. NO unprompted notify.message — the run card already confirms.\n"
     "4. NO buying-power guard before action.place_order — broker "
     "rejects insufficient-margin. Only fetch.portfolio when you need "
     "the holdings.\n"
     "5. QUANTITY IS NEVER A DEFAULT. 'buy some X' → ASK_USER. "
     "Exceptions: 'sell my SYMBOL' (fetch.portfolio + Mustache ref); "
     "SIPs.\n"
     "6. TTL phrases → top-level `valid_until` ISO date (resolve "
     "relative phrases); omit for perpetual.\n\n"
     "EXAMPLE — runtime-relative 5% drop trigger:\n"
     "  [{step_type:'trigger.schedule', config:{cron:'*/5 9-15 * * 1-5'}},\n"
     "   {step_type:'fetch.quote', config:{symbol:'RELIANCE'}},\n"
     "   {step_type:'fetch.relative_threshold', config:{symbol:'RELIANCE',reference:'day_open',offset_pct:-5}},\n"
     "   {step_type:'condition.numeric', config:{left:'{{context.1.ltp}}',operator:'<=',right:'{{context.2.value}}'}},\n"
     "   {step_type:'action.set_stoploss', config:{symbol:'RELIANCE',trigger_offset_pct:2}}]",
     {
         "name": {
             "type": "string",
             "description": "Short workflow title.",
         },
         "description": {
             "type": "string",
             "description": "One-sentence summary in the user's words.",
         },
         "steps": _PROPOSE_STEPS_SCHEMA,
         "rationale": {
             "type": "string",
             "description": "1-2 sentences mapping steps to the request.",
         },
         "valid_until": {
             "type": "string",
             "description": (
                 "Optional ISO YYYY-MM-DD. Set ONLY for TTL phrases "
                 "('valid till month end', 'till EOD'). Resolve "
                 "relative phrases to absolute. Omit for perpetual. "
                 "Scheduler stops at 23:59 IST on this date."
             ),
         },
     },
     ["name", "steps"])


# ── BACKTEST WORKFLOW (simulate; do NOT activate) ────────────────────
#
# Mirror of propose_workflow's step schema, routed to the workflow
# backtester instead of the activation registry. The model picks this
# whenever the user says "backtest …" / "how would X have done" /
# "simulate …" — anything historical-counterfactual. Returns the same
# IndicatorBacktestResult chart card the indicator backtester emits.
#
# WHY a separate tool: previously, "backtest …" prompts caused the model
# to call propose_workflow, which renders an Activate-this-strategy card
# rather than a backtest chart. Users got a draft when they asked for a
# simulation. Splitting the tools makes the intent unambiguous.

tool("backtest_workflow",
     "SIMULATES a strategy on historical daily-close data. Use for "
     "ANY 'backtest …' / 'how would X have done' / 'simulate …' / "
     "'what if I had bought …' prompt. Returns a chart card (price + "
     "equity + signals + metrics + buy-and-hold benchmark). "
     "Shares the EXACT `steps[]` schema with propose_workflow — emit the "
     "same step list. USE THIS, not propose_workflow (which activates) or "
     "run_backtest (legacy single-indicator only).\n\n"
     "Supported indicators: rsi, sma, ema, wma, macd (histogram; threshold "
     "0 = signal-line cross), adx, supertrend (direction; 0 = trend flip), "
     "bollinger/bb (%B; 0 = lower, 1 = upper), stoch (%K), stoch_rsi, cci, "
     "mfi, williams_r, atr, keltner, donchian, aroon, psar, roc, trix, "
     "obv, vwap.\n\n"
     "Backtest-specific step types beyond propose_workflow's set: "
     "action.set_takeprofit (mirror of set_stoploss on upside), "
     "fetch.rolling_high/rolling_low (max/min over lookback × multiplier — "
     "use multiplier=0.9 for '10% below 20-day high' in one step), "
     "condition.position / condition.market_status / condition.time_window. "
     "action.place_order accepts product: 'CNC'|'MIS' (MIS for intraday). "
     "action.set_stoploss accepts `trailing: true` with trigger_offset_pct.\n\n"
     "MULTI-CONDITION ENTRY pattern: trigger fires on the FIRST signal; "
     "chain `fetch.indicator → condition.numeric` pairs AFTER the trigger "
     "for AND-conditions. Use `{{ context.<idx>.value }}` refs. Never ask "
     "the user for clarification on a complex multi-condition backtest — "
     "emit the workflow.\n\n"
     "INDICATOR-VS-INDICATOR CROSSOVERS (e.g. 50-SMA crosses above 200-SMA, "
     "fast-EMA vs slow-EMA): use ONE trigger.indicator (fast period, op '>') "
     "+ fetch.indicator (slow period) + condition.numeric comparing context "
     "refs. Example for '50/200 SMA crossover on NIFTYBEES, 2y':\n"
     "  steps[0] trigger.indicator { symbol:'NIFTYBEES', indicator:'sma', "
     "period:50, operator:'>', value:0 }   # CLOSE > SMA(50) initially\n"
     "  steps[1] fetch.indicator { symbol:'NIFTYBEES', indicator:'sma', period:200 }\n"
     "  steps[2] condition.numeric { left:'{{context.0.value}}', operator:'>', "
     "right:'{{context.1.value}}' }   # SMA50 > SMA200\n"
     "  steps[3] action.place_order { symbol:'NIFTYBEES', side:'buy', "
     "quantity:1, order_type:'market' }\n"
     "Emit the FULL steps[] — do not ASK the user for these defaults.\n\n"
     "Defaults: period='5y'. Multi-symbol workflows fetch each feed "
     "independently; chart anchors on the first place_order's symbol.",
     {
         "name": {
             "type": "string",
             "description": "Short name for the chart card (e.g. 'TCS MACD signal cross').",
         },
         "steps": _PROPOSE_STEPS_SCHEMA,
         "period": {
             "type": "string",
             "enum": ["1y", "2y", "3y", "5y", "10y"],
             "default": "5y",
             "description": (
                 "Historical lookback window. Defaults to 5y. Use shorter "
                 "windows for symbols listed within the last few years."
             ),
         },
         "start_date": {
             "type": "string",
             "description": (
                 "OPTIONAL ISO YYYY-MM-DD. Clip the backtest to a fixed "
                 "window AFTER the period fetch. Use for event-driven "
                 "asks like '4 weeks around 2022-02-24' (Russia-Ukraine "
                 "macro shock) — pass start_date='2022-02-10', "
                 "end_date='2022-03-10'."
             ),
         },
         "end_date": {
             "type": "string",
             "description": "OPTIONAL ISO YYYY-MM-DD end of the window.",
         },
         "benchmark_symbol": {
             "type": "string",
             "description": (
                 "OPTIONAL symbol to use as the buy-and-hold benchmark "
                 "(default: the trade symbol). For baskets / pairs, "
                 "pass NIFTYBEES (NIFTY 50 ETF) or BANKBEES so the "
                 "comparison is fair."
             ),
         },
     },
     ["name", "steps"])


# ── DSL-tree BACKTEST + WORKFLOW TOOLS ──────────────────────────────
#
# These two tools wrap the Phase B/B+1/C.0 DSL-tree pipeline:
#   - backtest_dsl_tree   → simulate using POST /api/backtest/dsl/run
#   - propose_dsl_workflow → register a workflow whose entry is a
#                             trigger.compound DSL tree
#
# The chat-side LLM doesn't need to know the DSL grammar — it just
# hands the user's natural-language condition through as one string.
# The tools translate to a tree server-side (single LLM hop with a
# dedicated, well-tuned grammar prompt) so the chat-side prompt stays
# small and stable.

tool("backtest_dsl_tree",
     "SIMULATES a strategy that uses compound (multi-condition / "
     "cross-symbol / aggregator-based) entry rules. PREFER over "
     "backtest_workflow when any of the following are true:\n"
     "  • two or more 'AND' / 'OR' conditions ('RSI<30 AND price>SMA(50)')\n"
     "  • cross-symbol filters ('only when NIFTY is above 22000')\n"
     "  • cross-symbol comparisons ('TCS RSI lower than INFY RSI')\n"
     "  • indicator-vs-indicator crossings ('MACD line crosses signal')\n"
     "  • multi-output indicators (Bollinger bands, Donchian, Keltner, "
     "Stoch %K vs %D, Aroon up vs down)\n"
     "  • lookback / aggregator language ('20-day high breakout', "
     "'highest of last 20 bars', 'percentile of last year', 'bars since "
     "RSI was last below 30', 'correlation of TCS and INFY')\n"
     "  • time-shifted reference ('yesterday's open', 'gap-down')\n"
     "Hand the user's full natural-language condition through as the "
     "`condition` field — do NOT try to break it apart. The tool "
     "translates to a DSL tree internally. Returns the same chart-card "
     "shape as backtest_workflow (price + equity + signals + metrics).",
     {
         "condition": {
             "type": "string",
             "description": (
                 "The complete natural-language entry condition the "
                 "user described. Pass it through VERBATIM — don't "
                 "summarise, paraphrase, or strip operators."
             ),
         },
         "primary_symbol": {
             "type": "string",
             "description": (
                 "Symbol the trade fires on. The condition may "
                 "reference other symbols as filters (e.g. NIFTY) — "
                 "still pick the action symbol here."
             ),
         },
         "start_date": {
             "type": "string",
             "description": (
                 "OPTIONAL ISO YYYY-MM-DD. Defaults to 3 years before "
                 "end_date."
             ),
         },
         "end_date": {
             "type": "string",
             "description": (
                 "OPTIONAL ISO YYYY-MM-DD. Defaults to today."
             ),
         },
         "exit_kind": {
             "type": "string",
             "enum": ["n_day_hold", "stop_loss_pct"],
             "default": "n_day_hold",
             "description": (
                 "How to close a position. n_day_hold: exit after "
                 "exit_bars bars at the next open. stop_loss_pct: "
                 "exit at the stop price on bar-low (realistic SL)."
             ),
         },
         "exit_bars": {
             "type": "integer",
             "default": 10,
             "description": "Used when exit_kind=n_day_hold.",
         },
         "exit_pct": {
             "type": "number",
             "description": (
                 "Used when exit_kind=stop_loss_pct. 0.05 = 5% stop."
             ),
         },
         "starting_capital": {
             "type": "number",
             "default": 100000,
         },
         "quantity": {
             "type": "integer",
             "default": 10,
         },
     },
     ["condition", "primary_symbol"])


tool("propose_dsl_workflow",
     "PROPOSE a new live workflow (agent / automation) whose entry "
     "condition is a compound DSL tree. PREFER over propose_workflow "
     "when the trigger has any of: 2+ AND/OR conditions, cross-symbol "
     "filters, indicator-vs-indicator crossings, aggregators "
     "(20-day high, percentrank, barssince, ...), or multi-output "
     "indicator components (BB upper/lower, MACD line vs signal). "
     "Hand the user's full natural-language condition through as the "
     "`condition` field — the tool translates to a tree internally. "
     "Returns a workflow_draft_card the user activates from chat.",
     {
         "condition": {
             "type": "string",
             "description": (
                 "Natural-language entry condition. Pass verbatim."
             ),
         },
         "primary_symbol": {
             "type": "string",
             "description": (
                 "Symbol the action fires on (orders / notifications)."
             ),
         },
         "name": {
             "type": "string",
             "description": "Short human label, e.g. 'TCS RSI oversold buy'.",
         },
         "action_kind": {
             "type": "string",
             "enum": ["notify_only", "buy_market", "buy_limit"],
             "default": "notify_only",
             "description": (
                 "What to do when the trigger fires. notify_only "
                 "(default) just sends a push notification; buy_market "
                 "/ buy_limit register an order (requires Kite linked)."
             ),
         },
         "quantity": {
             "type": "integer",
             "default": 1,
             "description": "Shares to buy (only for action_kind=buy_*).",
         },
         "limit_price": {
             "type": "number",
             "description": (
                 "Limit price (₹). Required when action_kind=buy_limit."
             ),
         },
     },
     ["condition", "primary_symbol"])


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
     "(SIP-style / weekly / Monday rules). PREFER over propose_workflow "
     "for prompts like 'buy 5 NIFTYBEES every weekday at 09:15'. Server "
     "hydrates trigger.schedule + action.place_order (+ optional SL). "
     "Pass exactly ONE of `quantity` or `notional_inr`. STRICTLY "
     "SINGLE-TRIGGER, NO CONDITIONS/GUARDS — if the prompt has a second "
     "trigger, conditional second leg, or any 'if/unless/only when' "
     "guard, bail to propose_workflow.",
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
     "Build a workflow that places ONE PERPETUAL order when an "
     "indicator or price threshold fires. PREFER over propose_workflow "
     "for prompts like 'buy 10 INFY when RSI < 30' or 'sell 5 RELIANCE "
     "when price crosses above 2800'. Server hydrates trigger.{indicator|"
     "price} + action.place_order (+ optional SL). Pass exactly ONE of "
     "`quantity` or `notional_inr`. ABSOLUTE thresholds only — for "
     "runtime-relative ('5% below today's open') use propose_workflow with "
     "fetch.relative_threshold. NEVER call this when the user attaches a "
     "TTL/expiry phrase ('valid till month end', 'until Friday', 'good "
     "for the week') — no deactivation slot; route to propose_workflow "
     "with top-level valid_until.",
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
     "Build a workflow that allocates a ₹ budget across the top N stocks "
     "in a SECTOR. PREFER over propose_workflow for sector-named baskets "
     "('invest ₹1L equally across top 10 steel stocks'). Server hydrates "
     "trigger.schedule (+ optional gap-up/down gate) + fetch.screener + "
     "action.allocate_notional. Canonical sectors: steel, metals, banking, "
     "psu_bank, private_bank, it, auto, pharma, fmcg, energy, cement, "
     "defence, telecom. ONLY for sector-named baskets — when the user gives "
     "EXPLICIT TICKER symbols, use propose_workflow with action.allocate_"
     "notional. For non-canonical themes ('AI', 'EV', 'green', 'fintech'), "
     "ASK_USER first; do NOT silently substitute a sector.",
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
     "Build a workflow that acts on the user's EXISTING holding — sells "
     "ENTIRELY or sets a stop-loss. PREFER over propose_workflow for "
     "prompts like 'sell my INFY when RSI > 70', 'set 2% SL on my "
     "RELIANCE'. Two action shapes ('sell' entire holding / 'set_stoploss'). "
     "Three trigger shapes (indicator|price|schedule|manual). STRICTLY "
     "ENTIRE HOLDING — fractional sells ('sell half my INFY') go to "
     "propose_workflow. Avg-relative triggers ('+X% from buy price') also "
     "go to propose_workflow (no slot here).",
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


# ── META: find_tool (lazy-loader escape hatch) ─────────────────────────────
#
# Why this exists: the regex-based tool_router narrows the visible tool
# surface to ~8-12 tools per turn. When the user's intent doesn't match
# any keyword rule (unusual phrasings, novel verbs, niche capabilities),
# the model is left without the right tool to call. `find_tool` is the
# escape hatch: the LLM passes a free-form intent string, gets back the
# top_k matching tool names + one-line descriptions, then calls the
# chosen tool on the next hop. The chat hop loop lazy-loads the schemas
# of any tool the LLM names after a find_tool match.

tool(
    "find_tool",
    "Search the full catalog of internal tools by free-form intent. "
    "Use this when no obvious tool name matches the user's request, or "
    "when you need a capability that wasn't included in this turn's "
    "surface. Returns up to `top_k` matches with name + one-line "
    "description + category. The match is lexical (BM25-style) over the "
    "tool descriptions; pick the best match by name, then call the tool "
    "directly on the next hop — find_tool does NOT execute it for you, "
    "it only surfaces the schema. Categories: order, portfolio, "
    "market_data, indicator, workflow, backtest, news, account, meta.",
    {
        "query": {
            "type": "string",
            "description":
                "The user's intent in natural language. Examples: "
                "'compute moving average crossover', 'show me my P&L', "
                "'set a price alert', 'buy at 9:20 every Monday'.",
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "default": 5,
            "description":
                "How many candidate tools to return. Default 5. "
                "Bump to 10 if the first set looks off; otherwise pick "
                "the top match and call it next hop.",
        },
    },
    ["query"],
)


def get_tools_for_subset(subset_name: str) -> list:
    """Returns tool definition list for a given subset name."""
    names = TOOL_SUBSETS.get(subset_name, [])
    return [ALL_TOOLS[n] for n in names if n in ALL_TOOLS]
