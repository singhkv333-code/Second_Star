"""Chat-tool handlers that bridge the DSL backtester / workflow
proposal into the chat-tool dispatch surface.

Two tools:

  ``backtest_dsl_tree``  — given a natural-language entry condition and
                           a primary symbol + window, translate to a DSL
                           tree and run the Phase B backtester. Returns
                           a payload the FE renders with the existing
                           ``indicator_backtest_chart`` card.

  ``propose_dsl_workflow`` — given a natural-language entry condition,
                           action, and symbol, build a workflow draft
                           with a ``trigger.compound`` step (carrying the
                           translated tree) and an action step. Returns a
                           ``workflow_draft_card`` the user activates
                           from chat.

Both handlers do the LLM tree-translation server-side (via
``backend.workflows.dsl.llm_translate``) so the chat-side LLM only has
to extract the user's intent — it doesn't need to know the DSL grammar.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from backend.services.tool_errors import ToolRedirect
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from backend.workflows.dsl.llm_translate import (
    TranslationError,
    translate_condition_to_tree,
)


logger = logging.getLogger(__name__)


# Tokens that look like NSE tickers (3-15 uppercase letters) but aren't.
# Used to count REAL tickers in a NL condition string when deciding
# whether the prompt is single-symbol (DSL handles it) vs multi-symbol
# (must go through propose_workflow with one branch per symbol).
_DSL_NON_TICKER_TOKENS: frozenset[str] = frozenset({
    # Day-of-week / time
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
    "SATURDAY", "SUNDAY", "TODAY", "YESTERDAY", "TOMORROW",
    # Exchanges / boilerplate
    "NSE", "BSE", "INR", "IST", "EOD",
    # Indicators / order types
    "RSI", "SMA", "EMA", "MACD", "ADX", "ATR", "BB", "VIX",
    "WMA", "OBV", "VWAP", "CCI", "MFI", "ROC", "TRIX", "PSAR",
    "GTT", "OCO", "SL", "TP", "MP", "MIS", "CNC", "NRML",
    # Logical / order-noise words that get uppercased by accident
    "AND", "OR", "NOT", "IF", "WHEN", "THEN", "ELSE", "AT", "ON",
    "OF", "TO", "FROM", "IN", "IS", "AS",
    "BUY", "SELL", "PLACE", "SET", "ADD", "STOP", "LOSS",
    "AGENT", "STRATEGY", "WORKFLOW", "AUTOMATION", "ALERT",
    "MARKET", "LIMIT", "OPEN", "CLOSE", "HIGH", "LOW",
    "PRICE", "QUANTITY", "SHARES", "STOCK", "STOCKS",
    "ENTIRE", "FULL", "WHOLE", "ALL", "COMPLETE", "TOTAL", "EVERY",
    "HOLDING", "HOLDINGS", "POSITION", "POSITIONS",
    # Signal / pattern words that get uppercased mid-condition and were
    # being mis-read as ACTION tickers (e.g. "bullish MACD crossover" →
    # phantom tickers BULLISH/CROSSOVER → false multi-symbol reroute).
    "BULLISH", "BEARISH", "NEUTRAL", "CROSS", "CROSSOVER", "CROSSES",
    "GOLDEN", "DEATH", "SIGNAL", "LINE", "HISTOGRAM", "HIST",
    "BAND", "BANDS", "UPPER", "LOWER", "MIDDLE",
    "OVERSOLD", "OVERBOUGHT", "BREAKOUT", "BREAKDOWN", "FLIP",
    "PEAK", "TROUGH", "TREND", "MOMENTUM", "DIP", "DAILY", "WEEKLY",
    "MONTHLY", "PROFIT", "GAIN", "LOSS", "TARGET", "TRAILING",
    # Comparison / condition verbs that appear inside an entry/exit clause
    # and were being mis-collected as ACTION tickers ("RSI below 35 and
    # exits if it falls 5%" → phantom tickers BELOW/EXITS/FALLS).
    "ABOVE", "BELOW", "UNDER", "OVER", "EXIT", "EXITS", "ENTER", "ENTERS",
    "ENTRY", "FALL", "FALLS", "RISE", "RISES", "DROP", "DROPS", "HIT",
    "HITS", "REACH", "REACHES", "BREACH", "BREACHES", "MOVE", "MOVES",
    "GOES", "TURNS", "AFTER", "BEFORE", "UNTIL", "WHILE", "THAN", "WITH",
    "RUNNING", "AVERAGE", "VOLUME", "SPIKE",
})


_TICKER_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9\-_]{2,15}\b")


def _word_cap(text: str, cap: int = 90) -> str:
    """Cap a display label at `cap` chars WITHOUT cutting mid-token.

    A hard slice produced garbage titles like "…crosses above signal
    MACD(12,1" (from MACD(12,26,9)) that then leaked into readbacks and
    even got re-parsed on amendment turns. Cut at the last space before
    the cap and mark the elision."""
    t = (text or "").strip()
    if len(t) <= cap:
        return t
    cut = t[: cap - 1]
    sp = cut.rfind(" ")
    if sp > cap // 2:
        cut = cut[:sp]
    return cut + "…"


def _distinct_tickers_in(*texts: str) -> list[str]:
    """Return the distinct ticker-shaped tokens across all supplied
    strings, filtering out NSE/RSI/EMA/etc. that match the same regex
    but aren't tickers. Used by the multi-symbol guard below."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for txt in texts:
        if not txt:
            continue
        for m in _TICKER_TOKEN_RE.finditer(txt):
            tok = m.group(0).upper()
            if tok in _DSL_NON_TICKER_TOKENS:
                continue
            if tok in seen_set:
                continue
            seen_set.add(tok)
            seen.append(tok)
    return seen


_ACTION_VERB_RE = re.compile(
    r"\b(buy|buys|buying|sell|sells|selling|short|exit)\b",
    re.IGNORECASE,
)
# Tokens that interrupt a "buy A and B" sequence — once we hit one
# of these in the post-verb scan, we stop collecting tickers.
_ACTION_TERMINATORS_RE = re.compile(
    r"\b(when|if|whenever|while|at\s+(?:\d|the\s+open|the\s+close|"
    r"market\s+open|market\s+close|open|close)|on\s+(?:mon|tue|wed|"
    r"thu|fri|sat|sun)|every|after|before|until|till)\b",
    re.IGNORECASE,
)


# Deterministic parser for the COMMON position-relative exit phrasings, so
# we don't pay a slow (sometimes 100s+, untimed) LLM translate call — and
# never refuse a SUPPORTED exit shape. Matches the LLM translator's unit
# convention: percentages are FRACTIONS (6% → 0.06). Returns a DSL
# comparison tree over a position leaf, or None when nothing matches.
_EXIT_PEAK_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%[^.]{0,30}?\b(?:from|below|off|under)\b[^.]{0,20}?"
    r"\b(?:peak|high|top|highest)\b"
    r"|\bdrawdown[^.]{0,20}?(\d+(?:\.\d+)?)\s*%"
    r"|\btrail[^.]{0,20}?(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
_EXIT_PROFIT_RE = re.compile(
    r"\b(?:up|gains?|rises?|profit|gain\s+of|\+)\b[^.]{0,20}?(\d+(?:\.\d+)?)\s*%"
    r"|(\d+(?:\.\d+)?)\s*%[^.]{0,15}?\b(?:profit|gain|up)\b",
    re.IGNORECASE,
)
_EXIT_LOSS_RE = re.compile(
    r"\b(?:down|loses?|lose|falls?|drops?|loss\s+of)\b[^.]{0,20}?(\d+(?:\.\d+)?)\s*%"
    r"|(\d+(?:\.\d+)?)\s*%[^.]{0,15}?\b(?:loss|down|drop)\b",
    re.IGNORECASE,
)
_EXIT_BARS_RE = re.compile(
    r"\b(?:held|holding|after)\b[^.]{0,20}?(\d+)\s*(?:bars?|days?|sessions?)",
    re.IGNORECASE,
)


def _first_group(m: "re.Match") -> Optional[float]:
    for g in m.groups():
        if g is not None:
            try:
                return float(g)
            except (TypeError, ValueError):
                continue
    return None


def _deterministic_position_exit(text: str, *, force: bool = False) -> Optional[dict]:
    """Build a position-leaf exit tree for the common phrasings without an
    LLM hop. ``force=True`` is the failure-fallback: when the LLM translate
    errored/timed out but the text clearly names a position exit, still
    produce the right leaf rather than refusing a supported shape."""
    t = (text or "").strip().lower()
    if not t:
        return None

    def _cmp(field: str, op: str, value: float) -> dict:
        return {"type": "comparison", "op": op,
                "left": {"type": "position", "field": field},
                "right": {"type": "constant", "value": value}}

    # Order matters: "from peak" is more specific than a bare "drops X%".
    m = _EXIT_PEAK_RE.search(t)
    if m and (v := _first_group(m)) is not None:
        return _cmp("drawdown_from_peak_pct", ">=", round(v / 100.0, 6))
    m = _EXIT_PROFIT_RE.search(t)
    if m and (v := _first_group(m)) is not None:
        return _cmp("unrealised_pct", ">=", round(v / 100.0, 6))
    m = _EXIT_LOSS_RE.search(t)
    if m and (v := _first_group(m)) is not None:
        return _cmp("unrealised_pct", "<=", round(-v / 100.0, 6))
    m = _EXIT_BARS_RE.search(t)
    if m and (v := _first_group(m)) is not None:
        return _cmp("bars_held", ">=", int(v))
    if force:
        # Last resort: any % near a peak/profit/loss word.
        mm = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
        if mm:
            v = float(mm.group(1)) / 100.0
            if "peak" in t or "high" in t or "trail" in t:
                return _cmp("drawdown_from_peak_pct", ">=", round(v, 6))
            if any(w in t for w in ("loss", "down", "drop", "fall", "stop")):
                return _cmp("unrealised_pct", "<=", round(-v, 6))
            return _cmp("unrealised_pct", ">=", round(v, 6))
    return None


_INDICATOR_OR_PRICE_RE = re.compile(
    r"\b(?:rsi|sma|ema|wma|macd|adx|atr|cci|mfi|stoch|bollinger|bb|"
    r"donchian|keltner|supertrend|aroon|williams|obv|vwap|roc|trix|"
    r"psar|ichimoku|volume|price|close|open|high|low|"
    r"drawdown|peak|trough|"
    r">|<|crosses?\s+(?:above|below)|reaches?|hits?|breaches?|"
    r"oversold|overbought|"
    r"above|below|under|over|"
    r"\d+(?:\.\d+)?\s*%)\b",
    re.IGNORECASE,
)


def _has_indicator_or_price_word(text: str) -> bool:
    """True when the text mentions an indicator name or price-comparison
    operator. Used to distinguish schedule-only phrases ("every Monday
    at open buy 5 NIFTYBEES") from condition-shaped phrases ("RSI<30 on
    Mondays")."""
    return bool(_INDICATOR_OR_PRICE_RE.search(text or ""))


def _has_multi_action_tickers(condition: str) -> bool:
    """True when the condition string contains 2+ distinct
    action-ticker pairs (the user is asking for orders on multiple
    symbols). False when only ONE action-ticker pair appears (a
    legitimate cross-symbol trigger, fine for DSL).

    Strategy: split on action verbs and within the action span
    (verb → end of clause / trigger word), collect ticker-shaped
    tokens. 2+ distinct in the action span = multi-action.

    Examples:
      "buy RELIANCE 10 and TCS 5 when RSI<30" → True  (2 actions)
      "buy 10 HDFCBANK when ICICIBANK drops 3%" → False (1 action)
      "sell my INFY and TCS at 3pm" → True (2 actions)
    """
    if not condition:
        return False
    msg = condition
    distinct: set[str] = set()
    for verb_match in _ACTION_VERB_RE.finditer(msg):
        start = verb_match.end()
        rest = msg[start: start + 200]
        # Trim at the first trigger word — "when ICICIBANK drops"
        # marks the end of the action span.
        term = _ACTION_TERMINATORS_RE.search(rest)
        action_span = rest[: term.start()] if term else rest
        for m in _TICKER_TOKEN_RE.finditer(action_span):
            tok = m.group(0).upper()
            if tok in _DSL_NON_TICKER_TOKENS:
                continue
            distinct.add(tok)
        if len(distinct) >= 2:
            return True
    return len(distinct) >= 2


def _action_tickers_in(*texts: str) -> list[str]:
    """Distinct ticker tokens that appear inside an ACTION span (after
    a buy/sell verb, before the trigger word). Unlike
    `_distinct_tickers_in`, this excludes trigger-only symbols (e.g.
    NIFTY in "buy RELIANCE when NIFTY rises 1%"), so the multi-symbol
    redirect suggests only the symbols the user actually wants ordered.
    """
    found: list[str] = []
    seen: set[str] = set()
    for txt in texts:
        if not txt:
            continue
        for verb_match in _ACTION_VERB_RE.finditer(txt):
            start = verb_match.end()
            rest = txt[start: start + 200]
            term = _ACTION_TERMINATORS_RE.search(rest)
            span = rest[: term.start()] if term else rest
            for m in _TICKER_TOKEN_RE.finditer(span):
                tok = m.group(0).upper()
                if tok in _DSL_NON_TICKER_TOKENS or tok in seen:
                    continue
                seen.add(tok)
                found.append(tok)
    return found


# ── Non-structural amendment PATCH (P1, 2026-05-29 retail eval) ──────
# An expiry / quantity / notes / channel tweak to an EXISTING DSL draft
# must MUTATE the prior steps in place. Otherwise the model re-emits
# propose_dsl_workflow from the amendment text alone, drops action_kind/
# quantity/exit_condition, and silently collapses a 5-step buy+sell into a
# 2-step notify-only draft under the old name (snapshot session
# qty_amendment_expiry turn 2). The lost-action guardrail in
# validation_handler backstops anything this classifier conservatively skips.

_STRUCTURAL_AMEND_RE = re.compile(
    r"\b(?:add|also|include|append|remove|delete|instead|replace|besides)\b"
    r"|\bstop[\s-]?loss\b|\btrailing\b"
    r"|\b(?:sell|exit|square[\s-]?off)\b"
    r"|\bchange\s+it\s+to\b|\bswitch\s+to\b"
    r"|\b(?:rsi|macd|sma|ema|bollinger|stochastic|supertrend|vwap|atr|aroon|"
    r"donchian|keltner)\b"
    r"|\b(?:dip|rises?|drops?|falls?|crosses?|crossing|breakout|above|below|gap)\b",
    re.IGNORECASE,
)


def _amend_expiry_value(msg: str):
    """Return an ISO date str or an int day-count for an expiry amendment."""
    m = re.search(
        r"\b(?:valid\s+(?:until|till)|expir\w*\s+(?:on|date))\s*:?\s*"
        r"(\d{4}-\d{2}-\d{2})", msg)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{1,4})\s*[\s-]?(day|days|week|weeks|month|months)\b", msg)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("week"):
            n *= 7
        elif unit.startswith("month"):
            n *= 30
        return n
    return 30  # expiry mentioned with no number → default 30 days


