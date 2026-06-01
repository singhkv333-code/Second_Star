"""Server-side workflow-skeleton fast-path.

When the user describes a SIMPLE single-trigger automation in canonical
phrasing (scheduled SIP, RSI-threshold buy/sell, price-cross buy/sell),
emit the WorkflowDraft directly — no LLM hop. This mirrors the
indicator-backtest fast-path: same UX, microseconds instead of 17 seconds.

Canonical shapes covered:

  A. Scheduled buy/sell:
     "(buy|sell) N SYMBOL every (weekday|<DOW>|day) at HH:MM"
     "every (weekday|<DOW>) at HH:MM (buy|sell) N SYMBOL"

  B. Indicator-threshold (RSI/SMA/EMA) buy/sell:
     "(buy|sell) N SYMBOL when[ever] (its )?RSI (drops|falls|<) below VAL"
     "(buy|sell) N SYMBOL when[ever] (its )?RSI (rises|>) above VAL"
     "(buy|sell) N SYMBOL when[ever] it crosses (above|below) VAL (sma|ema)"

  C. Price-threshold buy/sell:
     "(buy|sell) N SYMBOL when[ever] (its )?price (drops|falls|<) below VAL"
     "(buy|sell) N SYMBOL when[ever] (its )?price (rises|>) above VAL"

Out of scope (kept on the LLM path):
  - Multi-trigger / multi-branch agents
  - Conditional-with-SL ("buy 3, with 2% stop loss")
  - "Sell my <SYM>" — needs fetch.portfolio + ref resolution
  - Anything with conditions (buying-power, time-window, etc.)

The bar for adding a pattern here is: predictable phrasing + canonical
2-step output (trigger + action). Anything fuzzier stays on the model.
"""
from __future__ import annotations

import re
from typing import Any, Optional


# ── Token-shape helpers ────────────────────────────────────────────


# Indian tickers are 3–15 alphanumerics; allow the hyphen for a few ETF
# names (NETFNIFTY-EQ etc.). Excluded: pure numbers, common stop words.
_SYMBOL_RE = r"[A-Z][A-Z0-9\-_]{1,15}"

# Tokens that match the symbol regex but are never tickers. The
# parsers reject any extracted symbol in this set so phrases like
# "buys 3 shares when price drops" don't pick up "shares" as the
# symbol — they simply don't match and we fall through to the LLM.
_SYMBOL_BLOCKLIST: frozenset[str] = frozenset({
    "SHARES", "SHARE", "STOCKS", "STOCK", "LOTS", "LOT", "UNITS",
    "UNIT", "QUANTITY", "QTY", "ORDER", "ORDERS", "TRADE", "TRADES",
    "MARKET", "LIMIT", "OPEN", "CLOSE", "HIGH", "LOW",
    "PRICE", "PRICES", "VALUE", "AT", "ON", "OF", "IN",
    # "sell my ENTIRE COALINDIA position" — these modifier words look
    # like tickers to the regex but aren't. Without the blocklist the
    # _try_sell_my parser grabs the word AFTER "my" / "the" verbatim
    # and emits symbol="ENTIRE" everywhere, producing a draft that
    # references a non-existent symbol.
    "ENTIRE", "FULL", "WHOLE", "ALL", "COMPLETE", "TOTAL", "EVERY",
    "HOLDING", "HOLDINGS", "POSITION", "POSITIONS",
    "MY", "THE", "THIS", "THAT", "IT", "THEM",
    "NOW", "TODAY", "YESTERDAY", "TOMORROW",
    # Indicator names that the macro doesn't whitelist — if one of
    # these slips into the symbol slot, we'd ship a draft for a
    # non-symbol like "MACD".
    "RSI", "SMA", "EMA", "MACD", "ADX", "ATR", "BB", "VIX",
})

# Token regex used by the cross-symbol guard. 3-15 uppercase chars +
# optional digits / dashes covers NSE ticker shapes (HDFCBANK, M&M
# becomes M_M via tokenization, NIFTYBEES, etc.) without matching
# common words like "AND", "OR". The 2-char minimum AND blocklist
# filter together keep this conservative — we'd rather miss a real
# multi-symbol intent than corrupt a single-symbol one. Module-level
# constant so it compiles once.
_TICKER_TOKEN_RE_SKELETON = re.compile(r"\b[A-Z][A-Z0-9_]{2,15}\b")


def _distinct_ticker_tokens(message: str) -> set[str]:
    """Return distinct ticker-shaped tokens in `message`, filtered
    against the blocklist. Used to detect 2+-ticker prompts that the
    single-symbol skeleton would silently collapse."""
    found: set[str] = set()
    for tok in _TICKER_TOKEN_RE_SKELETON.findall(message or ""):
        if tok in _SYMBOL_BLOCKLIST:
            continue
        # Common all-caps non-tickers that show up in prompts.
        if tok in {
            "AND", "OR", "NOT", "BUY", "SELL", "BUYS", "SELLS", "SIP",
            "GTT", "SL", "OCO", "EOD", "NSE", "BSE", "MCX", "INR",
            "IST", "AM", "PM", "USD", "INR", "RS",
        }:
            continue
        found.add(tok)
    return found


