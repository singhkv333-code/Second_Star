"""
backend/agents/tools.py

All Pivot tool definitions in OpenAI function calling format.
The LLM (Azure / OpenAI) reads these at call time to understand
available actions.

Tool VISIBILITY is owned by services/tool_registry.py (derived from
handlers); per-turn SELECTION by services/tool_router.py. The old
TOOL_SUBSETS classifier map that used to live here was deleted
2026-07-10 (chat-kernel cleanup) — it had no callers since the v2
cutover.
"""

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
     "Creates a FIXED-PRICE stop-loss GTT order to protect a holding. "
     "Accepts stop_price (absolute) OR stop_pct + entry_price (fixed % "
     "below a known entry).\n\n"
     "PREFER this for plain fixed SL requests where the user gives a "
     "specific stop_price or a stop_pct + entry_price, AND quantity is "
     "supplied.\n\n"
     "DO NOT pick this when:\n"
     "  • the user says **trailing** stop loss (this tool has no trail "
     "support — use propose_holding_action with action_kind='set_stoploss' "
     "and sl_offset_pct=N for trailing semantics)\n"
     "  • the user says **'on my <SYMBOL> holding/position'** without a "
     "quantity (use propose_holding_action — the workflow resolves "
     "quantity from holdings at fire time via fetch.portfolio)\n"
     "  • the user wants the SL tied to an entry from another agent "
     "(use propose_holding_action so the SL lives in the workflow).",
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
     "Cancels a pending regular or limit order by order_id. When the user "
     "names the order by SYMBOL ('cancel my LT order', 'drop the "
     "BERGEPAINT buy I queued', 'kill my pending HCLTECH order', 'scrap "
     "my TITAN limit') — the symbol IS enough. Do NOT ask the user for "
     "an opaque order_id. Instead, call `list_pending_orders` first, "
     "match the row where `tradingsymbol == <symbol>`, and pass that "
     "row's order_id to this tool. Asking the user for an order_id when "
     "they named the order by symbol is a forbidden capability gap.",
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
     "Returns all pending/open orders for today; each row carries "
     "{order_id, tradingsymbol, transaction_type, quantity, price}. "
     "CALL THIS BEFORE `cancel_order` or `modify_order` whenever the user "
     "named the order by SYMBOL rather than order_id ('cancel my LT "
     "order', 'change my pending TITAN limit to 3480'). Match by "
     "tradingsymbol, extract order_id, then call the action tool — same "
     "turn. Empty list = answer 'you have no pending orders right now', "
     "NOT a fabricated 'I'm not connected to your trading account'.",
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
     "Returns the ATM-centered option chain slice — bid/ask, OI, volume, IV "
     "and Greeks per strike — for an index, stock or commodity with listed "
     "options. Renders an interactive chain card. Use when the user asks "
     "about option premiums, the chain, OI, IV, strikes or expiries. "
     "For strategy SUGGESTIONS use suggest_option_strategy instead.",
     {
         "underlying": {"type": "string", "description":
                        "Underlying root, e.g. NIFTY, BANKNIFTY, RELIANCE, "
                        "SENSEX, CRUDEOIL (MCX commodities are tradeable, register-not-execute)."},
         "expiry":     {"type": "string", "description":
                        "ISO date (YYYY-MM-DD), 'nearest' (default) or 'next'. "
                        "The card lists the valid expiries."},
         "width":      {"type": "integer", "minimum": 1, "maximum": 20,
                        "default": 8, "description":
                        "Strikes each side of ATM (default 8)."},
     },
     ["underlying"])

tool("suggest_option_strategy",
     "THE options suggest-flow: give it the underlying and the user's view "
     "(bullish / bearish / neutral / volatile) and it returns 2-3 risk-"
     "tagged strategy candidates with live strikes, payoff, max loss/profit, "
     "probability of profit and a pre-trade critique — rendered as an "
     "editable strategy card. Use for 'I'm bullish on NIFTY', 'income "
     "strategy on BANKNIFTY', 'play the RBI event with options'. Do NOT ask "
     "the user for strikes/expiry first — the tool proposes liquid defaults "
     "and states its assumptions. Do NOT call this (or build/critique) for "
     "a purely conceptual ask with no intent to see a card — 'what would an "
     "iron condor look like', 'explain how a bull call spread works', 'what "
     "options play fits X view' asked out of curiosity. Answer those in "
     "prose with no tool call (get_option_chain only if you need a real "
     "strike/IV to ground the explanation) — a card the user didn't ask to "
     "build is a wrong-tool call, not a helpful extra.",
     {
         "underlying": {"type": "string", "description":
                        "e.g. NIFTY, BANKNIFTY, RELIANCE, SENSEX."},
         "view":       {"type": "string",
                        "enum": ["bullish", "bearish", "neutral", "volatile"],
                        "description":
                        "User's market view. 'expecting a big move' → "
                        "volatile; 'sideways/range/income' → neutral."},
         "expiry":     {"type": "string", "description":
                        "ISO date, 'nearest' (default) or 'next'."},
         "risk":       {"type": "string",
                        "enum": ["conservative", "moderate", "aggressive"],
                        "description":
                        "Risk appetite if the user stated one. Default "
                        "conservative — the card shows the other tiers too."},
         "qty_lots":   {"type": "integer", "minimum": 1, "default": 1},
     },
     ["underlying", "view"])

tool("build_option_strategy",
     "Builds ONE specific named option strategy with live strikes and an "
     "editable card (payoff, max loss/profit, POP, margin, critique). Use "
     "when the user names the structure: 'bull call spread on NIFTY', "
     "'iron condor', 'sell a 23000 put', 'covered call on RELIANCE'. "
     "Templates: long_call, long_put, bull_call_spread, bear_put_spread, "
     "bull_put_spread, bear_call_spread, cash_secured_put, covered_call, "
     "protective_put, long_straddle, short_straddle, long_strangle, "
     "short_strangle, iron_condor, iron_butterfly. For 'which strategy "
     "should I use' use suggest_option_strategy instead. If the user "
     "names a structure but is only asking how it works / what it would "
     "look like conceptually — not asking to see it built with live "
     "strikes — answer in prose, no tool call.",
     {
         "underlying": {"type": "string"},
         "template":   {"type": "string", "description":
                        "One of the named templates (snake_case)."},
         "expiry":     {"type": "string", "description":
                        "ISO date, 'nearest' (default) or 'next'."},
         "strikes":    {"type": "array", "items": {"type": "number"},
                        "description":
                        "Optional explicit strikes in leg order. Omit to "
                        "let the engine pick liquid delta-based strikes."},
         "qty_lots":   {"type": "integer", "minimum": 1, "default": 1},
     },
     ["underlying", "template"])

tool("critique_option_strategy",
     "Pre-trade critique (the Options Copilot): takes explicit legs the "
     "user already has in mind and returns the strategy card with verdict "
     "+ flags — liquidity, IV regime vs realized vol, max-loss vs account "
     "size, expiry-day gamma, undefined-risk warnings. Use for 'should I "
     "sell the 24000 call?', 'critique this trade', 'is this straddle ok?'. "
     "Requires the user's OWN specific legs — if they haven't named "
     "strikes/sides yet and are just asking what you'd suggest, that's "
     "suggest_option_strategy, not this.",
     {
         "underlying": {"type": "string"},
         "expiry":     {"type": "string", "description":
                        "ISO date, 'nearest' (default) or 'next'."},
         "legs":       {"type": "array", "minItems": 1, "maxItems": 6,
                        "items": {
                            "type": "object",
                            "properties": {
                                "option_type": {"type": "string",
                                                "enum": ["CE", "PE"]},
                                "side": {"type": "string",
                                         "enum": ["BUY", "SELL"]},
                                "strike": {"type": "number"},
                            },
                            "required": ["option_type", "side", "strike"],
                        },
                        "description": "The legs to critique."},
         "qty_lots":   {"type": "integer", "minimum": 1, "default": 1},
     },
     ["underlying", "legs"])

tool("get_portfolio_greeks",
     "Aggregate net option Greeks (delta/gamma/theta/vega) across the "
     "user's registered option strategies, with per-underlying breakdown. "
     "Use for 'what's my delta', 'portfolio greeks', 'how exposed am I to "
     "theta/vega'.",
     {},
     [])

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

tool("get_market_status",
     "Returns whether NSE is open, current time in IST, time until open/close, upcoming holidays.",
     {}, [])