def _is_nonstructural_dsl_amendment(message: str) -> dict:
    """Classify an amendment. Returns {field: value} for a NON-structural
    tweak (valid_until / quantity / name / channel), or {} when the message
    is empty, structural, or not an amendment. Conservative: ANY structural
    signal → {} (full re-translation; the lost-action guardrail backstops)."""
    msg = (message or "").strip().lower()
    if not msg or _STRUCTURAL_AMEND_RE.search(msg):
        return {}
    fields: dict = {}
    if re.search(r"\bexpir\w*\b|\bvalid\s+(?:until|till|for)\b", msg):
        fields["valid_until"] = _amend_expiry_value(msg)
    qm = (re.search(r"\bmake\s+it\s+(\d{1,7})\b", msg)
          or re.search(r"\bchange\s+(?:the\s+)?(?:qty|quantity)\s+(?:to\s+)?(\d{1,7})\b", msg)
          or re.search(r"\bset\s+(?:the\s+)?(?:qty|quantity)\s+(?:to\s+)?(\d{1,7})\b", msg)
          or re.search(r"\b(\d{1,7})\s+shares?\b", msg))
    if qm:
        fields["quantity"] = int(qm.group(1))
    rm = re.search(r"\b(?:rename|call\s+it|name\s+it)\s+(?:to\s+)?(.+)$", msg)
    if rm:
        fields["name"] = _word_cap(rm.group(1).strip(), 80)
    cm = re.search(
        r"\bnotify\s+(?:me\s+)?(?:by|on|via|through)\s+"
        r"(email|push|sms|whatsapp)\b", msg)
    if cm:
        fields["channel"] = cm.group(1)
    return fields