# Day-of-week → cron DOW digit
_DOW_TO_CRON = {
    "monday": "1", "mon": "1",
    "tuesday": "2", "tue": "2",
    "wednesday": "3", "wed": "3",
    "thursday": "4", "thu": "4",
    "friday": "5", "fri": "5",
}


def _parse_time_to_cron_fields(time_str: str) -> Optional[tuple[str, str]]:
    """'09:15' → ('15', '9'). '0915' → ('15', '9'). Returns
    (minute, hour) cron fields or None if unparseable."""
    s = time_str.strip().replace(" ", "").upper()
    # Strip am/pm hints — they're rare in market-hour contexts and the
    # caller's regex doesn't currently extract them. If a user writes
    # "9 AM" we treat as 9:00.
    is_pm = s.endswith("PM")
    is_am = s.endswith("AM")
    if is_pm or is_am:
        s = s[:-2]
    # HH:MM
    if ":" in s:
        h, m = s.split(":", 1)
        try:
            hh = int(h)
            mm = int(m)
        except ValueError:
            return None
    elif s.isdigit() and len(s) in (3, 4):
        # 0915 or 915
        s4 = s.zfill(4)
        hh = int(s4[:2])
        mm = int(s4[2:])
    elif s.isdigit() and len(s) <= 2:
        # "9" alone
        try:
            hh = int(s)
            mm = 0
        except ValueError:
            return None
    else:
        return None
    if is_pm and hh < 12:
        hh += 12
    if is_am and hh == 12:
        hh = 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return str(mm), str(hh)


# ── Pattern A: scheduled SIP-style ─────────────────────────────────


# "buy 5 NIFTYBEES every weekday at 09:15 IST"
# "agent that buys 5 NIFTYBEES every weekday at 09:15"
# "every weekday at 09:15 buy 5 NIFTYBEES"
# "sell 2 INFY every Monday 15:30"
# Verb captured loosely so "buy"/"buys"/"buying" all hit; we normalize
# downstream with rstrip. Same for "sell"/"sells"/"selling".
_SIDE_RE = r"(?P<side>buy|buys|buying|sell|sells|selling)"
_SCHED_RE_VERB_FIRST = re.compile(
    r"\b" + _SIDE_RE + r"\s+(?P<qty>\d+)\s+(?P<symbol>" + _SYMBOL_RE + r")\b"
    r"(?:[^\.]*?\bevery\s+(?P<dow>weekday|weekdays|day|"
    r"monday|tuesday|wednesday|thursday|friday|"
    r"mon|tue|wed|thu|fri)\b)"
    r"(?:[^\.]*?\bat\s+(?P<time>\d{1,2}(?::\d{2})?(?:\s*[ap]m)?))?",
    re.IGNORECASE,
)
_SCHED_RE_TIME_FIRST = re.compile(
    r"\bevery\s+(?P<dow>weekday|weekdays|day|"
    r"monday|tuesday|wednesday|thursday|friday|"
    r"mon|tue|wed|thu|fri)\b"
    r"(?:[^\.]*?\bat\s+(?P<time>\d{1,2}(?::\d{2})?(?:\s*[ap]m)?))?"
    r"[^\.]*?\b" + _SIDE_RE + r"\s+(?P<qty>\d+)\s+(?P<symbol>"
    + _SYMBOL_RE + r")\b",
    re.IGNORECASE,
)


def _normalize_side(raw: str) -> str:
    """'buy'|'buys'|'buying' → 'buy'. Same for sell."""
    s = raw.lower()
    if s.startswith("buy"):
        return "buy"
    return "sell"


def _try_scheduled(message: str) -> Optional[dict[str, Any]]:
    """Match scheduled SIP shapes. Returns a workflow dict on hit."""
    m = _SCHED_RE_VERB_FIRST.search(message) or _SCHED_RE_TIME_FIRST.search(message)
    if not m:
        return None

    side = _normalize_side(m.group("side"))
    qty = int(m.group("qty"))
    symbol = m.group("symbol").upper()
    if symbol in _SYMBOL_BLOCKLIST:
        return None
    dow_raw = m.group("dow").lower()
    time_str = (m.group("time") or "").strip()

    # Default to market open (09:15 IST) when no explicit time.
    if time_str:
        cron_fields = _parse_time_to_cron_fields(time_str)
        if cron_fields is None:
            return None
        minute, hour = cron_fields
    else:
        minute, hour = "15", "9"

    # Day-of-week → cron DOW field.
    if dow_raw in {"weekday", "weekdays"}:
        dow_field = "1-5"
        dow_label = "weekdays"
    elif dow_raw == "day":
        dow_field = "*"
        dow_label = "day"
    else:
        dow_field = _DOW_TO_CRON.get(dow_raw)
        if dow_field is None:
            return None
        dow_label = {
            "1": "Monday", "2": "Tuesday", "3": "Wednesday",
            "4": "Thursday", "5": "Friday",
        }[dow_field]

    cron = f"{minute} {hour} * * {dow_field}"
    name = f"{dow_label.capitalize()} {symbol} {side}"

    return {
        "name": name[:60],
        "description": (
            f"{side.capitalize()} {qty} {symbol} every {dow_label} "
            f"at {hour.zfill(2)}:{minute.zfill(2)} IST."
        ),
        "steps": [
            {
                "step_type": "trigger.schedule",
                "label": f"Every {dow_label} at {hour.zfill(2)}:{minute.zfill(2)}",
                "config": {"cron": cron, "timezone": "Asia/Kolkata"},
            },
            {
                "step_type": "action.place_order",
                "label": f"{side.capitalize()} {qty} {symbol}",
                "config": {
                    "symbol": symbol,
                    "side": side,
                    "quantity": qty,
                    "order_type": "market",
                    "requires_approval": False,
                },
            },
        ],
        "rationale": (
            f"Single schedule trigger ({cron}, IST) followed by a market "
            f"{side} for SIP-style execution."
        ),
        "warnings": [],
        "_render_hint": "workflow_draft_card",
    }