tool("get_upcoming_events",
     "Returns upcoming earnings dates, ex-dividend dates, RBI MPC meeting "
     "dates, F&O expiry dates. CALL THIS DIRECTLY for any 'when is X "
     "reporting', 'next results date for X', 'next earnings on X', "
     "'ex-dividend date for X', 'next dividend on X', 'when does X go "
     "ex-dividend', 'upcoming corporate action on Y' — these all map "
     "here. Do NOT call `find_tool` first — this tool handles all "
     "calendar-event lookups in the chat surface. If the result for the "
     "named symbol is empty, say 'no event on the {X} calendar I have' "
     "— NEVER say 'I don't have a calendar tool here' or 'no earnings-"
     "calendar lookup in this chat'; the tool exists and was just called.",
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

tool("compute",
     "Deterministic calculator — the COMPUTE lane. Run a short Python "
     "expression over values ALREADY IN CONTEXT (numbers the user typed, "
     "or values a tool returned earlier this conversation) and get an "
     "exact result. USE THIS for any quantitative transform that has no "
     "dedicated tool: percentile ranks, sorting/ranking a list by a "
     "metric, averages/medians/std-dev, spreads, weights, position "
     "sizing, P&L what-ifs ('if it falls 8% on 50 shares @ ₹1,520'), "
     "breakevens/payoff math from given strikes+premiums, CAGR from "
     "given start/end values, ratio math, date-free arithmetic of any "
     "kind. NEVER decline a computable ask because no dedicated tool "
     "exists, and NEVER do multi-step arithmetic in prose — route it "
     "here so the numbers are exact.\n\n"
     "HARD RULE (anti-fabrication): every input number in `code` MUST be "
     "a literal you saw in this conversation (user-supplied or "
     "tool-returned). If an input is missing, fetch it with the right "
     "data tool FIRST, then compute. This tool does maths; it is NOT a "
     "data source.\n\n"
     "Language subset: literals, arithmetic, comparisons, comprehensions, "
     "lambda, f-strings, assignments, sorted/sum/min/max/len/round/abs/"
     "enumerate/zip/map/filter/any/all, math.*, statistics.*. No imports, "
     "no loops, no I/O. Write inputs as a dict/list, transform, and end "
     "with the expression whose value answers the user. Example — "
     "percentile ranks:\n"
     "vals = {'TCS': 3.2, 'INFY': 1.8, 'WIPRO': 0.9}\n"
     "s = sorted(vals.values())\n"
     "{k: round(100*sum(1 for x in s if x <= v)/len(s)) "
     "for k, v in vals.items()}",
     {
         "code": {
             "type": "string",
             "description": "Python-subset script; the LAST expression's "
                            "value is returned.",
         },
         "note": {
             "type": "string",
             "description": "3-8 word label of what is being computed "
                            "(shown in traces).",
         },
     },
     ["code"])

# ── ANALYTICS / INDICATORS / RISK / COMPARISON ──────────────────────────────
# Bridges to /core/ (indicator vault + calculations + data layer).

tool("get_indicator",
     "Compute a single technical indicator on an NSE-listed ticker at a "
     "chosen bar `interval`. `interval` is REQUIRED: if the user named a "
     "timeframe ('on the daily', 'weekly', '15-min', 'hourly') pass it; if "
     "they did NOT, OMIT `interval` entirely — the platform asks the user "
     "and resumes — do NOT guess daily yourself (see the timeframe "
     "EXCEPTION in the system prompt). Use for: 'RELIANCE RSI on the "
     "daily', 'TCS 50-day SMA weekly', 'INFY 15-min MACD'. The `indicator` "
     "arg accepts rsi/sma/ema/wma/macd/adx/supertrend/atr/bollinger/"
     "donchian/keltner/obv/vwap/cci/mfi/stoch/williams_r/aroon/trix/roc/"
     "historical_vol. `period` is the lookback counted in BARS of the "
     "chosen `interval` (e.g. 14 for RSI(14)). Output includes "
     "current_value, signal (bullish/bearish/neutral), interpretation.",
     {
         "symbol":         {"type": "string"},
         "indicator":      {"type": "string"},
         "period":         {"type": "integer", "minimum": 2, "maximum": 250, "default": 14},
         "history_period": {"type": "string", "default": "6mo",
                            "description": "yfinance period: 1mo|3mo|6mo|1y|2y|5y"},
         "interval":       {"type": "string",
                            "description": "the bar interval / timeframe — "
                                           "1m, 5m, 15m, 30m, 1h, daily, weekly, "
                                           "or monthly (period counts BARS of it)"},
     },
     ["symbol", "indicator", "interval"])

tool("get_multiple_indicators",
     "Compute several indicators for one ticker in a single call — saves "
     "round-trips when the user asks for multiple ('RSI and MACD for "
     "INFY', 'show me Bollinger Bands and ATR for TCS'). All indicators "
     "share one `interval`, which is REQUIRED: pass the timeframe the user "
     "named, or OMIT it if they named none — the platform asks and resumes. "
     "Do NOT guess daily (see the timeframe EXCEPTION in the system prompt).",
     {
         "symbol":         {"type": "string"},
         "indicators":     {"type": "array", "items": {"type": "string"}},
         "history_period": {"type": "string", "default": "6mo"},
         "interval":       {"type": "string",
                            "description": "the bar interval / timeframe — "
                                           "1m, 5m, 15m, 30m, 1h, daily, weekly, "
                                           "or monthly (period counts BARS of it)"},
     },
     ["symbol", "indicators", "interval"])

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
     "Compare/rank 1+ tickers by a metric over a period (a single symbol works — it returns that one's return/vol/drawdown table). Use for ANY "
     "multi-stock comparison: 'compare RELIANCE and TCS', 'INFY vs TCS "
     "which gave better return last year', 'compare returns of HDFCBANK "
     "and ICICIBANK over 3 years', 'which is better WIPRO or INFOSYS', "
     "'rank these by Sharpe'. CRITICAL: for a two-stock comparison you "
     "MUST call this with BOTH symbols — never fetch one stock's number "
     "and state the other's from memory (that fabricates). Returns the "
     "full side-by-side table "
     "(total return %, volatility, Sharpe, max drawdown) for every "
     "symbol with a declared winner. IF the user also wants PE/ROE and/or "
     "SMA/RSI compared, set `include` below in THIS SAME call — do not "
     "separately call fetch_fundamentals/get_market_data once per "
     "symbol for a comparison ask, that's slower and the model routinely "
     "forgets half the calls, silently dropping metrics the user asked "
     "for.",
     {
         "symbols":  {"type": "array", "items": {"type": "string"}},
         "period":   {"type": "string", "default": "1y"},
         "metric":   {"type": "string", "default": "sharpe",
                      "enum": ["sharpe", "total_return", "volatility", "max_drawdown"]},
         "include":  {"type": "array", "items": {"type": "string"},
                      "description": "Optional add-ons merged into each "
                      "symbol's row: 'fundamentals' (PE/ROE/ROCE/D-E/"
                      "sector), 'technicals' (SMA20/50/200, RSI14, period "
                      "high/low). Pass both if the user asked for returns "
                      "+ fundamentals + technicals in one comparison."},
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

# ── FUNDAMENTAL SCREEN / SINGLE-STOCK FUNDAMENTALS / NEWS / IPO ───────────────

_SCREEN_FIELDS = [
    # valuation / quality / balance-sheet ratios
    "pe", "peg", "roe", "roce", "de", "payout", "price_to_book", "ev_to_ebitda", "roa",
    "current_ratio", "quick_ratio", "interest_coverage", "net_profit_margin",
    "ebitda_margin", "asset_turnover",
    # extended ratio set (scraped + pivot-derived backfill)
    "roic", "operating_margin", "gross_margin", "inventory_turnover",
    "receivables_turnover",
    # GROWTH (YoY over the two latest annual filings)
    "revenue_growth", "net_profit_growth", "eps_growth",
    # size (real market cap, ₹ crore, enrich-backed)
    "market_cap",
    # raw line items (₹ crore for absolutes, ₹ for per-share)
    "revenue", "net_profit", "operating_profit", "eps_basic", "eps_diluted",
    "total_debt", "total_equity", "reserves", "cash_from_ops",
    "book_value_per_share", "enterprise_value_cr", "interest_expense",
]

tool("screen_fundamentals",
     "Cross-sectional fundamental SCREEN over the financials DB. Returns the "
     "LIST of companies passing EVERY numeric constraint (filters AND-ed). The "
     "MANY-company tool ('pharma stocks with P/E under 25', 'ROE > 18 and "
     "positive revenue growth', 'cheap banking stocks'); for ONE company use "
     "fetch_fundamentals.\n\n"
     "FIELD COVERAGE — pick the POPULATED field, not just the literal word:\n"
     " • 'return on capital(-employed)' → `roce` (populated); `roic` is SPARSE "
     "(a roic screen usually comes back empty) — prefer roce and say so.\n"
     " • dividend yield, promoter pledge, promoter holding, ESG and "
     "shareholding are NOT screenable — don't map them onto a look-alike "
     "(payout ratio is NOT dividend yield); name the gap and offer the closest "
     "honest screen.\n"
     " • An EMPTY result on a mainstream ask usually means the field is sparse, "
     "not that no company qualifies — retry once with the populated sibling.\n"
     " • No market-cap floor unless you pass one; on GROWTH screens add a "
     "`market_cap` filter or base-effect microcaps top the table.\n\n"
     "SCREENABLE FIELDS (use the metric the user names; never substitute):\n"
     " • ratios: pe, peg (trailing P/E ÷ trailing YoY EPS growth), roe, roce, "
     "de (debt/equity), payout, price_to_book (P/B), ev_to_ebitda, roa, "
     "current_ratio, quick_ratio, interest_coverage, net_profit_margin, "
     "ebitda_margin, asset_turnover\n"
     " • GROWTH: revenue_growth, net_profit_growth, eps_growth — YoY by default "
     "(latest two annual filings); set growth_years=N for an N-year CAGR when "
     "the user names a horizon.\n"
     " • market_cap — numeric ₹ crore (enrich-backed). For a vague "
     "'large/mid/small cap' WORD with no number use market_cap_tier instead.\n"
     " • raw line items: revenue, net_profit, operating_profit (EBITDA), "
     "eps_basic, total_debt, total_equity, reserves, cash_from_ops, "
     "book_value_per_share, enterprise_value_cr (₹ crore / ₹ per share).\n\n"
     "CUSTOM RATIOS: a ratio not in the field list above (debt/EBITDA, "
     "price/sales, cash/debt, etc.) MUST be defined in custom_ratios as {name, "
     "numerator, denominator} over raw line items and referenced by `name` in "
     "filters/sort_by — never dropped or substituted. (EBITDA = "
     "operating_profit; sales = revenue.)\n\n"
     "Sector is optional + coarse: pharma, bank, it, energy, auto, "
     "autoancillary, metal, finance, chemicals, fmcg, infra, textiles. 'auto' = "
     "vehicle MAKERS (Maruti, Tata Motors, M&M, Bajaj, Hero); 'autoancillary' = "
     "auto PARTS suppliers only.\n\n"
     "This is a READ-ONLY ranking/screen, not a weighted basket with per-name "
     "rationale. For a 'build me a basket' ask wanting sizing/weights/a gate, "
     "use build_strategy instead.",
     {
         "filters": {"type": "array",
                     "description": "Numeric constraints, AND-ed. Include EVERY "
                     "constraint the user named. Compare against a NUMBER via "
                     "`value`, or against ANOTHER FIELD via `value_field` "
                     "('operating cash flow exceeds net profit' → {field:"
                     "'cash_from_ops', op:'>', value_field:'net_profit'}; 'P/E "
                     "below its growth rate' → {field:'pe', op:'<', value_field:"
                     "'net_profit_growth'}). Exactly one of value/value_field.",
                     "items": {"type": "object", "properties": {
                         "field": {"type": "string", "enum": list(_SCREEN_FIELDS)},
                         "op":    {"type": "string", "enum": ["<", "<=", ">", ">=", "="]},
                         "value": {"type": "number"},
                         "value_field": {"type": "string",
                                         "enum": list(_SCREEN_FIELDS)}},
                         "required": ["field", "op"]}},
         "sector":  {"type": "string",
                     "description": "Coarse sector. Set ONLY when the user "
                     "explicitly names a sector/industry. NEVER infer a sector "
                     "from the metrics, and NEVER narrow an all-market screen to "
                     "a sector the user did not ask for — an unsectored screen "
                     "spans the whole market.",
                     "enum": ["pharma", "bank", "it", "energy", "auto",
                              "autoancillary", "metal", "finance", "chemicals",
                              "fmcg", "infra", "textiles"]},
         "market_cap_tier": {"type": "string", "enum": ["large", "mid", "small"],
                     "description": "For a vague cap WORD with no ₹ number. "
                     "Restrict to large/mid/small by REAL market cap (large ≥ "
                     "₹50k Cr, mid ₹20k–50k Cr, small < ₹20k Cr). For an explicit "
                     "₹ threshold use a market_cap filter instead."},
         "custom_ratios": {"type": "array",
                     "description": "Derived metrics over raw line items, usable "
                     "by name in filters/sort_by.",
                     "items": {"type": "object", "properties": {
                         "name": {"type": "string"},
                         "numerator": {"type": "string"},
                         "denominator": {"type": "string"}},
                         "required": ["name", "numerator", "denominator"]}},
         "sort_by": {"type": "object", "properties": {
                         "field": {"type": "string", "enum": list(_SCREEN_FIELDS)},
                         "dir":   {"type": "string", "enum": ["asc", "desc"]}}},
         "limit":   {"type": "integer", "minimum": 1, "maximum": 100, "default": 15},
         "exclude": {"type": "array", "items": {"type": "string"},
                     "description": "Sectors, 'PSU', or named companies/tickers "
                     "to carve out of the results (hard-filtered, not advisory)."},
         "title": {"type": "string", "maxLength": 90,
                     "description": "REQUIRED with presentation='table': a "
                     "short, specific title rendered as the reply's heading "
                     "(3–9 words, title case). Make it reflect THIS ask — "
                     "'Profitable Growers: P/E Above 10, ROE Above 20%' or "
                     "'Cheapest Large Caps by Earnings' — never a generic "
                     "'Fundamental Screen' or a restated metric name."},
         "growth_years": {"type": "integer", "minimum": 1,
                     "description": "Horizon for ALL growth fields, in "
                     "financial years. Set this ONLY when the user names a "
                     "horizon ('over the last 5 years' → growth_years=5, "
                     "'10-year growth' → 10); when they don't, OMIT it — "
                     "never guess a window, the default is YoY (latest two "
                     "annual filings). N>1 = CAGR between the latest filing "
                     "and the one N filings earlier. Any N works — bounded "
                     "only by a company's actual filing history; names "
                     "without enough filings drop out rather than getting a "
                     "fabricated number."},
         "presentation": {"type": "string", "enum": ["table", "analysis"],
                     "default": "table",
                     "description": "How the rows reach the user. 'table' "
                     "(default): the ranked rows ARE the answer — a "
                     "deterministic table renders verbatim and you write "
                     "nothing more. 'analysis': the rows are INPUT to "
                     "research — the same exact table still renders, and YOU "
                     "write the analysis below it (comparisons, tradeoffs, "
                     "caveats, a defended view). Choose 'analysis' whenever "
                     "the user asks to analyze/research, says 'best' about a "
                     "broad judgment, or wants a view — a bare table is an "
                     "incomplete answer to those. Choose 'table' for a plain "
                     "'show me companies with X' screen."},
     },
     [],
     defaults={"limit": 15})

tool("fetch_fundamentals",
     "Snapshot of ONE stock's fundamentals AND company profile. Returns: P/E, "
     "ROE, ROCE, D/E, net margin, EPS, book value, dividend payout (financials "
     "DB); PLUS sector, industry, business_summary (what the company does), "
     "promoter_holding_pct (promoter/insider stake — a proxy from yfinance, not "
     "the exact SEBI filing), institution_holding_pct, website, and employees "
     "(enrichment DB). Use for 'should I buy X', 'what is X's PE/ROE', 'what "
     "sector/industry is X in', 'what does X do', 'who are the promoters of X / "
     "promoter holding of X', 'tell me about X', or one leg of a 'compare A vs "
     "B' (call once per symbol). Returns null for any metric not populated "
     "(coverage is sparse outside large caps) — if a value is null SAY it's "
     "unavailable, NEVER invent it. Note promoter_holding_pct is a proxy; "
     "present it as approximate. Not a live-price tool (use get_market_data view=quote).",
     {"symbol": {"type": "string",
                 "description": "NSE ticker, uppercase. Infosys->INFY, Reliance->RELIANCE."}},
     ["symbol"])

tool("query_financials",
     "Look up ANY single financial figure or ratio for ONE company from the "
     "financials DB — beyond the fixed fetch_fundamentals snapshot. Use this "
     "when the user asks for a specific line item or ratio that the snapshot "
     "doesn't carry: e.g. 'INFY's gross margin', 'Reliance inventory turnover', "
     "'TCS cash from operations', 'HDFCBANK interest coverage', 'employee "
     "benefit expense of Infosys', 'operating margin', 'sales per share', "
     "'P/S ratio'. Pass the user's phrasing verbatim in `metric` — the backend "
     "translates it (e.g. 'sales'->Net Sales/Revenue, 'cash from ops'->Net "
     "CashFlow From Operating Activities) and, for P/E, P/B, P/S, computes it "
     "LIVE as price/per-share. Returns {resolved, value, unit, line_item "
     "(the exact DB row matched), statement, period_label, matched_via}. When "
     "NOT confident it returns resolved=false + `candidates` (ranked line "
     "items) — pick the right one and call again with that exact line_item, or "
     "tell the user it's unavailable. NEVER invent a value; if resolved=false "
     "and no candidate fits, say the figure isn't in our data.",
     {"symbol": {"type": "string", "description": "NSE ticker, uppercase."},
      "metric": {"type": "string", "description":
                 "The figure the user wants, in their own words — e.g. "
                 "'gross margin', 'sales', 'cash from operations', 'P/S', "
                 "'inventory turnover', or an exact line_item from a prior "
                 "candidates list."},
      "basis":  {"type": "string", "enum": ["consolidated", "standalone"],
                 "default": "consolidated"},
      "history": {"type": "integer", "minimum": 0, "maximum": 15, "default": 0,
                  "description":
                  "Set to N (e.g. 12) to ALSO return the last N years as an "
                  "annual `series` (newest first) — use for trend / CAGR / "
                  "'which year had the highest profit' / 'revenue over the "
                  "years' asks. 0 (default) returns just the latest value."}},
     ["symbol", "metric"],
     defaults={"basis": "consolidated", "history": 0})

tool("get_symbol_news",
     "Recent news headlines for ONE stock via yfinance. Use for 'recent news "
     "on X', 'what's happening with X', 'any news on X'. Returns "
     "{title, publisher, link, published}. If empty, say so — do not fabricate "
     "headlines. For macro / non-company current-affairs use web_search_brief.",
     {"symbol": {"type": "string", "description": "NSE ticker, uppercase."},
      "limit":  {"type": "integer", "minimum": 1, "maximum": 50, "default": 5}},
     ["symbol"],
     defaults={"limit": 5})

tool("list_upcoming_ipos",
     "Lists current open + upcoming mainboard and SME IPOs from the live feed "
     "(NSE skeleton enriched with Trendlyne: name, symbol, price band, "
     "open/close dates, lot/issue size, type, status, PLUS subscription "
     "breakdown — total/retail/HNI/QIB ×, RHP prospectus link, and market "
     "cap). Use for 'any IPOs open right now?', 'upcoming IPOs', 'how "
     "subscribed is X', 'SME IPOs'. Cite the `source` (e.g. NSE + Trendlyne). "
     "Some Trendlyne-only rows carry no NSE symbol (`registerable: false`) — "
     "they're informational; do NOT offer to register/automate those. Read-"
     "only. Empty list = no live issues (not an error); if the feed is "
     "unreachable relay the note verbatim — NEVER invent IPOs.",
     {},
     [])

tool("get_ipo_details",
     "Full detail of ONE IPO matched by name or symbol (price band, dates, "
     "lot/issue size, type, status, PLUS the Trendlyne enrichment: "
     "subscription breakdown — total/retail/HNI/QIB ×, RHP link, allotment "
     "date/status, market cap). Use after list_upcoming_ipos when the user "
     "asks about a specific IPO ('tell me about the X IPO', 'how subscribed "
     "is X', 'I want to apply for X'). If found is false, present the "
     "candidate matches to disambiguate. NEVER fabricate IPO details.",
     {"name_or_symbol": {"type": "string",
                         "description": "IPO company name or NSE symbol. Case-insensitive."}},
     ["name_or_symbol"])

tool("get_ipo_listing",
     "Post-listing performance of an IPO — issue price, current return %, and "
     "the listing-day pop (issue→first-day open). Use for 'how did X list', "
     "'X listing gain', 'did X list well'. Reads NSE past-issues paired with "
     "the live price, and falls back to Trendlyne's published listing-day gain "
     "+ current return when NSE is sparse or the name has no live symbol. "
     "Returns the ipo_listed_card payload (issue/current/current-return%/"
     "listing-day%/listing date + source). NEVER fabricates the current price, "
     "the gain, the issue price, or the listing date — any unavailable field "
     "is null with an honest note.",
     {"name_or_symbol": {"type": "string",
                         "description": "IPO company name or NSE symbol. Case-insensitive."}},
     ["name_or_symbol"])

tool("propose_ipo_application",
     "Builds the editable IPO application card for a specific open IPO. Use "
     "when the user wants to apply ('I want to apply for X', 'apply for the X "
     "IPO', 'register me for X'). This REGISTERS THE USER'S INTENT only — "
     "Pivot never submits or funds the bid; the user places + approves the "
     "UPI mandate themselves in their broker app by 5 PM on close day. "
     "Returns the card payload (price band, lot size, default lots, amount "
     "estimate, in-band validation hints) — the FE renders the editable form "
     "and posts to /ipo-applications when the user clicks Register. NEVER "
     "claim Pivot places the bid.",
     {"name_or_symbol": {"type": "string",
                         "description": "IPO company name or NSE symbol. Case-insensitive."}},
     ["name_or_symbol"])

tool("propose_ipo_automation",
     "Builds the open-day reminder WORKFLOW for a specific upcoming / open IPO "
     "(a 3-step draft: trigger.ipo_open + action.arm_ipo_intent + notify.message). "
     "Use when the user wants automation/reminders ('set up reminders for the X "
     "IPO', 'remind me when X opens', 'automate the X IPO', 'open-day reminder "
     "for X'). The watcher fires once on the upcoming -> open edge, the action "
     "writes an intent_armed row, and the notify step pushes the open-day "
     "handoff text. PIVOT NEVER SUBMITS OR FUNDS THE BID — the verb is 'arm' / "
     "'remind', never 'apply'. The FE renders the same WorkflowDraftCard as "
     "propose_workflow; the user edits lots / category / mode and activates.",
     {"name_or_symbol": {"type": "string",
                         "description": "IPO company name or NSE symbol. Case-insensitive."}},
     ["name_or_symbol"])

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

# run_backtest (legacy single-indicator backtest) RETIRED 2026-06-01 — it used a
# divergent hardcoded cost model (10 bps) + 10%-of-capital sizing and carried no
# rigor battery; rsi/price_cross weren't even implemented. Backtests now route to
# backtest_workflow (simple) / backtest_dsl_tree (compound).

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
            "Ordered steps; step 0 MUST be trigger.*. Each `config` must "
            "contain the required keys listed for its step_type in the "
            "CATALOG. Server validates against registry models — extras allowed."
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
     "Do NOT call for 'how would I automate X' asked conceptually with no "
     "intent to actually arm it — answer in prose instead.\n\n"
     "FALLBACK workflow builder. PREFER in this order:\n"
     "  (1) `propose_dsl_workflow` — ANY multi-condition entry/exit, "
     "indicator-vs-indicator, multi-output component, aggregate window, "
     "volume-relative, cross-symbol spread, session-day filter, gap, "
     "pct_change, or exit referencing position state.\n"
     "  (2) `propose_scheduled_order` — recurring HH:MM, single action.\n"
     "  (3) `propose_threshold_order` — SINGLE-condition price/RSI/SMA/EMA "
     "absolute threshold; NEVER for AND/OR compounds.\n"
     "  (4) `propose_basket_allocation` — sector basket.\n"
     "  (5) `propose_holding_action` — sell/SL on existing holding.\n\n"
     "Use this tool ONLY when none fits: runtime-relative thresholds ('5% "
     "below today's open'), multi-trigger / multi-action workflows, "
     "news-event triggers, portfolio-state guards.\n\n"
     "NOT for amending or registering an option strategy card — 'make it "
     "2 lots', 'move the strike', 'show the aggressive one' re-emit "
     "`build_option_strategy`; registration happens on the card's button.\n\n"
     "NOT for BACKTESTS — 'test', 'backtest', 'simulate', 'run a … on', "
     "'how would X have done', 'what if I had …' NEVER route here (this "
     "tool activates a workflow, not metrics). Use `backtest_dsl_tree` "
     "for compound backtests and `backtest_workflow` for simple ones.\n\n"
     "NOT for single-action automation (use the four macros above).\n\n"
     "EMITTING IS NOT ACTIVATING — emit the draft, do NOT ASK_USER to "
     "confirm. Step 0 MUST be a trigger.*. Extra trigger.* start new "
     "BRANCHES; two adjacent triggers invalid. Inter-step refs use "
     "Mustache: `{{ context.<idx>.<field> }}` / `{{ now }}`. Indian "
     "stocks default NSE/INR; times Asia/Kolkata.\n\n"
     "CATALOG (step_type [category] required-keys; server validates "
     "against registry Pydantic models — extra keys allowed):\n"
     f"{_PROPOSE_CATALOG}\n\n"
     "STEP NOTES (tricky configs — see agentic_examples.json):\n"
     "- trigger.market_relative_time: anchor∈{open,close,pre_open,post_close}, "
     "offset_minutes signed; PREFER over hardcoded 09:15/15:30 cron.\n"
     "- trigger.indicator: `value` is a FIXED level NEVER a second indicator. "
     "For indicator-vs-indicator (50-EMA vs 200-EMA) use trigger.schedule + "
     "two fetch.indicator + condition.numeric.\n"
     "- fetch.indicator: only rsi|sma|ema|macd (macd=histogram, NOT macd_line/signal).\n"
     "- fetch.relative_threshold: reference∈{day_open,prior_close,prior_high,"
     "prior_low}, signed offset_pct. USE for runtime-relative levels — "
     "Mustache arithmetic NOT supported.\n"
     "- fetch.intraday_pnl: total_pct is already a % (compare -2 not -0.02).\n"
     "- fetch.portfolio holdings: {quantity,avg_buy_price,last_price,"
     "current_value_inr,pnl_inr,pnl_pct} — no holding_days.\n"
     "- fetch.screener sectors: steel, metals, banking, psu_bank, private_bank, "
     "it, auto, pharma, fmcg, energy, cement, defence, telecom.\n"
     "- action.place_order: quantity OR notional_inr; order_type∈{market,limit}.\n"
     "- action.set_stoploss: trigger_price OR trigger_offset_pct (2 = 2%).\n"
     "- notify.message: channel='push' (in-app only; email/SMS/WhatsApp NOT wired).\n"
     "- NOT AVAILABLE: prediction-market (Polymarket/Kalshi), macro-calendar "
     "outcome and news/headline triggers. A view about the WORLD ('if the RBI "
     "cuts', 'when there's news on X') is not a trigger we can fire on — say so "
     "in one line and offer the nearest wired thing: a PRICE or INDICATOR level "
     "on the instrument that view would move, or a SCHEDULE if the ask was "
     "really about timing. Drafts naming one are rejected.\n\n"
     "HARD RULES:\n"
     "1. STAY LITERAL — only what the user asked for; no unprompted "
     "sell/SL/trim branches, no unprompted notify.message, no buying-power "
     "guard.\n"
     "2. Multi-condition buy/sell → STOP and call `propose_dsl_workflow` "
     "instead. `trigger.indicator` / `trigger.price` here carries ONE leg "
     "only; chaining `condition.numeric` silently drops extra legs.\n"
     "3. QUANTITY IS NEVER A DEFAULT. 'buy some X' → ASK_USER. Exceptions: "
     "'sell my SYMBOL' (fetch.portfolio + Mustache ref); SIPs.",
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
     "SIMULATES a strategy on historical bars (daily by default; pass "
     "`interval='1h'` for hourly, `'15m'` etc for finer intraday). "
     "For 'backtest …' / "
     "'simulate …' / 'how would X have done' / 'what if I had bought …' "
     "prompts. Returns a chart card (price + equity + signals + metrics + "
     "buy-and-hold benchmark). Shares EXACT `steps[]` schema with "
     "propose_workflow.\n\n"
     "USE THIS for a simple single-indicator backtest, not propose_workflow "
     "(which activates an agent). For compound / crossover / multi-condition "
     "strategies, prefer backtest_dsl_tree.\n\n"
     "Indicators: rsi, sma, ema, wma, macd (histogram; 0=signal-line cross), "
     "adx, supertrend (0=trend flip), bollinger/bb (%B; 0=lower, 1=upper), "
     "stoch (%K), stoch_rsi, cci, mfi, williams_r, atr, keltner, donchian, "
     "aroon, psar, roc, trix, obv, vwap.\n\n"
     "Backtest-only steps: action.set_takeprofit; fetch.rolling_high/"
     "rolling_low (lookback × multiplier; 0.9 = '10% below 20-day high'); "
     "condition.position / market_status / time_window. action.place_order "
     "product: 'CNC'|'MIS'. action.set_stoploss supports trailing:true with "
     "trigger_offset_pct.\n\n"
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
                 "Historical lookback window. Use shorter windows for "
                 "recently-listed symbols."
             ),
         },
         "start_date": {
             "type": "string",
             "description": (
                 "OPTIONAL ISO YYYY-MM-DD. Clips the backtest to a fixed "
                 "window AFTER the period fetch — use for event-driven asks."
             ),
         },
         "end_date": {
             "type": "string",
             "description": "OPTIONAL ISO YYYY-MM-DD end of the window.",
         },
         "benchmark_symbol": {
             "type": "string",
             "description": (
                 "OPTIONAL buy-and-hold benchmark (default: the trade symbol). "
                 "For baskets / pairs, pass NIFTYBEES or BANKBEES."
             ),
         },
         "interval": {
             "type": "string",
             "enum": ["1d", "1wk", "1mo", "1h", "30m", "15m", "5m"],
             "default": "1d",
             "description": (
                 "OPTIONAL bar interval. Default '1d' (daily). Intraday "
                 "('1h', '15m', '5m', '30m') is accepted; the source's "
                 "rolling data cap will honestly clamp the window when "
                 "'period' exceeds it (1h yfinance=730d, Kite=400d)."
             ),
         },
         "starting_capital": {
             "type": "number",
             "description": (
                 "The ACTUAL ₹ amount being deployed. ALWAYS pass this "
                 "when a real figure is known — a basket's stated deploy "
                 "amount, a capital_inr the user gave, or an amount from "
                 "earlier in this conversation. Discrete-share rounding "
                 "makes the return % itself depend on capital size, so "
                 "silently defaulting to ₹10,00,000 when the user asked "
                 "about a different amount misreports the result, not "
                 "just the label. Omit ONLY when no figure is known."
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
     "SIMULATES a strategy with compound (multi-condition / cross-symbol / "
     "aggregator-based) entry rules. PREFER over backtest_workflow when any of:\n"
     "  • 2+ AND/OR conditions\n"
     "  • cross-symbol filter or comparison\n"
     "  • indicator-vs-indicator crossing\n"
     "  • multi-output indicators (Bollinger, Donchian, Keltner, Stoch %K/%D, Aroon up/down)\n"
     "  • lookback/aggregator ('20-day high breakout', 'highest of last 20 "
     "bars', 'percentile of last year', 'barssince RSI<30', 'correlation')\n"
     "  • day-of-week filter ('on Tuesday', 'every Friday')\n"
     "  • time-shifted reference ('yesterday's open', 'gap-down')\n"
     "\n"
     "ENTRY vs EXIT — CRITICAL: `condition` is the BUY/ENTRY rule ONLY. If "
     "user states BOTH buy AND sell, put buy in `condition` and sell in "
     "`exit_condition`. Do NOT AND them in `condition` — that's a "
     "contradiction (RSI<30 AND RSI>70 can never both hold); server rejects.\n"
     "\n"
     "Pass the user's condition(s) VERBATIM — do NOT paraphrase or simplify. "
     "Returns the same chart-card shape as backtest_workflow.",
     {
         "condition": {
             "type": "string",
             "description": (
                 "Natural-language entry condition. Pass VERBATIM — don't "
                 "summarise, paraphrase, or strip operators."
             ),
         },
         "primary_symbol": {
             "type": "string",
             "description": (
                 "Symbol the trade fires on. The condition may reference "
                 "other symbols as filters — still pick the action symbol here."
             ),
         },
         "start_date": {
             "type": "string",
             "description": "OPTIONAL ISO YYYY-MM-DD. Defaults to 3y before end_date.",
         },
         "end_date": {
             "type": "string",
             "description": "OPTIONAL ISO YYYY-MM-DD. Defaults to today.",
         },
         "exit_condition": {
             "type": "string",
             "description": (
                 "OPTIONAL natural-language EXIT rule. Pass verbatim whenever "
                 "the user describes SELL/EXIT/close ('sell when RSI > 70', "
                 "'exit on 8% drawdown from peak', 'close after 30 bars'). "
                 "Overrides exit_kind/bars/pct; engine evaluates the "
                 "translated exit tree each bar the position is open."
             ),
         },
         "interval": {
             "type": "string",
             "description": (
                 "REQUIRED bar interval — 1m/5m/15m/30m/1h/daily/weekly/"
                 "monthly. CRITICAL: indicator 'period' (RSI(14), SMA(50)) "
                 "counts BARS of THIS interval (RSI(14) on 15m = 14 "
                 "fifteen-minute bars, not 14 days). If user named a "
                 "timeframe, pass it; if NOT, OMIT — platform asks (do not "
                 "guess daily). Intraday windows shallow (1m→7d, "
                 "5/15/30m→60d, 1h→730d); handler clamps."
             ),
         },
         "exit_kind": {
             "type": "string",
             "enum": ["n_day_hold", "stop_loss_pct", "hold_to_end"],
             "default": "n_day_hold",
             "description": (
                 "n_day_hold: exit after exit_bars bars at next open "
                 "(DEFAULT when no sell rule). stop_loss_pct: exit at stop "
                 "price on bar-low. hold_to_end: NEVER sell early — carry "
                 "to final bar, mark-to-market. Use hold_to_end when user "
                 "says hold / don't sell / buy-and-hold, OR gives no exit "
                 "AND phrases a hold ('buy RELIANCE Jan 2023 and hold')."
             ),
         },
         "initial_position": {
             "type": "object",
             "description": (
                 "Seed an EXISTING holding on primary_symbol at window "
                 "start, so the exit rule backtests against a position the "
                 "user already owns ('I hold 50 INFY from ₹1400 — test "
                 "selling at RSI>70'). Pass instead of using `condition` "
                 "to open the position."
             ),
             "properties": {
                 "quantity": {"type": "integer",
                              "description": "Shares already held."},
                 "avg_price": {"type": "number",
                               "description": "Cost basis per share (₹)."},
                 "entry_date": {"type": "string",
                                "description": "OPTIONAL ISO YYYY-MM-DD."},
             },
             "required": ["quantity"],
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
             "description": (
                 "The ACTUAL ₹ amount being deployed, if known from this "
                 "conversation — never leave at the default when a real "
                 "figure was stated."
             ),
         },
         "quantity": {
             "type": "integer",
             "default": 10,
         },
         "sizing_mode": {
             "type": "string",
             "enum": ["fixed", "pct_equity", "vol_target", "atr_risk"],
             "default": "fixed",
             "description": (
                 "fixed=`quantity`; pct_equity=`pct` of equity; "
                 "vol_target=annualised `target_vol`; atr_risk=`risk_pct` "
                 "per trade with stop at `atr_mult`×ATR. Use when user says "
                 "'volatility targeting', 'risk N% per trade', 'ATR-based "
                 "size', or '% of capital'."
             ),
         },
         "pct": {"type": "number", "description":
                 "pct_equity: fraction of equity per entry (0.2 = 20%)."},
         "target_vol": {"type": "number", "description":
                        "vol_target: annualised vol target (0.15 = 15%)."},
         "risk_pct": {"type": "number", "description":
                      "atr_risk: fraction of equity risked per trade (0.01 = 1%)."},
         "atr_mult": {"type": "number", "description":
                      "atr_risk: stop distance in ATRs (default 2)."},
         "direction": {
             "type": "string",
             "enum": ["long", "short"],
             "default": "long",
             "description": (
                 "This engine only SIMULATES LONG (buy-then-sell) positions. "
                 "If the user asks to short / sell-short / go short, still "
                 "pass direction='short' here (do NOT silently drop it and "
                 "run it long) — the tool will refuse honestly rather than "
                 "fabricate a short. Never claim you backtested a short when "
                 "this returns an error."
             ),
         },
     },
     ["condition", "primary_symbol", "interval"])


tool("backtest_pairs",
     "BACKTESTS a PAIRS / statistical-arbitrage (mean-reversion) strategy on TWO "
     "stocks. Use for: 'pairs trade on HDFCBANK and ICICIBANK', 'is TCS/INFY "
     "cointegrated', 'mean-reversion spread between SBIN and PNB', 'stat-arb "
     "backtest on these two'. It runs an Engle-Granger cointegration test, "
     "estimates the hedge ratio, trades the spread's z-score (enter at ±entry_z, "
     "exit on mean-reversion), and reports the OU half-life plus the full Trust "
     "verdict / rigor battery. The result LEADS with whether the legs are "
     "cointegrated — relay that honestly; a non-cointegrated pair has no "
     "statistical basis to mean-revert.",
     {
         "symbol_a": {"type": "string", "description":
                      "First leg of the pair (e.g. HDFCBANK)."},
         "symbol_b": {"type": "string", "description":
                      "Second leg of the pair (e.g. ICICIBANK)."},
         "period": {"type": "string", "description":
                    "Lookback window, e.g. '2y', '3y', '5y'. Default '2y'."},
         "lookback": {"type": "integer", "description":
                      "Rolling window (days) for the hedge ratio + z-score. Default 60."},
         "entry_z": {"type": "number", "description":
                     "Z-score to enter (default 2.0)."},
         "exit_z": {"type": "number", "description":
                    "Z-score to exit toward the mean (default 0.5)."},
     },
     ["symbol_a", "symbol_b"])

tool("scan_pairs",
     "SCANS a list of stocks for cointegrated PAIRS, ranked by cointegration "
     "strength (ADF) with the mean-reversion half-life. Use for: 'find "
     "cointegrated pairs among PSU banks', 'which of these stocks pair-trade', "
     "'scan SBIN, PNB, BANKBARODA, CANBK for pairs'. Supply the candidate "
     "tickers in `symbols` (from the user's list or a sector you name). The "
     "result is an IN-SAMPLE screen — tell the user to confirm any hit with "
     "backtest_pairs before trusting it.",
     {
         "symbols": {"type": "array", "items": {"type": "string"},
                     "description": "Candidate tickers (2-40), e.g. "
                     "['SBIN','PNB','BANKBARODA','CANBK','UNIONBANK']."},
         "period": {"type": "string", "description":
                    "Lookback window, e.g. '2y','5y'. Default '2y'."},
         "min_level": {"type": "string", "enum": ["1%", "5%", "10%"],
                       "description": "Min cointegration significance. Default '5%'."},
     },
     ["symbols"])

tool("backtest_portfolio",
     "BACKTESTS a multi-stock MOMENTUM PORTFOLIO over a universe: ranks the names "
     "by trailing momentum, holds the top N (rebalancing on a schedule), and "
     "reports return + the Trust verdict / rigor battery. THE tool whenever the "
     "user gives a LIST/basket of stocks to rank, rotate, or hold-the-top-N of — "
     "NOT backtest_workflow/backtest_dsl_tree (those are single-symbol; never "
     "collapse the basket to one ticker). Use for: 'backtest a momentum portfolio "
     "of [stocks]', 'hold the top 5 momentum names rebalanced monthly', "
     "'long/short momentum on these', 'rotate into the strongest of this basket'. "
     "'Rebalanced monthly' is the portfolio rebalance, not a SIP. Supports "
     "dollar-neutral long/short (`long_short`), a per-"
     "sector cap (`sector_cap`, e.g. 0.4), and max-names (`top_n`). Relay the "
     "verdict honestly — a high return with a weak PSR/DSR is not an edge.",
     {
         "symbols": {"type": "array", "items": {"type": "string"},
                     "description": "The universe to rank (3+), e.g. "
                     "['RELIANCE','TCS','INFY','HDFCBANK','SBIN','ITC','LT','MARUTI']."},
         "top_n": {"type": "integer", "description": "How many names to hold (default 5)."},
         "rebalance": {"type": "string", "enum": ["W", "M", "Q"],
                       "description": "Rebalance frequency (default M = monthly)."},
         "long_short": {"type": "boolean", "description":
                        "Dollar-neutral long top / short bottom (default false = long-only)."},
         "sector_cap": {"type": "number", "description":
                        "Optional max fraction of a leg per sector, e.g. 0.4."},
         "period": {"type": "string", "description":
                    "Lookback window, e.g. '5y'. Default '5y' (momentum needs history)."},
     },
     ["symbols"])

tool("test_cointegration",
     "Tests whether a BASKET of 3+ stocks is cointegrated using the Johansen "
     "trace test, and returns the cointegration RANK plus the cointegrating "
     "weights (the basket combination that is stationary / mean-reverting). Use "
     "for: 'are RELIANCE, ONGC and BPCL cointegrated', 'is there a stationary "
     "basket among these stocks', 'Johansen test on [list]'. For just TWO stocks "
     "prefer `backtest_pairs`; to find pairs inside a list prefer `scan_pairs`. "
     "Rank 0 means no tradable basket spread — relay that honestly.",
     {
         "symbols": {"type": "array", "items": {"type": "string"},
                     "description": "The basket tickers (2-6), e.g. "
                     "['RELIANCE','ONGC','IOC','BPCL']."},
         "period": {"type": "string", "description":
                    "Lookback window, e.g. '2y','5y'. Default '2y'."},
     },
     ["symbols"])


tool("propose_dsl_workflow",
     "SINGLE-SYMBOL compound-workflow builder. DSL acts on ONE primary symbol. "
     "Returns workflow_draft_card.\n\n"
     "DO NOT pick for MULTIPLE TICKERS in one order intent (needs "
     "propose_workflow with one branch per symbol × action — routing multi-"
     "symbol here silently drops all-but-one ticker), nor for an EARNINGS-print "
     "trigger (no DSL earnings leaf; use propose_workflow + trigger.earnings).\n\n"
     "A trigger on NEWS, a HEADLINE, a MACRO OUTCOME (RBI/Fed/CPI), a SEBI "
     "announcement or a PREDICTION MARKET is NOT AVAILABLE in any tool — do not "
     "route or draft it. Say so in one line and offer the nearest wired trigger: "
     "a PRICE or INDICATOR level on the instrument the event would move, or a "
     "SCHEDULE if the ask was about timing.\n\n"
     "FIRST CHOICE (over propose_workflow / propose_threshold_order) for a "
     "single-symbol agent whose entry OR exit uses any of: 2+ conditions "
     "(AND/OR/NOT); multi-output components (MACD signal/hist, BB %B/bands, "
     "Stoch %K/%D, Aroon, Donchian/Keltner); indicator-vs-indicator (50-EMA vs "
     "200-EMA, price vs Supertrend); aggregate windows (percentrank, zscore, "
     "highest/lowest, barssince, valuewhen, correlation, count_when, rolling "
     "std); volume-relative; cross-symbol/spread/ratio; session-day filter; "
     "gap/pct_change; time-shifted reference (prior close, yesterday's high); or "
     "any position-state exit (drawdown_from_peak_pct, unrealised_pct, "
     "bars_held, peak_unrealised_pct, entry_price).\n\n"
     "Pass entry as `condition` and any exit as `exit_condition` (server emits "
     "trigger.compound entry + optional trigger.exit_compound branch). Pass "
     "exit_condition WHENEVER the user names an exit. Pass both VERBATIM — do "
     "NOT paraphrase or simplify.",
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
             "description": (
                 "REQUIRED in practice — a short human title YOU author, "
                 "3-6 words, like a nickname a trader would use: 'RELIANCE "
                 "dip buyer', 'TCS momentum exit'. NEVER a condition dump "
                 "('RSI(14) of X crosses…') — the card's subtitle already "
                 "shows the exact conditions. Re-supply an updated name "
                 "when an amendment changes the symbol or the meaning."
             ),
         },
         "summary": {
             "type": "string",
             "description": (
                 "1-2 plain-English sentences YOU author on how this agent "
                 "behaves — what gets it in, what gets it out, how often it "
                 "checks. Shown as the text above the card. No jargon dumps."
             ),
         },
         "action_kind": {
             "type": "string",
             "enum": ["buy_market", "buy_limit"],
             "default": "buy_market",
             "description": (
                 "buy_market/buy_limit place an ORDER when the condition "
                 "fires. Exit branch (when exit_condition set) always "
                 "market-sells the runtime-held quantity. This tool builds "
                 "ORDER automations only — price/condition ALERTS and "
                 "notifications are NOT available, so never use this to build "
                 "a 'just alert me' / 'notify me' / 'ping me' workflow; state "
                 "that boundary in prose instead."
             ),
         },
         "quantity": {
             "type": "integer",
             "minimum": 1,
             "description": (
                 "Shares to buy (REQUIRED for buy_market / buy_limit). DO "
                 "NOT default to 1. If user didn't state a size, call "
                 "ASK_USER first — DO NOT emit this tool until they answer. "
                 "Silent quantity=1 ships wrong-size trades."
             ),
         },
         "limit_price": {
             "type": "number",
             "description": "Limit price (₹). Required for buy_limit.",
         },
         "exit_condition": {
             "type": "string",
             "description": (
                 "Optional natural-language EXIT. Adds trigger.exit_compound "
                 "branch that fires only when the workflow holds an open "
                 "position ('price > upper Bollinger band', 'RSI > 70', "
                 "'unrealised P&L below -2%', 'drawdown from peak >= 5%', "
                 "'after 10 bars held'). Pass verbatim — translation allows "
                 "PositionNode leaves."
             ),
         },
         "valid_until": {
             "type": "string",
             "description": (
                 "Optional ISO YYYY-MM-DD. Set ONLY for TTL phrases ('for "
                 "next 30 days', 'until 30 June', 'till Friday'). Resolve "
                 "relative phrases yourself. Omit for perpetual. Scheduler "
                 "auto-deactivates at 23:59 IST. Granularity is a full DAY — "
                 "do NOT set this for a sub-day window ('for the next hour', "
                 "'for 30 minutes'); rounding that up to end-of-day silently "
                 "misrepresents the duration. Omit it instead and say plainly "
                 "in your reply that the card has no built-in expiry and the "
                 "user should deactivate it manually when the window passes."
             ),
         },
         "interval": {
             "type": "string",
             "enum": [
                 "1m", "3m", "5m", "10m", "15m", "30m", "1h",
                 "1d", "1wk", "1mo",
             ],
             "default": "1d",
             "description": (
                 "Bar interval for entry/exit indicators AND for any price "
                 "leg with offset > 0 ('lower than it was N minutes ago' — "
                 "'previous bar' means nothing without this). 'period' "
                 "(RSI(14), SMA(50)) counts BARS of THIS interval. If user "
                 "did NOT pin a timeframe, ASK — do not guess. Default '1d'."
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
     "Build a workflow that places ONE order on a schedule — RECURRING "
     "(SIP-style / weekly / Monday rules, via `days`+`time_ist`) OR ONE-TIME "
     "(a single non-repeating run, via `run_at`). PREFER over propose_workflow "
     "for prompts like 'buy 5 NIFTYBEES every weekday at 09:15' or 'buy 10 "
     "RELIANCE just tomorrow at 1pm'. Server hydrates trigger.schedule + "
     "action.place_order (+ optional SL). Pass exactly ONE of `quantity` or "
     "`notional_inr`, and exactly ONE of `days` (recurring) or `run_at` "
     "(one-time). STRICTLY SINGLE-TRIGGER, NO CONDITIONS/GUARDS — if the "
     "prompt has a second trigger, conditional second leg, or any "
     "'if/unless/only when' guard, bail to propose_workflow.",
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
             "description": "RECURRING schedule: days to fire — list of "
                            "mon|tue|wed|thu|fri|weekday|daily. Use "
                            "['weekday'] for Mon-Fri. Omit when using run_at.",
         },
         "time_ist": {
             "type": "string",
             "description": "Fire time for a recurring schedule, HH:MM in IST. "
                            "Default 09:15.",
         },
         "run_at": {
             "type": "string",
             "description": (
                 "ONE-TIME run: a single ISO 8601 IST datetime "
                 "(YYYY-MM-DDTHH:MM:SS) you resolve yourself from today's "
                 "date — e.g. 'just tomorrow at 1pm', 'once at 3pm today', "
                 "'only this Friday'. The order fires exactly once, then the "
                 "agent is done. When set, OMIT `days`/`time_ist`. Use the "
                 "recurring fields instead for repeating schedules."
             ),
         },
         "sl_pct": {"type": "number", "minimum": 0.1, "maximum": 50,
                    "description": "Optional % stop-loss after the fill."},
         "requires_approval": {"type": "boolean",
                               "description": "Default false (auto-execute)."},
         "valid_until": {
             "type": "string",
             "description": (
                 "Optional ISO YYYY-MM-DD. Set when the user attaches "
                 "a TTL phrase ('for the next 30 days', 'until 30 "
                 "June', 'till next Friday'). Resolve relative phrases "
                 "to absolute dates yourself. Omit for perpetual."
             ),
         },
     },
     ["symbol", "side"])