def _patch_dsl_draft(prior: dict, fields: dict):
    """Deep-copy the prior DSL draft and mutate ONLY the named non-structural
    fields, preserving every buy/exit/sell step byte-for-byte. Returns the
    patched draft (same shape propose_dsl_workflow returns), or None on error."""
    import copy
    from datetime import datetime, timezone, timedelta
    try:
        draft = copy.deepcopy(prior)
        steps = draft.get("steps") or []
        if "quantity" in fields and fields["quantity"]:
            q = int(fields["quantity"])
            for s in steps:
                if not isinstance(s, dict) or s.get("step_type") != "action.place_order":
                    continue
                cfg = s.get("config") or {}
                # Only mutate the BUY leg's explicit qty; leave the sell leg's
                # runtime mustache ref ({{ context...holdings...quantity }}) alone.
                if str(cfg.get("side", "")).lower() == "buy" and isinstance(
                    cfg.get("quantity"), (int, float)
                ):
                    cfg["quantity"] = q
                    s["config"] = cfg
        if fields.get("valid_until") is not None:
            v = fields["valid_until"]
            if isinstance(v, int):
                v = (datetime.now(timezone.utc).date() + timedelta(days=v)).isoformat()
            draft["valid_until"] = str(v)
            draft.pop("expires_at", None)
            try:
                from backend.agents.tool_executor import _stamp_expires_at
                _stamp_expires_at(draft)
            except Exception:
                pass
        if fields.get("name"):
            draft["name"] = str(fields["name"])
        elif not (draft.get("name") or "").strip():
            # No model name at all → regenerate from the readback (R10).
            # A model-authored human title is otherwise KEPT across
            # mutations: the description/readback subtitle is always
            # re-derived from the tree, so the conditions can't go stale;
            # the tool description asks the model to re-supply a name when
            # a mutation changes the symbol or meaning.
            _rb = (draft.get("readback") or "").strip()
            _erb = (draft.get("exit_readback") or "").strip()
            if _rb:
                _title = f"{_rb} → {_erb}" if _erb else _rb
                draft["name"] = _word_cap(_title, 90)
        if fields.get("channel"):
            for s in steps:
                if isinstance(s, dict) and s.get("step_type") == "notify.message":
                    cfg = s.get("config") or {}
                    cfg["channel"] = fields["channel"]
                    s["config"] = cfg
        draft["steps"] = steps
        draft["draft_id"] = str(uuid.uuid4())
        draft["created_at"] = datetime.utcnow().isoformat() + "Z"
        draft["_render_hint"] = "workflow_draft_card"
        try:
            from backend.services.backtest_resolvability import check_draft
            bt_ok, bt_blockers = check_draft(steps)
            draft["backtestable"] = bool(bt_ok)
            draft["backtest_blockers"] = bt_blockers
        except Exception:
            pass
        return draft
    except Exception:
        logger.exception("DSL draft patch failed; falling back to re-translation")
        return None


def _tree_has_indicator(tree: Any) -> bool:
    """True when a translated DSL tree (dict, pre-validation) contains at
    least one IndicatorNode, or a PriceNode with offset > 0 — i.e. the
    trigger is timeframe-sensitive. A price lookback ('price 1 bar ago')
    is just as timeframe-sensitive as an indicator: offset=1 means
    "yesterday's close" on daily bars but "5 minutes ago" on 5m bars,
    and the DSL has no way to tell which the user meant without this."""
    if isinstance(tree, dict):
        if tree.get("type") == "indicator":
            return True
        if tree.get("type") == "price" and int(tree.get("offset") or 0) > 0:
            return True
        return any(_tree_has_indicator(v) for v in tree.values())
    if isinstance(tree, list):
        return any(_tree_has_indicator(item) for item in tree)
    return False


def _apply_interval_to_indicators(tree: Any, interval: str) -> None:
    """Walk a translated DSL tree (still a dict — pre-validation) and
    set ``timeframe=interval`` on every IndicatorNode, and on every
    PriceNode with offset > 0, that doesn't already have one. The LLM
    grammar prompt doesn't yet know about non-daily intervals, so the
    user's chat-level choice ('on 15-minute bars', 'every 5 minutes')
    is plumbed in here rather than by re-prompting.

    Daily (the default) is a no-op so already-persisted trees and the
    existing eval suite are byte-for-byte unchanged.
    """
    if not interval or interval == "1d":
        return

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "indicator" and not node.get("timeframe"):
                node["timeframe"] = interval
            elif (
                node.get("type") == "price"
                and int(node.get("offset") or 0) > 0
                and not node.get("timeframe")
            ):
                node["timeframe"] = interval
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(tree)


# ── backtest_dsl_tree ────────────────────────────────────────────────

# Strip innocuous "short ..." phrases before checking for an actual
# short-selling intent word: short-term/short of/shortfall, AND "short"
# used as a crossover-leg adjective ("short SMA/MA/EMA/WMA/MACD crosses
# the long ..." is THE flagship backtest_dsl_tree use case — a bare
# indicator-direction word, not a short-sell request).
_SHORT_BENIGN_RE = re.compile(
    r"short[\s-]*(?:term|of|fall|sma|ma|ema|wma|macd|period|window|"
    r"lookback|leg|line|average|moving)\b",
    re.I,
)
_SHORT_INTENT_RE = re.compile(r"\bshort(?:ing|ed|s)?\b", re.I)


def _mentions_short(text: str) -> bool:
    """True if ``text`` reads as a short-selling request ('short X', 'go
    short', 'sell short', 'shorting', 'short-sell') after stripping the
    benign 'short-term'/'short of'/'shortfall'/'short SMA'-style
    crossover-leg phrasings."""
    if not text:
        return False
    return bool(_SHORT_INTENT_RE.search(_SHORT_BENIGN_RE.sub("", text)))


