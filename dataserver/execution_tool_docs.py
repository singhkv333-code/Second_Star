"""Charto's own descriptions for the Pivot tools execution mode borrows.

Pivot's tool definitions are written for Pivot's chat: they route to macros,
option builders and a clarify tool that are not on this wire, and they say
"the user places orders in their own broker app". A parameter doc sits closer
to the call than any system prompt, so a sentence that routes to a tool the
model cannot call is followed anyway and then fails. Correcting the wire text
here, once, replaces the string-equality patches `execution_bridge._retarget`
had to make and makes the wire smaller.

Only DESCRIPTION text lives here. Parameter names, types, enums and
``required`` come from Pivot's schema unchanged (a test asserts every
overridden parameter exists there), so the two cannot drift apart silently.

Nothing below names a tool that is absent from ``execution_bridge.PIVOT_TOOLS``
or the Charto tools in ``_EXECUTION_CHARTO_TOOLS``, invents a default, or
claims a broker. The runtime is a simulated paper book; a draft or plan is
register-not-execute.

``apply(defn)`` returns a copy of a Pivot function definition with these
descriptions in place. Two placeholders are filled from Pivot's own text so the
generated step catalog is never retyped here:

  {catalog}     ``propose_workflow``'s CATALOG block, minus option and IPO steps
                (neither is on this surface)
  {step_notes}  its STEP NOTES block, minus the lines about those steps
"""
from __future__ import annotations

import re
from typing import Optional

# Step types this surface cannot fulfil. Dropped from the catalog Pivot
# generates, by prefix, so a step added over there still appears here.
_OFF_SURFACE_STEPS = (
    "action.place_option_strategy", "action.arm_ipo_intent",
    "trigger.ipo_open", "trigger.expiry_day",
)