tool("propose_threshold_order",
     "Build a workflow that places ONE PERPETUAL order when a SINGLE "
     "indicator or price threshold fires. ONLY for genuinely "
     "single-condition prompts like 'buy 10 INFY when RSI < 30' or "
     "'sell 5 RELIANCE when price crosses above 2800'. Server hydrates "
     "trigger.{indicator|price} + action.place_order (+ optional SL). "
     "Pass exactly ONE of `quantity` or `notional_inr`. ABSOLUTE "
     "thresholds only.\n\n"
     "DO NOT CALL when the prompt has AND/OR between conditions, a "
     "multi-output component (MACD signal/hist, BB upper/lower, Stoch %K), "
     "an aggregate window (percentrank, zscore, highest, barssince), "
     "a volume-relative term ('volume > 2x 20-day avg'), a spread/ratio, "
     "a session-day filter ('only on Tuesdays'), or any exit referencing "
     "position state. ALL of those route to `propose_dsl_workflow` — this "
     "macro can only carry a single comparison and will silently drop the "
     "extra legs.\n\n"
     "DO NOT CALL for runtime-relative thresholds ('5% below today's "
     "open') — use propose_workflow with fetch.relative_threshold.\n\n"
     "TTL/expiry phrases ('valid till month end', 'until Friday', "
     "'for the next N days') ARE supported here — pass the resolved "
     "absolute date as `valid_until` (YYYY-MM-DD).\n\n"
     "QUANTITY (REQUIRED): pass exactly ONE of `quantity` or "
     "`notional_inr`. DO NOT default to 1. If the user did not state a "
     "size, call ASK_USER first ('How many shares of <SYMBOL> per fire, "
     "or what rupee budget per fire?'). A silent quantity=1 has shipped "
     "wrong-size trades.",
     {
         "symbol": {"type": "string"},
         "side": {"type": "string", "enum": ["buy", "sell"]},
         "quantity": {"type": "integer", "minimum": 1, "description": "Shares per fire — REQUIRED unless notional_inr is passed. Never default to 1."},
         "notional_inr": {"type": "number", "minimum": 1, "description": "Rupee budget per fire — REQUIRED unless quantity is passed."},
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
                              "description": "Default 14 for RSI, 50 for SMA/EMA. "
                                             "Counted in BARS of `timeframe`."},
         "timeframe": {"type": "string",
                       "description": "Bar interval the indicator trigger watches "
                                      "— 1m, 5m, 15m, 30m, 1h, daily, weekly, or "
                                      "monthly (REQUIRED for trigger_kind='indicator'). "
                                      "Pass the timeframe the user named; if they "
                                      "named none, OMIT it — the platform asks. Do "
                                      "NOT guess daily."},
         "sl_pct": {"type": "number", "minimum": 0.1, "maximum": 50},
         "requires_approval": {"type": "boolean"},
         "valid_until": {
             "type": "string",
             "description": (
                 "Optional ISO YYYY-MM-DD. Set when the user attaches "
                 "a TTL phrase ('for the next 30 days', 'until 30 "
                 "June', 'till next Friday'). Resolve relative phrases "
                 "to absolute dates yourself. Omit for perpetual."
             ),
         },
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
             "type": "string",
             # Backward-compatible: the legacy values ("equal", "mcap_weighted")
             # still validate and behave exactly as before. The new schemes
             # (Workstream B, plan §3b) let the model name a real weighting
             # rule instead of collapsing to bare 1/N — `risk_parity` (ERC) is
             # the smart default, `min_variance` for capital preservation,
             # `black_litterman` to fold a chat view into the mcap prior,
             # `factor` for quality/value/momentum tilts.
             "enum": [
                 "equal", "mcap_weighted",
                 "risk_parity", "min_variance", "black_litterman", "factor",
             ],
             "default": "equal",
             "description": "INTERNAL weighting scheme — YOU (the builder) pick "
                            "it from the user's risk/view/horizon; the user is "
                            "NEVER asked to choose one. Do NOT echo these enum "
                            "names back to the user as a question (no 'equal, "
                            "mcap, risk-parity, min-variance, black-litterman or "
                            "factor?'). risk_parity is the smart default; "
                            "min_variance for capital preservation; "
                            "black_litterman to fold a stated view into the "
                            "mcap prior; factor for a quality/value/momentum "
                            "tilt; equal only for <=4 names.",
         },
         "selection_gate": {
             "type": "string",
             # Fundamentals-DB selection gate (plan §3a Step 1). Names HOW the
             # constituents were chosen so the basket is never "top mcap" alone:
             # `fscore` (Piotroski), `magic_formula` (ROC × earnings-yield),
             # `multifactor` (quality+value). `none` only for pure
             # price/technical baskets — still drops fundamentally broken names.
             "enum": ["fscore", "magic_formula", "multifactor", "none"],
             "description": "INTERNAL fundamentals gate used to pick "
                            "constituents — YOU choose it; the user is NEVER "
                            "asked to pick a gate, and these enum names are NOT "
                            "surfaced to the user as a question. Prefer a real "
                            "gate over 'none' for equity baskets.",
         },
         "sector_cap": {
             "type": "number", "minimum": 1, "maximum": 100,
             "description": "Single-sector weight ceiling (% of the basket) so "
                            "it can't collapse into one sector. ~30-35% is the "
                            "default band; omit to let the server enforce ~32%.",
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


# ── STRATEGY BUILDER + DYNAMIC CLARIFYING QUESTIONS (Workstreams A & B) ───────
#
# These two tools replace "bland-by-construction" baskets with a DB-driven
# builder and a value-of-information question engine. The LLM does NOT author
# the basket weights/constituents or the clarifying questions field-by-field —
# it passes the REQUEST CONTEXT (and, for the builder, the filled slot-state)
# and the backend engines (services/clarify_engine.py + services/
# strategy_builder.py) do the construction. See docs/plans/
# STRATEGY_BUILDER_AND_QUESTIONS_PLAN.md §2-3 and services/strategy_contracts.py
# (the single source of truth for the wire shapes / render hints).

tool("ask_user_dynamic",
     "Ask the user dynamically-generated, VOI-ranked clarifying questions "
     "BEFORE building a strategy/basket when the request is under-specified "
     "AND clarifying would materially change what gets built (high value of "
     "information). You do NOT author the questions — pass the REQUEST CONTEXT "
     "and the backend generates a ranked, MECE, grounded ≤5-question 'N of M' "
     "card (a clarify_card). Use this INSTEAD of ASK_USER for strategy/basket "
     "asks: ASK_USER is one shallow free-text question; this is the structured "
     "multi-question elicitation. Do NOT call it on reflex — if the request is "
     "already specific (named the view, risk, capital, instruments), skip it "
     "and call build_strategy directly. The backend's skip-entirely gate will "
     "also return no card when nothing is worth asking; in that case proceed "
     "to build_strategy. NEVER invent a fixed questionnaire — questions are "
     "generated per request. This is the ONLY way to clarify a strategy/basket "
     "build: do NOT ask in prose and do NOT call ASK_USER for one. The card "
     "asks about the user's GOALS (capital, horizon, risk comfort, what to "
     "include) — it NEVER asks the user to pick a weighting scheme or a "
     "selection gate; those internal enums are the builder's choice and are "
     "never surfaced.",
     {
         "request": {
             "type": "string",
             "description": "The user's strategy/basket request, verbatim or "
                            "lightly normalised. The engine infers which slots "
                            "are unknown+decision-relevant from this.",
         },
         # Optional hints the model already parsed from the request. The engine
         # treats anything present here as 'specified' (so it won't ask about
         # it) and otherwise infers from `request`. All optional — never block
         # the call on these.
         "theme": {
             "type": "string",
             "description": "Optional thematic/sector tilt the user named "
                            "('quality compounders', 'rate-cut beneficiaries', "
                            "'IT', 'defence').",
         },
         "capital_inr": {
             "type": "number", "minimum": 0,
             "description": "Optional investable capital in ₹ if the user "
                            "stated it.",
         },
     },
     ["request"])


tool("ask_agent_clarify",
     "Ask ONE-CLICK structured clarifying questions BEFORE building an "
     "AUTOMATION / AGENT when the request named an instrument but left the "
     "KIND of automation open — i.e. there is an action verb (buy/sell/SIP/"
     "alert) but NO trigger (when/every/if/at open/at close/RSI<n) and NO "
     "size (n shares/lots/₹n). Example: 'make me an agent that buys options "
     "in RELIANCE', 'build an agent for TCS'. The backend generates a short "
     "grounded clarify_card (action-kind + size) the user taps. This is the "
     "ONLY way to clarify an under-specified agent build: do NOT ask in prose "
     "and do NOT call ASK_USER for one. Do NOT call it when a trigger or size "
     "is already present (build the draft directly via propose_workflow / the "
     "macro), and do NOT call it for strategy/basket asks (use "
     "ask_user_dynamic for those). The backend's gate returns no card when "
     "the ask is specific enough to build — in that case proceed to "
     "propose_workflow.",
     {
         "request": {
             "type": "string",
             "description": "The user's automation/agent request, verbatim or "
                            "lightly normalised. The engine infers the named "
                            "symbol + whether it's an options or equity agent.",
         },
         "symbol": {
             "type": "string",
             "description": "Optional NSE ticker the agent is about "
                            "(RELIANCE, TCS). The engine also extracts it from "
                            "`request`; pass it when obvious.",
         },
     },
     ["request"])


tool("build_strategy",
     "Builds a DB-driven EQUITY + GOLD basket (weighting scheme + fundamentals "
     "gate + sector cap + correlation check + optional gold sleeve). Returns a "
     "register-not-execute strategy_builder_card (user places orders in own "
     "broker app; options/hedge NOT built this phase).\n\n"
     "PREFER over propose_basket_allocation for portfolio asks wanting "
     "thoughtful structure ('build me a long-term portfolio', 'balanced basket "
     "of quality stocks', 'invest ₹2L for the long run'). Pass filled "
     "SLOT-STATE; skipped slots take defaults — never block the build. For an "
     "UNDER-SPECIFIED ask (no view/risk/horizon/capital) call ask_user_dynamic "
     "FIRST.\n\n"
     "Weighting-scheme (equal/mcap/risk-parity/min-variance/black-litterman/"
     "factor) and selection-gate (fscore/magic-formula/multifactor) names are "
     "INTERNAL levers YOU pick — never ask the user to choose one or echo these "
     "enum names in a question.\n\n"
     "PASS THE USER'S CONSTRAINTS — `filters` (ROE/ROCE/D-E/PE/earnings-yield/"
     "payout/market-cap thresholds), `mcap_band`, `weight_by`, `gold_pct` and "
     "`asset_prefs.exclusions` all reach the engine. A constraint outside that "
     "set (dividend yield, promoter pledge, ESG…) is NOT screenable — say so "
     "rather than pretending it applied.\n\n"
     "SELF-SUFFICIENT — do NOT pre-call screen_fundamentals / fetch_fundamentals "
     "/ compare_performance / compute. Returns, per leg: sector, `gate_metrics` "
     "(ROE, ROCE, D/E, P/E, earnings yield), `weight_pct`, `allocation_inr` (the "
     "₹ slice — never recompute weight × capital), `weight_reason`. Plus "
     "card-level `rejected` (names excluded + why), `constraints_not_applied` "
     "(you MUST disclose these in your reply), `assumptions`, `sleeves`, "
     "`alternatives`. Call it FIRST and quote it.\n\n"
     "A pinned symbol not in the NSE universe comes back in `rejected` as 'not "
     "found in Pivot's NSE universe' — do NOT present that leg as if it exists; "
     "re-call with the correct ticker or say the name has no listed expression. "
     "A name that resolves but has no fundamentals is kept and shows "
     "'(no data)' — report it as a data gap, not a bad ticker.",
     {
         "request": {
             "type": "string",
             "description": "The user's request, verbatim — drives universe "
                            "construction and the rationale.",
         },
         # The slot-state (services/strategy_contracts.SlotState). Every field
         # is optional with a sensible default so an under-specified call still
         # builds (the card surfaces '(assumed …)' for any defaulted slot).
         "view": {
             "type": "object",
             "description": "The user's market view.",
             "properties": {
                 "direction": {"type": "string",
                               "enum": ["bull", "bear", "neutral", "none"]},
                 "target": {"type": "string",
                            "enum": ["stock", "sector", "index", "market"]},
                 "conviction": {"type": "string",
                                "enum": ["low", "medium", "high"]},
             },
         },
         "filters": {
             "type": "array",
             "description": (
                 "The user's HARD fundamental constraints — pass EVERY one "
                 "they stated ('ROE above 15, debt-to-equity under 1' → "
                 "[{field:'roe',op:'>',value:15},{field:'de',op:'<',value:1}]). "
                 "These EXCLUDE names (the internal gate only ranks), and a "
                 "name that fails comes back in `rejected` with the reason. "
                 "Fields outside the enum aren't screenable — don't invent one; "
                 "state the gap in your reply instead."
             ),
             "items": {
                 "type": "object",
                 "properties": {
                     "field": {"type": "string",
                               "enum": ["roe", "roce", "de", "pe",
                                        "earnings_yield", "payout",
                                        "market_cap_cr"]},
                     "op": {"type": "string", "enum": [">", ">=", "<", "<="]},
                     "value": {"type": "number"},
                 },
                 "required": ["field", "op", "value"],
             },
         },
         "max_names": {
             "type": "integer",
             "description": (
                 "How many constituents the user asked for ('exactly 5 "
                 "stocks', 'a 4-stock basket'). Overrides the engine's own "
                 "size band — pass it whenever a count is stated, and never "
                 "clarify a count the user already gave."
             ),
         },
         "mcap_band": {
             "type": "string",
             "enum": ["large", "mid", "small"],
             "description": (
                 "Restrict discovery to a size band when the user names one "
                 "('midcap manufacturing basket', 'quality smallcaps'). "
                 "large ≥ ₹20,000 cr · mid ₹5,000-20,000 cr · small < ₹5,000 cr. "
                 "Omit when no size was asked for."
             ),
         },
         "weight_by": {
             "type": "string",
             "enum": ["roe", "roce", "de", "pe", "earnings_yield", "payout",
                      "market_cap_cr"],
             "description": (
                 "Weight the legs IN PROPORTION to this metric when the user "
                 "says so ('4 private banks weighted by ROE' → 'roe'). This is "
                 "the answer to 'weighted by <metric>' — never clarify it and "
                 "never fall back to asking. Lower-is-better metrics (de, pe) "
                 "are inverted automatically. Omit to let the engine choose."
             ),
         },
         "gold_pct": {
             "type": "number",
             "description": (
                 "Gold sleeve as a % of the WHOLE portfolio when the user "
                 "states a split ('70% equity 30% gold' → 30). Required for any "
                 "stated split — without it the engine's own heuristic caps "
                 "gold at ~15% and your split will NOT be honoured. Omit when "
                 "the user didn't name a gold share."
             ),
         },
         "risk": {
             "type": "string",
             "enum": ["conservative", "balanced", "aggressive"],
             "description": "User's risk appetite. Drives internal weighting-"
                            "scheme choice and gold ballast %. Do NOT ask the "
                            "user to pick a weighting scheme or a gate, and do "
                            "NOT surface those internal enum names in a question.",
         },
         "horizon": {
             "type": "string",
             "enum": ["tactical", "medium", "long"],
             "description": "tactical <1y · medium 1-5y · long 5y+.",
         },
         "capital_inr": {
             "type": "number", "minimum": 0,
             "description": "Investable capital in ₹. Gates #names + sizing; "
                            "omit to size in percentages.",
         },
         "asset_prefs": {
             "type": "object",
             "description": "Which asset classes the user will / won't hold "
                            "plus exclusions.",
             "properties": {
                 "allow": {"type": "array", "items": {
                     "type": "string",
                     "enum": ["equity", "etf_mf", "options", "gold"]}},
                 "deny": {"type": "array", "items": {
                     "type": "string",
                     "enum": ["equity", "etf_mf", "options", "gold"]}},
                 "exclusions": {"type": "array", "items": {"type": "string"},
                                "description": "Sectors, 'PSU', 'ESG' themes, "
                                               "or named tickers to carve out."},
             },
         },
         "theme": {
             "type": "string",
             "description": (
                 "Optional sector/tilt for DISCOVERY — used ONLY when you are "
                 "not pinning `symbols`. Sectors with a real curated universe "
                 "behind them: banking (spans private+psu), private bank, psu "
                 "bank, it, auto, pharma, fmcg, energy, metals (includes "
                 "steel), steel, cement, defence, telecom — plus tilts like "
                 "'quality compounders' / 'momentum'. THAT IS THE WHOLE LIST. "
                 "Anything else — chemicals, infra, realty, media, consumer "
                 "durables, a macro view, 'PLI electronics', 'demographic "
                 "dividend' — has NO universe behind it: the engine silently "
                 "falls back to a broad cross-sector pool and notes it in "
                 "`assumptions`, so the basket is NOT what the user asked for "
                 "even though the card looks fine. For anything off that list, "
                 "name the companies yourself and pin `symbols` + "
                 "`symbol_reasons` — a pinned basket is checked against the "
                 "full ~4,600-name NSE universe, a theme string is not."
             ),
         },
         "symbols": {
             "type": "array",
             "items": {"type": "string"},
             "description": "Explicit NSE constituents YOU chose. When present "
                            "the builder PINS the universe to exactly these "
                            "names — no discovery runs and the sector cap turns "
                            "advisory. A name with no fundamentals is KEPT and "
                            "shown as '(no data)'; a name that doesn't resolve "
                            "to a listed NSE symbol is NOT allocated and comes "
                            "back in `rejected`. Weighting + sizing still "
                            "computed. This is the THEMATIC path: the builder "
                            "has no thematic knowledge of its own, so reason out "
                            "who actually benefits and pin them here rather than "
                            "passing a `theme` string and hoping. THIS IS THE "
                            "DEFAULT PATH — for almost every basket (including a "
                            "plain 'create a basket') YOU choose the names and "
                            "pin them, so the basket reflects the user's ask and "
                            "your analysis, not a fixed engine selection. OMIT "
                            "`symbols` ONLY when the user gave hard screening "
                            "constraints (ROE>20, D/E<0.5) — then the engine's "
                            "deterministic screen is what they asked for.",
         },
         "rationale": {
             "type": "string",
             "description": (
                 "The card's defence, authored by YOU — 3-5 sentences tying "
                 "the thesis to THIS structure in the user's own terms: why "
                 "these names, why these weights, what would confirm or "
                 "invalidate it, and what it is NOT. Omit and the engine falls "
                 "back to a generic template that cannot know your thesis — "
                 "supply it for any view-driven or thematic build."
             ),
         },
         "symbol_reasons": {
             "type": "object",
             "description": "Per-symbol one-line WHY, authored by YOU — the "
                            "causal hook tying each pinned name to the thesis "
                            "({\"ONGC\": \"upstream producer; realisations rise "
                            "with crude\"}). Shown on the card as each leg's "
                            "reason. Supply alongside `symbols` for any "
                            "thematic/view-driven build; without it legs fall "
                            "back to generic quality-metric text.",
         },
         "weight_overrides": {
             "type": "object",
             "description": "Explicit per-symbol weights for a RE-WEIGHT / "
                            "REBUILD amendment. When the user asks to 'rebuild "
                            "it heavier in X', 'make KSB 40%', 'tilt to the "
                            "leaders', 'equal-weight it', re-call build_strategy "
                            "with the SAME symbols plus this map "
                            "({\"KSB\": 40, \"SHAKTIPUMP\": 30}) — percents or "
                            "fractions; named symbols take their share and the "
                            "rest split the remainder by conviction. This is how "
                            "a rebuild ACTUALLY re-allocates. Omit for a first "
                            "build (backend picks conviction/quality weights). "
                            "For a bare 'rebuild' with NO stated change, do NOT "
                            "silently reproduce — briefly EXPLAIN the existing "
                            "weights and offer concrete tilts.",
             "additionalProperties": {"type": "number"},
         },
     },
     ["request"])


tool("propose_holding_action",
     "Build a workflow that acts on the user's EXISTING holding — sells "
     "ENTIRELY or sets a stop-loss (fixed or TRAILING). PREFER over "
     "propose_workflow for prompts like 'sell my INFY when RSI > 70', "
     "'set 2% SL on my RELIANCE', 'trail my stoploss 8% below the running "
     "high'. Two action shapes ('sell' entire holding / 'set_stoploss'). "
     "Four trigger shapes (indicator|price|schedule|manual). STRICTLY "
     "ENTIRE HOLDING — fractional sells ('sell half my INFY') go to "
     "propose_workflow. Avg-relative triggers ('+X% from buy price') also "
     "go to propose_workflow (no slot here).\n\n"
     "TRAILING STOP: when the user says 'trailing stop', 'trail N%', "
     "'N% below the running high', 'trail from peak' — set `trailing=true` "
     "with `sl_offset_pct=N`. The engine will track the high-water mark "
     "and trigger at N% below peak, NOT below entry.",
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
                            "price (fixed SL) or % below running high "
                            "(trailing SL when trailing=true). XOR with "
                            "sl_trigger_price.",
         },
         "sl_trigger_price": {
             "type": "number",
             "description": "For action_kind='set_stoploss'. Absolute price. "
                            "XOR with sl_offset_pct. Cannot be used with "
                            "trailing=true.",
         },
         "trailing": {
             "type": "boolean",
             "description": "For action_kind='set_stoploss'. When true, the "
                            "stop tracks the high-water mark and triggers "
                            "at sl_offset_pct below peak. Use for 'trailing "
                            "stop', 'trail N%', 'N% below running high'.",
         },
         "requires_approval": {"type": "boolean"},
         "valid_until": {
             "type": "string",
             "description": (
                 "Optional ISO YYYY-MM-DD. Set when the user attaches "
                 "a TTL phrase ('for the next 30 days', 'until 30 "
                 "June', 'till next Friday'). Omit for perpetual."
             ),
         },
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


tool(
    "compose_multistep",
    "ORCHESTRATOR for COMPOUND user intents that chain analysis → "
    "decision → action. Use ONLY when the prompt has 2+ sequential "
    "verbs whose later steps depend on EARLIER step results — e.g. "
    "'compare A, B, C, find the one with lowest drawdown, build an "
    "agent on that one' or 'backtest strategy X vs Y, tell me which "
    "won, set up the winner'. Pass a `plan` array where each step "
    "names a tool + args; use `$step_id.field` to reference a prior "
    "step's output deterministically. The server executes each step "
    "in order and resolves refs between them (no LLM hop for the "
    "threading). Maximum 6 steps per plan.\n\n"
    "DO NOT call for single-verb intents — those go to the appropriate "
    "single tool (place_order, propose_workflow, "
    "compare_performance, etc.) directly. The orchestrator adds "
    "latency; it only earns its keep on genuine multi-step chains.\n\n"
    "Inline helpers usable as `tool` inside the plan:\n"
    "  • `extract_winner_symbol(from, metric, direction)` — pick the "
    "best/worst symbol from a comparison result.\n"
    "  • `compare_backtests(strategies, period)` — run 2-4 strategy "
    "backtests in parallel and rank by total_return / Sharpe / max_dd.\n"
    "Real tools (propose_threshold_order, propose_workflow, "
    "compare_performance, etc.) also usable as plan steps.\n\n"
    "Pass `user_intent` verbatim so the chat layer can summarise "
    "the plan execution at the end.",
    {
        "plan": {
            "type": "array",
            "minItems": 2,
            "maxItems": 6,
            "description":
                "Ordered list of {step_id, tool, args} dicts. step_id "
                "must be unique within the plan. Args may contain "
                "`$step_id` or `$step_id.field.path` refs to prior "
                "step outputs.",
            "items": {
                "type": "object",
                "properties": {
                    "step_id": {"type": "string"},
                    "tool":    {"type": "string"},
                    "args":    {"type": "object"},
                },
                "required": ["step_id", "tool"],
            },
        },
        "user_intent": {
            "type": "string",
            "description":
                "The user's original message verbatim. The orchestrator "
                "uses it for the final summary; do NOT paraphrase.",
        },
    },
    ["plan", "user_intent"],
)

tool(
    "extract_winner_symbol",
    "Deterministic helper used INSIDE a compose_multistep plan: given "
    "a prior step's result (typically from compare_performance / "
    "compare_backtests / get_performance_metrics), return the symbol "
    "with the best (or worst) value of a named metric. No LLM hop.\n\n"
    "Use direction='max' for Sharpe, total_return, win_rate. "
    "Use direction='min' for max_drawdown (as a negative number, smaller "
    "magnitude = better — use 'max' for the LEAST DRAWDOWN — i.e. the "
    "drawdown CLOSEST TO ZERO is the winner) or volatility. Read the "
    "metric's directional sense and pick accordingly.",
    {
        "from": {
            "type": "object",
            "description":
                "Pass `$step_id` referencing a prior step that produced "
                "per-symbol metric rows (a `ranked` list OR a flat dict "
                "`{SYM: {metric: value, ...}, ...}`). The server "
                "resolves the $ref before calling this helper."
        },
        "metric": {
            "type": "string",
            "description":
                "Metric name to compare on (must match a key in the "
                "prior step's rows). Examples: 'max_drawdown', "
                "'sharpe_ratio', 'total_return_pct', 'volatility', "
                "'cagr'."
        },
        "direction": {
            "type": "string",
            "enum": ["min", "max"],
            "default": "max",
            "description":
                "'max' picks the highest, 'min' the lowest. For "
                "max_drawdown (negative numbers), 'max' picks the "
                "smallest drawdown — i.e. the BEST performer."
        },
    },
    ["from", "metric"],
)

tool(
    "compare_backtests",
    "Run 2-4 strategy specs through the same workflow backtester in "
    "PARALLEL and return a side-by-side comparison. Each strategy is "
    "a {name, steps[]} dict — the same `steps` shape "
    "`propose_workflow` / `backtest_workflow` accept. Returns rankings "
    "by total_return_pct / sharpe / max_drawdown.\n\n"
    "Use inside compose_multistep when the prompt is 'backtest X vs Y "
    "and tell me which won' / 'compare these 3 styles on INFY'.\n\n"
    "DO NOT call directly for a single strategy — use backtest_workflow "
    "instead.",
    {
        "strategies": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description":
                            "List of workflow step dicts — same shape "
                            "as propose_workflow's `steps[]`.",
                    },
                },
                "required": ["name", "steps"],
            },
        },
        "period": {
            "type": "string",
            "enum": ["1y", "2y", "3y", "5y"],
            "default": "2y",
        },
        "benchmark_symbol": {
            "type": "string",
            "description":
                "Optional override for the buy-and-hold benchmark. Defaults "
                "to each strategy's own primary trade symbol.",
        },
    },
    ["strategies"],
)


