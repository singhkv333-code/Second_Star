"""
Chart-request parser for the Compare feature.

Two-stage:
  1. Cheap keyword filter — if the message has no chart-ish words, return None
     immediately without an LLM call.
  2. One Sarvam call with an emulated function-calling tool (parse_chart_request)
     to extract symbols + period + chart_type. Falls back to a regex/heuristic
     extractor if Sarvam is in mock mode or the parse fails.

Returns None when the message is not a chart/comparison request, or a dict:
  {symbols, period, start_date, end_date, chart_type, normalise, sip_amount}
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from backend.agents.sarvam_client import SARVAM_MOCK_MODE, call_sarvam
from backend.market.yfinance_service import (
    NAME_TO_TICKER,
    VALID_PERIODS,
    canonical_symbol,
)

logger = logging.getLogger(__name__)

CHART_KEYWORDS = (
    "compare", "vs", "versus", "chart", "plot", "graph", "show", "history",
    "performance", "perform", "how has", "how have", "price of", "over the last",
    "since", "backtest", "return", "returns", "grew", "fell", "rallied",
    "drawdown", "outperform",
)

PARSE_TOOL = {
    "type": "function",
    "function": {
        "name": "parse_chart_request",
        "description": "Parse a chart or comparison request into structured parameters.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "NSE tickers extracted from the message (INFY, TCS, RELIANCE...). "
                                   "Indices: NIFTY50, SENSEX, BANKNIFTY.",
                },
                "period": {
                    "type": "string",
                    "enum": ["1w", "1m", "3m", "6m", "1y", "2y", "5y", "ytd", "max"],
                    "description": "Time window. Default 1y if unspecified.",
                },
                "start_date": {"type": ["string", "null"], "description": "YYYY-MM-DD or null."},
                "end_date": {"type": ["string", "null"], "description": "YYYY-MM-DD or null."},
                "chart_type": {
                    "type": "string",
                    "enum": ["comparison", "single", "backtest"],
                    "description": "comparison if multiple symbols, single if one, backtest if SIP simulation.",
                },
                "normalise": {"type": "boolean", "description": "True for comparison (rebase to 100)."},
                "sip_amount": {"type": ["number", "null"], "description": "Monthly SIP rupees if backtest."},
            },
            "required": ["symbols", "period", "chart_type", "normalise"],
        },
    },
}

PARSE_SYSTEM_PROMPT = """You extract chart parameters from a natural-language message.

Extract:
- symbols: NSE tickers (convert company names to tickers — Infosys→INFY, Reliance→RELIANCE, HDFC Bank→HDFCBANK, ICICI Bank→ICICIBANK, Axis Bank→AXISBANK, Tata Motors→TATAMOTORS, etc.)
- Indices: Nifty/Nifty50→NIFTY50, Sensex→SENSEX, Bank Nifty→BANKNIFTY
- period: one of 1w, 1m, 3m, 6m, 1y, 2y, 5y, ytd, max — default 1y if not specified
- chart_type: "comparison" if multiple symbols, "single" if one, "backtest" if SIP simulation
- normalise: true for comparison (default), false for single price chart
- sip_amount: number if user mentions monthly SIP, else null

Call parse_chart_request with the extracted parameters. Always emit a <TOOL_CALL> block.

