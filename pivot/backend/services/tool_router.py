"""Per-hop tool router + route-stable cache keys.

Why: the chat hop sees all 48 tools on every LLM call. That schema is
~4,900 input tokens, repeated on every hop, every turn. For a turn that
needs `get_live_price`, the model doesn't need to read the SIP, GTT,
backtest, or scheduler tool definitions to make a good decision.

This module narrows the visible tool list to a small candidate set per
hop using cheap keyword heuristics. No LLM, no embeddings, microseconds.

Design rules:
  1. Always include `propose_workflow` and `ASK_USER` (synthetic) — the
     model needs the agent-builder and the clarification escape hatch
     available regardless of what it routes to.
  2. If no rule matches, return None — caller falls back to the full
     tool list. We never ship a turn without enough tool surface.
  3. The router returns NAMES; the caller filters the actual `ToolDef`
     list. Keeps router unit-testable without LLM imports.
  4. Conservative on misses, aggressive on hits: when a rule matches,
     include the entire tool family (e.g. all SIP tools, not just
     `create_sip`). The cost of including 4 extra SIP tools is ~150
     tokens; the cost of missing the right one is a wrong tool call.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional


# Tools that are ALWAYS in scope. The agent-builder, the four macro
# variants, and the clarification tool need to be available regardless
# of what the user typed — they're the escape hatches the model relies
# on. Including the macros up-front means a "buy 5 NIFTYBEES every
# weekday" prompt sees them even if the keyword router didn't classify
# the message as agent-y.
#
# `propose_workflow` is included now that its LLM-facing schema was
# collapsed from a 41-branch oneOf discriminated union into a flat
# `{step_type: enum, config: object}` shape (see
# `backend/agents/tools.py::_build_propose_workflow_schema`). Full tool
# object dropped from ~39,955 B (~9,988 tok) to ~7,362 B (~1,840 tok),
# so the cost of unconditional inclusion is ~1.8k tokens/turn — small
# enough to justify removing the route-misclassification risk where a
# multi-step prompt missed every keyword rule. Server-side Pydantic
# models in `workflows/schemas.py` still validate each step's config,
# so the trim does not weaken safety. The keyword rules below still
# mention `propose_workflow` for clarity; the redundancy is harmless.
_ALWAYS_INCLUDE: frozenset[str] = frozenset({
    "propose_workflow",
    "propose_scheduled_order",
    "propose_threshold_order",
    "propose_basket_allocation",
    "propose_holding_action",
    # `find_tool` is the lazy-load escape hatch when no keyword rule
    # surfaces the right tool. The schema itself is tiny (one string +
    # one int), so the cost of unconditional inclusion is negligible
    # vs. the failure mode of the model not knowing the escape exists.
    "find_tool",
    "ASK_USER",  # synthetic; added by the chat service, not the registry
})


# Each rule: (compiled regex over the lowercased message, set of tools
# to include if it matches). Order doesn't matter — all matching rules
# union into the final set.
_Rule = tuple[re.Pattern[str], frozenset[str]]


def _r(pattern: str, *tools: str) -> _Rule:
    return re.compile(pattern, re.IGNORECASE), frozenset(tools)


_RULES: list[_Rule] = [
    # ── Agent / strategy / workflow building (also covered by
    # _ALWAYS_INCLUDE, but explicit so the LLM understands intent).
    _r(
        r"\b(build|create|set up|setup|make)\s+(?:me\s+)?an?\s+(agent|strategy|automation|rule|workflow)"
        r"|\bevery\s+(monday|tuesday|wednesday|thursday|friday|weekday|day|week|morning|evening)"
        r"|\bevery\s+\d"
        r"|\bif\s+(rsi|sma|ema|price|the\s+price)"
        r"|\bwhen\s+(rsi|sma|ema|price|the\s+price)",
        "propose_workflow",
    ),

    # ── Order-card quantity/price amendments ──────────────────────
    # "make it 5", "no 3 shares", "actually 10", "change to ₹1950",
    # "just 7 lots" — short edits the user types after seeing a logiccard.
    # WHY this rule exists: these short phrases don't hit any keyword rule
    # (no "buy", no "sell", no ticker), so the router returned only
    # _FALLBACK_TOOLS (data-read tools). The LLM called get_live_price to
    # estimate value but couldn't re-emit the order card — the user saw prose
    # instead of an updated LogicCard with a Confirm button. Adding these
    # amendment patterns brings the full order tool set back into scope.
    _r(
        r"\b(?:no|make\s+it|change\s+(?:it\s+)?to|actually|instead)\s*[₹]?\d+\s*(?:shares?|units?|lots?)?\b"
        r"|\bjust\s+\d+\s+(?:shares?|units?|lots?)\b"
        r"|\b\d+\s+(?:shares?|units?|lots?)\s+(?:instead|only|please)\b",
        "place_market_order", "place_limit_order", "create_gtt_order",
        "create_sl_order", "create_oco_order", "create_sip",
        "squareoff_all_intraday", "squareoff_symbol",
        "get_live_price",
    ),

    # ── Live price / quote / OHLC ──────────────────────────────────
    _r(
        r"\b(price|quote|snapshot|ltp|last\s+traded|how\s+(much|is)\s+\w+\s+trading"
        r"|what(?:'s| is)\s+\w+\s+(at|trading)|level\s+of\s+(nifty|sensex|banknifty)"
        r"|nifty|sensex|banknifty)\b"
        r"|^\s*[A-Z]{2,12}\s*\??\s*$",
        "get_live_price", "get_index_level", "get_ohlc", "get_market_status",
    ),

    # ── Analytics: indicators / risk / comparison / correlation ──
    # WHY this rule exists: prompts that mention an indicator name
    # (RSI, MACD, ADX, Bollinger, Supertrend, ATR), a risk metric
    # (Sharpe, Sortino, drawdown, volatility, VaR, beta), a return
    # query ("how has X done", "X return", "YTD"), or a multi-stock
    # comparison ("rank these by Sharpe", "correlated") need the
    # /core/ analytics bridge tools. Without this rule those tools
    # weren't surfaced and the model would either hallucinate the
    # values or fall back to get_live_price.
    _r(
        # Indicator names (any of these → bring all indicator tools)
        r"\bRSI\b|\bMACD\b|\bADX\b|\bSMA\b|\bEMA\b|\bWMA\b"
        r"|\bBollinger\b|\bSupertrend\b|\bATR\b|\bKeltner\b|\bDonchian\b"
        r"|\bOBV\b|\bVWAP\b|\bCCI\b|\bMFI\b|\bStochastic\b|\bWilliams\s*%R\b"
        r"|\bAroon\b|\bIchimoku\b|\bTRIX\b|\bROC\b|\bChaikin\b"
        r"|\bvolume[- ]?weighted\b|\bmoving\s+average\b"
        # Risk / performance metrics
        r"|\bSharpe\b|\bSortino\b|\bCalmar\b|\btreynor\b|\bbeta\b"
        r"|\bvolatility\b|\bdrawdown\b|\bmax[- ]?dd\b|\bVaR\b|\bCVaR\b"
        r"|\binformation\s+ratio\b|\bomega\s+ratio\b|\balpha\b"
        r"|\brisk[- ]?adjusted\b|\bdownside\s+deviation\b"
        # Casual risk / overbought-oversold phrasings.
        # WHY: "how risky is X", "is X overbought rn" used to route to
        # get_live_price / get_price_history, missing the analytics
        # tools entirely. The strategic suite caught both.
        r"|\b(?:how\s+)?risk(?:y|ier|iest)\b"
        r"|\bover[- ]?bought\b|\bover[- ]?sold\b"
        r"|\b(?:is|am|are)\s+\w+\s+(?:risky|safe|volatile|stable)\b"
        # Return queries
        r"|\bytd\b|\b(?:total|cumulative|annualised|annualized)\s+return\b"
        r"|\bhow\s+has\s+\w+\s+(?:done|performed)\b"
        # Multi-stock comparison / correlation
        r"|\b(?:rank|compare|correlat\w*|covariance|diversif\w*)\b"
        r"|\bmost\s+(?:correlated|uncorrelated)\b",
        "get_indicator", "get_multiple_indicators",
        "get_performance_metrics", "compare_performance",
        "get_correlation_matrix", "get_returns",
        "get_live_price", "get_price_history",
    ),

    # ── Top gainers / losers ──────────────────────────────────────
    # WHY this rule exists: prompts like "today's top gainers" / "who's
    # moving most" need `get_top_movers` (read-only chat tool) and
    # `propose_workflow` (when the prompt is "buy the top gainer at
    # close..."). The order rule didn't match either case because the
    # phrasing has no buy/sell verb, so the tool was unreachable.
    _r(
        r"\btop\s+(?:gainers?|losers?|movers?)\b"
        r"|\bbiggest\s+(?:gainers?|losers?|movers?)\b"
        r"|\bday'?s?\s+(?:top|biggest)\s+(?:gainers?|losers?|movers?)\b"
        r"|\bgainer\s+of\s+the\s+day\b|\bloser\s+of\s+the\s+day\b"
        r"|\bwho'?s?\s+(?:moving|gaining|losing)\s+most\b",
        "get_top_movers",
        "propose_workflow",
        "place_market_order", "place_limit_order",  # for "buy top gainer..."
    ),

    # ── 52-week range, price history, charts ───────────────────────
    _r(
        r"\b(52\s*-?\s*week|52w|all[- ]time\s+(high|low)|chart|history|past\s+\d+\s*(year|month|week))"
        r"|\bhow\s+has\s+\w+\s+done"
        r"|\bshow\s+me\s+(?:the\s+)?(?:chart|history|past)",
        "get_price_history", "get_52wk_range",
    ),

    # ── Portfolio / holdings / sector ──────────────────────────────
    # WHY calculate_tax_impact is here: it was missing from every rule,
    # so "what's the tax hit if I sell my RELIANCE" had no path to the
    # right tool — the LLM grabbed `calculate_order_qty` (visible via the
    # order rule) and produced a wrong number. Tax-impact questions
    # virtually always co-occur with portfolio/holding language, so this
    # is the natural rule to host it.
    _r(
        r"\b(portfolio|holdings|my\s+(stocks|positions|investments)"
        r"|sector\s+breakdown|allocation|p&?l|profit|loss"
        r"|tax\s+(summary|impact|loss|hit)|stcg|ltcg)\b",
        "get_portfolio_summary", "get_holdings", "get_sector_breakdown",
        "get_holding_detail", "get_tax_summary", "get_active_products",
        "calculate_tax_impact",
    ),

    # ── Order placement (immediate / limit / GTT) ─────────────────
    _r(
        r"\b(buy|sell|order|place|short|exit|squareoff|square\s+off|cancel"
        r"|stop\s*-?\s*loss|stoploss|gtt|target|limit|market)\b",
        "place_market_order", "place_limit_order", "create_gtt_order",
        "create_sl_order", "create_oco_order", "create_dip_buy",
        "place_basket_order", "cancel_order", "cancel_gtt",
        "list_pending_orders", "list_gtt_orders",
        "squareoff_all_intraday", "squareoff_symbol",
        "calculate_order_qty", "calculate_sl_price",
        "calculate_dip_price", "calculate_margin",
        "get_live_price",  # almost always needed alongside an order
        # Order keywords frequently combine with conditions / schedules
        # ("buy NIFTYBEES at open and sell at close"). Include
        # propose_workflow so the model can build the multi-step shape.
        "propose_workflow",
    ),

    # ── SIP (recurring investment) ────────────────────────────────
    _r(
        r"\bs\.?i\.?p\.?s?\b|recurring\s+(invest|buy)|monthly\s+invest",
        "create_sip", "list_sips", "pause_sip", "resume_sip",
        "delete_sip", "pause_all_sips",
    ),

    # ── Pause / resume / delete management commands ───────────────
    # WHY this rule exists: a turn like "pause all of them" after the
    # user has just listed their SIPs has no SIP keyword in it — the
    # router sees only "pause all" and surfaces neither SIP nor
    # strategy management tools. The model then picks
    # `propose_scheduled_order` (a macro from _ALWAYS_INCLUDE) and
    # produces a wrong card. Including the management verbs without
    # requiring a domain keyword brings both the SIP and strategy
    # pause/resume/delete tools into scope so pronoun resolution can
    # land on the right one.
    _r(
        r"\b(pause|resume|delete|cancel|stop|kill)\s+(all|every|each|both|them|those|my)\b"
        r"|\b(pause|resume|delete)\s+(it|that)\b",
        "pause_sip", "resume_sip", "delete_sip", "pause_all_sips",
        "list_sips",
        "pause_strategy", "resume_strategy", "delete_strategy",
        "list_strategies",
    ),

    # ── Strategy automation (single-rule) ──────────────────────────
    _r(
        r"\b(strategy|strategies|automation|rule|monitor|watch\s+for|trigger)\b",
        "create_strategy", "list_strategies", "pause_strategy",
        "resume_strategy", "delete_strategy",
    ),

    # ── Backtest ──────────────────────────────────────────────────
    # ONLY backtest_workflow is exposed for chat backtest intents.
    # run_backtest is the legacy single-indicator tool whose required
    # `trigger_condition` field made the LLM ask "what's the trigger
    # condition?" for every compound query. It's still in ALL_TOOLS so
    # programmatic callers (test scripts, REST integration tests) can
    # use it, but the chat router never surfaces it.
    _r(
        r"\bback\s*test(?:ed|ing)?\b|\bsimulate\b|\bif\s+i\s+had\s+(bought|invested)"
        r"|\bhow\s+(?:would|did)\b.{0,40}\b(?:perform(?:ed)?|do(?:ne)?|fare(?:d)?)\b",
        "backtest_workflow",
    ),

    # ── Sector basket / multi-stock allocation ────────────────────
    # WHY this rule exists: prompts like "make me a basket of steel
    # stocks with equal weightage and 1L to invest" don't trigger any
    # of the order/strategy/SIP rules, so the only macro tools the
    # model sees are the floor in _ALWAYS_INCLUDE. The model then has
    # propose_basket_allocation in scope but no salience cue — and
    # often picks propose_workflow instead. Surfacing a basket-shaped
    # rule with the right tool family puts the macro front-and-center
    # and pulls in the supporting screener/order tools.
    _r(
        r"\bbasket\s+of\b"
        r"|\b(?:invest|allocate|put|deploy|split)\b.{0,40}\b(?:across|equally|weighted)\b"
        r"|\btop\s+\d+\s+(?:[a-z_]+\s+)?stocks?\b"
        r"|\bequal\s+weight(?:age)?\b|\bmcap[- ]weighted\b|\bmarket[- ]cap\s+weighted\b"
        r"|\bsector\s+(?:basket|allocation)\b",
        "propose_basket_allocation",
        "place_basket_order",
        "get_live_price",
    ),

    # ── Yields / cash parking ─────────────────────────────────────
    # WHY "fixed[- ]income" / "bond" / "sgb" / "recommend.*invest" added:
    # "recommend the best fixed-income option for 2 years" matched none
    # of the prior patterns — yield tools weren't surfaced and the model
    # answered with prose that had no data. Bond and SGB are first-class
    # retail-investor terms that share the recommendation surface.
    _r(
        r"\byield(?:s|ed)?\b|fixed\s+deposit|fixed[- ]income|\bfd\b|liquid\s+fund"
        r"|overnight\s+fund|park\s+(my|the)?\s*(cash|money|idle)"
        r"|savings\s+account|after[- ]tax|\bsgb\b|sovereign\s+gold"
        r"|government\s+bond|\bg-?sec\b|treasury\s+bill|\bt-?bill\b"
        r"|recommend\s+(?:the\s+)?(?:best\s+)?(?:fixed|bond|debt|safe)",
        "compare_yields", "get_yield_recommendation",
    ),

    # ── Scheduler status ──────────────────────────────────────────
    # WHY "scheduled" / "what.*scheduled" / "what.*queued" added: the
    # earlier rule only matched the literal word "scheduler" or
    # "upcoming", missing the much more natural "what jobs are
    # scheduled for today" and "what's queued up". Without these
    # patterns the bot answered in prose with no data; the user
    # couldn't see active automations.
    _r(
        r"\b(scheduler|next\s+(run|sip|job)|upcoming\s+(job|sip|task))\b"
        r"|\bis\s+automation\s+(running|on)"
        r"|\b(?:what|which)\s+(?:jobs?|tasks?|automations?|agents?|runs?)\s+"
        r"(?:are\s+)?(?:scheduled|queued|coming\s+up|upcoming|pending)\b"
        r"|\bscheduled\s+(?:for\s+)?(?:today|tomorrow|this\s+week)\b",
        "get_scheduler_status", "list_upcoming_jobs",
    ),

    # ── Pending / open order listings ─────────────────────────────
    # WHY this rule exists: the order rule includes `list_pending_orders`
    # but only matches when an order verb (buy/sell/cancel/etc) is
    # present. A bare "show me my pending orders" had no verb match,
    # so the listing tools were absent and the bot returned empty
    # tools_called. This rule surfaces the read-only listing tools
    # for any "pending" / "open" / "active" order query.
    _r(
        r"\b(?:show|list|view|get|see|check|what(?:'s| is)|any)\b"
        r".{0,30}\b(?:pending|open|active|outstanding|live|placed|unfilled)\b"
        r".{0,15}\b(?:orders?|trades?|gtts?|positions?)\b"
        r"|\b(?:my|the)\s+(?:pending|open|active|outstanding)\s+orders?\b"
        r"|\bpending\s+orders?\b|\bopen\s+orders?\b",
        "list_pending_orders", "list_gtt_orders",
        "cancel_order", "cancel_gtt", "modify_order",
    ),

    # ── Pivot products (only when user explicitly names one) ──────
    _r(
        r"\b(safegrow|earnmore|stormshield|pivot\s+product)\b",
        "get_product_spec", "get_active_products",
    ),

    # ── Market status / hours / holidays ──────────────────────────
    _r(
        r"\b(market\s+(open|status|hours|holiday|close)|nse\s+(open|status))\b"
        r"|\bis\s+(?:the\s+)?market\s+open",
        "get_market_status",
    ),
]


# Hard floor: if a turn matches no rules, fall back to the full set so
# we never starve the model. This used to be `None`, but several
# evaluation prompts exercise unusual phrasings ("show me a snapshot of
# my world") that don't tickle any keyword — those still need at least
# the read-side of the catalog.
_FALLBACK_TOOLS: frozenset[str] = frozenset({
    "get_live_price", "get_portfolio_summary", "get_holdings",
    "get_market_status", "get_price_history",
    "backtest_workflow",
})


def select_tool_names(message: str) -> Optional[set[str]]:
    """Return the set of tool names the chat hop should see for this
    message, or None to signal "fall back to the full registry".

    Today we always return a set: matching rules union with the
    always-include floor; if nothing matches we still return the
    `_FALLBACK_TOOLS` floor + always-include. The Optional return is
    kept for the future case where we'd rather show the full 48-tool
    catalog (e.g. for explicit slash commands or admin debug paths).

    `ASK_USER` is in `_ALWAYS_INCLUDE` but is a synthetic tool — the
    chat service appends its ToolDef separately from the registry.
    Filtering by name here is harmless because the registry lookup
    will skip the synthetic one anyway.
    """
    if not message:
        return set(_ALWAYS_INCLUDE | _FALLBACK_TOOLS)

    msg = message.lower()
    selected: set[str] = set(_ALWAYS_INCLUDE)
    for pattern, tools in _RULES:
        if pattern.search(msg):
            selected.update(tools)

    # If only the always-include floor matched, blend in the fallback
    # read-side tools so a vague turn like "what's happening" still has
    # quote / portfolio / market-status to call.
    if selected <= _ALWAYS_INCLUDE:
        selected |= _FALLBACK_TOOLS

    return selected


def filter_registry_tools(
    all_tools: list[dict],
    selected: Optional[set[str]],
) -> list[dict]:
    """Filter the registry's tool-schema list down to `selected` names.

    `all_tools` is the OpenAI-shaped list returned by
    `tool_registry.get_tool_schema()`. Falls through to the full list
    when `selected` is None.
    """
    if selected is None:
        return all_tools
    out: list[dict] = []
    for defn in all_tools:
        fn = defn.get("function") or {}
        if fn.get("name") in selected:
            out.append(defn)
    return out


# ── Route-stable cache key ─────────────────────────────────────────


_CACHE_KEY_PREFIX = "pivot-chat-v2"


def cache_key_for(selected: Optional[set[str]]) -> str:
    """Build a deterministic prompt-cache key for this routed toolset.

    Why a per-route key matters: OpenAI's prompt cache is keyed by the
    *prefix bytes* of the request, scored against `prompt_cache_key`
    as a routing hint. When the visible toolset varies turn-to-turn
    (because the router narrows it based on user keywords), the
    system + tools prefix bytes differ, so a single global key
    misses on the first turn of every route.

    The fix: hash the sorted tool name list into a short tag and
    suffix the cache key with it. Each route signature now caches
    its own prefix; cache hits become turn-1 instead of turn-2.

    Returns a string like ``"pivot-chat-v2-fb1c83"``. The hash space
    is ample: 24 bits = 16M routes, vs ~50 plausible toolsets.

    Note on find_tool / lazy-load: callers MUST pass the *final* tool
    name set (router selection ∪ loaded_extras) on each hop. If
    `find_tool` surfaces e.g. `get_indicator` on hop N and we lazy-load
    it for hop N+1, the prompt cache key must differ from a hop that
    never called find_tool — otherwise the prefix bytes change but the
    OpenAI cache routing collapses two distinct surfaces into one slot.
    """
    if not selected:
        return f"{_CACHE_KEY_PREFIX}-all"
    # ASK_USER is synthetic and added downstream — exclude it from
    # the signature so its presence/absence doesn't shift the key.
    canonical = ",".join(sorted(n for n in selected if n != "ASK_USER"))
    sig = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    return f"{_CACHE_KEY_PREFIX}-{sig}"