tool(
    "regime_compare_metrics",
    "Pre/post-PIVOT-DATE regime comparison. Splits a symbol's price "
    "history at the named date and returns risk + return metrics for "
    "each window separately, plus a delta block + a one-line "
    "interpretation. Use when the user asks 'compare X before and "
    "after <date>' / 'how did X behave pre- vs post-2022' / "
    "'X before Covid vs after'. The pivot is the LAST DAY of the "
    "before-window (inclusive); the after-window starts the next "
    "bar.\n\n"
    "Useful for the regime-aware step of a compose_multistep plan: "
    "split → identify which regime fits the user's intent → build "
    "strategy on top.\n\n"
    "Period: use '5y' or 'max' for far-back pivots; the data layer "
    "may downsample longer windows to weekly / monthly bars — the "
    "metrics still work but with coarser resolution.",
    {
        "symbol": {"type": "string"},
        "pivot_date": {
            "type": "string",
            "description":
                "ISO YYYY-MM-DD or a 4-digit year (e.g. '2022' → "
                "2022-01-01) marking the LAST DAY of the before-window."
        },
        "period": {
            "type": "string",
            "enum": ["1y", "2y", "5y", "max"],
            "default": "5y",
        },
    },
    ["symbol", "pivot_date"],
)


tool(
    "web_search_brief",
    "ENTITY-GROUNDING web lookup. Returns 1-3 short snippets (title + "
    "1-2 lines + URL) from DuckDuckGo Instant Answer with a Wikipedia "
    "fallback. Use when the user asks about an INSTITUTION / CONCEPT / "
    "INSTRUMENT that Pivot's local data doesn't cover — e.g. \"what is "
    "the Reserve Bank of India\", \"explain GIFT Nifty\", \"what's an "
    "arbitrage fund\", \"how does a capital-guaranteed note work\".\n\n"
    "DO NOT call for live prices (use get_live_price), current "
    "indicators (use get_indicator), portfolio data (use "
    "get_portfolio_summary), or fundamentals on Indian listed equity "
    "(use fetch.fundamental / propose_workflow). DO NOT call for "
    "REAL-TIME NEWS — Pivot does not have a live news feed wired "
    "through this tool. If a user asks for recent earnings / today's "
    "macro print, say so plainly.\n\n"
    "Cite the source: include the URL in your reply text so the user "
    "can verify. Keep the summary tight (1-3 sentences) — this is "
    "context for the chat layer, not a research dump.",
    {
        "query": {
            "type": "string",
            "description":
                "Short entity name or concept. Examples: "
                "'Reserve Bank of India repo rate', 'NIFTY 50 index', "
                "'arbitrage fund India', 'gold ETF India'. Avoid "
                "questions ('what is X') — phrase as a topic.",
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "default": 2,
        },
    },
    ["query"],
)


