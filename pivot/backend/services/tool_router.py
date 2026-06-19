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
    "propose_dsl_workflow",
    "propose_scheduled_order",
    "propose_threshold_order",
    "propose_basket_allocation",
    "propose_holding_action",
    # Workstream B: the DB-driven equity+gold builder. Always in scope (like
    # propose_basket_allocation) so a thoughtful-portfolio ask reaches the
    # builder even when the basket regex doesn't fire — the model then chooses
    # a named weighting scheme + a fundamentals gate instead of a bare 1/N
    # macro. `ask_user_dynamic` is DELIBERATELY *not* here: it is gated behind
    # the basket/strategy intent rule so it can never fire on non-strategy
    # turns (avoids the over-asking failure mode; plan §2c "don't ask on
    # reflex"). The builder's own skip-entirely gate handles confident asks.
    "build_strategy",
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
    # ── Cross-sectional fundamental screener (2026-05-29) ────────────
    # "pharma stocks under PE 25", "stocks with ROE > 18", "low debt
    # high roe names", "cheap banking stocks". The MANY-company screen;
    # the single-company PE/ROE ask routes to fetch_fundamentals below.
    _r(
        r"\b(?:screen|screener|filter|find|show\s+me|list|give\s+me)\b[^.]{0,80}?"
        r"\b(?:stocks?|companies|names|shares)\b"
        r"|\b(?:stocks?|companies|names|shares)\b[^.]{0,40}?"
        r"\b(?:roe|roce|p\/?e|pe\s+ratio|debt[\s/-]?to[\s/-]?equity|d\/e|payout|dividend)\b"
        r"|\b(?:roe|roce|p\/?e|pe|debt[\s/-]?to[\s/-]?equity|payout)\b\s*"
        r"(?:>|<|>=|<=|=|under|over|above|below|greater|less|more)\s*\d"
        r"|\b(?:low\s+debt|high\s+roe|high\s+roce|cheap|undervalued)\b[^.]{0,30}\b(?:stocks?|companies|names)\b"
        # vague/quality screens: "best dividend paying stocks", "high
        # dividend yield names", "best value stocks", "cheap banks".
        r"|\b(?:dividend|high[\s-]?yield|best\s+dividend)\b[^.]{0,30}\b(?:stocks?|companies|names|payers?|paying)\b"
        r"|\bbest\s+(?:dividend|value|quality)\b[^.]{0,20}\b(?:stocks?|companies|names|payers?)\b"
        r"|\bcheap\s+(?:bank|banking|it|pharma|auto|metal|energy|fmcg)\b",
        "screen_fundamentals",
    ),

    # ── Sector outlook / sector-health analysis (P3, 2026-05-29) ─────
    # "outlook for the IT sector", "how is the pharma sector doing",
    # "view on the auto sector". These are think-AND-ground asks: surface
    # the cross-sectional screener + comparison + news so the LLM answers
    # with data, not evergreen prose. Requires the literal token 'sector'
    # adjacent to an analysis verb/noun, so 'nifty level?' / 'sector
    # breakdown' (portfolio) / 'cheap banking stocks' (screener) do NOT match.
    _r(
        r"\b(?:outlook|prospects?|view|thesis|how(?:'?s| is| are)|state)\b"
        r"[^.]{0,40}?"
        r"\b(?:it|tech|pharma|bank(?:ing|s)?|auto|metal|metals|energy|fmcg|infra|"
        r"realty|real\s+estate|chemical|chemicals|finance|psu|defen[cs]e|telecom|cement)\b"
        r"\s*sector\b"
        r"|\bsector\b[^.]{0,20}\b(?:outlook|prospects?|view|thesis|doing|health)\b"
        r"|\b(?:outlook|prospects?|view|thesis)\b[^.]{0,30}\bsector\b",
        "screen_fundamentals", "compare_performance", "get_symbol_news",
        "get_top_movers", "get_live_price",
    ),

    # ── Single-stock fundamentals + buy-decision reasoning ───────────
    # "should I buy reliance", "what's TCS's PE/ROE", "is X a good buy".
    _r(
        r"\bshould\s+i\s+(?:buy|invest\s+in|sell)\b"
        r"|\bis\s+\w+\s+a\s+(?:good\s+)?(?:buy|investment)\b"
        r"|\bgood\s+time\s+to\s+(?:buy|invest)\b"
        r"|\b(?:fundamentals?|valuation)\s+(?:of|for|on)\b"
        r"|\b(?:pe|p\/e|roe|roce|net\s+margin|book\s+value|eps|debt[\s-]to[\s-]equity)\s+(?:of|for|on)\s+\w+"
        # "reliance PE", "TCS's ROE", "infy pe and roe" — ticker BEFORE
        # the metric (surfaces fetch_fundamentals on hop 1, no wasted find_tool).
        r"|\b\w+'?s?\s+(?:pe|p\/?e|roe|roce|eps|book\s+value|margins?|d\/e)\b"
        r"|\b(?:pe|p\/?e|roe|roce)\b\s*(?:and|&|,)\s*(?:pe|p\/?e|roe|roce)\b"
        r"|\bhow\s+(?:financially\s+)?(?:strong|healthy|sound)\s+is\b"
        # ── Single-stock DIVIDEND intent (A8) ───────────────────────
        # "is ITC a (solid) dividend play", "<NAME> dividend yield",
        # "<NAME>'s dividend", "what's the yield on RELIANCE",
        # "how's <NAME>'s payout". These are EQUITY fundamentals asks,
        # NOT cash-park yield asks — surface fetch_fundamentals so the
        # answer carries the stock's real yield/payout/DPS. The
        # cash-park rule below is gated to NOT fire when these match.
        r"|\bis\s+\w+\s+(?:still\s+)?a\s+(?:solid\s+|good\s+|reliable\s+)?dividend\s+(?:play|stock|name)\b"
        r"|\b\w+'?s?\s+(?:dividend|payout|yield)\b"
        r"|\b(?:dividend|payout)\s+(?:of|for|on)\s+\w+"
        r"|\byield\s+(?:of|for|on)\s+\w+"
        r"|\bdividend\s+(?:play|story|history|track\s+record)\b",
        "fetch_fundamentals", "get_live_price", "get_symbol_news",
        "get_price_history",
    ),

    # ── Company-specific news ────────────────────────────────────────
    _r(
        r"\b(?:recent|latest|any)\s+news\s+(?:on|about|for)?\s*\w+"
        r"|\bnews\s+(?:on|about|for)\s+\w+"
        r"|\bwhat'?s?\s+happening\s+(?:with|to)\s+\w+"
        r"|\bwhy\s+(?:is|did)\s+\w+\s+(?:up|down|fall|drop|rise|jump|crash)",
        "get_symbol_news", "get_top_movers", "get_live_price",
    ),

    # ── IPO open-day reminder / automation (P2) ──────────────────────
    # "set up reminders for the X IPO", "automate the X IPO",
    # "remind me when X IPO opens", "open-day reminder for X IPO".
    # IMPORTANT: keep BEFORE the generic IPO route so this matches first
    # for automation phrasings; the generic route still surfaces it as a
    # fallback via the tool list below.
    _r(
        r"\b(?:set\s+up|setup)\s+(?:open[\s-]day\s+)?reminders?\b.*\bipo\b"
        r"|\bautomate\b.*\bipo\b"
        r"|\bremind\s+me\b.*\bipo\b"
        r"|\bopen[\s-]day\s+reminders?\b"
        r"|\bipo\b.*\b(?:reminders?|automation|automate)\b",
        "propose_ipo_automation", "get_ipo_details", "list_upcoming_ipos",
    ),

    # ── IPO post-listing tracking (P4) ───────────────────────────────
    # "how did TIKONA list", "TIKONA listing gain", "X listing price",
    # "did X list well/up/down", "listing day pop for X", "IPO listing
    # gain for X". Routes to get_ipo_listing (past-issues + live price).
    # IMPORTANT: keep BEFORE the generic IPO route so listing-outcome
    # phrasings prefer get_ipo_listing; the generic route still surfaces
    # it as a fallback via the IPO tool list.
    _r(
        r"\bhow\s+did\s+\w[\w\s.&'-]*\s+list\b"
        r"|\blisting\s+(?:gain|gains|price|day|pop|performance)\b"
        r"|\bipo\b[^.]{0,40}\blisting\b"
        r"|\blisting\b[^.]{0,30}\bipo\b"
        r"|\bdid\s+\w[\w\s.&'-]*\s+list\s+(?:well|up|down|good|bad|poor|strong|weak)\b"
        r"|\b\w[\w.&'-]*\s+listing\s+gain\b",
        "get_ipo_listing", "get_ipo_details", "list_upcoming_ipos",
    ),

    # ── IPOs (upcoming / open mainboard + SME) ───────────────────────
    # "any IPOs open?", "upcoming IPOs", "tell me about the X IPO",
    # "I want to apply for the <name> IPO".
    _r(
        r"\bipos?\b"
        r"|\binitial\s+public\s+offering\b"
        r"|\b(?:mainboard|sme)\s+(?:issue|listing|ipo)\b"
        r"|\bnew\s+(?:listing|issue)s?\b"
        r"|\bapply\s+(?:for|to)\s+(?:the\s+)?[\w\s]+\bipo\b",
        "list_upcoming_ipos", "get_ipo_details", "propose_ipo_application",
        "propose_ipo_automation", "get_ipo_listing",
    ),

    # ── Two-stock comparison (2026-05-29) ───────────────────────────
    # "compare X and Y", "X vs Y", "which is better X or Y", "compare
    # returns of X and Y over N years". Surface compare_performance so
    # the model uses the real two-symbol tool instead of fetching one
    # and asserting the other's number (the fabrication the audit found).
    _r(
        r"\b(?:compare|comparison\s+of|versus|vs\.?)\b"
        r"|\bwhich\s+(?:is|one\s+is|of\s+\w+)\s+(?:better|stronger|the\s+best)\b"
        r"|\b\w+\s+vs\.?\s+\w+\b"
        r"|\bbetter\s+(?:return|performer|investment)\b",
        "compare_performance", "get_correlation_matrix", "fetch_fundamentals",
    ),

    # ── L14 T4: pre/post-pivot regime comparison ────────────────────
    _r(
        r"\b(?:before\s+and\s+after|pre[\s-](?:and[\s-])?post|"
        r"pre[\s-]?\d{4}|post[\s-]?\d{4}|"
        r"before\s+(?:covid|2020|2021|2022|2023|2024|2025)|"
        r"after\s+(?:covid|2020|2021|2022|2023|2024|2025)|"
        r"pre[\s-]?covid|post[\s-]?covid|"
        r"in\s+(?:both\s+)?regimes?|regime\s+shift|"
        r"\bsince\s+(?:january|february|march|april|may|june|july|"
        r"august|september|october|november|december)\s+\d{4})\b",
        "regime_compare_metrics",
        "compose_multistep",
    ),

    # ── L14 T2: entity-grounding web search ─────────────────────────
    # Surface `web_search_brief` when the prompt asks about an
    # institution / concept / financial-instrument category that the
    # local data doesn't cover. Conservative: requires a definitional
    # phrase (what is, explain, define, tell me about) OR an entity
    # the LLM is likely to be uncertain about (RBI, repo rate,
    # arbitrage fund, capital-guaranteed note, gold ETF, etc.).
    _r(
        r"\b(?:what\s+is|what\s+are|explain|define|tell\s+me\s+about|"
        r"how\s+does\s+(?:a|an|the))\s+"
        r"(?:rbi|reserve\s+bank|repo\s+rate|"
        r"arbitrage\s+fund|capital[\s-]?guaranteed|"
        r"gold\s+etf|liquid\s+fund|debt\s+fund|"
        r"covered\s+call|protective\s+put|"
        r"sip|nifty|banknifty|sensex|"
        r"smallcap|midcap|largecap|"
        r"gift\s+nifty|niftybees|goldbees|liquidbees)"
        r"|\b(?:what\s+does|tell\s+me\s+about)\s+\w+\s+"
        r"(?:mean|stand\s+for)\b",
        "web_search_brief",
    ),

    # ── L14 multi-step orchestrator ────────────────────────────────
    # Surface `compose_multistep` (and its helpers) when the prompt
    # carries TWO+ sequential verbs whose later step depends on the
    # earlier step's result. Examples:
    #   "compare A, B → tell me which won → build agent on the winner"
    #   "backtest X vs Y → set up the better one"
    #   "research X → design a strategy → backtest → create agent"
    # Conservative: requires either (a) analysis verb + connector +
    # action verb, OR (b) "X vs Y" + ranking ask, OR (c) explicit
    # "full plan" / "do all four" phrasing.
    _r(
        r"\b(compare|backtest|research|show\s+me|analyze|analyse|compute|"
        r"rank|score|compute)\b[^.]{0,180}?"
        r"(?:,|then|and\s+(?:then\s+)?|\s—\s|;|\.\s+(?:then|now|"
        r"show|tell|then\s+))\s*"
        r"(?:tell\s+me\s+(?:which|the\s+winner)|"
        r"show\s+me\s+(?:which|the\s+winner)|"
        r"build|create|set\s+up|"
        r"setup|make|turn\s+(?:it|that|the\s+winning)|design\s+a\s+"
        r"strategy|pick|identify)\b"
        # (b) "X vs Y" + ranking verb (compare-and-pick shape).
        # Use [\s\S] so the regex spans periods between sentences.
        r"|\b\w+\s+(?:vs|versus)\s+\w+\b[\s\S]{0,200}?"
        r"(?:tell\s+me\s+(?:which|the\s+winner|which\s+strategy\s+won|"
        r"which\s+won|by\s+how\s+much)|"
        r"show\s+me\s+(?:which|the\s+winner|the\s+better|which\s+strategy)|"
        r"identify|pick|compare\s+(?:to|with))\b"
        # (c) catch-all "full plan" phrasings.
        r"|\b(?:do\s+all\s+(?:four|three|five)|full\s+plan)\b"
        # (d) reverse order — "tell me which / show me which" → "build"
        r"|\b(?:tell\s+me\s+which|which\s+(?:one\s+won|had\s+the)|"
        r"the\s+winner)\b.*?\b(?:build|create|set\s+up|design|make)\b",
        "compose_multistep",
        "compare_backtests",
        # Surface the underlying analytical tools too so the LLM can
        # populate the plan steps.
        "compare_performance",
        "get_performance_metrics",
        "get_correlation_matrix",
        "propose_workflow",
        "propose_threshold_order",
        "propose_holding_action",
        "backtest_workflow",
    ),

    # ── Agent / strategy / workflow building (also covered by
    # _ALWAYS_INCLUDE, but explicit so the LLM understands intent).
    _r(
        r"\b(build|create|set up|setup|make)\s+(?:me\s+)?an?\s+(agent|strategy|automation|rule|workflow)"
        r"|\bevery\s+(monday|tuesday|wednesday|thursday|friday|weekday|day|week|morning|evening)"
        r"|\bevery\s+\d"
        r"|\bif\s+(rsi|sma|ema|price|the\s+price)"
        r"|\bwhen\s+(rsi|sma|ema|price|the\s+price)",
        "propose_workflow",
        "propose_dsl_workflow",
        # Under-specified agent ask ("make me an agent that buys options in
        # RELIANCE" — action verb, no trigger, no size) → the deterministic
        # clarify_card. Its own gate (should_ask_agent) self-filters: it
        # returns no card when a trigger/size is present (the every-<day> / if-
        # <rsi> branches above), so co-surfacing it here is safe.
        "ask_agent_clarify",
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

    # ── Dip-buy automation (GAN R4 F6) ─────────────────────────────
    # "build a dip-buying strategy for it", "buy the dip on RELIANCE",
    # "dip-buy automation", "buy ₹10k of X if it falls 5%". The order
    # rule below also surfaces create_dip_buy on a "buy" verb, but the
    # "build a dip-buying strategy for it" phrasing carries no order verb
    # — only "build" + "strategy" — so create_dip_buy was never in scope
    # and the model punted to ASK_USER. Surface it directly on any dip cue.
    _r(
        r"\bdip[\s-]?buy(?:ing)?\b"
        r"|\bbuy\s+(?:the\s+|on\s+a\s+)?dip\b"
        r"|\b(?:buy|accumulate|add)\b[^.]{0,40}\b(?:falls?|drops?|dips?)\b"
        r"\s*\d",
        "create_dip_buy", "calculate_dip_price", "get_live_price",
        "propose_workflow", "propose_dsl_workflow",
    ),

    # ── Broad MARKET OVERVIEW intent ───────────────────────────────
    # "tell me about the market today", "how's the market", "market
    # update / overview / wrap", "what are the markets doing", "how did
    # the market do today" — these mean the BROAD market (indices +
    # breadth), NOT a single stock and NOT a clarification.
    #
    # WHY this rule is load-bearing: `select_tool_names` UNIONS every
    # matching rule, and before this existed "tell me about the market
    # today" matched ONLY the single-stock analysis rule below — so it
    # got a toolset with get_live_price + fundamentals but NO
    # get_index_level / get_top_movers. With the overview tools missing,
    # the model non-deterministically either tried get_live_price (which
    # failed → the "give me an NSE ticker" message) or asked "Nifty /
    # Sensex or a specific stock?". Surfacing the index+movers+status
    # tools here makes the overview chain available EVERY time, so the
    # answer is deterministic. (See the matching directive in system.md.)
    _r(
        # overview verb/noun sitting near the word market(s)
        r"\b(?:tell\s+me\s+about|how(?:'s|\s+is|\s+are|\s+did)|"
        r"what(?:'s|\s+is|\s+are|\s+happened|\s+happening)|state\s+of|"
        r"recap\s+of|overview\s+of|update\s+on|summary\s+of)\b"
        r"[^.?!]{0,24}\bmarkets?\b"
        # market(s) immediately followed by an overview noun / time word
        r"|\bmarkets?\b\s*(?:today|now|update|overview|wrap|recap|summary|"
        r"round[- ]?up|this\s+(?:morning|week)|doing|looking)\b"
        # bare "the market today?" / "markets today" / "the markets?"
        r"|^\s*(?:the\s+)?markets?(?:\s+today)?\s*\??\s*$",
        "get_index_level", "get_top_movers", "get_market_status",
        "get_symbol_news",
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
        # Trend / structure asks — need the SMA stack + RSI + returns, NOT
        # a single-day index level. (P1.3)
        r"|\b(?:up[- ]?trend|down[- ]?trend|\btrend\b|topping|bottoming|"
        r"sideways|consolidat\w*|breakout|breakdown|momentum|structure)\b"
        # Multi-stock comparison / correlation
        r"|\b(?:rank|compare|correlat\w*|covariance|diversif\w*)\b"
        r"|\bmost\s+(?:correlated|uncorrelated)\b",
        "get_indicator", "get_multiple_indicators",
        "get_performance_metrics", "compare_performance",
        "get_correlation_matrix", "get_returns",
        "get_live_price", "get_price_history",
    ),

    # ── Cross-sectional "cheapest / best of N on a metric" → screen ──
    # WHY: "which of HDFCBANK, ICICIBANK, SBIN is cheapest on PE" / "rank
    # these by ROE" should be ONE ranked screen_fundamentals, not N x
    # fetch_fundamentals (sparse + N hops). (P1.5)
    _r(
        r"\b(?:cheapest|most\s+expensive|priciest|best|worst|rank|top|"
        r"which\s+(?:of\s+)?(?:these|them|the)?)\b[^.?]{0,40}"
        r"\b(?:p/?e|pe\b|roe|roce|p/?b|valuation|debt|d/?e|payout|"
        r"dividend|margin|cheap|expensive)\b"
        r"|\b(?:p/?e|roe|roce|p/?b|valuation|payout|dividend\s+yield)\b"
        r"[^.?]{0,30}\b(?:cheapest|highest|lowest|best|rank)\b",
        "screen_fundamentals", "fetch_fundamentals", "compare_performance",
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

    # ── Generic single-stock ANALYSIS intent ───────────────────────
    # WHY: "analyse HDFC Bank", "what do you think about RELIANCE", "your
    # view on INFY", "tell me about TCS", "deep dive on …", "is X a buy"
    # carry NO indicator/risk keyword, so they fell through to a toolset
    # with only get_live_price — the model then apologised that it "has no
    # price-history / fundamentals tool" (it does). Surface the full
    # read-only analysis suite so the model fetches data and interprets it
    # instead of declining.
    _r(
        r"\b(analyse|analyze|analysis|deep[- ]dive|break\s*down|evaluate|"
        r"assess|overview|outlook|fundamentals?|valuation)\b"
        r"|\bwhat\s+do\s+you\s+think\s+(?:about|of)\b"
        r"|\byour\s+(?:view|take|opinion|read|thoughts?)\s+on\b"
        r"|\bthoughts?\s+on\b"
        # "tell me about <X>" is single-stock — but NOT "tell me about the
        # market(s)", which the broad market-overview rule above owns.
        r"|\btell\s+me\s+about\b(?!\s+(?:the\s+|all\s+)?markets?\b)"
        r"|\b(?:is|should\s+i\s+(?:buy|consider|look\s+at))\s+\w+\s+a?\s*"
        r"(?:buy|good|worth|investment)\b",
        "get_price_history", "get_52wk_range", "get_indicator",
        "get_multiple_indicators", "get_performance_metrics",
        "fetch_fundamentals", "get_symbol_news", "get_live_price",
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
    # Broadened (2026-05-29): "invest ₹2000 in gold every month",
    # "put 5000 in niftybees monthly", "start investing 3000 in silver
    # every month" carry no literal "sip" token but ARE monthly SIPs.
    # create_sip supports day_of_month; propose_scheduled_order does NOT
    # (it only has a weekday cron), so without surfacing create_sip these
    # silently downgraded "every month" → "every weekday". "every day"
    # is deliberately NOT matched (a daily scheduled buy, not a SIP).
    _r(
        r"\bs\.?i\.?p\.?s?\b"
        r"|recurring\s+(?:invest|buy|order|purchase)"
        r"|monthly\s+(?:invest|buy|purchase)"
        r"|\b(?:invest|buy|put|add|start)\b[^.]{0,40}\bevery\s+(?:month|week|fortnight)\b"
        r"|\b(?:invest|buy|put|add|start)\b[^.]{0,40}\b(?:monthly|weekly|fortnightly)\b"
        r"|\bevery\s+(?:month|week)\b[^.]{0,30}\b(?:invest|buy|in\s+(?:gold|silver|nifty))\b",
        "create_sip", "list_sips", "pause_sip", "resume_sip",
        "delete_sip", "pause_all_sips",
        # GAN R4 F7: weekly/weekday SIPs route to propose_scheduled_order
        # (registerable + amendable from chat). It is in _ALWAYS_INCLUDE
        # but listing it here documents the SIP-lifecycle intent.
        "propose_scheduled_order",
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
    # Two backtester tools are surfaced for chat backtest intents:
    #   backtest_dsl_tree    — Phase B+1+C.0 tree-based engine.
    #                         Preferred for compound / cross-symbol /
    #                         aggregator-based conditions. The LLM
    #                         hands the user's NL condition through as
    #                         a single string and the tool translates
    #                         to a DSL tree internally.
    #   backtest_workflow    — legacy workflow-shape backtester. Kept
    #                         for prompts that fit the flat steps[]
    #                         shape cleanly (single trigger.indicator
    #                         + order action).
    # run_backtest is the legacy single-indicator tool whose required
    # `trigger_condition` field made the LLM ask "what's the trigger
    # condition?" for every compound query. Still in ALL_TOOLS for
    # programmatic callers, never surfaced in chat.
    _r(
        r"\bback\s*test(?:ed|ing)?\b|\bsimulate\b|\bif\s+i\s+had\s+(bought|invested)"
        r"|\bhow\s+(?:would|did)\b.{0,40}\b(?:perform(?:ed)?|do(?:ne)?|fare(?:d)?)\b",
        "backtest_dsl_tree", "backtest_workflow",
    ),

    # ── Quant strategy classes (Phase 2.1-2.4) ────────────────────
    # pairs / cointegration / Johansen baskets / momentum portfolios.
    # These tools are NOT in the generic backtest rule above, so without
    # this the model has to find_tool its way to them (an extra hop ->
    # tokens + latency) or, lacking the tool in scope, asks for
    # clarification instead of running. Surface them on the intent.
    _r(
        r"\bpairs?\s+trad|\bcointegrat|\bstat[\s-]?arb|\bmean[\s-]?revert"
        r"|\bjohansen\b|\bspread\b.{0,20}\b(?:trade|strateg|z-?score)"
        r"|\bmomentum\s+portfolio\b|\blong[\s/-]?short\b|\brotat(?:e|ion|ing)\b"
        r"|\b(?:top|best|strongest)\s+\d*\s*(?:momentum|names|stocks?)\b"
        r"|\bhold(?:ing)?\s+the\s+(?:top|strongest)\b",
        "backtest_pairs", "scan_pairs", "test_cointegration", "backtest_portfolio",
        "backtest_dsl_tree", "backtest_workflow",
    ),

    # ── Upcoming events / earnings / dividend calendar ────────────
    # "when does X report", "next results date", "ex-dividend", "RBI MPC",
    # "F&O expiry" — surface get_upcoming_events directly (system.md says
    # call it without a find_tool detour; this makes that possible).
    _r(
        r"\bwhen\s+(?:is|are|does|do|will)\b[^.]{0,40}"
        r"\b(?:report|reporting|results?|earnings|ex[\s-]?div|dividend|expir)"
        r"|\b(?:next|upcoming)\s+(?:earnings|results?|dividend|ex[\s-]?dividend|mpc|expiry|board\s+meeting|corporate\s+action)\b"
        r"|\bearnings\s+(?:date|calendar)\b|\bex[\s-]?dividend\b|\bresults?\s+date\b"
        r"|\b(?:rbi|mpc)\s+(?:meeting|date|decision)\b|\bf&o\s+expiry\b",
        "get_upcoming_events",
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
    #
    # Workstreams A & B: this is ALSO the entry point for the DB-driven
    # builder (build_strategy) + dynamic clarifying questions
    # (ask_user_dynamic). The regex now also catches the thoughtful-
    # portfolio framings ("build me a long-term portfolio", "a balanced
    # basket of quality stocks", "invest ₹2L for the long run", "design
    # a strategy") that should get a named weighting scheme + a
    # fundamentals gate rather than a bare equal-weight macro. We co-
    # surface screen_fundamentals + fetch_fundamentals so the DB
    # selection gate is always reachable from the basket path (plan §3b:
    # the fundamentals tools were never wired into the basket path).
    _r(
        r"\bbasket\s+of\b"
        r"|\b(?:invest|allocate|put|deploy|split)\b.{0,40}\b(?:across|equally|weighted|portfolio|long[\s-]?term|long\s+run)\b"
        r"|\btop\s+\d+\s+(?:[a-z_]+\s+)?stocks?\b"
        r"|\bequal\s+weight(?:age)?\b|\bmcap[- ]weighted\b|\bmarket[- ]cap\s+weighted\b"
        r"|\brisk[\s-]?parity\b|\bmin(?:imum)?[\s-]?variance\b|\bblack[\s-]?litterman\b"
        r"|\bsector\s+(?:basket|allocation)\b"
        # Thoughtful-portfolio / strategy-builder framings.
        r"|\b(?:build|make|create|design|construct|put\s+together|give\s+me)\b"
        r"[^.]{0,40}\b(?:portfolio|basket|strateg(?:y|ies)|allocation)\b"
        r"|\b(?:portfolio|basket)\b[^.]{0,30}\b(?:of\s+(?:quality|value|growth|dividend|good)\s+stocks?|for\s+(?:the\s+)?long)\b",
        "propose_basket_allocation",
        # Workstreams A & B — DB-driven builder + dynamic questions.
        "build_strategy",
        "ask_user_dynamic",
        # Co-surface the fundamentals-DB tools so the selection gate
        # (F-score / Magic-Formula / multi-factor) is always reachable.
        "screen_fundamentals",
        "fetch_fundamentals",
        "place_basket_order",
        "get_live_price",
    ),

    # ── Yields / cash parking ─────────────────────────────────────
    # WHY "fixed[- ]income" / "bond" / "sgb" / "recommend.*invest" added:
    # "recommend the best fixed-income option for 2 years" matched none
    # of the prior patterns — yield tools weren't surfaced and the model
    # answered with prose that had no data. Bond and SGB are first-class
    # retail-investor terms that share the recommendation surface.
    #
    # A8 GATING: the bare word "yield" used to hijack single-stock
    # dividend asks ("is ITC a dividend play, what's the yield doing")
    # into compare_yields/get_yield_recommendation (FD/G-Sec table, zero
    # stock numbers). These tools are CASH-PARK ONLY. The "yield" token
    # now ONLY routes here in an explicit cash-park context
    # (park/idle/cash/where-to-park/after-tax), never on a bare ticker
    # dividend phrasing — those are caught by the single-stock rule above.
    _r(
        r"fixed\s+deposit|fixed[- ]income|\bfd\b|liquid\s+fund"
        r"|overnight\s+fund|park\s+(?:my|the)?\s*(?:cash|money|idle|surplus|funds?)"
        r"|where\s+(?:should|to|can)\s+i\s+park"
        r"|idle\s+(?:cash|money|funds?)"
        r"|savings\s+account|after[- ]tax\s+(?:yield|return)|\bsgb\b|sovereign\s+gold"
        r"|government\s+bond|\bg-?sec\b|treasury\s+bill|\bt-?bill\b"
        r"|recommend\s+(?:the\s+)?(?:best\s+)?(?:fixed|bond|debt|safe)"
        # "yield" only in a cash-park / fixed-income context, never bare.
        r"|\byield\b[^.?]{0,30}\b(?:fd|deposit|debt|bond|liquid|overnight|"
        r"cash|park|gsec|g-sec|sgb|safe|fixed)\b"
        r"|\b(?:fd|deposit|debt|bond|liquid|overnight|cash|park|gsec|g-sec|"
        r"sgb|safe|fixed)\b[^.?]{0,30}\byield\b"
        r"|best\s+yield(?:s)?\s+(?:for|on)\s+(?:idle|cash|park|short)",
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

    # ── Option roll / adjustment (Track C #3) ─────────────────────
    # "roll the 24000 call to next expiry", "shift the strike up 200",
    # "becha tha, loss me hai — next expiry me roll kar do", "adjust my
    # short call". Rolling is the #1 retail F&O follow-up; surface the
    # dedicated tool (plus the chain + build fallbacks) whenever a roll
    # cue appears in an options context.
    _r(
        r"\broll(?:ing|ed)?\b[^.]{0,60}\b(?:call|put|option|strike|expiry|"
        r"position|short|leg|ce|pe)\b"
        r"|\b(?:call|put|option|strike|short|leg|ce|pe)\b[^.]{0,60}\broll\b"
        r"|\bshift\s+(?:the\s+)?strike\b"
        r"|\bmove\s+(?:the\s+|my\s+)?(?:strike|short\s+(?:call|put))\b"
        r"|\broll\s*(?:kar|karo|krdo|kar\s*do)\b"
        r"|\b(?:becha|bechi)\b[^.]{0,80}\b(?:next\s+expiry|roll)\b"
        r"|\badjust\b[^.]{0,40}\b(?:short\s+)?(?:call|put|strangle|straddle|leg)\b",
        "roll_option_position", "get_option_chain", "build_option_strategy",
        "critique_option_strategy", "get_live_price",
    ),

    # ── Workflow arming + armed-state introspection (Track C #1) ──
    # "register it", "activate the agent", "arm it", "make it live",
    # "is it actually live?", "when do you check?", "how often does it
    # evaluate?", "what's the status of my automation?".
    _r(
        r"\b(?:register|activate|arm|enable|turn\s+on|switch\s+on|"
        r"go\s+live\s+with|make)\s+(?:it|that|this|the\s+(?:agent|workflow|"
        r"automation|rule|draft|strategy)|my\s+(?:agent|workflow|"
        r"automation|rule))\b(?:\s+(?:live|active|now))?"
        r"|\bis\s+(?:it|that|this|the\s+(?:agent|workflow|automation|rule)|"
        r"my\s+(?:agent|workflow|automation|rule))\s+"
        r"(?:actually\s+|really\s+)?(?:live|running|armed|active|on|working)\b"
        r"|\bwhen\s+do(?:es)?\s+(?:you|it|pivot)\s+(?:check|evaluate|poll|"
        r"look|scan)\b"
        r"|\bhow\s+often\b[^.]{0,40}\b(?:check|checked|evaluate|evaluated|"
        r"poll|polled|run|scan)"
        r"|\b(?:status|state)\s+of\s+(?:my|the)\s+(?:agent|workflow|"
        r"automation|rule|trigger)\b",
        "register_workflow", "get_workflow_status",
    ),

    # ── F&O / options (P1) ────────────────────────────────────────
    # Chain exploration, strategy suggestion/building, pre-trade
    # critique, portfolio greeks. The suggest tool carries the
    # minimal-ask flow, so vague options intents ("play NIFTY with
    # options", "income strategy") surface it without demanding
    # strikes upfront. Strategy names route to build; explicit-leg
    # second-person asks ("should I sell the 24000 call?") route to
    # critique. All four ride together — the model picks.
    _r(
        r"\boption\s+chain\b|\boi\s+data\b|\bopen\s+interest\b"
        r"|\boptions?\b|\bf\s*&?\s*o\b|\bfno\b"
        r"|\b(?:call|put)\s+(?:option|premium|writing|buying|selling)\b"
        r"|\bstraddles?\b|\bstrangles?\b|\biron\s+(?:condor|butterfly)\b"
        r"|\b(?:bull|bear)\s+(?:call|put)\s+spread\b"
        r"|\bcovered\s+call\b|\bprotective\s+put\b|\bcash[- ]?secured\s+put\b"
        r"|\bnaked\s+(?:call|put)\b|\bcredit\s+spread\b|\bdebit\s+spread\b"
        r"|\b(?:calendar|diagonal|ratio|vertical|options?)\s+spread\b"
        r"|\bwrite\s+a?\s*(?:call|put)s?\b"
        r"|\b(?:sell|buy|short|long)\s+a?\s*(?:call|put)s?\b"
        r"|\bstrike\s+price\b|\bexpiry\b|\bweekly\s+(?:call|put|option)\b"
        r"|\bimplied\s+vol(?:atility)?\b|\biv\s+(?:rank|percentile|chart)\b"
        r"|\b(?:portfolio|net|my)\s+(?:delta|theta|vega|gamma|greeks)\b"
        r"|\bgreeks\b|\bmax\s+pain\b|\bpcr\b|\bput[- ]call\s+ratio\b",
        "get_option_chain", "suggest_option_strategy",
        "build_option_strategy", "critique_option_strategy",
        "get_portfolio_greeks", "roll_option_position",
    ),

    # ── Polymarket: BROWSE / discover open prediction-market contracts.
    # Surfaced for "what's on polymarket", "show me crypto markets on
    # poly", "browse politics markets". Pairs naturally with
    # propose_polymarket_trigger after the user picks a market.
    _r(
        r"\bpoly(?:market)?\b.{0,40}\b(?:browse|show|list|what(?:'s| is)|"
        r"open|trending|hot|popular|markets?|contracts?|events?)\b"
        r"|\b(?:browse|show|list)\b.{0,30}\bpoly(?:market)?\b"
        r"|\b(?:browse|show)\b.{0,30}\b(?:bitcoin|btc|crypto|political|politics|"
        r"election|sports|nba|nfl|world\s+cup|fifa)\s+markets?\b"
        r"|\bwhat\s+(?:can|do)\s+(?:i|we)\s+bet\s+on\b"
        r"|\bwhat(?:'s| is)\s+hot\s+on\s+poly",
        "browse_polymarket_markets",
    ),

    # ── Polymarket: PREDICTION-MARKET TRIGGER (threshold OR resolution).
    # Matches user asks that name a prediction-market event ("Trump 2028",
    # "Modi 2029", "Bitcoin $150k probability", "Iran ceasefire", "Fed
    # cut", "election", "world cup") OR explicitly say polymarket. Also
    # catches the "alert me when X probability above/below N%" /
    # "execute when X resolves / actually happens" phrasings. Both modes
    # of the slice-4 trigger live behind the SAME tool — handler picks
    # mode='threshold' vs 'resolution' from the prompt.
    _r(
        r"\bpoly(?:market)?\b"
        r"|\bprediction\s+market(?:s)?\b"
        r"|\bprobability\s+of\b.{0,60}\b(?:above|below|over|under|hits?|crosses?|reaches?|goes?)\b"
        r"|\b(?:alert|tell|ping|notify|wake|let\s+me\s+know|execute|fire|trigger)\s+(?:me\s+)?(?:if|when|once)\b.{0,80}\b(?:probability|chance|odds|resolves?|actually|happens?|wins?|loses?)\b"
        r"|\b(?:trump|biden|harris|vance|modi|bjp|congress)\b.{0,30}\b(?:2024|2025|2026|2027|2028|2029|election|wins?|loses?|nominee|president)\b"
        r"|\b(?:fed|rbi|ecb|boe|boj)\b.{0,40}\b(?:cuts?|hikes?|holds?|rate(?:s)?|meeting|decision)\b"
        r"|\b(?:bitcoin|btc|ethereum|eth|sol|xrp)\b.{0,40}\b\$?\d{2,7}k?\b.{0,30}\b(?:above|below|probability|chance|hits?|crosses?|reaches?)\b"
        r"|\b(?:world\s+cup|olympics?|t20|ipl|champions\s+league|nba|nfl)\b.{0,40}\b(?:winner|wins?|champion|probability)\b"
        r"|\b(?:resolves?|resolved|resolution)\s+(?:yes|no|either)\b"
        # Geopolitical / current-event nouns wrapped in an alert-shaped
        # ask. "alert me if Iran ceasefire breaks down", "tell me when
        # Russia-Ukraine peace deal lands", "ping me if Khamenei
        # regime falls". Discriminated from Indian-stock alerts by the
        # absence of an NSE ticker / share / RSI keyword (those rules
        # match first and add their own tools).
        r"|\b(?:alert|tell|ping|notify|wake|let\s+me\s+know|execute|fire|trigger)\s+(?:me\s+)?(?:if|when|once)\b.{0,100}\b(?:ceasefire|war|peace|treaty|summit|deal|regime|invasion|invades?|airspace|sanctions?|hostages?|coup|impeachment|nomination|nominee|nobel|grammy|oscar)\b",
        "propose_polymarket_trigger",
        "browse_polymarket_markets",
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
    "backtest_workflow", "backtest_dsl_tree",
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