async def backtest_dsl_tree(args: dict) -> dict:
    """Run a DSL-tree backtest from a natural-language condition.

    Args (all required unless noted):
      condition         — NL trading ENTRY condition only. If the user
                          stated a sell/exit rule too, pass it as
                          ``exit_condition`` — never bake it into this
                          field as an AND, that produces a contradiction.
      primary_symbol    — symbol the trade fires on (e.g. "TCS")
      start_date        — ISO date (optional; defaults to 5y ago)
      end_date          — ISO date (optional; defaults to today)
      interval          — bar interval the backtest runs on
                          (1m/3m/5m/10m/15m/30m/1h/1d/1wk/1mo;
                          aliases 'daily'/'weekly'/'day' supported).
                          Default '1d'. ASK the user if their prompt
                          doesn't pin a timeframe — 'period' on every
                          indicator is counted in BARS of this interval
                          (RSI(14, 15m) ≠ RSI(14, daily)). Intraday
                          intervals have shallow rolling windows
                          (5/15/30m → ~60 days, 1m → ~7 days) and the
                          handler clamps the start_date accordingly,
                          surfacing a diagnostic when it does.
      exit_condition    — Optional NL EXIT condition. When set, the
                          tool translates it to a DSL exit tree and
                          overrides the declarative exit_kind/bars/pct.
      exit_kind         — "n_day_hold" | "stop_loss_pct"
                          (default n_day_hold). Ignored when
                          exit_condition is set.
      exit_bars         — int, used when exit_kind=n_day_hold (default 10)
      exit_pct          — float in 0..1, used when exit_kind=stop_loss_pct
      starting_capital  — ₹, default 100_000
      quantity          — shares per fire, default 10
    """
    args = args or {}
    condition = (args.get("condition") or "").strip()
    primary = (args.get("primary_symbol") or "").strip().upper()
    exit_condition_text = (args.get("exit_condition") or "").strip()
    # Normalize the interval up-front so every downstream branch (date
    # clamping, payload assembly, diagnostics) sees the canonical form.
    from backend.core.data.intervals import (
        is_intraday as _is_intraday,
        max_lookback_days as _max_lookback_days,
        normalize_interval as _normalize_interval,
    )
    interval = _normalize_interval(args.get("interval"))
    if not condition:
        raise ValueError(
            "backtest_dsl_tree needs a 'condition' (natural-language "
            "entry condition like 'Buy TCS when RSI(14) drops below 30')."
        )
    if not primary:
        raise ValueError(
            "backtest_dsl_tree needs a 'primary_symbol' (the symbol the "
            "trade fires on, e.g. TCS)."
        )

    # This engine is LONG-ONLY: `_open_position`/`_close_position_at_price`
    # in workflows/dsl/backtest/engine.py buy at entry and sell at exit —
    # there is no direction/short mechanism anywhere in BacktestRequest or
    # the sim loop (P&L, stop-loss bar-low semantics, and peak tracking all
    # assume a long). Rather than silently running a "short" request long
    # and narrating it as if shorting had been modeled (a mechanics
    # fabrication caught in eval), refuse deterministically. Checked via
    # BOTH the explicit `direction` arg AND a text backstop, because the
    # chat LLM has been observed to drop "short" language before it ever
    # reaches this tool's args.
    direction = str(args.get("direction") or "long").strip().lower()
    if direction == "short" or _mentions_short(condition) or _mentions_short(
        exit_condition_text
    ):
        raise ValueError(
            "backtest_dsl_tree only simulates LONG (buy-then-sell) "
            "positions today — it has no short-selling mechanism, so it "
            "cannot backtest shorting this. Say so plainly; do not run "
            "this as a long backtest and describe it as a short."
        )

    # 51-sweep arg-repair: "I hold 50 INFY at 1400 — backtest a 10%
    # trailing stop" arrives with the TRAILING rule in `condition` (the
    # entry slot), which the semantic validator rightly rejects
    # (position leaves aren't entry logic). With a seeded holding and no
    # exit_condition, an exit-shaped condition IS the exit rule: move it
    # there and disable fresh entries so only the held position is
    # tested (engine-supported — see test_backtest_holding_semantics A3).
    _seeded_exit_repair = False
    if (isinstance(args.get("initial_position"), dict)
            and (args.get("initial_position") or {}).get("quantity")
            and not exit_condition_text
            and re.search(r"\b(?:trail(?:ing)?|stop[- ]?loss|from\s+"
                          r"(?:the\s+)?peak|take[- ]?profit|book\s+"
                          r"profits?|exit|sell)\b", condition, re.I)
            and not re.search(r"\b(?:buy|enter|long|accumulate|add)\b",
                              condition, re.I)):
        exit_condition_text = condition
        # Plain comparison (never-fires) — "crosses above" tripped the
        # self-comparison/tautology detector on retest.
        condition = f"price of {primary} is above 99999999"
        _seeded_exit_repair = True

    try:
        tree, tx_meta = await translate_condition_to_tree(
            condition,
            primary_symbol=primary,
            cache_key="dsl.chat.backtest.v1",
        )
    except TranslationError as exc:
        raise ValueError(
            f"could not translate condition into a DSL tree: {exc}"
        ) from None

    # Date window — default to 5 years ending today. (Was 3y; the other two
    # backtest engines — workflow_backtester + indicator_backtest — already
    # default to 5y, and 3y starved slow signals: a 50/200 golden cross fired
    # only 1 trade in 3y → "insufficient data". Kite serves 5y daily fine.
    # This is only the DEFAULT — an explicit start_date gives any window.)
    _DEFAULT_WINDOW_DAYS = 365 * 5 + 2
    today = date.today()
    try:
        end_d = (
            date.fromisoformat(args["end_date"])
            if args.get("end_date") else today
        )
    except ValueError:
        end_d = today
    try:
        start_d = (
            date.fromisoformat(args["start_date"])
            if args.get("start_date")
            else end_d - timedelta(days=_DEFAULT_WINDOW_DAYS)
        )
    except ValueError:
        start_d = end_d - timedelta(days=_DEFAULT_WINDOW_DAYS)
    if end_d <= start_d:
        end_d = start_d + timedelta(days=365)

    # Honest intraday lookback clamp. yfinance keeps a rolling window
    # for intraday bars (1m → 7d, 5/15/30m → 60d, 1h → 730d). If the
    # caller asked for a longer window than the source can serve, move
    # ``start_d`` up to today − cap and surface a diagnostic string so
    # the UI can explain the truncation rather than silently shipping
    # a backtest on fewer bars than requested.
    interval_diagnostics: list[str] = []
    if _is_intraday(interval):
        cap = _max_lookback_days(interval, has_kite=False)
        if cap is not None:
            earliest = today - timedelta(days=int(cap))
            if start_d < earliest:
                old_start = start_d
                start_d = earliest
                interval_diagnostics.append(
                    f"intraday {interval} data only available from "
                    f"{earliest.isoformat()}; backtest window was clamped "
                    f"(requested {old_start.isoformat()})"
                )
                if end_d <= start_d:
                    end_d = min(today, start_d + timedelta(days=int(cap)))

    # Assumptions the reply MUST surface (never silent). The default
    # n_day_hold exit and any seeded initial position are recorded here
    # and threaded into both the structured payload and summary_text.
    assumptions: list[str] = []
    if _seeded_exit_repair:
        assumptions.append(
            "The stated rule is an EXIT rule on your existing holding — "
            "fresh entries were disabled; only the seeded position is "
            "tested against it."
        )

    # Exit policy — exit_condition (NL) wins over declarative fields
    # so a chat prompt like "buy on RSI<30, sell on RSI>70" gets a
    # real tree exit and not a degenerate AND.
    exit_tx_meta: Optional[dict] = None
    if exit_condition_text:
        try:
            exit_tree_dict, exit_tx_meta = await translate_condition_to_tree(
                exit_condition_text,
                allow_position=True,
                primary_symbol=primary,
                cache_key="dsl.chat.backtest.exit.v1",
            )
        except TranslationError as exc:
            raise ValueError(
                f"could not translate exit_condition into a DSL tree: "
                f"{exc}"
            ) from None
        from backend.workflows.dsl.schema import normalize_tree_aliases
        exit_policy = {
            "kind": "tree",
            "tree": normalize_tree_aliases(exit_tree_dict),
            "exit_at": "next_open",
        }
    else:
        exit_kind_raw = args.get("exit_kind")
        exit_kind = (exit_kind_raw or "n_day_hold").lower()
        if exit_kind not in ("n_day_hold", "stop_loss_pct", "hold_to_end"):
            exit_kind = "n_day_hold"
        if exit_kind == "hold_to_end":
            # Carry the position to the final bar — the buy-and-hold /
            # "don't sell" shape. No early exit is ever taken.
            exit_policy = {"kind": "hold_to_end"}
            assumptions.append(
                "Exit: held to the end of the window (no early sell)."
            )
        elif exit_kind == "n_day_hold":
            bars = int(args.get("exit_bars") or 10)
            exit_policy = {"kind": "n_day_hold", "bars": bars}
            if not exit_kind_raw:
                # Default exit — the user stated no sell rule. Surface it
                # explicitly so the reply never silently ships a hidden
                # 10-bar sale as if it were the user's plan.
                assumptions.append(
                    f"Exit: {bars}-bar hold (assumed) — say 'hold till end' "
                    f"to carry the position to the window end, or give a "
                    f"sell rule."
                )
        else:
            v = float(args.get("exit_pct") or 0.05)
            v = max(0.001, min(0.5, v))
            exit_policy = {"kind": "stop_loss_pct", "value": v}

    # Build BacktestRequest and run engine in a worker thread.
    from backend.workflows.dsl.backtest.engine import run_backtest
    from backend.workflows.dsl.backtest.schema import BacktestRequest
    from backend.workflows.dsl.validators import (
        DSLValidationError, semantic_validate,
    )
    from backend.workflows.dsl.schema import Tree
    from pydantic import TypeAdapter, ValidationError

    # Position sizing (Phase 2.2). Default 'fixed' uses quantity; the others size
    # from equity + the asset's volatility/ATR. Only the keys relevant to the
    # chosen mode are forwarded — the Sizing model fills the rest with defaults.
    sizing_mode = str(args.get("sizing_mode") or "fixed").lower()
    if sizing_mode not in ("fixed", "pct_equity", "vol_target", "atr_risk"):
        sizing_mode = "fixed"
    sizing: dict = {"mode": sizing_mode}
    if sizing_mode == "pct_equity" and args.get("pct") is not None:
        sizing["pct"] = float(args["pct"])
    elif sizing_mode == "vol_target":
        if args.get("target_vol") is not None:
            sizing["target_vol"] = float(args["target_vol"])
        if args.get("vol_lookback") is not None:
            sizing["vol_lookback"] = int(args["vol_lookback"])
    elif sizing_mode == "atr_risk":
        for k, cast in (("risk_pct", float), ("atr_period", int), ("atr_mult", float)):
            if args.get(k) is not None:
                sizing[k] = cast(args[k])

    # Seed an already-owned position at window start ("I hold 50 INFY
    # from ₹1400 — backtest selling at RSI>70"). Only accepted with a
    # positive quantity; avg_price/entry_date are optional.
    initial_position: Optional[dict] = None
    ip_arg = args.get("initial_position")
    if isinstance(ip_arg, dict) and ip_arg.get("quantity"):
        try:
            ip_qty = int(ip_arg["quantity"])
        except (TypeError, ValueError):
            ip_qty = 0
        if ip_qty > 0:
            initial_position = {"quantity": ip_qty}
            if ip_arg.get("avg_price") is not None:
                initial_position["avg_price"] = float(ip_arg["avg_price"])
            if ip_arg.get("entry_date"):
                initial_position["entry_date"] = str(ip_arg["entry_date"])
            _basis_txt = (
                f"₹{initial_position['avg_price']:g}"
                if "avg_price" in initial_position
                else "the window's opening price"
            )
            assumptions.append(
                f"Seeded an existing holding of {ip_qty} {primary} at a "
                f"cost basis of {_basis_txt}; the exit rule is tested "
                f"against that position."
            )

    # 51-sweep: normalize planner-emitted shape aliases AND collapse
    # single-operand and/or before validation — the day-of-week
    # translator wrapped a lone filter in a 1-item AND, and the raw
    # "logic.and expects at least 2 operands" leaked to the user.
    from backend.workflows.dsl.schema import normalize_tree_aliases
    tree = normalize_tree_aliases(tree)

    payload = {
        "tree": tree,
        "primary_symbol": primary,
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
        "starting_capital": float(args.get("starting_capital") or 100_000.0),
        "quantity": int(args.get("quantity") or 10),
        "sizing": sizing,
        "exit_policy": exit_policy,
        "save": False,
        "interval": interval,
    }
    if initial_position is not None:
        payload["initial_position"] = initial_position
    try:
        request = BacktestRequest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"request validation failed: {exc.errors()[0]['msg']}"
        ) from None

    # Belt-and-suspenders: validate the tree separately so the error
    # message is DSL-flavoured rather than Pydantic-generic.
    try:
        parsed_tree = TypeAdapter(Tree).validate_python(payload["tree"])
        semantic_validate(parsed_tree)
    except (DSLValidationError, ValidationError) as exc:
        raise ValueError(f"tree validation failed: {exc}") from None

    try:
        result = await asyncio.to_thread(
            run_backtest, request=request, user_id=0, fetcher=None,
        )
    except ValueError as exc:
        raise ValueError(f"backtest engine: {exc}") from None
    except Exception as exc:  # noqa: BLE001 — surface anything else cleanly
        logger.exception("[chat.dsl.backtest] engine crashed: %s", exc)
        raise ValueError(f"backtest engine crashed: {type(exc).__name__}")

    # Shape the BacktestResult into the same chart-card payload the FE
    # already renders for legacy backtests, PLUS the DSL-specific
    # extras (tree_summary, full trades list) that the card's
    # extended renderer uses when present. We keep render_hint =
    # "indicator_backtest_chart" so existing ChatDemo dispatch is
    # unchanged; the card itself sniffs `tree_summary` and trades to
    # decide which surface to draw.
    metrics = result.metrics
    from backend.services.backtest_metrics import methodology_note
    _method = methodology_note(start=start_d.isoformat(), end=end_d.isoformat())
    # Rigor battery as plain dicts. Deflate the Deflated Sharpe for how many
    # DISTINCT strategy variants this CONVERSATION has backtested (selection-bias
    # guard — tuning one idea in a chat deflates together), then recompute the
    # verdict from the deflated battery so it stays consistent.
    from backend.services.backtest.validation import trust_verdict
    from backend.services.backtest.validation.trials import (
        record_and_deflate, strategy_fingerprint,
    )
    from backend.services.turn_context import trial_group_for
    _fs_dict = metrics.forward_stats.model_dump() if metrics.forward_stats else None
    _mc_dict = metrics.monte_carlo.model_dump() if metrics.monte_carlo else None
    _sp_dict = metrics.sub_periods.model_dump() if metrics.sub_periods else None
    _grp = trial_group_for(None)  # conversation, from turn_context
    if _fs_dict and _grp:
        _fs_dict = record_and_deflate(
            _fs_dict, _grp,
            strategy_fingerprint(
                tree, primary, start_d.isoformat(), end_d.isoformat(), exit_policy,
            ),
        )
    _verdict_dict = trust_verdict(
        forward_stats=_fs_dict, monte_carlo=_mc_dict, sub_periods=_sp_dict,
        total_return_pct=float(metrics.total_return_pct),
        n_trades=metrics.total_trades,
    )
    _verdict_lead = (
        f"**Verdict — {_verdict_dict['label']}.** {_verdict_dict['rationale']}"
        if _verdict_dict else ""
    )
    _psr = _fs_dict.get("psr") if _fs_dict else None
    _nt = (_fs_dict or {}).get("num_trials") or 1
    _dsr = (_fs_dict or {}).get("deflated_sharpe")
    _sizing_txt = ""
    if sizing.get("mode") == "vol_target":
        _sizing_txt = f"Sized to a {sizing.get('target_vol', 0.15):.0%} annualised vol target."
    elif sizing.get("mode") == "atr_risk":
        _sizing_txt = (
            f"Sized by ATR risk ({sizing.get('risk_pct', 0.01):.1%}/trade, "
            f"{sizing.get('atr_mult', 2.0):g}×ATR stop)."
        )
    elif sizing.get("mode") == "pct_equity":
        _sizing_txt = f"Sized at {sizing.get('pct', 0.2):.0%} of equity per entry."
    _assume_txt = ("Assumptions: " + " ".join(assumptions)) if assumptions else ""
    # ── Results TABLE (structured, not a run-on paragraph) ──────────────
    # Verdict + method stay prose; the numbers go in a compact two-column
    # table. Signed values keep +/- so the FE colours them green / red.
    # No `**bold**` in table cells — the FE renders cells as raw strings, so
    # emphasis leaks literal asterisks; signed values get gain/loss colouring.
    _mrows: list[tuple[str, str]] = [
        ("Strategy return (whole account)", f"{metrics.total_return_pct:+.1f}%"),
    ]
    if metrics.return_on_deployed_pct is not None:
        # Un-annualized, dollar-weighted return on capital actually put at
        # risk — distinct from the whole-account figure above, which is
        # diluted by however long capital sat idle in cash. Shown together
        # with capital_utilization_pct so a rare-trigger strategy can't
        # read as "always performs this well" from this row alone.
        _mrows.append((
            "Return on capital deployed",
            f"{metrics.return_on_deployed_pct:+.1f}%",
        ))
    if metrics.capital_utilization_pct is not None:
        _mrows.append((
            "Capital deployed",
            f"{metrics.capital_utilization_pct:.0f}% of the window",
        ))
    if metrics.benchmark_return_pct is not None:
        _mrows.append(("Buy & hold", f"{metrics.benchmark_return_pct:+.1f}%"))
    _mrows.append(("Trades", f"{metrics.total_trades}"))
    _mrows.append(("Max drawdown", f"{metrics.max_drawdown_pct:.1f}%"))
    _mrows.append(("Win rate", f"{metrics.win_rate_pct:.0f}%"))
    if metrics.sharpe_ratio is not None:
        _mrows.append(("Sharpe", f"{metrics.sharpe_ratio:.2f}"))
    if isinstance(_psr, (int, float)):
        _mrows.append(("PSR", f"{_psr:.0%}"))
    if _nt > 1 and isinstance(_dsr, (int, float)):
        _mrows.append((f"Deflated Sharpe ({_nt} variants)", f"{_dsr:.0%}"))
    _table = "| Metric | Value |\n| --- | --- |\n" + "\n".join(
        f"| {_k} | {_v} |" for _k, _v in _mrows
    )
    _tail_bits = [t for t in (_sizing_txt, _assume_txt) if t]
    _tail = (" " + " ".join(_tail_bits)) if _tail_bits else ""
    _method_line = (
        f"_Results are {_method['costs']}, on {_method['basis']}.{_tail}_"
    )
    summary = "\n\n".join(p for p in (_verdict_lead, _table, _method_line) if p)

    # Build the legacy-shaped signals list (buy + sell as separate
    # entries) AND a richer per-trade list so the card can show
    # entry/exit pairs.
    signals: list[dict] = []
    rich_trades: list[dict] = []
    for t in result.trades:
        signals.append({
            "t": t.entry_date.isoformat(),
            "side": "buy",
            "price": float(t.entry_price),
            "indicator_value": None,
        })
        if t.exit_date is not None and t.exit_price is not None:
            signals.append({
                "t": t.exit_date.isoformat(),
                "side": "sell",
                "price": float(t.exit_price),
                "indicator_value": None,
            })
        rich_trades.append({
            "trade_id": t.trade_id,
            "entry_date": t.entry_date.isoformat(),
            "entry_price": float(t.entry_price),
            "exit_date": t.exit_date.isoformat() if t.exit_date else None,
            "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "quantity": int(t.quantity),
            "net_pnl": float(t.net_pnl),
            "return_pct": float(t.return_pct),
            "exit_reason": t.exit_reason,
        })

    n_wins = metrics.winning_trades
    n_trades = metrics.total_trades

    return {
        "_render_hint": "indicator_backtest_chart",
        "symbol": result.request.primary_symbol,
        # Compound DSL trees don't fit the (indicator,period,operator,
        # threshold) shape. The FE's IndicatorBacktestCard now checks
        # tree_summary FIRST and falls back to indicator-based when
        # that field is absent — set sane no-ops here.
        "indicator": "compound",
        "indicator_period": 0,
        "operator": "tree",
        "threshold": 0.0,
        "period_label": (
            f"{start_d.isoformat()} → {end_d.isoformat()}"
        ),
        # Map equity curve into both panels so the FE has something
        # in each thumbnail slot.
        "price_curve": [
            {"t": p.date.isoformat(), "v": float(p.equity)}
            for p in result.equity_curve
        ],
        "equity_curve": [
            {"t": p.date.isoformat(), "v": float(p.equity)}
            for p in result.equity_curve
        ],
        "indicator_curve": [],
        "signals": signals,
        "metrics": {
            # Legacy-shape keys the existing IndicatorBacktestCard reads.
            "total_return_pct": float(metrics.total_return_pct),
            "cagr_pct": float(metrics.cagr_pct),
            "max_drawdown_pct": float(metrics.max_drawdown_pct),
            "hit_rate_pct": float(metrics.win_rate_pct),
            "sharpe": metrics.sharpe_ratio,
            "sortino": metrics.sortino_ratio,
            "n_trades": int(n_trades),
            "n_wins": int(n_wins),
            "return_on_deployed_pct": metrics.return_on_deployed_pct,
            "capital_utilization_pct": metrics.capital_utilization_pct,
            "benchmark_return_pct": metrics.benchmark_return_pct,
            "starting_capital": float(request.starting_capital),
            "ending_value": float(metrics.ending_value),
            # Statistical-rigor battery (PSR/DSR/MinTRL · Monte-Carlo ·
            # sub-periods · Trust verdict) — same Trust panel as the
            # workflow-backtest card; DSR + verdict are trial-deflated above.
            "forward_stats": _fs_dict,
            "monte_carlo": _mc_dict,
            "sub_periods": _sp_dict,
            "trust_verdict": _verdict_dict,
        },
        "bench_buy_hold_return_pct": metrics.benchmark_return_pct,
        "methodology": _method,
        "summary_text": summary,
        # Explicit, non-silent assumptions the reply MUST state (default
        # exit policy, seeded position). Empty when the user pinned every
        # parameter. See prompts/modules/backtest.md → state-the-assumption.
        "assumptions": assumptions,
        # DSL-native fields — present ONLY on DSL responses. The card
        # uses these to render the readback as the title and (later)
        # a trades-list expansion.
        "tree_summary": result.tree_summary,
        "trades": rich_trades,
        "interval": interval,
        "interval_notes": interval_diagnostics,
        "diagnostics": {
            "bars_evaluated": result.diagnostics.bars_evaluated,
            "fire_bars": result.diagnostics.fire_bars,
            "unknown_value_bars": result.diagnostics.unknown_value_bars,
            "interval": interval,
            "interval_notes": interval_diagnostics,
        },
        "translation_meta": tx_meta,
        "exit_translation_meta": exit_tx_meta,
    }