# ── Track C: chat-side workflow registration + armed-state introspection ──

tool(
    "register_workflow",
    "ARMS a workflow: persists the draft and flips it ACTIVE — the same "
    "thing the card's 'Save & activate' button does, but from chat. Call "
    "when the user says 'register it', 'activate it', 'arm it', 'make it "
    "live', 'go ahead and set it up' about a workflow draft on screen. "
    "Pass the draft's name/description/steps verbatim (do NOT invent "
    "steps), OR a workflow_id to activate an existing saved workflow. "
    "On a trigger fire the workflow REGISTERS an order the user confirms "
    "in their broker app — Pivot never auto-executes (register-not-execute).",
    {
        "name":        {"type": "string", "description": "Workflow name from the draft."},
        "description": {"type": "string", "description": "Draft description."},
        "steps":       {"type": "array", "items": {"type": "object"},
                        "description": "The draft's steps[] verbatim "
                                       "({step_type, config, label})."},
        "workflow_id": {"type": "string", "description":
                        "Existing workflow id to activate instead of "
                        "creating from a draft."},
        "expires_at":  {"type": "string", "description":
                        "Optional ISO expiry timestamp from the draft."},
    },
    [],
)

tool(
    "get_workflow_status",
    "Grounded armed-state readback for a workflow: is it actually live, "
    "what the trigger is, the REAL evaluation cadence (price/indicator "
    "triggers are polled ~every 60s during NSE market hours; cron "
    "schedules fire at their cron time), the current indicator value "
    "when computable, and what happens on a fire (an order is REGISTERED "
    "for user confirmation — never auto-executed). Call when the user "
    "asks 'is it live?', 'is it actually running?', 'when do you check?', "
    "'how often does it evaluate?', 'what's the status of my agent?'. "
    "workflow_id optional — defaults to the most recently activated "
    "workflow.",
    {
        "workflow_id": {"type": "string", "description":
                        "Workflow id. Omit for the most recent one."},
    },
    [],
)