# ── Pattern B: indicator-threshold ─────────────────────────────────


_IND_RE = re.compile(
    r"\b" + _SIDE_RE + r"\s+(?P<qty>\d+)\s+(?P<symbol>" + _SYMBOL_RE + r")\b"
    r"[^\.]*?\bwhen(?:ever)?\b[^\.]*?"
    r"(?P<indicator>rsi|sma|ema)"
    r"(?:[\(\s]+(?P<period>\d{1,3})[\)\s]*)?"
    r"\s*(?:is\s+|value\s+)?"
    # Direction phrase: a verb (drops/rises/...) optionally followed by
    # a preposition (below/above/under/over) — both, either, or just an
    # operator symbol. "rises above" / "drops below" / "falls under"
    # / "<" / "below" all parse the same way.
    r"(?P<dir>(?:drops?|fell|falls?|rises?|rose|"
    r"crosses?|broke|breaks?|goes?|moves?)"
    r"(?:\s+(?:below|above|under|over))?"
    r"|<|>|below|above|under|over)\s+"
    r"(?P<val>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _try_indicator(message: str) -> Optional[dict[str, Any]]:
    """Match RSI/SMA/EMA threshold-based buy/sell."""
    m = _IND_RE.search(message)
    if not m:
        return None

    side = _normalize_side(m.group("side"))
    qty = int(m.group("qty"))
    symbol = m.group("symbol").upper()
    if symbol in _SYMBOL_BLOCKLIST:
        return None
    indicator = m.group("indicator").lower()
    val = float(m.group("val"))
    period_str = m.group("period")
    period = (
        int(period_str) if period_str
        else {"rsi": 14, "sma": 50, "ema": 50}[indicator]
    )
    dir_token = m.group("dir").lower()
    if "cross" in dir_token:
        operator = "crosses_below" if "below" in dir_token else "crosses_above"
    elif any(w in dir_token for w in ("drop", "fall", "fell", "below", "under", "<")):
        operator = "<"
    else:
        operator = ">"

    dir_label = {
        "<": "below",
        ">": "above",
        "crosses_above": "crossing above",
        "crosses_below": "crossing below",
    }[operator]

    return {
        "name": f"{symbol} {indicator.upper()}({period}) {dir_label} {val:g}"[:60],
        "description": (
            f"{side.capitalize()} {qty} {symbol} when {period}-period "
            f"{indicator.upper()} is {dir_label} {val:g}."
        ),
        "steps": [
            {
                "step_type": "trigger.indicator",
                "label": f"{indicator.upper()}({period}) {operator} {val:g}",
                "config": {
                    "symbol": symbol,
                    "indicator": indicator,
                    "period": period,
                    "operator": operator,
                    "value": val,
                },
            },
            {
                "step_type": "action.place_order",
                "label": f"{side.capitalize()} {qty} {symbol}",
                "config": {
                    "symbol": symbol,
                    "side": side,
                    "quantity": qty,
                    "order_type": "market",
                    "requires_approval": False,
                },
            },
        ],
        "rationale": (
            f"{indicator.upper()} indicator trigger then a market {side}. "
            f"Period {period} is the conventional default."
        ),
        "warnings": [],
        "_render_hint": "workflow_draft_card",
    }


# ── Pattern C: price-threshold ─────────────────────────────────────


_PRICE_RE = re.compile(
    r"\b" + _SIDE_RE + r"\s+(?P<qty>\d+)\s+(?P<symbol>" + _SYMBOL_RE + r")\b"
    r"[^\.]*?\bwhen(?:ever)?\b[^\.]*?"
    r"(?:its\s+)?(?:price|it)?\s*"
    r"(?P<dir>(?:drops?|fell|falls?|rises?|rose|"
    r"crosses?|broke|breaks?|goes?|moves?)"
    r"(?:\s+(?:below|above|under|over))?"
    r"|<|>|below|above|under|over)\s+"
    r"₹?\s*(?P<val>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _try_price(message: str) -> Optional[dict[str, Any]]:
    """Price-threshold buy/sell. Lower priority than indicator: only
    fires when no indicator (RSI/SMA/EMA) is mentioned."""
    if re.search(r"\b(rsi|sma|ema|macd)\b", message, re.IGNORECASE):
        return None
    m = _PRICE_RE.search(message)
    if not m:
        return None

    side = _normalize_side(m.group("side"))
    qty = int(m.group("qty"))
    symbol = m.group("symbol").upper()
    if symbol in _SYMBOL_BLOCKLIST:
        return None
    val = float(m.group("val"))
    dir_token = m.group("dir").lower()
    if "cross" in dir_token:
        operator = "crosses_below" if "below" in dir_token else "crosses_above"
    elif any(w in dir_token for w in ("drop", "fall", "fell", "below", "under", "<")):
        operator = "<"
    else:
        operator = ">"

    dir_label = {
        "<": "below",
        ">": "above",
        "crosses_above": "crossing above",
        "crosses_below": "crossing below",
    }[operator]

    return {
        "name": f"{symbol} price {dir_label} ₹{val:g}"[:60],
        "description": (
            f"{side.capitalize()} {qty} {symbol} when its price is "
            f"{dir_label} ₹{val:g}."
        ),
        "steps": [
            {
                "step_type": "trigger.price",
                "label": f"Price {operator} ₹{val:g}",
                "config": {
                    "symbol": symbol,
                    "operator": operator,
                    "value": val,
                    "exchange": "NSE",
                },
            },
            {
                "step_type": "action.place_order",
                "label": f"{side.capitalize()} {qty} {symbol}",
                "config": {
                    "symbol": symbol,
                    "side": side,
                    "quantity": qty,
                    "order_type": "market",
                    "requires_approval": False,
                },
            },
        ],
        "rationale": (
            f"Price trigger ({operator} ₹{val:g}) then a market {side}."
        ),
        "warnings": [],
        "_render_hint": "workflow_draft_card",
    }


# ── Pattern D: buy-with-percentage-stop-loss ──────────────────────


# "buy 3 HDFCBANK when price drops 2% with a 2% stop loss"
# "create a strategy that watches HDFCBANK and buys 3 shares when price
#  drops 2% below today's open, with a 2% stop loss"
# Two-stage: extract trigger (price-cross / indicator) and SL pct
# separately. Trigger is required; SL must be a "X% (stop|SL|loss)".
_SL_PCT_RE = re.compile(
    r"(?P<sl_pct>\d+(?:\.\d+)?)\s*%\s*(?:stop[- ]?loss|stop|sl|loss)\b",
    re.IGNORECASE,
)


def _try_buy_with_pct_sl(message: str) -> Optional[dict[str, Any]]:
    """Buy + percentage stop-loss. Reuses indicator/price trigger
    parsers, then appends action.set_stoploss with trigger_offset_pct.

    Only fires when:
      - SL pct matches
      - either RSI/SMA/EMA threshold OR price-threshold is detectable
      - the side is BUY (an SL on a SELL doesn't make sense in v1)
    """
    sl_match = _SL_PCT_RE.search(message)
    if not sl_match:
        return None
    sl_pct = float(sl_match.group("sl_pct"))
    if not (0 < sl_pct <= 50):
        return None

    # Try indicator trigger first, then price trigger.
    base = _try_indicator(message) or _try_price(message)
    if base is None:
        return None
    # Must be a buy (SL implies opening a long position).
    place_step = next(
        (s for s in base["steps"] if s["step_type"] == "action.place_order"),
        None,
    )
    if not place_step or place_step["config"].get("side") != "buy":
        return None

    symbol = place_step["config"]["symbol"]
    qty = place_step["config"]["quantity"]
    base["steps"].append({
        "step_type": "action.set_stoploss",
        "label": f"{sl_pct:g}% stop-loss",
        "config": {
            "symbol": symbol,
            "trigger_offset_pct": sl_pct,
            "quantity": qty,
        },
    })
    base["name"] = f"{base['name']} +{sl_pct:g}% SL"[:60]
    base["description"] = (
        f"{base['description']} Stop-loss at {sl_pct:g}% below entry."
    )
    base["rationale"] = (
        f"{base['rationale']} Stop-loss applied as percentage offset "
        f"from the buy fill."
    )
    return base


# ── Pattern E: sell-my-SYM-when-trigger ────────────────────────────


# "sell my INFY when RSI rises above 70"
# "exit my TCS when price drops below 4000"
# Uses fetch.portfolio + a Mustache ref for quantity so the user
# doesn't have to specify a number. Matches the canonical "sell
# entire holding" pattern from the system prompt.
_SELL_MY_RE = re.compile(
    r"\b(?:sell|exit|squareoff|square\s*off)\s+(?:my|the)\s+"
    r"(?P<symbol>" + _SYMBOL_RE + r")\b"
    r"[^\.]*?\bwhen(?:ever)?\b[^\.]*?"
    r"(?:"
    r"(?P<indicator>rsi|sma|ema)"
    r"(?:[\(\s]+(?P<period>\d{1,3})[\)\s]*)?"
    r"\s*(?:is\s+|value\s+)?"
    r"(?P<ind_dir>(?:drops?|fell|falls?|rises?|rose|crosses?|broke|breaks?|goes?|moves?)"
    r"(?:\s+(?:below|above|under|over))?|<|>|below|above|under|over)\s+"
    r"(?P<ind_val>\d+(?:\.\d+)?)"
    r"|"
    r"(?:its\s+|the\s+)?(?:price|it)\s*"
    r"(?P<price_dir>(?:drops?|fell|falls?|rises?|rose|crosses?|broke|breaks?|goes?|moves?)"
    r"(?:\s+(?:below|above|under|over))?|<|>|below|above|under|over)\s+"
    r"₹?\s*(?P<price_val>\d+(?:\.\d+)?)"
    r")",
    re.IGNORECASE,
)


def _direction_to_operator(token: str) -> str:
    t = token.lower()
    if "cross" in t:
        return "crosses_below" if "below" in t else "crosses_above"
    if any(w in t for w in ("drop", "fall", "fell", "below", "under", "<")):
        return "<"
    return ">"


def _try_sell_my(message: str) -> Optional[dict[str, Any]]:
    """Sell entire holding when trigger fires."""
    m = _SELL_MY_RE.search(message)
    if not m:
        return None
    symbol = m.group("symbol").upper()
    if symbol in _SYMBOL_BLOCKLIST:
        return None

    if m.group("indicator"):
        indicator = m.group("indicator").lower()
        period = (
            int(m.group("period")) if m.group("period")
            else {"rsi": 14, "sma": 50, "ema": 50}[indicator]
        )
        operator = _direction_to_operator(m.group("ind_dir"))
        val = float(m.group("ind_val"))
        trigger_step = {
            "step_type": "trigger.indicator",
            "label": f"{indicator.upper()}({period}) {operator} {val:g}",
            "config": {
                "symbol": symbol, "indicator": indicator, "period": period,
                "operator": operator, "value": val,
            },
        }
        when_label = f"when {indicator.upper()}({period}) {operator} {val:g}"
    else:
        operator = _direction_to_operator(m.group("price_dir"))
        val = float(m.group("price_val"))
        trigger_step = {
            "step_type": "trigger.price",
            "label": f"Price {operator} ₹{val:g}",
            "config": {
                "symbol": symbol, "operator": operator, "value": val,
                "exchange": "NSE",
            },
        }
        when_label = f"when price {operator} ₹{val:g}"

    # fetch.portfolio is index 1, so the place_order ref points there.
    holdings_ref = (
        "{{ context.1.holdings." + symbol + ".quantity }}"
    )
    return {
        "name": f"Exit {symbol} {when_label}"[:60],
        "description": (
            f"Sell the entire {symbol} holding {when_label}."
        ),
        "steps": [
            trigger_step,
            {
                "step_type": "fetch.portfolio",
                "label": "Get holdings",
                "config": {},
            },
            {
                "step_type": "action.place_order",
                "label": f"Sell entire {symbol} holding",
                "config": {
                    "symbol": symbol,
                    "side": "sell",
                    "quantity": holdings_ref,
                    "order_type": "market",
                    "requires_approval": False,
                },
            },
        ],
        "rationale": (
            f"Trigger fires on {when_label}; fetch the portfolio so the "
            "place_order step can reference the current holding quantity, "
            "then sell the full position at market."
        ),
        "warnings": [],
        "_render_hint": "workflow_draft_card",
    }


# ── Pattern F: multi-trigger (one workflow, two branches) ──────────


# "buy 5 NIFTYBEES every Monday at 09:15 AND sell at Monday close
#  (15:30) if RSI < 30"
# We split on "and" / "AND" / ".\s*also" and try each half through the
# scheduled / indicator / price parsers. If both halves yield a single
# trigger.* + action.* pair, we concatenate the steps into one draft.
_AND_SPLIT_RE = re.compile(
    r"\s+(?:and(?:\s+(?:then|also))?|;\s*then|;)\s+",
    re.IGNORECASE,
)


def _try_multi_trigger(message: str) -> Optional[dict[str, Any]]:
    """Two coordinated branches — emit them in one workflow."""
    parts = _AND_SPLIT_RE.split(message)
    if len(parts) < 2:
        return None
    # Try every adjacent pair of halves. Real prompts often wrap the
    # build verb at the start ("Build me an agent that buys ... AND
    # sells ...") so the first half includes the build verb and the
    # second is bare. Strip the build-verb noise from the first part.
    head = re.sub(
        r"^[^.]*?\b(?:agent|strategy|workflow|automation|bot)\s+(?:that|which|to)\s+",
        "",
        parts[0],
        count=1,
        flags=re.IGNORECASE,
    )
    candidates = [head] + parts[1:]
    drafts: list[dict[str, Any]] = []
    for half in candidates:
        if len(drafts) >= 4:
            break
        # Only run pure single-trigger parsers; SL / sell-my live in
        # the dedicated patterns. Trying buy-with-SL inside a multi-
        # trigger branch isn't supported here yet.
        sub = (
            _try_scheduled(half)
            or _try_indicator(half)
            or _try_price(half)
            or _try_sell_my(half)
        )
        if sub is None:
            continue
        drafts.append(sub)
    if len(drafts) < 2:
        return None

    # Concatenate steps. Each sub-draft starts with one trigger.*
    # followed by its branch body. Workflows v1 supports multiple
    # trigger.* steps — they each kick off an independent branch.
    combined_steps: list[dict[str, Any]] = []
    seen_triggers: list[str] = []
    for d in drafts:
        for s in d["steps"]:
            combined_steps.append(s)
            if s["step_type"].startswith("trigger."):
                seen_triggers.append(s["step_type"])

    name_parts = [d["name"] for d in drafts]
    return {
        "name": " + ".join(name_parts)[:60],
        "description": " ".join(d["description"] for d in drafts)[:200],
        "steps": combined_steps,
        "rationale": (
            f"{len(drafts)}-branch workflow: each trigger ({', '.join(seen_triggers)}) "
            "starts an independent branch. The engine runs only the branch "
            "of the trigger that fired."
        ),
        "warnings": [],
        "_render_hint": "workflow_draft_card",
    }


# ── Public entrypoint ──────────────────────────────────────────────


# Build/create verbs that gate skeleton emission. Without one of these
# we don't try to emit a draft — "what's RELIANCE's price" must not
# get a workflow even if other tokens line up.
_BUILD_VERB_RE = re.compile(
    r"\b(?:build|create|set\s*up|setup|make|generate|automate"
    r"|every\s+(?:weekday|day|monday|tuesday|wednesday|thursday|friday|"
    r"week|hour|morning|evening)"
    r"|when(?:ever)?\b[^\.]*?\b(?:rsi|sma|ema|price)"
    r"|(?:buy|sell|exit)\s+(?:my|the\s+)?\d*\s*[A-Z][A-Z0-9\-_]+\s+(?:every|when))"
    r"|\bsell\s+my\s+[A-Z][A-Z0-9\-_]+",
    re.IGNORECASE,
)

# Phrases that still skip the skeleton — features we genuinely can't
# express yet. Stop-loss is now SUPPORTED via Pattern D. Notifications,
# conditions, buying-power gates, and runtime-relative thresholds
# (e.g. "2% below today's open" — the threshold isn't an absolute
# value, it depends on the day's data) remain LLM-only.
#
# CRITICAL: this regex is the only thing between a user's compound /
# multi-output / aggregator / cross-symbol prompt and a silently-
# truncated single-condition draft. The skeleton's competence is RSI/
# SMA/EMA + price + schedule + sell-my, single-condition only. ANY
# signal that the user wants something richer MUST bail to the LLM,
# which routes to propose_dsl_workflow.
_COMPLEXITY_RE = re.compile(
    r"\b(?:buying[- ]power|sector\s+exposure)\b"
    # L14: multi-step compound intents must bail to the LLM so the
    # orchestrator (compose_multistep) can pick them up. Otherwise the
    # skeleton silently produces ONLY the build step, dropping the
    # research / compare / regime context the user asked for.
    r"|\b(?:compare|backtest)\b[^.]{0,140}?\b(?:then|and|,)\b[^.]{0,140}?"
    r"\b(?:build|create|set\s+up|setup|make|turn\s+into|design|pick|identify)\b"
    r"|\b(?:before\s+and\s+after|pre[\s-]?\d{4}|post[\s-]?\d{4}|"
    r"pre[\s-]?(?:and[\s-])?post|in\s+(?:both\s+)?regimes?|regime\s+shift)\b"
    r"|\b(?:full\s+plan|do\s+all\s+(?:three|four|five))\b"
    r"|\bnotify\s+me\b|\bemail\s+me\b|\bsms\s+me\b|\bpush\s+(?:notif|me)"
    r"|\bif\b[^.]*?\bbuying\s+power\b"
    r"|\bcondition\b[^.]*?\bportfolio\b"
    # Runtime-relative thresholds. "2% below today's open" / "below
    # previous close" / "above yesterday's high" all need price data
    # at fire time; the skeleton can't compute them. Send to LLM.
    r"|\d+\s*%\s*(?:below|above|under|over)\s+(?:today|yesterday|prev|previous|last)"
    r"|\b(?:below|above|under|over)\s+(?:today|yesterday|prev(?:ious)?|last)(?:'s)?\s+(?:open|close|high|low)"
    r"|\bif\s+(?:current|today)\s+(?:price|open|close)\s+is\s+(?:above|below|over|under)"
    # TTL / valid-until phrases.
    r"|\b(?:valid\s+(?:till|until)|until\s+\d|good\s+(?:for|till)|expires?\s+(?:on|after)|till\s+(?:eod|the\s+end|next|month|week|friday)|next\s+\d+\s+(?:days|weeks))"
    # ── Indicators OUTSIDE the skeleton's RSI/SMA/EMA whitelist.
    # The skeleton can only emit trigger.indicator with rsi|sma|ema.
    # Anything else needs propose_dsl_workflow or the LLM hop to
    # build a richer step shape. NEVER let these silently route to a
    # single trigger.indicator(rsi…) with the rest dropped.
    r"|\b(?:macd|adx|bollinger\s+band|bollinger|bb|atr|keltner|donchian|"
    r"stoch(?:astic)?(?:_rsi)?|supertrend|aroon|cci|mfi|williams[_\s]?r|"
    r"obv|vwap|roc|trix|psar|vix|ichimoku|chaikin)\b"
    # ── Multi-output indicator components.
    r"|\b(?:macd\s+(?:line|signal|hist|histogram)|bollinger\s+(?:upper|lower|middle|%?b|pctb|bandwidth)|"
    r"stoch(?:astic)?\s+%?[kd]|bb\s+(?:upper|lower|middle)|"
    r"donchian\s+(?:upper|lower)|keltner\s+(?:upper|lower)|"
    r"aroon\s+(?:up|down))\b"
    # ── Aggregate windows (percentrank, zscore, highest-N, lowest-N,
    # rolling X-day, correlation, barssince, valuewhen).
    r"|\b(?:percent[- ]?rank|percentile|z[- ]?score|barssince|valuewhen|"
    r"correlation|correlated\s+with|rolling\s+\d|n[- ]?day\s+high|"
    r"\d+[- ]?(?:day|bar)\s+(?:high|low|average|mean|std|sigma)|"
    r"\d+[- ]?bar\s+(?:high|low|mean|std)|highest\s+(?:close\s+)?of\s+"
    r"(?:the\s+)?last\s+\d|lowest\s+(?:close\s+)?of\s+(?:the\s+)?last\s+\d)\b"
    # ── Volume-relative comparisons.
    r"|\bvolume\b[^.]{0,40}?(?:above|greater\s+than|more\s+than|>|exceed|"
    r"spike|times|x\s+(?:its|the)|x\s+\d+[- ]?(?:day|bar)|\d+x\b)"
    # ── Cross-symbol spread / ratio (e.g. TCS/INFY, KOTAKBANK/HDFCBANK,
    # ratio between two tickers, spread phrasing).
    r"|\b(?:spread|ratio)\s+(?:of|between)\b"
    r"|\b[A-Z]{3,15}\s*/\s*[A-Z]{3,15}\b"
    # ── Market-relative time. The skeleton's _try_scheduled only
    # parses fixed HH:MM. Open/close offsets need trigger.market_relative_time.
    r"|\b(?:\d+\s+)?(?:minutes?|mins?|hours?|hrs?)\s+(?:before|after)\s+"
    r"(?:market\s+|the\s+)?(?:open|close)\b"
    r"|\bpre[- ]?open\s+session\b|\bin\s+(?:the\s+)?pre[- ]?open\b"
    r"|\b(?:at|after|before)\s+(?:the\s+)?market\s+(?:open|close)\b"
    r"|\bat\s+market\s+(?:open|close)\b"
    # ── Exit / position-state references. These belong in a DSL exit
    # tree (trigger.exit_compound with PositionNode leaves), not a
    # flat scheduled/threshold workflow.
    r"|\b(?:drawdown|peak[- ]?to[- ]?(?:current|trough)|peak\s+unrealised|"
    r"trail(?:ing)?\s+(?:stop|\d+)|trail\s+a?\s*\d|bars?\s+held|"
    r"after\s+\d+\s+bars?|been\s+holding|"
    r"unrealised|unrealized|position\s+is\s+up|position\s+up)\b"
    # ── Gap / pct_change leaves.
    r"|\bgap[- ]?(?:up|down)\b|\bgaps?\s+(?:down|up)\b"
    r"|\bpercent\s+change|\bpct[- ]?change\b"
    # ── Indicator crossover, in EITHER word order. The skeleton can only
    # emit a single trigger.indicator vs a SCALAR threshold — it cannot
    # express two series crossing, so ANY crossover must bail to the LLM →
    # backtest_dsl_tree. Catches "50/200 SMA crossover", "9/21 EMA crosses",
    # "MACD crosses signal", "golden/death cross", "fast EMA above slow EMA".
    r"|\bgolden\s+cross\b|\bdeath\s+cross\b"
    r"|\b(?:e?ma|sma|macd|moving\s+average)\b[^.]{0,30}?\bcross(?:es|ed|ing|over)?\b"
    r"|\bcross(?:es|ed|ing|over)?\b[^.]{0,30}?\b(?:e?ma|sma|macd|signal|moving\s+average)\b"
    r"|\b\d+[- ]?(?:day|bar|period)?\s*[A-Z]?(?:EMA|SMA|MA)\b[^.]{0,40}?"
    r"\b(?:cross|above|below)\b[^.]{0,40}?\b\d+[- ]?(?:day|bar|period)?\s*[A-Z]?(?:EMA|SMA|MA)\b"
    # ── Compound logic: explicit "AND" / "OR" / "NOT" connecting two
    # condition clauses inside a single intent. Indicator-or-price word
    # on either side is the conservative guard.
    r"|\b(?:rsi|sma|ema|macd|adx|atr|bollinger|stoch|donchian|keltner|"
    r"supertrend|aroon|cci|mfi|williams|price|volume|close|open|high|low)"
    r"[^.]{1,80}?\b(?:AND|and|OR|or)\b[^.]{1,80}?"
    r"\b(?:rsi|sma|ema|macd|adx|atr|bollinger|stoch|donchian|keltner|"
    r"supertrend|aroon|cci|mfi|williams|price|volume|close|open|high|low|above|below|>|<)\b"
    # ── Session-day filter mid-condition ("only on Tuesdays", "Mon-Wed only").
    r"|\bonly\s+on\s+(?:mon|tue|wed|thu|fri)"
    r"|\b(?:mon|tue|wed|thu|fri)[a-z]*(?:\s+and\s+(?:mon|tue|wed|thu|fri)[a-z]*)+\s+only\b",
    re.IGNORECASE,
)


# Heuristic: the message looks like a multi-trigger / multi-clause
# ask if EITHER:
#   (a) two action verbs connected by "and"/";"/"," — classic
#       "buy X and sell Y" / "Mon buy X, Wed buy Y";
#   (b) a single action verb with multiple day names joined by
#       "and"/"," — "every Wed and Fri buy 5 X". The scheduled
#       parser only emits ONE day, so we must bail rather than
#       silently drop the others.
# When this matches but the multi-trigger parser fails, fall to the
# LLM. Better an extra hop than a partial draft the user can't see is
# wrong.
_LOOKS_MULTI_TRIGGER_RE = re.compile(
    # (a) Two verbs with a connector
    r"\b(?:buy|buys|buying|sell|sells|selling|exit)\b[^.]{0,160}?"
    r"(?:\s+and\s+(?:then\s+)?|;\s*|,\s+(?!and\s)(?:on\s+)?)"
    r"(?:also\s+)?(?:buy|buys|buying|sell|sells|selling|exit)\b",
    re.IGNORECASE,
)

# Standalone day-name detector — used by `_looks_like_multi_day`
# below. Matches mon|tue|wed|thu|fri|sat|sun with optional
# day/s/nesday/rsday/urday suffix.
_DAY_TOKEN_RE = re.compile(
    r"\b(mon|tue|wed|thu|fri|sat|sun)"
    r"(?:day|s|sday|nesday|rsday|urday)?\b",
    re.IGNORECASE,
)


def _looks_like_multi_day(message: str) -> bool:
    """True when a single sentence in the message names two+ distinct
    weekdays. Catches both 'every Wed and Fri' (days adjacent) and
    'every Monday buy X, every Wednesday buy Y' (days separated by a
    full clause). The single-day scheduled parser only emits ONE day,
    so any prompt past the first listed day silently disappears —
    bail to LLM rather than ship a partial draft."""
    if not message:
        return False
    # Sentence-split on .!? so "Monday open, Wednesday close" stays
    # in one sentence but "Buy on Monday. Sell on Friday." doesn't
    # trigger (those are two distinct asks the user typed separately).
    for sentence in re.split(r"[.!?]\s+", message):
        days = {m.group(1).lower() for m in _DAY_TOKEN_RE.finditer(sentence)}
        if len(days) >= 2:
            return True
    return False


def try_workflow_skeleton(message: str) -> Optional[dict[str, Any]]:
    """Try each canonical pattern in priority order. Return the first
    matching workflow draft dict, or None to fall through to the LLM.

    Priority order matters:
      multi-trigger > sell-my > buy-with-SL > scheduled > indicator > price.
    Multi-trigger and sell-my are checked first because they're the
    most specific shapes; buy-with-SL needs to win over plain
    indicator/price (which would otherwise match without the SL step);
    scheduled / indicator / price are the fallbacks for single-step
    automations.
    """
    if not message or not _BUILD_VERB_RE.search(message):
        return None
    if _COMPLEXITY_RE.search(message):
        return None

    # Cross-symbol guard: the skeleton's single-symbol parsers grab the
    # FIRST ticker-shaped token they see, so a prompt naming two
    # different tickers ("buy 10 HDFCBANK when ICICIBANK drops 3%
    # intraday") silently collapses to a single-symbol workflow on
    # HDFCBANK with "₹3" parsed as the price level — a wholly wrong
    # draft the user can't see is wrong. Detect 2+ distinct
    # ticker-shaped tokens (after filtering the blocklist) and bail.
    distinct = _distinct_ticker_tokens(message)
    if len(distinct) >= 2:
        return None

    looks_multi = bool(_LOOKS_MULTI_TRIGGER_RE.search(message))
    looks_multi_day = _looks_like_multi_day(message)

    for parser in (
        _try_multi_trigger,
        _try_sell_my,
        _try_buy_with_pct_sl,
        _try_scheduled,
        _try_indicator,
        _try_price,
    ):
        # Multi-trigger guard: if the message LOOKS multi-trigger
        # but we're on a single-trigger parser, skip it. Better to
        # fall through to the LLM than emit a partial workflow.
        if looks_multi and parser is not _try_multi_trigger:
            continue
        # Multi-day guard: same reasoning. The scheduled parser only
        # emits ONE day; if the user listed several, the LLM has to
        # build a multi-trigger workflow. Don't let scheduled silently
        # eat a single day and drop the rest.
        if looks_multi_day and parser is _try_scheduled:
            continue
        result = parser(message)
        if result is not None:
            return result
    return None