OVERRIDES: dict[str, dict] = {
    "propose_dsl_workflow": {
        "description": (
            "Builds a single-instrument rule: an entry condition, optionally an exit, "
            "and a simulated market or limit buy. Returns a workflow_draft_card.\n\n"
            "Use it whenever the rule watches one instrument, however complex the "
            "condition: several conditions (AND/OR/NOT), indicator against indicator, "
            "multi-output indicators (MACD signal/histogram, Bollinger bands and %B, "
            "Stochastic, Aroon, Donchian, Keltner), aggregate windows (highest/lowest, "
            "percentile rank, z-score, bars since, correlation), volume-relative or "
            "cross-symbol conditions, day-of-week filters, gaps and percent changes, "
            "prior-bar references, and position-aware exits (unrealised P&L, bars "
            "held, peak gain, drawdown from peak, entry price).\n\n"
            "Pass the entry as `condition` and the exit as `exit_condition`, both in "
            "the user's own words. A downside rule (stop, trailing stop, time stop) "
            "belongs in `exit_condition`. Several instruments in one order intent go "
            "through `propose_workflow` (one branch per instrument) or, for a "
            "selection to own, `register_plan`. An earnings trigger goes through "
            "`propose_workflow` with `trigger.earnings`. A news, headline or macro "
            "outcome trigger cannot be built: say so in one line and offer a price or "
            "indicator level on the instrument the event would move.\n\n"
            "The draft is a proposal; nothing fills until the user arms it. It "
            "places simulated orders in the paper book only. To be told when a level "
            "is reached, use `set_alert` instead."
        ),
        "params": {
            "condition": "The entry condition in natural language, in the user's words. It is translated into a condition tree for you.",
            "primary_symbol": "The instrument the rule buys. The condition may reference other instruments as filters.",
            "name": "A short title you write, 3-6 words, the way a trader would name it ('RELIANCE dip buyer'). Not a restatement of the conditions; the card shows those. Send a new one when an amendment changes the instrument or the idea.",
            "summary": "One or two plain sentences you write on how the rule behaves: what gets it in, what gets it out, how often it checks.",
            "action_kind": "buy_market or buy_limit. The exit branch, when there is one, sells the whole held quantity at market. This tool builds order automations only.",
            "quantity": "Shares per entry for buy_market / buy_limit. Choose a size that fits the idea when the user gave none, and state it in your reply so they can change it.",
            "limit_price": "Limit price in rupees; needed for buy_limit.",
            "exit_condition": "The exit rule in natural language, in the user's words: an indicator level, unrealised P&L, drawdown from peak, bars held, or several joined. It runs only while a position is open.",
            "valid_until": "ISO date (YYYY-MM-DD) for a phrase like 'for the next 30 days'; resolve relative phrases yourself. Omit for no expiry. Granularity is a full day, so omit it for a shorter window and say the card has no built-in expiry.",
            "interval": "The bar size the rule is evaluated on. Indicator periods count bars of THIS interval (RSI(14) on 15m is fourteen 15-minute bars). Use the chart's interval when the user named none, and say which you used.",
        },
    },
    "propose_workflow": {
        "description": (
            "Builds a multi-step workflow from typed steps; returns a workflow_draft_card. "
            "Use it when the rule is not a single-instrument condition: several "
            "instruments with their own branches, an earnings trigger, or "
            "runtime-relative levels ('5% below today's open'). A single-instrument "
            "rule, however many conditions it has, goes through `propose_dsl_workflow`, "
            "and a selection to own goes through `register_plan`.\n\n"
            "Not for backtests (`backtest_dsl_tree`, `backtest_workflow`), not for a "
            "conceptual 'how would I automate X', and not for alerts (`set_alert`).\n\n"
            "Step 0 must be a trigger.*; a further trigger.* starts a new branch. "
            "Later steps read earlier ones with `{{ context.<idx>.<field> }}`. Indian "
            "stocks are NSE, amounts are INR, times are Asia/Kolkata.\n\n"
            "A recurring clock (a SIP, 'every Monday') can be drafted and backtested "
            "but cannot be armed here, because the paper runtime evaluates "
            "conditions, not schedules.\n\n"
            "CATALOG (step_type [category] required keys):\n{catalog}\n\n"
            "STEP NOTES:\n{step_notes}\n\n"
            "Keep to what the user asked for: no stop, trim or notify step they did not "
            "mention. A single trigger.indicator / trigger.price carries one comparison, "
            "so a multi-condition rule belongs in `propose_dsl_workflow`. Give every "
            "buy a size (`quantity` or `notional_inr`), chosen to fit the idea and "
            "stated in your reply."
        ),
        "params": {
            "name": "Short workflow title.",
            "description": "One sentence in the user's words.",
            "steps": "Ordered steps. Each config carries the required keys listed for its step_type in the catalog; extra keys are allowed.",
            "rationale": "One or two sentences mapping the steps to the request.",
            "valid_until": "ISO date (YYYY-MM-DD) for a phrase like 'valid till month end'. Omit for no expiry.",
        },
    },
    "backtest_dsl_tree": {
        "description": (
            "Runs a rule against history and returns the results card with a "
            "buy-and-hold comparison over the same window (return and max "
            "drawdown). Use it for any single-instrument rule with more than one "
            "condition, an indicator crossing another, multi-output indicators, "
            "lookback windows, day-of-week filters or prior-bar references. "
            "Simple one-indicator tests can use `backtest_workflow`.\n\n"
            "`condition` is the entry rule only. Put the sell rule in `exit_condition`; "
            "never AND an entry with an exit. Pass both in the user's words. No exit "
            "is assumed: give the user's, or choose the one that fits the idea and "
            "say you chose it, or use exit_kind='hold_to_end'.\n\n"
            "By default the rule is fully invested while in a position, so the "
            "return does not depend on the capital and a rupee figure is capital x "
            "return. It simulates long positions only; for a short request pass "
            "direction='short' and report the refusal. A holding the user already "
            "owns is seeded with `initial_position`; omit `condition` to test only "
            "the exit on it.\n\n"
            "Run it when the user asks to test the rule, or presses Backtest on a "
            "draft."
        ),
        "params": {
            "condition": "The entry rule in natural language, in the user's words. Omit only with initial_position, to test an exit on an existing holding.",
            "primary_symbol": "The instrument traded. The condition may reference other instruments as filters.",
            "start_date": "ISO date. Defaults to five years before end_date.",
            "end_date": "ISO date. Defaults to today.",
            "exit_condition": "The exit rule in natural language ('sell when RSI > 70', 'exit on an 8% drawdown from peak', 'close after 30 bars'). Overrides exit_kind.",
            "interval": "Bar size. Indicator periods count bars of THIS interval. Use the user's timeframe, or the one the idea is about, and say which. Intraday history is shallow (1m about 7 days, 5-30m about 60, 1h about 730) and the window is clamped with a note.",
            "exit_kind": "A declarative exit when there is no exit_condition: n_day_hold (exit_bars bars), stop_loss_pct (exit_pct), or hold_to_end.",
            "exit_bars": "Bars to hold, for exit_kind=n_day_hold.",
            "exit_pct": "Stop distance as a fraction (0.05 = 5%), for exit_kind=stop_loss_pct.",
            "starting_capital": "Rupees the result is expressed in. Pass the user's figure when they gave one; the return percentage does not depend on it.",
            "sizing_mode": "full (default): fully invested while in a position. Use another mode only when the strategy itself sizes positions: pct_equity (`pct`), vol_target (`target_vol`), atr_risk (`risk_pct`, `atr_mult`), or fixed (`quantity`).",
            "direction": "Pass 'short' when the user asks to short. The tool refuses, because it simulates long positions only; report that, and do not present a long test as a short one.",
        },
    },
    "backtest_workflow": {
        "description": (
            "Simulates a step-based workflow on historical bars and returns the "
            "results card with a buy-and-hold benchmark. Use it for a single-indicator "
            "test, a scheduled or recurring buy (SIP, one-time dated buy), or a "
            "basket, all of which the step schema below expresses. Multi-condition "
            "rules and indicator crossings go to `backtest_dsl_tree`.\n\n"
            "Steps use the same schema as `propose_workflow`. Also available in "
            "backtests: action.set_takeprofit, fetch.rolling_high / fetch.rolling_low "
            "(lookback x multiplier; 0.9 is '10% below the 20-day high'), "
            "condition.position, market_status and time_window, and "
            "action.set_stoploss with trailing:true and trigger_offset_pct.\n\n"
            "Indicators: rsi, sma, ema, wma, macd (histogram), adx, supertrend, bb "
            "(%B), stoch (%K), stoch_rsi, cci, mfi, williams_r, atr, keltner, "
            "donchian, aroon, psar, roc, trix, obv, vwap.\n\n"
            "A buy-and-hold is sized to deploy the capital (`notional_inr`, or no "
            "quantity), not to a small share count. The result reports which "
            "capital its percentages are on (`metrics.capital_basis`); quote it."
        ),
        "params": {
            "name": "Short name for the results card.",
            "period": "Lookback window. Choose a shorter one for recently listed instruments.",
            "start_date": "ISO date; clips the run to a fixed window after the period is fetched.",
            "end_date": "ISO date; end of the window.",
            "benchmark_symbol": "Buy-and-hold benchmark; defaults to the traded instrument. For baskets and pairs use a broad index ETF such as NIFTYBEES.",
            "interval": "Bar size. Intraday history is shallow and the window is clamped with a note.",
            "starting_capital": "The rupee amount being deployed, when a figure is known: a stated basket size, or an amount from earlier in the conversation. Whole-share rounding makes the return depend on capital size.",
        },
    },
    "build_strategy": {
        "description": (
            "Builds an equity basket, with an optional gold sleeve, from a screened "
            "or pinned universe: a weighting scheme, a fundamentals gate, a sector "
            "cap and a correlation check. Returns the construction: per leg the "
            "sector, gate metrics (ROE, ROCE, D/E, P/E, earnings yield), "
            "`weight_pct`, `allocation_inr` (the rupee slice; never recompute weight x "
            "capital) and a reason; plus `rejected` (names excluded and why), "
            "`constraints_not_applied` (disclose these), `assumptions`, `sleeves` "
            "and `alternatives`.\n\n"
            "Use it for portfolio asks that want structure ('a balanced basket of "
            "quality stocks', 'invest 2 lakh for the long run'). Fill the slots you "
            "can infer and let the rest take their defaults; the assumptions come "
            "back in the result. The weighting scheme and gate are your choice; do "
            "not ask the user to pick one.\n\n"
            "It builds its own universe, so it does not need a screener run first. "
            "Pass every constraint the user stated (`filters`, `mcap_band`, "
            "`weight_by`, `gold_pct`, exclusions in `asset_prefs`); a constraint "
            "outside that set (dividend yield, promoter pledge, ESG) cannot be "
            "applied, so say so. A pinned symbol that is not a listed NSE name "
            "comes back in `rejected`; do not present it as a leg. A name with no "
            "fundamentals is kept and shown as '(no data)': a data gap, not a bad "
            "ticker.\n\n"
            "This is a research step. Register the result with `register_plan`, "
            "which is the card the user activates into the simulated paper book."
        ),
        "params": {
            "request": "The user's request in their own words.",
            "filters": "The user's hard fundamental constraints, every one they stated ('ROE above 15, debt-to-equity under 1' -> [{field:'roe',op:'>',value:15},{field:'de',op:'<',value:1}]). They exclude names; a name that fails comes back in `rejected`. Fields outside the enum cannot be screened; say so.",
            "theme": "Optional sector or tilt for discovery, used only when `symbols` is not pinned. It works for sectors with a curated universe (banking, private and PSU banks, IT, auto, pharma, FMCG, energy, metals and steel, cement, defence, telecom) and tilts such as 'quality compounders' or 'momentum'. For anything else, name the companies yourself and pin `symbols`.",
            "symbols": "Explicit NSE constituents you chose. The universe is pinned to exactly these and the sector cap becomes advisory. A name that does not resolve to a listed NSE symbol is not allocated and comes back in `rejected`. For a themed or view-driven basket, reason out who benefits and pin them; omit `symbols` when the user gave hard screening constraints and the deterministic screen is what they asked for.",
            "rationale": "The card's defence, written by you: 3-5 sentences tying the thesis to this structure in the user's terms: why these names and weights, what would confirm or invalidate it, and what it is not.",
            "symbol_reasons": "One line per pinned symbol, written by you, on why it is in the basket ({\"ONGC\": \"upstream producer; realisations rise with crude\"}). Shown as each leg's reason.",
            "weight_overrides": "Explicit per-symbol weights for a re-weight amendment ('make KSB 40%'): re-call with the same `symbols` plus this map ({\"KSB\": 40, \"SHAKTIPUMP\": 30}); named symbols take their share and the rest split the remainder. Omit for a first build. For a bare 'rebuild' with no stated change, explain the current weights and offer concrete tilts.",
        },
    },
    "backtest_pairs": {
        "description": (
            "Backtests a two-instrument mean-reversion spread. Leads with whether the "
            "pair is cointegrated, then the hedge ratio, z-score entries and exits, "
            "the results and a trust verdict. A pair that is not cointegrated has "
            "no statistical basis to revert, so a positive return there is not "
            "an edge."
        ),
        "params": {
            "symbol_a": "First leg (e.g. HDFCBANK).",
            "symbol_b": "Second leg (e.g. ICICIBANK).",
            "period": "Lookback window such as '2y', '3y', '5y'.",
            "lookback": "Rolling window in days for the hedge ratio and z-score.",
            "entry_z": "Z-score at which to enter.",
            "exit_z": "Z-score at which to exit, toward the mean.",
        },
    },
    "scan_pairs": {
        "description": (
            "Screens a list of instruments for cointegrated pairs and ranks them. "
            "Use it for 'which of these pair-trade'; use `backtest_pairs` once a "
            "pair is chosen."
        ),
        "params": {
            "symbols": "Candidate tickers (2-40).",
            "period": "Lookback window such as '2y' or '5y'.",
            "min_level": "Minimum cointegration significance: '1%', '5%' or '10%'.",
        },
    },
    "test_cointegration": {
        "description": (
            "Tests a basket of 3 to 6 instruments for cointegration (Johansen) and "
            "returns the cointegration rank and stationary basket weights. A "
            "basket with no stationary combination has no basis to mean-revert."
        ),
        "params": {
            "symbols": "The basket tickers (2-6).",
            "period": "Lookback window such as '2y' or '5y'.",
        },
    },
    "backtest_portfolio": {
        "description": (
            "Backtests a momentum portfolio over a universe: rank, hold the top "
            "names, rebalance. Use it for 'hold the strongest N of these, "
            "rebalanced monthly'. It returns the results and a trust verdict; a "
            "large return with a weak verdict is not an edge. Single-instrument "
            "rules go to `backtest_dsl_tree`."
        ),
        "params": {
            "symbols": "The universe to rank (3 or more).",
            "top_n": "How many names to hold.",
            "rebalance": "Rebalance frequency: W, M or Q.",
            "long_short": "Long the top and short the bottom (dollar-neutral). The simulated book is long-only, so this is a research view, not something that can be armed.",
            "sector_cap": "Maximum fraction of a leg per sector, e.g. 0.4.",
            "period": "Lookback window; momentum needs history.",
        },
    },
}