# ── propose_dsl_workflow ────────────────────────────────────────────


async def propose_dsl_workflow(args: dict) -> dict:
    """Build a workflow draft whose entry trigger is a DSL
    ``trigger.compound`` tree, with an optional exit branch driven by
    ``trigger.exit_compound`` for runtime-position-aware exits.

    Args:
      condition         — NL entry condition (required)
      name              — short human label for the workflow
      primary_symbol    — symbol the action targets (required)
      action_kind       — "notify_only" (default) | "buy_market" | "buy_limit"
      quantity          — int, only used when action_kind starts with 'buy'
      limit_price       — float, only used when action_kind=buy_limit
      exit_condition    — OPTIONAL NL exit condition. When set, the tool
                          translates it to a DSL tree (with PositionNode
                          leaves allowed — entry_price, unrealised_pct,
                          bars_held, drawdown_from_peak_pct, ...) and
                          emits a SECOND branch:
                              trigger.exit_compound + fetch.portfolio +
                              action.place_order(sell, qty=runtime ref).
                          Use for prompts like "buy X when RSI<30, sell
                          when price > upper Bollinger band" or "exit
                          when drawdown from peak >= 5%".
    """
    args = args or {}

    # ── PATCH fast-path (P1) ─────────────────────────────────────────
    # A non-structural amendment (expiry/qty/notes/channel) on an EXISTING
    # DSL draft mutates the prior steps in place instead of re-translating
    # from the amendment text — re-translation silently dropped the buy/
    # exit/sell legs (action_kind fell back to notify_only). __prior_dsl_draft
    # and __user_message are injected post-validation by validation_handler.
    _prior = args.get("__prior_dsl_draft")
    if isinstance(_prior, dict) and (_prior.get("steps") or []):
        _fields = _is_nonstructural_dsl_amendment(args.get("__user_message") or "")
        if _fields:
            _patched = _patch_dsl_draft(_prior, _fields)
            if _patched is not None:
                logger.info(
                    "propose_dsl_workflow PATCH applied (fields=%s) — preserved "
                    "%d steps", sorted(_fields), len(_patched.get("steps") or []))
                return _patched

    condition = (args.get("condition") or "").strip()
    primary = (args.get("primary_symbol") or "").strip().upper()
    label = (args.get("name") or "").strip() or f"{primary} compound trigger"
    action_kind = (args.get("action_kind") or "notify_only").lower()

    # Price/condition ALERTS are not available (product decision). A notify-only
    # DSL workflow has no wired delivery channel, so rather than render a card
    # that never notifies, refuse deterministically — this closes BOTH the path
    # where the LLM picks action_kind='notify_only' itself AND the forced-alert
    # path. An order automation (action_kind='buy_market'/'buy_limit') is
    # unaffected. Checked here (not just in the prompt) because the LLM has been
    # observed to draft an alert despite the system-prompt boundary.
    if action_kind == "notify_only":
        raise ValueError(
            "Price/condition ALERTS and notifications aren't available right "
            "now — Pivot doesn't send alerts, pings, or 'tell me when' "
            "messages. Do NOT draft an alert/notify workflow. State this "
            "boundary in one plain line. Only if the user wants to ACT at that "
            "level, offer a broker-held GTT/threshold ORDER instead — never for "
            "a 'just alert / don't trade' ask."
        )
    exit_condition_text = (args.get("exit_condition") or "").strip()
    # User-specified bar interval flows onto every IndicatorNode in the
    # translated entry/exit trees. Default '1d' keeps existing daily
    # workflows byte-for-byte unchanged.
    from backend.core.data.intervals import normalize_interval as _normalize_interval
    interval = _normalize_interval(args.get("interval"))
    # Normalize "no exit" placeholders the LLM occasionally emits when
    # there isn't an exit condition. Without this, the translator tries
    # to translate the placeholder and produces a vacuous tree
    # (1.0 == 1.0) → "translated exit tree is invalid" error.
    if exit_condition_text.lower() in {
        "none", "null", "n/a", "na", "no exit", "no", "—", "-",
    }:
        exit_condition_text = ""
    if not condition:
        raise ValueError(
            "propose_dsl_workflow needs a 'condition' (NL entry "
            "condition such as 'when RSI(14) < 30 and price > SMA(50)')."
        )
    if not primary:
        raise ValueError(
            "propose_dsl_workflow needs a 'primary_symbol' — the "
            "symbol the action fires on."
        )

    # Early-bail: trailing-stop / exit-only intents on a holding
    # belong in propose_holding_action, not DSL. The DSL requires
    # an entry condition; "set 2% trailing stop on my INFY" has no
    # entry. The LLM keeps picking DSL anyway, so refuse here with
    # a structured route hint.
    _COMBINED = (condition + " " + exit_condition_text).lower()
    _IS_TRAILING_STOP = bool(re.search(
        r"\btrailing\s+(?:stop|sl)|\btrail\s+(?:a\s+)?\d|"
        r"\b\d+%?\s+from\s+(?:peak|high|top)|"
        r"\bdrawdown\s+from\s+peak\b",
        _COMBINED,
    ))
    _HAS_HOLDING_REF = bool(re.search(
        r"\b(?:my|existing|current)\s+(?:position|holding|stake)\b|"
        r"\bon\s+my\s+\w+\s+(?:position|holding|stake)?\b",
        _COMBINED,
    ))
    _MISSING_ENTRY_VERB = not re.search(
        r"\b(?:buy|enter|long|when|if|whenever|crosses?|>|<|"
        r"above|below|reaches?|hits?|breaches?)\b",
        condition.lower(),
    )
    if _IS_TRAILING_STOP and (_HAS_HOLDING_REF or _MISSING_ENTRY_VERB):
        raise ToolRedirect(
            "propose_dsl_workflow needs an ENTRY condition (buy/enter "
            "trigger), but the prompt is exit-only / a trailing stop "
            "on an existing holding. Use propose_holding_action with "
            "action_kind='set_stoploss' and sl_offset_pct=N for "
            "trailing-percentage stops. If this is part of a fresh "
            "buy-entry workflow, include both entry AND exit "
            "conditions in this tool's args (condition='when X', "
            "exit_condition='trail N% from peak').",
            redirect_to="propose_holding_action",
        )

    # Schedule-shaped condition or exit_condition — the DSL grammar
    # expects PRICE/INDICATOR/AGGREGATE leaves, not scheduling. When
    # the user packs a time-anchored phrase ("every Monday at open
    # buy 5 NIFTYBEES" / "on Friday close squareoff full NIFTYBEES")
    # into the condition or exit slot, the translator fails with
    # tautology errors. Detect and refuse with structured route hint.
    _SCHED_RE = re.compile(
        r"\b(?:every\s+(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|"
        r"thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|"
        r"weekday|day|week|month)|"
        r"on\s+(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|"
        r"thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)|"
        r"squareoff|square[\s-]off|"
        r"at\s+(?:market\s+)?(?:open|close)|"
        r"\d{1,2}:\d{2}\s*(?:am|pm|ist)?)",
        re.IGNORECASE,
    )
    if (
        (_SCHED_RE.search(condition) and not _has_indicator_or_price_word(condition))
        or (
            exit_condition_text
            and _SCHED_RE.search(exit_condition_text)
            and not _has_indicator_or_price_word(exit_condition_text)
        )
    ):
        raise ToolRedirect(
            "propose_dsl_workflow can only translate price / "
            "indicator / aggregate conditions, NOT scheduling phrases. "
            "The prompt has a time-anchored leg (\"every Monday at "
            "open\" / \"on Friday close\" / \"squareoff\"). Use "
            "propose_workflow with one branch per time-anchored leg "
            "(trigger.schedule + action.* per branch).",
            redirect_to="propose_workflow",
        )

    # External-event-trigger detector — global price (crypto/forex/global
    # commodity), earnings beats/misses, and webhook delivery are NOT DSL
    # condition shapes. The DSL grammar covers price/indicator/aggregate
    # leaves on the primary equity symbol; non-Kite assets and event
    # triggers belong in propose_workflow with the matching trigger.* /
    # notify.webhook step. Catch the obvious phrasings here so the user
    # gets a clean route hint instead of a translator failure.
    _EXT_EVENT_RE = re.compile(
        r"\b(?:bitcoin|btc|ethereum|eth|"
        r"usdinr|eurusd|gbpusd|forex|"
        r"wti\s+crude|brent|xauusd|xagusd|"
        r"earnings\s+(?:beat|miss|meet)|"
        r"beats?\s+(?:eps|earnings)|misses?\s+(?:eps|earnings)|"
        r"post\s+to\s+(?:my\s+)?(?:webhook|endpoint|url)|"
        r"ping\s+(?:my\s+)?(?:webhook|endpoint|url))\b",
        re.IGNORECASE,
    )
    if _EXT_EVENT_RE.search(condition) or (
        exit_condition_text and _EXT_EVENT_RE.search(exit_condition_text)
    ):
        raise ValueError(
            "propose_dsl_workflow translates only price / indicator / "
            "aggregate conditions on a single equity symbol. Global "
            "crypto / forex / non-Kite-commodity prices belong in "
            "propose_workflow with a trigger.global_price step; earnings "
            "beat/miss/meet asks belong in propose_workflow with a "
            "trigger.earnings step; webhook delivery is a notify.webhook "
            "action step. Re-route via propose_workflow."
        )

    # Multi-trigger semicolon detector — "Every Monday at open buy 5
    # NIFTYBEES; on Friday close squareoff full NIFTYBEES" packs TWO
    # time-anchored triggers into one condition string. DSL is
    # single-trigger. Refuse and point at propose_workflow.
    _SEMI_PARTS = [p.strip() for p in re.split(r"[;]+", condition) if p.strip()]
    if len(_SEMI_PARTS) >= 2:
        # Each part has its own time/condition anchor → looks like
        # branches, not a single compound condition.
        _ANCHOR_RE = re.compile(
            r"\b(?:every|at\s+(?:open|close|\d)|"
            r"on\s+(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|"
            r"thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)|"
            r"when|if)\b",
            re.IGNORECASE,
        )
        if all(_ANCHOR_RE.search(p) for p in _SEMI_PARTS):
            raise ToolRedirect(
                f"propose_dsl_workflow is single-trigger but the "
                f"prompt has {len(_SEMI_PARTS)} semicolon-separated "
                "trigger clauses, each with its own anchor (time / "
                "schedule / condition). Use propose_workflow with "
                "one branch per clause — each branch is a "
                "(trigger.* + action.*) pair.",
                redirect_to="propose_workflow",
            )

    # ── Multi-symbol guard ────────────────────────────────────────
    # propose_dsl_workflow is SINGLE-ACTION-SYMBOL: one entry trigger
    # fires actions on the primary symbol, optionally with one exit
    # branch on the same symbol.
    #
    # Two failure shapes to distinguish:
    #
    # (1) Multi-ACTION ticker — "buy RELIANCE, TCS and BAJFINANCE when
    #     they drop 2% from open". The user expects orders on ALL named
    #     symbols. The DSL would silently use only the primary, dropping
    #     the others. → refuse and route to propose_workflow.
    #
    # (2) Cross-symbol TRIGGER — "buy 10 HDFCBANK when ICICIBANK drops
    #     3% intraday". The user expects ONE action (HDFCBANK) gated by
    #     a condition on a different symbol (ICICIBANK). The DSL's
    #     PriceLeaf / IndicatorLeaf grammar accepts arbitrary symbols
    #     on leaves, so this IS supported. Refusing here forces the LLM
    #     into prose and disappoints the user.
    #
    # Heuristic: only fire the guard when MULTIPLE distinct tickers
    # appear immediately AFTER an action verb (buy/sell). A single
    # action ticker + condition tickers elsewhere is fine.
    # C6: action tickers frequently live ONLY in the original user
    # prompt ("buy RELIANCE, TCS and BAJAJFIN when NIFTY rises 1%"),
    # never in the `condition` arg the model passes here. Scan the
    # threaded user message (uppercased) too — otherwise the guard sees
    # one symbol, builds a single-ticker draft, and silently drops the
    # rest (the "applies to RELIANCE only" UI bug).
    user_msg = (args.get("__user_message") or "").upper()
    action_tickers = _action_tickers_in(condition, exit_condition_text, user_msg)
    extras = [t for t in action_tickers if t != primary]
    if extras:
        all_named = sorted(set([primary] + action_tickers))
        raise ToolRedirect(
            f"propose_dsl_workflow is single-symbol but the request "
            f"names multiple ACTION tickers ({', '.join(all_named)}). "
            f"Use propose_workflow with "
            f"action.allocate_notional(symbols=[{', '.join(all_named)}]) "
            f"so ONE trigger fans the order across every named symbol. "
            f"Do NOT build for just the first symbol and tell the user "
            f"to duplicate the card.",
            redirect_to="propose_workflow",
        )

    try:
        tree, tx_meta = await translate_condition_to_tree(
            condition,
            primary_symbol=primary,
            cache_key="dsl.chat.propose.v1",
        )
    except TranslationError as exc:
        raise ValueError(
            f"could not translate condition into a DSL tree: {exc}"
        ) from None

    # Always-ask the timeframe: if the user didn't name one (the chat loop
    # strips a guessed `interval` for this tool when the message has no
    # timeframe) and the entry tree actually uses an indicator, raise so the
    # LLM asks — never build an indicator trigger on a silent daily default.
    raw_interval = (args.get("interval") or "").strip()
    if not raw_interval and _tree_has_indicator(tree):
        raise ValueError(
            "propose_dsl_workflow: timeframe (bar interval) is required for an "
            "indicator condition, or a price condition that looks back N bars "
            "(e.g. 'lower than it was N minutes ago'). Call ASK_USER first: "
            "ask 'Which timeframe — 1m / 5m / 15m / 30m / 1h / daily / weekly "
            "/ monthly?'. Do NOT default to daily — the indicator period, or "
            "the price lookback, counts BARS of the chosen interval."
        )

    # Overlay the user-specified interval on every IndicatorNode in the
    # translated tree (the LLM grammar prompt doesn't know about it yet,
    # so we patch the dict in-place). Non-daily defaults flow through
    # to the live engine + readback (e.g. "RSI on 15m bars").
    _apply_interval_to_indicators(tree, interval)

    # Validate the tree before we wrap it in a workflow step.
    from backend.workflows.dsl.schema import Tree
    from backend.workflows.dsl.validators import (
        DSLValidationError, semantic_validate,
    )
    from pydantic import TypeAdapter, ValidationError
    try:
        parsed = TypeAdapter(Tree).validate_python(tree)
        semantic_validate(parsed)
    except (DSLValidationError, ValidationError) as exc:
        raise ValueError(f"translated tree is invalid: {exc}") from None

    from backend.workflows.dsl.readback import tree_to_english
    readback = tree_to_english(parsed)

    # ── Optional exit-tree translation (allow_position=True) ──
    exit_tree = None
    exit_tx_meta = None
    exit_readback = None
    if exit_condition_text:
        # Fast path: parse the common position-exit phrasings deterministically
        # (no LLM hop). Falls through to the LLM translator only for shapes the
        # parser doesn't recognise, and that call is TIME-CAPPED so a hung
        # provider can't stall the turn ~2 minutes. On any failure we fall back
        # to the deterministic leaf rather than refusing a supported exit shape.
        exit_tree = _deterministic_position_exit(exit_condition_text)
        if exit_tree is None:
            try:
                exit_tree, exit_tx_meta = await asyncio.wait_for(
                    translate_condition_to_tree(
                        exit_condition_text,
                        allow_position=True,
                        primary_symbol=primary,
                        cache_key="dsl.chat.propose.exit.v1",
                    ),
                    timeout=25,
                )
            except (TranslationError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                exit_tree = _deterministic_position_exit(
                    exit_condition_text, force=True,
                )
                if exit_tree is None:
                    raise ValueError(
                        f"could not translate exit_condition into a DSL tree: {exc}"
                    ) from None
                logger.info(
                    "exit translate failed (%s); used deterministic leaf for %r",
                    type(exc).__name__, exit_condition_text[:60],
                )
        # Overlay user-specified interval on indicator leaves in the
        # exit tree too (same defence-in-depth as the entry tree above).
        _apply_interval_to_indicators(exit_tree, interval)
        try:
            parsed_exit = TypeAdapter(Tree).validate_python(exit_tree)
            semantic_validate(parsed_exit, allow_position=True)
        except (DSLValidationError, ValidationError) as exc:
            # Last-ditch deterministic leaf before refusing.
            fb = _deterministic_position_exit(exit_condition_text, force=True)
            if fb is not None and fb is not exit_tree:
                try:
                    _apply_interval_to_indicators(fb, interval)
                    parsed_exit = TypeAdapter(Tree).validate_python(fb)
                    semantic_validate(parsed_exit, allow_position=True)
                    exit_tree = fb
                except (DSLValidationError, ValidationError):
                    raise ValueError(f"translated exit tree is invalid: {exc}") from None
            else:
                raise ValueError(
                    f"translated exit tree is invalid: {exc}"
                ) from None
        exit_readback = tree_to_english(parsed_exit)

    # Build the entry action step. For v1 we only support three:
    #   notify_only  → notify.message (push channel)
    #   buy_market   → action.place_order(side=buy, order_type=market)
    #   buy_limit    → action.place_order(side=buy, order_type=limit)
    if action_kind not in ("notify_only", "buy_market", "buy_limit"):
        action_kind = "notify_only"

    # There is no short/sell ENTRY action in this v1 schema (only buy_
    # market/buy_limit/notify_only) — the same long-only gap already
    # refused honestly in backtest_dsl_tree (task #6, 2026-07-14). A
    # "short X" entry condition must never silently register a BUY.
    if (action_kind in ("buy_market", "buy_limit")
            and (_mentions_short(condition) or _mentions_short(label))):
        raise ValueError(
            "propose_dsl_workflow: this entry action would place a BUY "
            "order, but the request describes a SHORT/sell entry — "
            "short-entry automations aren't supported yet. Do not "
            "silently register a buy for a short ask."
        )

    # Refuse silent qty=1 default for buy actions. The user must
    # have specified a quantity (the LLM should have asked first).
    # notify_only is exempt because no order is placed.
    raw_qty = args.get("quantity")
    if action_kind in ("buy_market", "buy_limit"):
        if raw_qty is None or (isinstance(raw_qty, (int, float)) and int(raw_qty) <= 0):
            raise ValueError(
                "propose_dsl_workflow: 'quantity' is required when "
                f"action_kind='{action_kind}'. Call ASK_USER first: "
                "'How many shares per fire?'. Do NOT default to 1 — "
                "silent defaults have produced wrong-size trades."
            )
    qty = int(raw_qty) if raw_qty is not None else 1
    limit_px = args.get("limit_price")

    if action_kind == "notify_only":
        entry_action = {
            "step_type": "notify.message",
            "config": {
                "channel": "push",
                "message": (
                    f"{label} fired — entry condition: {readback}"
                ),
            },
        }
    elif action_kind == "buy_market":
        entry_action = {
            "step_type": "action.place_order",
            "config": {
                "symbol": primary,
                "side": "buy",
                "quantity": qty,
                "order_type": "market",
                "product": "CNC",
            },
        }
    else:   # buy_limit
        if limit_px is None:
            raise ValueError(
                "buy_limit action requires 'limit_price'"
            )
        entry_action = {
            "step_type": "action.place_order",
            "config": {
                "symbol": primary,
                "side": "buy",
                "quantity": qty,
                "order_type": "limit",
                "limit_price": float(limit_px),
                "product": "CNC",
            },
        }

    # ── Assemble steps[] ──
    steps: list[dict] = [
        {
            "step_type": "trigger.compound",
            "config": {
                "entry": tree,
                "symbol": primary,
                "exchange": "NSE",
            },
        },
        entry_action,
    ]

    # Optional exit branch — only if an exit_condition was supplied AND
    # the entry actually opens a position (notify_only has nothing to
    # exit, so skip the exit branch in that case).
    if exit_tree is not None and action_kind != "notify_only":
        exit_trigger_idx = len(steps)
        fetch_portfolio_idx = exit_trigger_idx + 1
        steps.extend([
            {
                "step_type": "trigger.exit_compound",
                "config": {
                    "entry": exit_tree,
                    "target_symbol": primary,
                },
            },
            {
                "step_type": "fetch.portfolio",
                "config": {},
            },
            {
                "step_type": "action.place_order",
                "config": {
                    "symbol": primary,
                    "side": "sell",
                    # Runtime reference — sell whatever quantity is
                    # currently held in this symbol. fetch.portfolio
                    # populated it at index `fetch_portfolio_idx`.
                    "quantity": (
                        "{{ context." + str(fetch_portfolio_idx)
                        + ".holdings." + primary + ".quantity }}"
                    ),
                    "order_type": "market",
                    "product": "CNC",
                },
            },
        ])

    description = f"Entry: {readback}"
    if exit_readback:
        description += f" · Exit: {exit_readback}"

    # Title: the MODEL-authored `name` wins (short human label — the card
    # subtitle carries the exact regenerated readback, so a friendly name
    # can't hide the conditions). The R10 readback title is the FALLBACK
    # for calls that omitted a name — the "AXISBANK price below ₹4"
    # stale-name freeze can't recur because description/readback below
    # are always re-derived from the tree.
    _model_name = str(args.get("name") or "").strip()
    if _model_name:
        label = _word_cap(_model_name, 60)
    else:
        _readback_title = readback.strip()
        if exit_readback:
            _readback_title = f"{_readback_title} → {exit_readback.strip()}"
        # Keep it a short label: prefix the symbol if not already present.
        if primary and primary.upper() not in _readback_title.upper():
            _readback_title = f"{primary}: {_readback_title}"
        label = _word_cap(_readback_title, 90) or label

    valid_until_raw = (args.get("valid_until") or "").strip() or None
    _model_summary = str(args.get("summary") or "").strip()
    draft = {
        "_render_hint": "workflow_draft_card",
        "draft_id": str(uuid.uuid4()),
        "name": label,
        "description": description,
        **({"summary": _model_summary[:400]} if _model_summary else {}),
        "steps": steps,
        "readback": readback,
        "exit_readback": exit_readback,
        "translation_meta": tx_meta,
        "exit_translation_meta": exit_tx_meta,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    if valid_until_raw:
        draft["valid_until"] = valid_until_raw
    # P1.10: a peak/trailing exit leaf (drawdown_from_peak_pct /
    # peak_unrealised_pct) implies a ratchet the LIVE executor doesn't yet
    # do — it places the initial stop and re-ratcheting is backtest-only.
    # Force the disclosure onto the card's warnings so it can't be swallowed
    # by the prose summariser (which kept reducing it to "Drafted.").
    _exit_blob = json.dumps(exit_tree or {})
    if "drawdown_from_peak_pct" in _exit_blob or "peak_unrealised_pct" in _exit_blob:
        draft.setdefault("warnings", []).append(
            "Trailing/peak exit: the ratchet is fully modeled in backtests. "
            "Live, this registers the initial stop — live re-ratcheting on "
            "new highs is coming, not wired yet."
        )
    # R4a: pre-flight backtest resolvability so the FE knows whether
    # to surface the Backtest button — and so the runtime float-cast
    # error never fires for an unresolvable Mustache ref.
    try:
        from backend.services.backtest_resolvability import (
            check_draft, check_live_fireable,
        )
        bt_ok, bt_blockers = check_draft(steps)
        draft["backtestable"] = bool(bt_ok)
        draft["backtest_blockers"] = bt_blockers
        draft["live_warnings"] = check_live_fireable(steps)
    except Exception:
        draft["backtestable"] = True
        draft["backtest_blockers"] = []
    # R4b follow-up: derive expires_at from valid_until in one place.
    try:
        from backend.agents.tool_executor import _stamp_expires_at
        _stamp_expires_at(draft)
    except Exception:
        pass
    return draft
