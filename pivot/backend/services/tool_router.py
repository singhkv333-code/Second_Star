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
_ALWAYS_INCLUDE: frozenset[str] = frozenset({
    "propose_workflow",
    "propose_scheduled_order",
    "propose_threshold_order",
    "propose_basket_allocation",
    "propose_holding_action",
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

    # ── Live price / quote / OHLC ──────────────────────────────────
    _r(
        r"\b(price|quote|snapshot|ltp|last\s+traded|how\s+(much|is)\s+\w+\s+trading"
        r"|what(?:'s| is)\s+\w+\s+(at|trading)|level\s+of\s+(nifty|sensex|banknifty)"
        r"|nifty|sensex|banknifty)\b"
        r"|^\s*[A-Z]{2,12}\s*\??\s*$",
        "get_live_price", "get_index_level", "get_ohlc", "get_market_status",
    ),

    # ── 52-week range, price history, charts ───────────────────────
    _r(
        r"\b(52\s*-?\s*week|52w|all[- ]time\s+(high|low)|chart|history|past\s+\d+\s*(year|month|week))"
        r"|\bhow\s+has\s+\w+\s+done"
        r"|\bshow\s+me\s+(?:the\s+)?(?:chart|history|past)",
        "get_price_history", "get_52wk_range",
    ),

    # ── Portfolio / holdings / sector ──────────────────────────────
    _r(
        r"\b(portfolio|holdings|my\s+(stocks|positions|investments)"
        r"|sector\s+breakdown|allocation|p&?l|profit|loss"
        r"|tax\s+(summary|impact|loss)|stcg|ltcg)\b",
        "get_portfolio_summary", "get_holdings", "get_sector_breakdown",
        "get_holding_detail", "get_tax_summary", "get_active_products",
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
    ),

    # ── SIP (recurring investment) ────────────────────────────────
    _r(
        r"\bs\.?i\.?p\.?s?\b|recurring\s+(invest|buy)|monthly\s+invest",
        "create_sip", "list_sips", "pause_sip", "resume_sip",
        "delete_sip", "pause_all_sips",
    ),

    # ── Strategy automation (single-rule) ──────────────────────────
    _r(
        r"\b(strategy|strategies|automation|rule|monitor|watch\s+for|trigger)\b",
        "create_strategy", "list_strategies", "pause_strategy",
        "resume_strategy", "delete_strategy",
    ),

    # ── Backtest ──────────────────────────────────────────────────
    _r(
        r"\bback\s*test(?:ed|ing)?\b|\bsimulate\b|\bif\s+i\s+had\s+(bought|invested)",
        "run_backtest",
    ),

    # ── Yields / cash parking ─────────────────────────────────────
    _r(
        r"\byield(?:s|ed)?\b|fixed\s+deposit|\bfd\b|liquid\s+fund"
        r"|overnight\s+fund|park\s+(my|the)?\s*(cash|money|idle)"
        r"|savings\s+account|after[- ]tax",
        "compare_yields", "get_yield_recommendation",
    ),

    # ── Scheduler status ──────────────────────────────────────────
    _r(
        r"\b(scheduler|next\s+(run|sip|job)|upcoming\s+(job|sip|task))\b"
        r"|\bis\s+automation\s+(running|on)",
        "get_scheduler_status", "list_upcoming_jobs",
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
    "run_backtest",
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
    """
    if not selected:
        return f"{_CACHE_KEY_PREFIX}-all"
    # ASK_USER is synthetic and added downstream — exclude it from
    # the signature so its presence/absence doesn't shift the key.
    canonical = ",".join(sorted(n for n in selected if n != "ASK_USER"))
    sig = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    return f"{_CACHE_KEY_PREFIX}-{sig}"