Examples:
"compare INFY and TCS over the last 6 months" → symbols:["INFY","TCS"], period:"6m", chart_type:"comparison", normalise:true
"show me Nifty performance this year" → symbols:["NIFTY50"], period:"ytd", chart_type:"single", normalise:false
"how has Reliance done since 2022" → symbols:["RELIANCE"], period:"max", start_date:"2022-01-01", chart_type:"single", normalise:false
"backtest buying NIFTYBEES 5000 every month for 2 years" → symbols:["NIFTYBEES"], period:"2y", chart_type:"backtest", normalise:false, sip_amount:5000
"""


_PERIOD_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bytd\b|\bthis year\b|\byear[- ]to[- ]date\b", re.I), "ytd"),
    (re.compile(r"\b(?:last|past|over)?\s*(\d+)\s*(?:weeks?|w)\b", re.I), "WEEK"),
    (re.compile(r"\b(?:last|past|over)?\s*(\d+)\s*(?:months?|mo|m)\b", re.I), "MONTH"),
    (re.compile(r"\b(?:last|past|over)?\s*(\d+)\s*(?:years?|yrs?|y)\b", re.I), "YEAR"),
    (re.compile(r"\bsince\s+(\d{4})\b", re.I), "SINCE_YEAR"),
    (re.compile(r"\b(?:all[- ]?time|max|max(?:imum)?)\b", re.I), "max"),
    (re.compile(r"\b(last|past)\s+year\b", re.I), "1y"),
    (re.compile(r"\b(last|past)\s+month\b", re.I), "1m"),
    (re.compile(r"\b(last|past)\s+week\b", re.I), "1w"),
]


def _extract_period(text: str) -> tuple[str, Optional[str]]:
    """Return (period, start_date) — start_date is set only for 'since YYYY' patterns."""
    low = text.lower()
    for pat, label in _PERIOD_PATTERNS:
        m = pat.search(low)
        if not m:
            continue
        if label in {"ytd", "max", "1w", "1m", "1y"}:
            return label, None
        if label == "WEEK":
            n = int(m.group(1))
            if n <= 1:
                return "1w", None
            return "1m", None
        if label == "MONTH":
            n = int(m.group(1))
            if n <= 1:
                return "1m", None
            if n <= 3:
                return "3m", None
            if n <= 6:
                return "6m", None
            return "1y", None
        if label == "YEAR":
            n = int(m.group(1))
            if n <= 1:
                return "1y", None
            if n <= 2:
                return "2y", None
            if n <= 5:
                return "5y", None
            return "max", None
        if label == "SINCE_YEAR":
            year = int(m.group(1))
            return "max", f"{year}-01-01"
    return "1y", None


_TICKER_RE = re.compile(r"\b[A-Z][A-Z0-9&\-]{1,9}\b")
_NOISE_TICKERS = {
    "I", "A", "AND", "OR", "VS", "THE", "MY", "YOU", "ME", "FOR", "OF", "TO",
    "ON", "IN", "IS", "AT", "OK", "SIP", "ETF", "USD", "INR", "OHLC", "NSE",
    "BSE", "FNO", "GTT", "OCO", "LTP", "AI", "IT", "PE",
    "BUY", "SELL", "SHOW", "PLOT", "CHART", "COMPARE", "VERSUS", "PRICE",
    "PERFORMANCE", "RETURN", "RETURNS", "HISTORY", "BACKTEST", "OVER", "LAST",
    "PAST", "SINCE", "YEAR", "YEARS", "MONTH", "MONTHS", "WEEK", "WEEKS",
    "YTD", "MAX",
}


def _extract_symbols(text: str) -> list[str]:
    """Greedy ticker + name extraction. Preserves order, deduplicates."""
    found: list[str] = []
    seen: set[str] = set()

    low = text.lower()
    for name in sorted(NAME_TO_TICKER.keys(), key=len, reverse=True):
        if " " in name and name in low:
            canon = NAME_TO_TICKER[name]
            if canon not in seen:
                seen.add(canon)
                found.append(canon)
                low = low.replace(name, " ")

    for word in re.findall(r"[A-Za-z][A-Za-z0-9&\-]+", text):
        canon: Optional[str] = None
        if word.lower() in NAME_TO_TICKER:
            canon = NAME_TO_TICKER[word.lower()]
        elif word.isupper() and len(word) >= 2 and word not in _NOISE_TICKERS:
            canon = canonical_symbol(word)
        if canon and canon not in seen:
            seen.add(canon)
            found.append(canon)
    return found


def _has_chart_keyword(text: str) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in CHART_KEYWORDS)


def _heuristic_parse(message: str) -> Optional[dict]:
    symbols = _extract_symbols(message)
    if not symbols:
        return None
    period, start_date = _extract_period(message)

    sip_match = re.search(r"\b(\d{3,7})\s*(?:rupees|rs|inr|₹)?\s*(?:every|per|a)\s*month", message, re.I)
    sip_amount = float(sip_match.group(1)) if sip_match else None
    if sip_amount is None:
        sip_match2 = re.search(r"\bsip\b.*?(\d{3,7})", message, re.I)
        if sip_match2:
            sip_amount = float(sip_match2.group(1))

    is_backtest = bool(
        re.search(r"\bbacktest|sip\b|every (?:month|week|day)|monthly", message, re.I)
    )
    chart_type = "backtest" if (is_backtest and sip_amount) else (
        "comparison" if len(symbols) >= 2 else "single"
    )
    normalise = chart_type == "comparison"

    return {
        "symbols": symbols[:5],
        "period": period,
        "start_date": start_date,
        "end_date": None,
        "chart_type": chart_type,
        "normalise": normalise,
        "sip_amount": sip_amount,
    }


def _normalise_parsed(raw: dict) -> Optional[dict]:
    """Coerce a parsed dict (from Sarvam or heuristic) into the canonical shape."""
    if not isinstance(raw, dict):
        return None
    syms_raw = raw.get("symbols") or []
    if not isinstance(syms_raw, list):
        return None
    symbols: list[str] = []
    seen: set[str] = set()
    for s in syms_raw:
        if not isinstance(s, str) or not s.strip():
            continue
        canon = canonical_symbol(s)
        if canon and canon not in seen:
            seen.add(canon)
            symbols.append(canon)
    if not symbols:
        return None

    period = (raw.get("period") or "1y").lower()
    if period not in VALID_PERIODS:
        period = "1y"

    chart_type = raw.get("chart_type") or ("comparison" if len(symbols) >= 2 else "single")
    if chart_type not in {"comparison", "single", "backtest"}:
        chart_type = "comparison" if len(symbols) >= 2 else "single"

    normalise = raw.get("normalise")
    if not isinstance(normalise, bool):
        normalise = chart_type == "comparison"

    sip = raw.get("sip_amount")
    sip_amount = float(sip) if isinstance(sip, (int, float)) and sip > 0 else None

    return {
        "symbols": symbols[:5],
        "period": period,
        "start_date": raw.get("start_date") if isinstance(raw.get("start_date"), str) else None,
        "end_date": raw.get("end_date") if isinstance(raw.get("end_date"), str) else None,
        "chart_type": chart_type,
        "normalise": normalise,
        "sip_amount": sip_amount,
    }


async def parse_chart_request(message: str) -> Optional[dict]:
    """Return structured chart params, or None if `message` isn't a chart request."""
    if not message or not message.strip():
        return None
    if not _has_chart_keyword(message):
        return None

    if SARVAM_MOCK_MODE:
        return _normalise_parsed(_heuristic_parse(message) or {})

    try:
        result = await call_sarvam(
            messages=[{"role": "user", "content": message}],
            system_prompt=PARSE_SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=400,
            tools=[PARSE_TOOL],
            reasoning_effort=None,
        )
        tool_call = result.get("tool_call") if isinstance(result, dict) else None
        if tool_call and tool_call.get("name") == "parse_chart_request":
            normalised = _normalise_parsed(tool_call.get("arguments") or {})
            if normalised:
                logger.info(
                    "Chart parse: symbols=%s period=%s type=%s",
                    normalised["symbols"], normalised["period"], normalised["chart_type"],
                )
                return normalised
        content = result.get("content") if isinstance(result, dict) else ""
        if content:
            try:
                cleaned = re.sub(r"```json|```", "", content).strip()
                normalised = _normalise_parsed(json.loads(cleaned))
                if normalised:
                    return normalised
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Sarvam chart parse failed, falling back to heuristic: {e}")

    return _normalise_parsed(_heuristic_parse(message) or {})