def _block(text: str, start: str, end: Optional[str]) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = text.find(end, i) if end else -1
    return text[i: j if j >= 0 else len(text)].strip("\n")


def _drop_off_surface(block: str) -> str:
    keep = []
    for line in block.splitlines():
        s = line.strip()
        if any(s.startswith(p) or f" {p}" in s for p in _OFF_SURFACE_STEPS):
            continue
        # Notes about steps this surface does not have (options, IPOs).
        if re.match(r"-\s*(trigger\.ipo|action\.arm_ipo|action\.place_option)", s):
            continue
        keep.append(line)
    return "\n".join(keep)


def apply(defn: dict) -> dict:
    """A copy of a Pivot function definition with Charto's descriptions in it.

    A definition with no override is returned unchanged. Never mutates the
    input: Pivot's chat reads the same registry object in-process.
    """
    name = defn.get("name", "")
    ov = OVERRIDES.get(name)
    if not ov:
        return defn
    fn = dict(defn)
    desc = ov["description"]
    original = defn.get("description") or ""
    if "{catalog}" in desc:
        catalog = _drop_off_surface(_block(original, "CATALOG (", "STEP NOTES"))
        # The first line of that block is the tail of the heading Pivot writes.
        catalog = catalog.split("\n", 1)[1] if "\n" in catalog else catalog
        notes = _drop_off_surface(_block(original, "STEP NOTES", "HARD RULES"))
        notes = notes.split("\n", 1)[1] if "\n" in notes else notes
        # Pivot's own not-available bullet routes to tools by name; ours is above.
        notes = "\n".join(l for l in notes.splitlines()
                          if not l.lstrip().startswith("- NOT AVAILABLE"))
        desc = desc.replace("{catalog}", catalog.strip("\n")).replace(
            "{step_notes}", notes.strip("\n"))
    fn["description"] = desc
    params = fn.get("parameters")
    if isinstance(params, dict) and isinstance(params.get("properties"), dict):
        props = dict(params["properties"])
        for key, text in (ov.get("params") or {}).items():
            spec = props.get(key)
            if isinstance(spec, dict):
                props[key] = {**spec, "description": text}
        fn["parameters"] = {**params, "properties": props}
    return fn