# ── Track C: option roll / adjustment ─────────────────────────────────

tool(
    "roll_option_position",
    "ROLL an existing option leg to a new strike and/or later expiry — "
    "the standard adjustment when a short option goes against you "
    "('roll the 24000 call to next expiry', 'shift the strike up 200', "
    "'becha tha, loss me hai, next expiry me roll karo'). Prices BOTH "
    "sides off the live chain: the close of the existing leg (BUY-to-"
    "close a short) and the open of the new leg on the target expiry, "
    "then returns a 2-leg strategy card with the roll's net "
    "credit/debit plus the NEW position's max loss, breakeven and POP. "
    "Defaults: to_expiry='next'; new strike = nearest liquid strike "
    "just OTM of ATM when unspecified. Registers an intent only — the "
    "user confirms the actual close+open in their broker app.",
    {
        "underlying":   {"type": "string", "description":
                         "Underlying, e.g. NIFTY, BANKNIFTY, RELIANCE."},
        "strike":       {"type": "number", "description":
                         "Strike of the EXISTING leg being rolled."},
        "option_type":  {"type": "string", "enum": ["CE", "PE"]},
        "side":         {"type": "string", "enum": ["BUY", "SELL"],
                         "description": "Side of the EXISTING leg. A "
                         "sold/written option = SELL (default)."},
        "from_expiry":  {"type": "string", "description":
                         "Existing leg's expiry (YYYY-MM-DD). Omit for "
                         "the nearest expiry."},
        "to_expiry":    {"type": "string", "description":
                         "'next' (default), 'monthly', or YYYY-MM-DD."},
        "new_strike":   {"type": "number", "description":
                         "Absolute new strike. Omit to default."},
        "strike_offset": {"type": "integer", "description":
                          "Move N strikes further OTM from the old "
                          "strike ('up 2' on a call → +2 strikes)."},
        "qty_lots":     {"type": "integer", "minimum": 1, "default": 1},
    },
    ["underlying", "strike", "option_type"],
    defaults={"side": "SELL", "to_expiry": "next", "qty_lots": 1},
)
