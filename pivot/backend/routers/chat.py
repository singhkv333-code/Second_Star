"""Chat router — thin, delegates to backend.services.chat_service.

This is a deliberate rewrite: the previous router had ~600 lines of intent
routing, regex shortcuts, classifier calls, and canned responses. Those
concerns now live behind ``ChatService``. The router's job is auth, request
shape, slash-command shortcuts, and serialising the response.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.jwt_handler import get_user_id_from_token
from backend.database import get_db
from backend.models import User
from backend.services.chat_service import ChatService, UserContext


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])
_chat_service = ChatService()


# ---- Request shape -----------------------------------------------------


class ChatRequest(BaseModel):
    messages: list                          # client-carried history (also used as conv_id seed)
    include_portfolio_context: bool = True
    conversation_id: Optional[str] = None   # explicit Redis key when client tracks it
    # Optional mode hint from the FE (composer mode pills). When set,
    # the chat service deterministically routes the tool surface to
    # the matching family — bypassing the keyword classifier. None
    # means "let the classifier decide", which is the default.
    mode: Optional[str] = None              # "automation" | "agent" | "backtest"


# ---- Helpers -----------------------------------------------------------


def _auth(authorization: str) -> int:
    if not authorization:
        # In development mode fall back to the default dev user so the
        # chat UI works without a login flow.
        from backend.config import settings as _cfg
        if getattr(_cfg, "app_env", "development") == "development":
            return 1
        raise HTTPException(401, "Missing token")
    token = authorization.replace("Bearer ", "")
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(401, "Invalid token")
    return user_id


def _last_user_message(messages: list) -> str:
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            return (m.get("content") or "").strip()
    return ""


def _kite_token_for(db: Session, user_id: int) -> str:
    user = db.query(User).filter(User.id == user_id).first()
    if user and getattr(user, "kite_session", None):
        return user.kite_session.access_token
    return "mock_token"


def _conv_id(req: ChatRequest, user_id: int) -> str:
    """Per-user conversation id. The client can override with an explicit one."""
    if req.conversation_id:
        return req.conversation_id
    return f"u{user_id}"


# ---- Slash-command shortcuts (deterministic, user-typed, kept) ---------


_SCREEN_PREFIX_RE = re.compile(
    r"^/screen\s+(?P<expr>.+?)(?:\s+@\s*(?P<date>\d{4}-\d{2}-\d{2}))?\s*$",
    re.IGNORECASE,
)
_BT_PREFIX_RE = re.compile(
    r"^/(?:expr-?backtest|fund-?backtest)\s+(?P<expr>.+?)\s+"
    r"from\s+(?P<start>\d{4}-\d{2}-\d{2})\s+to\s+(?P<end>\d{4}-\d{2}-\d{2})"
    r"(?:\s+rebalance\s+(?P<rb>[DWMQYdwmqy]))?\s*$",
    re.IGNORECASE,
)

# Natural-language backtest patterns — work without leading slash.
# Accepts:
#   "backtest pe_ratio < 15 from 2020-01-01 to 2024-12-31"
#   "backtest pe_ratio < 15 from 2020 to 2024 quarterly"
#   "run a backtest on roe > 18 from 2018 to 2024 rebalance Q"
_NL_BT_RE = re.compile(
    r"^(?:run\s+(?:a\s+)?)?backtest\s+(?:on\s+)?(?P<expr>.+?)\s+"
    r"from\s+(?P<start>\d{4}(?:-\d{2}-\d{2})?)\s+"
    r"to\s+(?P<end>\d{4}(?:-\d{2}-\d{2})?)"
    r"(?:\s+(?:rebalance\s+(?P<rb>[DWMQYdwmqy])"
    r"|(?P<word>daily|weekly|monthly|quarterly|yearly)))?\s*$",
    re.IGNORECASE,
)

# Indicator backtest — single-symbol RSI/SMA/EMA strategies. Runs off
# yfinance + pandas_ta, no fundamentals DB required. Two phrasings:
#
# A) "<verb>? <SYMBOL> when[ever] (its )?<rsi|sma|ema>( <N>)? (op) <num>"
#    e.g. "backtest buying reliance whenever its rsi drops below 50"
#         "buy infy when rsi falls under 30"
# B) "<verb>? <SYMBOL> when[ever] (it )?cross(es|ed) (above|below)? <N> (sma|ema)"
#    e.g. "buying reliance whenever it crossed 200 ema"
#         "buy tcs when it crosses above 50 sma"
# Verb fragment that means "below" — covers past + present + over/under/below.
_VERB_DOWN = (
    r"(?:drops?|dropped|falls?|fell|crosses?|crossed|breaks?|broke|"
    r"goes?\s+(?:below|under)|moves?\s+(?:below|under))"
    r"\s+(?:below|under)"
    r"|<"
)
_VERB_UP = (
    r"(?:rises?|rose|crosses?|crossed|breaks?|broke|"
    r"goes?\s+(?:above|over)|moves?\s+(?:above|over))"
    r"\s+(?:above|over)"
    r"|>"
)
_VERB_ANY_DIR = f"(?P<op>{_VERB_DOWN}|{_VERB_UP})"

_NL_IND_RE_A = re.compile(
    r"^(?:backtest\s+)?(?:buy(?:ing)?|sell(?:ing)?|long|short)?\s*"
    r"(?P<symbol>[A-Z][A-Z0-9\-_]{1,15})\s+"
    r"(?:when(?:ever)?|on)\s+(?:its\s+|the\s+)?"
    r"(?P<indicator>rsi|sma|ema)"
    r"(?:[\(\s]+(?P<period>\d{1,3})[\)\s]*)?\s*"
    r"(?:is\s+|value\s+)?"
    + _VERB_ANY_DIR +
    r"\s+(?P<threshold>\d+(?:\.\d+)?)"
    r"(?:\s+over\s+(?:the\s+)?last\s+(?P<years>\d+)\s+years?)?"
    r"\s*$",
    re.IGNORECASE,
)
_NL_IND_RE_B = re.compile(
    r"^(?:backtest\s+)?(?:buy(?:ing)?|sell(?:ing)?|long|short)?\s*"
    r"(?P<symbol>[A-Z][A-Z0-9\-_]{1,15})\s+"
    r"(?:when(?:ever)?|on)\s+(?:it\s+|the\s+price\s+)?"
    r"(?P<op>cross(?:es|ed)?(?:\s+(?P<dir>above|below))?)\s+"
    r"(?P<period>\d{1,3})\s+"
    r"(?P<indicator>sma|ema)"
    r"(?:\s+over\s+(?:the\s+)?last\s+(?P<years>\d+)\s+years?)?"
    r"\s*$",
    re.IGNORECASE,
)
# C) Indicator name AFTER the threshold — common in casual phrasing:
#    "backtest buying reliance whenever it dropped below 50 rsi"
#    "buy infy when it falls under 30 rsi"
_NL_IND_RE_C = re.compile(
    r"^(?:backtest\s+)?(?:buy(?:ing)?|sell(?:ing)?|long|short)?\s*"
    r"(?P<symbol>[A-Z][A-Z0-9\-_]{1,15})\s+"
    r"(?:when(?:ever)?|on)\s+(?:it\s+|its\s+|the\s+(?:price\s+)?)?"
    + _VERB_ANY_DIR +
    r"\s+(?P<threshold>\d+(?:\.\d+)?)\s+"
    r"(?P<indicator>rsi|sma|ema)"
    r"(?:\s*\(?\s*(?P<period>\d{1,3})\s*\)?)?"
    r"(?:\s+over\s+(?:the\s+)?last\s+(?P<years>\d+)\s+years?)?"
    r"\s*$",
    re.IGNORECASE,
)
# "the testing period is last N years" — follow-up that re-runs the
# previous backtest with a new period. Stateful across turns: chat
# router doesn't track this; we just match the phrase to expose the N.
_NL_TESTING_PERIOD_RE = re.compile(
    r"(?:the\s+)?testing\s+period\s+is\s+(?:the\s+)?last\s+(?P<years>\d+)\s+years?",
    re.IGNORECASE,
)
# Natural-language screen — "screen roe > 18" or "find companies where pe < 15"
_NL_SCREEN_RE = re.compile(
    r"^(?:screen|find(?:\s+companies)?(?:\s+where)?)\s+(?P<expr>.+?)"
    r"(?:\s+(?:as\s+of|@)\s*(?P<date>\d{4}-\d{2}-\d{2}))?\s*$",
    re.IGNORECASE,
)
_REBALANCE_WORD_MAP = {
    "daily": "D", "weekly": "W", "monthly": "M",
    "quarterly": "Q", "yearly": "Y",
}

# Hard gate for natural-language backtest paths. The heuristic parsers
# below used to fire on phrasings like "buy reliance whenever rsi drops
# below 50", "what if I had bought INFY", "how would TCS have done" —
# all of which the user wanted treated as agent-build / chat intents,
# not historical backtests. Now we require the literal word.
_HAS_BACKTEST_WORD_RE = re.compile(r"\bbacktest(?:ed|ing|s)?\b", re.IGNORECASE)

# Follow-up backtest patterns ("the same with X=25 instead of 30",
# "again but with Y", "do it again"). These references depend on the
# previous turn's backtest — the router's fast-path parsers can't see
# the prior symbol/indicator, so they crash trying to extract one from
# a message that doesn't contain it ("backtest the same with RSI
# threshold 25 instead of 30" used to extract symbol="same" and blow up
# yfinance with a type error). Let these fall through to the LLM, which
# CAN read the prior turn's context.
_FOLLOWUP_BT_RE = re.compile(
    r"\bthe\s+same\b"
    r"|\binstead\s+of\s+\d"
    r"|\bsame\s+with\b"
    r"|\bagain\s+with\b"
    r"|\bbut\s+with\s+(?:rsi|sma|ema|threshold|period)",
    re.IGNORECASE,
)


def _normalize_date_input(s: str) -> str:
    """Accept either a YYYY date (→ Jan 1) or full YYYY-MM-DD."""
    s = s.strip()
    return f"{s}-01-01" if re.fullmatch(r"\d{4}", s) else s


async def _maybe_run_slash(text: str) -> Optional[dict]:
    """Match either explicit slash commands OR natural-language patterns
    that map to deterministic backend tools (backtest / screen). Both
    short-circuit before the LLM is called, so they work even when the
    LLM provider is down."""
    body = (text or "").strip()
    if not body:
        return None

    # 1. Slash commands (legacy).
    if body.startswith("/"):
        if (m := _BT_PREFIX_RE.match(body)):
            return await _run_expr_backtest(
                expression=m.group("expr").strip(),
                start=m.group("start"), end=m.group("end"),
                rebalance=(m.group("rb") or "Q").upper(),
            )
        if (m := _SCREEN_PREFIX_RE.match(body)):
            return await _run_expr_screen(
                expression=m.group("expr").strip(),
                as_of=m.group("date"),
            )
        return None

    # 2. Indicator backtest (single-symbol, RSI/SMA/EMA via yfinance).
    #    Strict regex first (cheap, deterministic for canonical phrasings),
    #    then a permissive heuristic parser for anything else.
    #
    #    Hard gate: the prompt must literally contain "backtest". Without
    #    this, casual phrasings like "buy reliance when rsi drops" or
    #    "what if I had bought INFY" hijacked the LLM path and ran a
    #    backtest the user never asked for.
    has_backtest_word = bool(_HAS_BACKTEST_WORD_RE.search(body))
    # Follow-up bypass: phrasings that reference a prior backtest
    # ("the same with X", "instead of N") need conversation context
    # the router can't see. Punt to the LLM. WHY at the top: any of
    # the heuristic parsers below would otherwise extract garbage
    # ("same" as symbol) and crash the backtester.
    if has_backtest_word and _FOLLOWUP_BT_RE.search(body):
        return None
    if has_backtest_word and not _looks_like_agent_intent(body):
        if (
            (m := _NL_IND_RE_A.match(body))
            or (m := _NL_IND_RE_B.match(body))
            or (m := _NL_IND_RE_C.match(body))
        ):
            return await _run_indicator_backtest(m)
        if (parsed := _heuristic_indicator_intent(body)) is not None:
            return await _run_indicator_backtest_dict(parsed)

    # 3. Natural-language fundamentals backtest. Same gate.
    if has_backtest_word and (m := _NL_BT_RE.match(body)):
        rb = m.group("rb")
        if not rb and (word := m.group("word")):
            rb = _REBALANCE_WORD_MAP.get(word.lower(), "Q")
        return await _run_expr_backtest(
            expression=m.group("expr").strip(),
            start=_normalize_date_input(m.group("start")),
            end=_normalize_date_input(m.group("end")),
            rebalance=(rb or "Q").upper(),
        )
    # 4. Natural-language screen.
    if (m := _NL_SCREEN_RE.match(body)):
        return await _run_expr_screen(
            expression=m.group("expr").strip(),
            as_of=m.group("date"),
        )

    # 5. Open/close intraday roundtrip backtest.
    if has_backtest_word and (parsed := _parse_open_close_backtest(body)):
        return await _run_open_close_backtest(**parsed)

    # 5b. Weekly close → next-week open swing backtest.
    #     "buy at last trading day of each week and sell at the open of
    #     next week" / "buy weekly close sell next week open" /
    #     "weekend hold on RELIANCE".
    if has_backtest_word and (parsed := _parse_weekly_swing_backtest(body)):
        return await _run_weekly_swing_backtest(**parsed)

    # 6. "backtest …" with no parsable shape → deterministic
    #    capability-gap message. Without this, prompts like
    #    "backtest <something not yet supported>" used to fall
    #    through to the LLM, which would call run_backtest with a
    #    missing `trigger_condition` and surface the opaque
    #    "what's the trigger condition?" question. Be honest.
    if has_backtest_word:
        return _unsupported_backtest_message(body)
    return None


# Open/close intraday roundtrip — "backtest buy open sell close on
# <SYMBOL>", "backtest open close roundtrip on RELIANCE", "backtest
# RELIANCE open to close every day", etc. Captures: symbol, optional
# years window. The actual backtester (services.open_close_backtest)
# only needs symbol + period.
_NL_OPEN_CLOSE_RE = re.compile(
    r"\bbacktest\b.{0,80}?"
    r"\b(?:buy(?:ing)?\s+)?open\b.{0,40}?"
    r"\b(?:and\s+)?(?:sell(?:ing)?\s+)?close\b"
    r".{0,80}?\bon\s+(?P<symbol>[A-Z][A-Z0-9\-_]{1,15})\b"
    r"(?:.{0,80}?\b(?:over|for|in)\s+(?:the\s+)?(?:last|past)\s+"
    r"(?P<years>\d+)\s+years?\b)?",
    re.IGNORECASE | re.DOTALL,
)
# Permissive variant: "backtest <SYMBOL> open close" / "backtest
# <SYMBOL> open to close" — symbol BEFORE open/close. Catches the
# shape the user used in the report.
_NL_OPEN_CLOSE_RE_B = re.compile(
    r"\bbacktest\b\s+(?P<symbol>[A-Z][A-Z0-9\-_]{1,15})\b"
    r".{0,40}?\bopen\b.{0,40}?\bclose\b"
    r"(?:.{0,80}?\b(?:over|for|in)\s+(?:the\s+)?(?:last|past)\s+"
    r"(?P<years>\d+)\s+years?\b)?",
    re.IGNORECASE | re.DOTALL,
)


def _parse_open_close_backtest(body: str) -> dict | None:
    """Extract {symbol, years} from a free-form 'backtest open/close'
    request. Returns None if neither shape matches."""
    for rx in (_NL_OPEN_CLOSE_RE, _NL_OPEN_CLOSE_RE_B):
        m = rx.search(body)
        if m:
            sym = m.group("symbol").upper()
            if sym in {"OPEN", "CLOSE"}:
                continue
            years = int(m.group("years") or 5)
            return {"symbol": sym, "years": years}
    return None


# ── Weekly close → next-week open swing ─────────────────────────────
#
# Phrasings the user types:
#   "buy at last trading day of each week and sell at open of next week"
#   "buy weekly close sell next week open on RELIANCE"
#   "buy on Friday close and sell on Monday open"
#   "weekend hold on RELIANCE"
#
# We don't try to capture every variant; the gates are loose, and we
# extract the symbol via verb-anchor like the indicator parser.

_NL_WEEKLY_SWING_RE = re.compile(
    r"\bbacktest\b.{0,200}?"
    r"(?:"
    # Pattern A: "last trading day of (each|the) week" + "open of (next|the next) week"
    r"\blast\s+(?:trading\s+)?day\s+of\s+(?:each|every|the)\s+week\b"
    r".{0,80}?\bopen\s+of\s+(?:next|the\s+next|the)\s+week\b"
    # Pattern B: "weekly close" + "next week open"
    r"|\bweekly\s+close\b.{0,80}?\bnext\s+week(?:\s+'?s)?\s+open\b"
    # Pattern C: "Friday close" + "Monday open" (most common informal)
    r"|\bfriday\s+(?:'?s\s+)?close\b.{0,40}?\bmonday\s+(?:'?s\s+)?open\b"
    # Pattern D: "weekend hold"
    r"|\bweekend\s+hold\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_YEARS_BACKTEST_RE = re.compile(
    r"\b(?:over|for|in)\s+(?:the\s+)?(?:last|past)\s+(?P<years>\d+)\s+years?\b",
    re.IGNORECASE,
)


def _parse_weekly_swing_backtest(body: str) -> dict | None:
    """Extract {symbol, years} from a weekly-swing backtest request.
    Returns None if no match.

    Symbol extraction reuses the indicator-backtest verb-anchor logic
    so phrasings like "buy reliance at the last trading day of each
    week" find RELIANCE.
    """
    if not _NL_WEEKLY_SWING_RE.search(body):
        return None

    _STOP = {
        "EACH", "EVERY", "THE", "NEXT", "LAST", "WEEK", "WEEKS",
        "FRIDAY", "MONDAY", "OPEN", "CLOSE", "DAY", "DAYS",
        "TRADING", "WEEKEND", "HOLD", "BUY", "BUYING", "BOUGHT",
        "SELL", "SELLING", "SOLD", "ON", "FOR", "AT", "OF", "FROM",
        "TO", "AND", "THEN", "BACKTEST", "RUN", "OVER", "PAST",
        "YEARS", "YEAR", "MONTHS", "MONTH",
    }

    sym = None
    # Walk every word-pair shaped (verb word) — non-overlapping — but
    # also consider every word that DIRECTLY follows another verb.
    # The earlier finditer-only approach missed "buying reliance"
    # when "on buying" was matched first (regex doesn't backtrack into
    # already-consumed positions). Strategy: tokenize, then for each
    # verb token, look at the NEXT token; pick the first non-stopword.
    tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z0-9\-_]*", body)]
    verbs = {"buy", "buying", "sell", "selling", "bought", "sold",
             "on", "for", "of", "long", "short"}
    for i, tok in enumerate(tokens[:-1]):
        if tok.lower() in verbs:
            nxt = tokens[i + 1]
            cand = nxt.upper()
            if cand not in _STOP and len(cand) >= 3:
                sym = cand
                break
    if sym is None:
        # Fallback: first ALL-CAPS token in the body that isn't a stopword.
        for cap in re.finditer(r"\b([A-Z][A-Z0-9\-_]{2,15})\b", body):
            cand = cap.group(1)
            if cand not in _STOP:
                sym = cand
                break
    if sym is None:
        return None
    # Resolve common-name aliases to canonical NSE tickers (zomato →
    # ETERNAL, hdfc → HDFCBANK). Same map yfinance_service uses for
    # snapshot resolution — keeps behaviour consistent across surfaces.
    try:
        from backend.market.yfinance_service import NAME_TO_TICKER
        alias = NAME_TO_TICKER.get(sym.lower())
        if alias and alias.upper() != sym:
            sym = alias.upper()
    except Exception:
        pass
    years_m = _YEARS_BACKTEST_RE.search(body)
    years = int(years_m.group("years")) if years_m else 5
    return {"symbol": sym, "years": years}


async def _run_weekly_swing_backtest(*, symbol: str, years: int) -> Optional[dict]:
    """Call services.open_close_backtest.run_weekly_swing_backtest and
    shape the result for the FE's FinancialBacktestCard (same card as
    the daily roundtrip — different summary text and different metrics).

    Returns None on data-fetch shortfall so the LLM can take over.
    """
    import asyncio
    from backend.services.open_close_backtest import run_weekly_swing_backtest

    yf_period = f"{years}y"
    try:
        result = await asyncio.to_thread(
            run_weekly_swing_backtest, symbol=symbol, period=yf_period,
        )
    except ValueError as e:
        if "insufficient data" in str(e).lower():
            return None
        return _slash_error(f"Weekly-swing backtest error: {e}")
    except Exception as e:
        return _slash_error(f"Weekly-swing backtest failed: {str(e)[:200]}")

    expression = (
        f"buy {symbol} at last trading day's close, sell at "
        f"next week's open ({years}y window)"
    )
    payload = {
        "expression": expression,
        "start": result.start_iso,
        "end": result.end_iso,
        "rebalance": "Weekly",
        "metrics": result.metrics,
        "equity_curve": result.equity_curve,
        "benchmark_curve": result.benchmark_curve,
        "rebalances": [],
        "n_trades": result.n_trades,
        "warnings": [],
    }
    return {
        "response": result.summary_text,
        "intent": "WEEKLY_SWING_BACKTEST",
        "expr_backtest_data": payload,
        "raw_data": {
            "_render_hint": "financial_backtest_chart",
            **payload,
        },
        "screen_data": None, "backtest_data": None,
        "chart_data": None, "logiccard": None,
        "requires_clarification": False,
    }


async def _run_open_close_backtest(*, symbol: str, years: int) -> Optional[dict]:
    """Call services.open_close_backtest and shape the result for the
    FE's FinancialBacktestCard (we reuse that card; no new component
    needed). Returns None on data-fetch shortfall so the caller can
    fall through to the LLM."""
    import asyncio
    from backend.services.open_close_backtest import run_open_close_backtest

    yf_period = f"{years}y"
    try:
        result = await asyncio.to_thread(
            run_open_close_backtest, symbol=symbol, period=yf_period,
        )
    except ValueError as e:
        if "insufficient data" in str(e).lower():
            return None
        return _slash_error(f"Open/close backtest error: {e}")
    except Exception as e:
        return _slash_error(f"Open/close backtest failed: {str(e)[:200]}")

    expression = (
        f"buy {symbol} at open, sell at close (every weekday, "
        f"{years}y window)"
    )
    payload = {
        "expression": expression,
        "start": result.start_iso,
        "end": result.end_iso,
        "rebalance": "Daily",
        "metrics": result.metrics,
        "equity_curve": result.equity_curve,
        "benchmark_curve": result.benchmark_curve,
        "rebalances": [],
        "n_trades": result.n_trades,
        "warnings": [],
    }
    return {
        "response": result.summary_text,
        "intent": "OPEN_CLOSE_BACKTEST",
        "expr_backtest_data": payload,
        "raw_data": {
            "_render_hint": "financial_backtest_chart",
            **payload,
        },
        "screen_data": None, "backtest_data": None,
        "chart_data": None, "logiccard": None,
        "requires_clarification": False,
    }


# Indicator / fundamentals tokens — if any of these is in the body,
# the strict regexes above SHOULD have matched (and we wouldn't be
# here). Presence here means the user wrote a backtest ask in a
# non-canonical shape; the unsupported-message branch below tells
# them what shapes ARE supported.
_BACKTESTABLE_TOKENS_RE = re.compile(
    r"\b(rsi|sma|ema|pe|pe_ratio|p/e|p/b|pb|roe|roa|"
    r"market\s*cap|debt|earnings|dividend\s*yield)\b",
    re.IGNORECASE,
)


def _unsupported_backtest_message(body: str) -> dict:
    """User said `backtest …` but nothing parseable matched. Surface
    a focused message naming what Pivot CAN backtest, what it can't,
    and offering the agent-build path."""
    body_lc = body.lower()
    if _BACKTESTABLE_TOKENS_RE.search(body_lc):
        # Looks like it WANTS to be backtestable but the shape
        # isn't quite right — give the canonical phrasings.
        text = (
            "I couldn't parse that backtest shape. The two formats "
            "I can run:\n\n"
            "- **Indicator on a single stock** — `backtest RELIANCE "
            "when its RSI drops below 30 over the last 5 years`\n"
            "- **Fundamentals expression on the universe** — "
            "`backtest pe_ratio < 15 and roe > 18 from 2020-01-01 "
            "to 2024-12-31 quarterly`\n\n"
            "Could you restate using one of those shapes?"
        )
    else:
        text = (
            "I can backtest these strategy shapes:\n\n"
            "- **Indicator on a single stock** (RSI / SMA / EMA on "
            "daily close) — e.g. `backtest RELIANCE when its RSI "
            "drops below 30 over the last 5 years`\n"
            "- **Open → close intraday roundtrip** — e.g. "
            "`backtest buy open sell close on RELIANCE over the "
            "last 5 years`\n"
            "- **Weekly close → next-week open swing** — e.g. "
            "`backtest buying RELIANCE at the last trading day of "
            "each week and selling at the open of next week over "
            "the last 5 years` (weekend-hold variant)\n"
            "- **Fundamentals expression on the universe** — e.g. "
            "`backtest pe_ratio < 15 from 2020-01-01 to 2024-12-31 "
            "quarterly`\n\n"
            "Calendar SIPs (every Monday) and other order-flow "
            "shapes aren't backtestable yet — but I can draft a "
            "live agent for those. Which would you like?"
        )
    return {
        "response": text, "intent": "BACKTEST_UNSUPPORTED",
        "screen_data": None, "expr_backtest_data": None,
        "backtest_data": None, "chart_data": None,
        "logiccard": None, "requires_clarification": False,
        "raw_data": {"_render_hint": "ask_user"},
    }


def _normalize_op(op_text: str, direction: str | None = None) -> str:
    """Map a phrase like 'drops below' / 'crossed above' to the
    indicator-backtest operator vocabulary."""
    s = op_text.lower().strip()
    if "below" in s or "<" in s or "drop" in s or "fall" in s or "fell" in s:
        return "<"
    if "above" in s or ">" in s or "rise" in s or "rose" in s or "goes" in s:
        return ">"
    if "cross" in s:
        # "crossed 200 ema" with no direction → above (most common intent)
        if direction == "below":
            return "crosses_below"
        return "crosses_above"
    return "<"


_DEFAULT_PERIOD_BY_INDICATOR = {"rsi": 14, "sma": 50, "ema": 50}


# Heuristic parser — runs after the strict regexes fail.
#
# Trigger: message contains "backtest" OR starts with a buy/sell/long/short
# verb followed by "<sym> when[ever]". We then extract pieces independently
# rather than trying to constrain word order:
#   - indicator    (rsi|sma|ema)         required
#   - op           (< / > / crosses_*)   inferred from verbs
#   - threshold    (number nearest to a directional cue)
#   - symbol       (first non-stopword content token)
#   - years        ("N year(s)" anywhere in the sentence)
# This is intentionally a fallback; canonical phrasings should still hit
# the strict regexes for predictability.
_INDICATOR_RE = re.compile(r"\b(rsi|sma|ema)\b", re.IGNORECASE)
# Words that strongly signal historical-backtest intent. Used as the
# trigger gate for `_heuristic_indicator_intent`. Includes the literal
# "backtest" word AND counterfactual phrasings ("what if I had
# bought…", "how would X have done…", "over the (last|past) N…")
# that the strict regexes don't match.
_BACKTEST_TRIGGER_RE = re.compile(
    r"\bbacktest\b"
    r"|\bwhat\s+if\s+(?:i\s+)?(?:had|i)\b"
    r"|\bhow\s+(?:would|did)\b"
    r"|\bif\s+i\s+had\s+(?:bought|invested|sold)\b"
    r"|\b(?:over|in)\s+the\s+(?:last|past)\s+\d+\s+(?:year|month|week)s?\b"
    r"|\bsimulate\b"
    r"|\bhistorical(?:ly)?\b",
    re.IGNORECASE,
)


# Phrases that signal "user wants a FUTURE-action agent, not a historical
# backtest" — even when the wording matches the indicator-backtest regex.
# When any of these match AND the prompt doesn't say "backtest" outright,
# we defer to the LLM's propose_workflow path.
_AGENT_INTENT_SIGNALS_RE = re.compile(
    r"\bmy\s+[A-Za-z]"              # "sell MY infy" / "in MY portfolio"
    r"|\bset\s+up\b"
    r"|\bcreate\b"
    r"|\bbuild\b"
    r"|\bwatch(?:es)?\b"
    r"|\bmonitor\b"
    r"|\bagent\b"
    r"|\bstrategy\b"
    r"|\bautomation\b"
    r"|\balert\s+me\b"
    r"|\bnotify\s+me\b"
    r"|\bevery\s+(?:monday|tuesday|wednesday|thursday|friday|"
    r"weekday|day|week|hour)\b"
    # Quantity in the verb phrase = forward-looking order, NOT a
    # historical backtest. "buy 10 INFY when RSI < 30" is an agent
    # ask; "what if I had bought INFY whenever RSI < 30" is a backtest.
    # Backtests don't carry a share count.
    r"|\b(?:buy|sell)\s+\d+\s+[A-Z][A-Z0-9\-_]+\b"
    # SL / take-profit / stop-loss phrasing is always forward-looking.
    r"|\b(?:set|place|put|create|add)\s+(?:a\s+|an\s+)?"
    r"(?:[\d.]+\s*%\s+)?(?:stop[- ]?loss|sl|stoploss|trailing\s+stop|"
    r"take[- ]?profit|tp|target)\b"
    # "if X dips/drops/rises N% then Y" — conditional rule, not a backtest.
    r"|\bif\b[^\.]{0,120}\b(?:dips?|drops?|falls?|rises?|crosses?|hits?|reaches?)\b",
    re.IGNORECASE,
)


def _looks_like_agent_intent(body: str) -> bool:
    """True when the prompt mentions building / watching / monitoring
    something — i.e., the user wants a future-action workflow rather
    than a historical backtest. The presence of the word ``backtest``
    overrides this (an explicit backtest is always a backtest)."""
    if _BACKTEST_TRIGGER_RE.search(body):
        return False
    return bool(_AGENT_INTENT_SIGNALS_RE.search(body))
_VERB_START_RE = re.compile(
    r"^(buy(?:ing)?|sell(?:ing)?|long|short)\b", re.IGNORECASE,
)
_THRESHOLD_NEAR_DIR_RE = re.compile(
    r"(?:below|under|<|above|over|>|of|at|=)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_INDICATOR_PERIOD_BEFORE_RE = re.compile(
    r"(\d+)\s*(?:-?day\s+)?(?:rsi|sma|ema)\b", re.IGNORECASE,
)
_YEARS_RE = re.compile(r"(?:last|past)\s+(\d+)\s+years?\b", re.IGNORECASE)
_DIRECTION_DOWN_RE = re.compile(
    r"\b(?:drops?|dropped|drop|falls?|fell|below|under)\b", re.IGNORECASE,
)
_DIRECTION_UP_RE = re.compile(
    r"\b(?:rises?|rose|above|over|exceed(?:s|ed)?|breaks?\s+out)\b",
    re.IGNORECASE,
)
_DIRECTION_CROSS_RE = re.compile(
    r"\b(cross(?:es|ed)?)(?:\s+(above|below))?\b", re.IGNORECASE,
)

# Stopwords excluded when picking a symbol candidate. Lowercase; matching
# is done case-insensitively. Includes filler words ("what", "happens"),
# sentence connectors, indicator/direction terms, conversational openers
# ("okay", "could", "kindly"), etc. The `okay/can/you/where/strategy`
# additions came from a real failure: a user prompt starting with "Okay
# and can you backtest a strategy where I buy reliance whenever..." was
# picking "Okay" as the ticker because "okay" wasn't on the list.
_SYMBOL_STOPWORDS = frozenset({
    "backtest", "buy", "buying", "sell", "selling", "long", "short",
    "what", "happens", "happen", "when", "whenever", "the", "last",
    "past", "years", "year", "rsi", "sma", "ema", "drops", "dropped",
    "drop", "falls", "fell", "below", "above", "of", "in", "on",
    "over", "under", "with", "at", "and", "or", "if", "for", "to",
    "from", "rose", "rises", "rise", "crosses", "crossed", "cross",
    "exceed", "exceeds", "exceeded", "by", "is", "as", "it", "its",
    "a", "an", "this", "that", "value", "price", "show", "showed",
    "do", "does", "did", "i", "me", "my", "we", "our", "your",
    "any", "some", "happens", "run",
    # Conversational openers / fillers.
    "okay", "ok", "alright", "right", "yeah", "yes", "yep", "sure",
    "please", "kindly", "could", "would", "should", "can", "may",
    "might", "shall", "will", "want", "wanna", "let", "lets",
    # Question-like structure words.
    "where", "which", "who", "whom", "whose", "how",
    # Vague nouns that aren't tickers.
    "strategy", "backtested", "trade", "trades", "share", "shares",
    "thing", "stuff", "anything", "nothing", "something",
    "way", "case", "test", "stock", "stocks", "name", "ticker",
    # Pronouns / demonstratives missed before.
    "you", "he", "she", "they", "we", "us", "them", "him", "her",
    "all", "both", "each", "every", "few", "many", "much",
    # Aux verbs / conjunctions missed before.
    "be", "been", "being", "am", "are", "was", "were", "have",
    "has", "had", "having", "but", "than", "then", "so", "yet",
    "though", "although", "while", "until", "since", "because",
    "after", "before", "during",
    # Follow-up / continuation words. WHY: "backtest the same with RSI
    # threshold 25 instead of 30" used to extract symbol="same" and
    # crash the indicator backtester. With these added, the heuristic
    # parser bails (no symbol), the message falls through to the LLM,
    # which can call run_backtest with the previous symbol from context.
    "same", "previous", "again", "instead", "threshold", "different",
    "another", "other", "only", "just",
})


def _heuristic_indicator_intent(body: str) -> dict | None:
    """Permissive parser: pulls indicator + symbol + threshold + direction
    + period out of free-form chat input. Returns the same dict shape the
    regex `m.groupdict()` would produce, or None if no indicator backtest
    intent is detectable."""
    # Trigger gate.
    if not (_BACKTEST_TRIGGER_RE.search(body) or _VERB_START_RE.match(body)):
        return None
    # Indicator is required.
    ind_m = _INDICATOR_RE.search(body)
    if not ind_m:
        return None
    indicator = ind_m.group(1).lower()
    # Agent-intent disambiguation — when the user says "sell MY infy"
    # or "set up", "create a strategy", "watch", "alert me", they want
    # a future-action agent, not a historical backtest. Bail out so
    # the chat hop's propose_workflow path handles it. The shortcut
    # still fires when the prompt has the word "backtest" explicitly,
    # because that's an unambiguous signal.
    if not _BACKTEST_TRIGGER_RE.search(body):
        agent_signals = (
            r"\bmy\s+[A-Za-z]",        # "sell MY infy"
            r"\bset\s+up\b",
            r"\bcreate\b",
            r"\bbuild\b",
            r"\bwatch(?:es)?\b",
            r"\bmonitor\b",
            r"\bagent\b",
            r"\bstrategy\b",
            r"\bautomation\b",
            r"\balert\s+me\b",
            r"\bnotify\s+me\b",
            r"\bevery\s+(monday|tuesday|wednesday|thursday|friday|"
            r"weekday|day|week|hour)\b",
        )
        for pat in agent_signals:
            if re.search(pat, body, re.IGNORECASE):
                return None

    # Direction.
    if _DIRECTION_CROSS_RE.search(body):
        cm = _DIRECTION_CROSS_RE.search(body)
        direction = (cm.group(2) or "").lower() if cm else ""
        op = "crosses_below" if direction == "below" else "crosses_above"
    elif _DIRECTION_UP_RE.search(body) and not _DIRECTION_DOWN_RE.search(body):
        op = ">"
    else:
        # Default to `<` because most casual queries mean "buy when X
        # drops below" (oversold / dip-buy). Tests for this default
        # in test_chat_nl_shortcuts.
        op = "<"

    # Threshold — prefer a number that follows a directional cue
    # ("below 50", "above 30", "of 50"), then fall back to any number
    # next to the indicator name.
    thr_m = _THRESHOLD_NEAR_DIR_RE.search(body)
    threshold = float(thr_m.group(1)) if thr_m else None

    # Indicator period — for SMA/EMA only ("200 EMA").
    ip_m = _INDICATOR_PERIOD_BEFORE_RE.search(body)
    indicator_period = (
        int(ip_m.group(1)) if ip_m else _DEFAULT_PERIOD_BY_INDICATOR[indicator]
    )

    # If RSI and threshold still missing, no point continuing.
    if indicator == "rsi" and threshold is None:
        # Try any number that isn't the year and isn't the indicator period.
        all_nums = re.findall(r"\b(\d+(?:\.\d+)?)\b", body)
        years_m_local = _YEARS_RE.search(body)
        years_str = years_m_local.group(1) if years_m_local else ""
        candidates = [n for n in all_nums if n != years_str]
        if candidates:
            threshold = float(candidates[0])
    if indicator == "rsi" and threshold is None:
        return None
    # For SMA/EMA the threshold is implicit (= indicator period).
    if threshold is None:
        threshold = float(indicator_period)

    # Years.
    years_m = _YEARS_RE.search(body)
    years = int(years_m.group(1)) if years_m else 5

    # Symbol extraction — two-stage:
    #   1. Look for the noun immediately after a buy/sell/long/short verb.
    #      This is the strongest signal — natural-language trade descriptions
    #      always say the verb-then-instrument ("buy reliance", "sell INFY").
    #   2. Fall back to the first non-stopword content token.
    # Stage 1 closes the "Okay and can you backtest a strategy where I buy
    # reliance" failure: the verb-anchor finds "reliance" verb-anchored
    # rather than "Okay" position-anchored.
    symbol: str | None = None
    verb_anchor = re.search(
        r"\b(?:buy|buying|bought|sell|selling|sold|long|short|backtest)\s+"
        r"(?:(?:on|some|me|the)\s+)*"  # absorb tiny filler words
        r"([A-Za-z][A-Za-z0-9\-_]{1,15})\b",
        body,
        re.IGNORECASE,
    )
    if verb_anchor:
        candidate = verb_anchor.group(1)
        if candidate.lower() not in _SYMBOL_STOPWORDS:
            symbol = candidate
    if symbol is None:
        # Stage 2 — first non-stopword content token.
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-_]+", body)
        for tok in tokens:
            if tok.lower() in _SYMBOL_STOPWORDS:
                continue
            if len(tok) < 2 or len(tok) > 16:
                continue
            symbol = tok
            break
    if symbol is None:
        return None

    return {
        "symbol": symbol,
        "indicator": indicator,
        "period": str(indicator_period),
        "op": "drops below" if op == "<" else (
            "rises above" if op == ">" else "crosses"
        ),
        "dir": "below" if op == "crosses_below" else (
            "above" if op == "crosses_above" else None
        ),
        "threshold": str(threshold),
        "years": str(years),
    }


async def _run_indicator_backtest_dict(gd: dict) -> Optional[dict]:
    """Heuristic-parser variant — takes a plain dict instead of a regex
    match, otherwise identical to _run_indicator_backtest."""
    import asyncio
    from backend.services.indicator_backtest import run_indicator_backtest

    symbol = gd["symbol"].upper()
    indicator = gd["indicator"].lower()
    period = int(gd.get("period")) if gd.get("period") else _DEFAULT_PERIOD_BY_INDICATOR[indicator]
    operator = _normalize_op(gd.get("op", "<"), gd.get("dir"))
    threshold = float(gd.get("threshold") or period)
    years = int(gd.get("years") or 5)
    yf_period = f"{years}y"
    try:
        result = await asyncio.to_thread(
            run_indicator_backtest,
            symbol=symbol, indicator=indicator,  # type: ignore[arg-type]
            indicator_period=period, operator=operator,  # type: ignore[arg-type]
            threshold=threshold, period=yf_period,
        )
    except ValueError as e:
        # Data-fetch shortfall (yfinance returned 0 bars, rate-limit,
        # symbol mis-spelt) → fall through to the LLM hop with
        # `run_backtest` available. Surfacing the raw error stalled
        # the conversation; the LLM can at least explain or offer an
        # alternative window/symbol.
        if "insufficient data" in str(e).lower():
            return None
        return _slash_error(f"Backtest error: {e}")
    except Exception as e:
        return _slash_error(f"Backtest failed: {str(e)[:200]}")
    return {
        "response": result.summary_text,
        "intent": "INDICATOR_BACKTEST",
        "screen_data": None, "expr_backtest_data": None, "backtest_data": None,
        "chart_data": None, "logiccard": None, "requires_clarification": False,
        "raw_data": {
            "_render_hint": "indicator_backtest_chart",
            "symbol": result.symbol,
            "indicator": result.indicator,
            "indicator_period": result.indicator_period,
            "operator": result.operator,
            "threshold": result.threshold,
            "period_label": result.period_label,
            "price_curve": result.price_curve,
            "equity_curve": result.equity_curve,
            "indicator_curve": result.indicator_curve,
            "signals": result.signals,
            "metrics": result.metrics,
            "bench_buy_hold_return_pct": result.bench_buy_hold_return_pct,
        },
    }


async def _run_indicator_backtest(m: "re.Match[str]") -> Optional[dict]:
    """Convert a regex match into a call into
    `services.indicator_backtest.run_indicator_backtest`. The chat router
    runs in async context but the backtester is sync (CPU-bound +
    yfinance HTTP); we offload via `asyncio.to_thread`.

    Returns None on data-fetch shortfalls so the caller falls through
    to the LLM hop (which still has `run_backtest` available)."""
    import asyncio
    from backend.services.indicator_backtest import run_indicator_backtest

    gd = m.groupdict()
    symbol = gd["symbol"].upper()
    indicator = gd["indicator"].lower()
    period = int(gd.get("period")) if gd.get("period") else _DEFAULT_PERIOD_BY_INDICATOR[indicator]
    op_text = gd.get("op", "<")
    direction = gd.get("dir")
    operator = _normalize_op(op_text, direction)
    threshold = float(gd.get("threshold") or period)  # SMA/EMA: threshold = period (ignored)
    years = int(gd.get("years") or 5)
    yf_period = f"{years}y"

    try:
        result = await asyncio.to_thread(
            run_indicator_backtest,
            symbol=symbol, indicator=indicator,  # type: ignore[arg-type]
            indicator_period=period, operator=operator,  # type: ignore[arg-type]
            threshold=threshold, period=yf_period,
        )
    except ValueError as e:
        if "insufficient data" in str(e).lower():
            return None
        return _slash_error(f"Backtest error: {e}")
    except Exception as e:
        return _slash_error(f"Backtest failed: {str(e)[:200]}")

    return {
        "response": result.summary_text,
        "intent": "INDICATOR_BACKTEST",
        "screen_data": None, "expr_backtest_data": None, "backtest_data": None,
        "chart_data": None, "logiccard": None, "requires_clarification": False,
        # Flagged so the FE renders a chart card inline rather than a
        # plain bubble. The shape mirrors what IndicatorBacktestCard
        # consumes in pivot-next/components/chat/.
        "raw_data": {
            "_render_hint": "indicator_backtest_chart",
            "symbol": result.symbol,
            "indicator": result.indicator,
            "indicator_period": result.indicator_period,
            "operator": result.operator,
            "threshold": result.threshold,
            "period_label": result.period_label,
            "price_curve": result.price_curve,
            "equity_curve": result.equity_curve,
            "indicator_curve": result.indicator_curve,
            "signals": result.signals,
            "metrics": result.metrics,
            "bench_buy_hold_return_pct": result.bench_buy_hold_return_pct,
        },
    }


async def _run_expr_screen(*, expression: str, as_of: Optional[str]) -> dict:
    import asyncpg, datetime as _dt
    from backend.config import settings as _s
    from backtester.universe import universe_at
    from backtester.expr.validator import ValidationError

    base = (_s.database_url
            .replace("postgresql+psycopg2://", "postgresql://")
            .replace("postgresql+asyncpg://", "postgresql://"))
    dsn = base if "/financials" in base else f"{base.rpartition('/')[0]}/financials"

    target = _dt.date.fromisoformat(as_of) if as_of else _dt.date.today()
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=4)
    except Exception as e:
        return _slash_error(f"Could not reach the financials DB: {e}")
    try:
        try:
            snap = await universe_at(conn, expression, target)
        except ValidationError as ve:
            return _slash_error(f"Invalid expression: {ve}")
    finally:
        await conn.close()

    rows = [dict(r) for r in snap.rows[:25]]
    text = (
        f"Found {len(snap.rows)} compan{'y' if len(snap.rows) == 1 else 'ies'} "
        f"matching `{expression}` as of {target}. "
        "This is automation of your screening rule, not financial advice."
        if snap.rows else
        f"No companies match `{expression}` as of {target}. "
        "Either the universe is empty or the underlying data isn't backfilled yet."
    )
    return {
        "response": text, "intent": "EXPR_SCREEN",
        "screen_data": {
            "expression": expression, "as_of": str(target),
            "n_total": len(snap.rows), "leaf_fields": snap.leaf_fields,
            "referenced_fields": snap.referenced_fields,
            "rows": [_jsonable(r) for r in rows],
            "truncated": len(snap.rows) > 25,
        },
        "expr_backtest_data": None, "backtest_data": None, "chart_data": None,
        "logiccard": None, "requires_clarification": False,
    }


async def _run_expr_backtest(*, expression: str, start: str, end: str, rebalance: str) -> dict:
    import asyncpg, datetime as _dt
    from backend.config import settings as _s
    # The fundamentals backtester lives in the sibling `pivot-backtester`
    # package; it's an optional dependency. If it isn't installed in the
    # running interpreter, surface a clean message instead of a 500.
    try:
        from backtester.engine import BacktestConfig, run_backtest as _run_bt
        from backtester.metrics import compute_metrics
        from backtester.universe import universe_at
        from backtester.expr.validator import ValidationError
    except ModuleNotFoundError:
        return _slash_error(
            "Fundamentals backtester isn't installed in this environment. "
            "Install it with `pip install -e ../pivot-backtester` from the "
            "pivot directory, then restart the backend. Indicator "
            "backtests (RSI / SMA / EMA) still work — try `backtest "
            "<symbol> when its rsi drops below 30`."
        )

    base = (_s.database_url
            .replace("postgresql+psycopg2://", "postgresql://")
            .replace("postgresql+asyncpg://", "postgresql://"))
    dsn = base if "/financials" in base else f"{base.rpartition('/')[0]}/financials"

    try:
        cfg = BacktestConfig(
            expression=expression,
            start=_dt.date.fromisoformat(start),
            end=_dt.date.fromisoformat(end),
            rebalance=rebalance,
        )
    except Exception as e:
        return _slash_error(f"Bad date input: {e}")

    mapping_summary = None
    price_summary = None
    try:
        scrub = await asyncpg.connect(dsn=dsn, timeout=4)
        try:
            snap = await universe_at(scrub, expression, cfg.start)
        finally:
            await scrub.close()
        if snap.rows:
            sc_ids = [r["sc_id"] for r in snap.rows]
            from backend.agents.symbol_mapper import map_and_persist
            from backtester.data.prices import backfill_prices
            pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
            try:
                mapping_summary = await map_and_persist(pool, sc_ids)
                price_summary = await backfill_prices(
                    pool, since=cfg.start, until=cfg.end,
                    sc_ids=sc_ids, sleep_between=0.05,
                )
            finally:
                await pool.close()
    except ValidationError as ve:
        return _slash_error(f"Invalid expression: {ve}")
    except Exception as e:
        mapping_summary = {"error": str(e)[:200]}

    try:
        result = await _run_bt(dsn, cfg)
    except ValidationError as ve:
        return _slash_error(f"Invalid expression: {ve}")
    except Exception as e:
        return _slash_error(f"Backtest failed: {e}")

    metrics = compute_metrics(
        result.equity_curve, benchmark_curve=result.benchmark_curve,
        trades=result.trades,
    ).to_dict()
    suffix = ""
    if isinstance(mapping_summary, dict) and "verified" in mapping_summary:
        v = mapping_summary.get("verified", 0)
        a = mapping_summary.get("already_mapped", 0)
        if v or a:
            suffix = f" Mapped {v} new, {a} cached."
    if isinstance(price_summary, dict) and price_summary.get("rows_inserted"):
        suffix += f" Pulled {price_summary['rows_inserted']} price rows from yfinance."
    text = (
        f"Backtested `{expression}` from {start} to {end}, {cfg.rebalance} rebalance.{suffix} "
        f"CAGR {metrics['cagr_pct']:+.1f}%, max drawdown {metrics['max_drawdown_pct']:.1f}%, "
        f"{len(result.rebalances)} rebalances, {len(result.trades)} trades. "
        "Past performance does not guarantee future results."
    )
    # Serialise the equity / benchmark curves to plain JSON-able lists.
    # `result.equity_curve` is List[BacktestEquityPoint(date: date, value: float)].
    # benchmark_curve is None when the backtester runs without one
    # (e.g. universe screen with no NIFTY data) — guard so the JSON
    # serialiser doesn't 500 trying to iterate None.
    def _curve_to_json(curve) -> list[dict]:
        if not curve:
            return []
        out = []
        for p in curve:
            d = p.date if hasattr(p, "date") else p["date"]
            v = p.value if hasattr(p, "value") else p["value"]
            out.append({
                "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                "value": float(v),
            })
        return out

    def _rebalance_to_json(rb) -> dict:
        d = rb.date if hasattr(rb, "date") else rb["date"]
        entered = rb.entered if hasattr(rb, "entered") else rb.get("entered", [])
        exited = rb.exited if hasattr(rb, "exited") else rb.get("exited", [])
        return {
            "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
            "entered": [
                {"symbol": (e.symbol if hasattr(e, "symbol") else e["symbol"]),
                 "weight": float(e.weight if hasattr(e, "weight") else e["weight"])}
                for e in entered
            ],
            "exited": [
                {"symbol": (x.symbol if hasattr(x, "symbol") else x["symbol"])}
                for x in exited
            ],
        }

    equity_json = _curve_to_json(result.equity_curve)
    benchmark_json = _curve_to_json(result.benchmark_curve)
    rebalances_json = [_rebalance_to_json(rb) for rb in result.rebalances[:50]]

    return {
        "response": text, "intent": "EXPR_BACKTEST",
        "expr_backtest_data": {
            "expression": expression, "start": start, "end": end,
            "rebalance": cfg.rebalance, "metrics": metrics,
            "equity_curve": equity_json,
            "benchmark_curve": benchmark_json,
            "rebalances": rebalances_json,
            "n_trades": len(result.trades),
            "warnings": result.warnings[:5],
            "symbol_mapping": mapping_summary,
        },
        # Tag for the FE so ChatDemo dispatches to FinancialBacktestCard.
        # Same convention as indicator_backtest_chart (line ~436 above).
        "raw_data": {
            "_render_hint": "financial_backtest_chart",
            "expression": expression,
            "start": start,
            "end": end,
            "rebalance": cfg.rebalance,
            "metrics": metrics,
            "equity_curve": equity_json,
            "benchmark_curve": benchmark_json,
            "rebalances": rebalances_json,
            "n_trades": len(result.trades),
            "warnings": result.warnings[:5],
        },
        "screen_data": None, "backtest_data": None, "chart_data": None,
        "logiccard": None, "requires_clarification": False,
    }


def _slash_error(msg: str) -> dict:
    return {
        "response": msg, "intent": "ERROR",
        "screen_data": None, "expr_backtest_data": None, "backtest_data": None,
        "chart_data": None, "logiccard": None, "requires_clarification": False,
    }


def _jsonable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if v is None or isinstance(v, (int, str, float, bool)):
            out[k] = v
        else:
            try: out[k] = float(v)
            except (TypeError, ValueError): out[k] = str(v)
    return out


# ---- Main route --------------------------------------------------------


@router.post("")
async def chat(
    request: ChatRequest,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    user_id = _auth(authorization)
    last_msg = _last_user_message(request.messages)
    if not last_msg:
        raise HTTPException(400, "no user message in payload")

    # 1. Slash-command shortcuts (the user typed them explicitly).
    if (slash := await _maybe_run_slash(last_msg)) is not None:
        return slash

    # 2. Mainline LLM path.
    kite_token = _kite_token_for(db, user_id)
    holdings: list[dict] = []
    if request.include_portfolio_context:
        try:
            from backend.kite.portfolio import get_holdings
            holdings = get_holdings(kite_token)
        except Exception:
            holdings = []

    ctx = UserContext(user_id=user_id, kite_token=kite_token, db=db, holdings=holdings)
    conv_id = _conv_id(request, user_id)

    # The frontend always sends the rolling history in `messages` — that
    # IS the per-session window. We pass it through verbatim (capped to
    # the last N pairs in the service) and DO NOT fall back to Redis-
    # stored history when the FE's window is empty. This was the root
    # cause of the "new chat starts with old context from a different
    # workflow" bug in the PDF report — Redis kept 24h of history under
    # the per-user conv_id, so opening a fresh chat with `messages=[]`
    # in the request still resurfaced the prior session's draft.
    history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (request.messages or [])
        if isinstance(m, dict)
        and m.get("role") in {"user", "assistant"}
        and m.get("content")
    ][:-1]                                    # drop the just-arrived user msg

    turn = await _chat_service.handle(
        last_msg, conv_id, ctx,
        # Always pass the FE's history (even when empty) — this is the
        # session boundary signal. None would re-hydrate from Redis.
        history_override=history,
        mode_override=request.mode,
    )

    if turn.sanitised:
        logger.warning("post-processor stripped output for user %s conv %s",
                       user_id, conv_id)

    raw_data = turn.raw_data or {}
    # Tools that emit a card payload (propose_workflow → workflow_draft_card,
    # run_backtest → indicator_backtest_chart, …) put it under
    # raw_data[tool_name]. The frontend reads `_render_hint` at the top
    # level, so we need to lift that nested payload up. We pick the first
    # nested dict that carries a `_render_hint`; in practice only one
    # tool is called per turn so there's no ambiguity.
    if not raw_data.get("_render_hint"):
        for _key, val in list(raw_data.items()):
            if isinstance(val, dict) and val.get("_render_hint"):
                # Merge the nested payload over the top so existing keys
                # (e.g. _render_hint, name, steps, …) are visible to the FE.
                raw_data = {**raw_data, **val}
                break

    # If a tool produced a LogicCard and nothing else has set a render
    # hint, tag the response so the frontend renders the unified
    # LogicCardChip. This is the single switchboard for the ~30 chat
    # tools that build a LogicCard (orders, GTT, SL, OCO, dip-buy,
    # basket, squareoff, SIP create, etc.).
    if turn.logiccard and not raw_data.get("_render_hint"):
        raw_data = {**raw_data, "_render_hint": "logic_card"}

    return {
        "response": turn.response,
        "intent": None,                       # intent classifier removed
        "tools_called": turn.tools_called,
        "logiccard": turn.logiccard,
        "requires_clarification": False,
        "missing_params": [],
        "tool_call": None,                    # legacy field — never populated now
        "raw_data": raw_data or None,
        "latency_breakdown": turn.latency_breakdown,
        "latency_ms": turn.latency_ms,
    }


# ---- Streaming (kept lean — used by the streaming chat UI path) --------


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """True SSE stream: emits typed events as the agentic loop runs.

    Event shape (one JSON object per `data:` line):
      {"type": "start"}
      {"type": "tool_start", "name": "..."}
      {"type": "tool_done",  "name": "...", "ok": bool, "error": str|null}
      {"type": "delta",      "text": "..."}                  # final-hop tokens
      {"type": "replace",    "text": "..."}                  # post-processor rewrite
      {"type": "done",       "response": "...", "tools_called": [...],
                              "logiccard": {...}|null, "raw_data": {...}|null,
                              "latency_ms": int,
                              "latency_breakdown": {...}}
      {"type": "error",      "message": "..."}

    On the OpenAI provider, `delta` events come straight from the
    Responses API stream — first token typically lands ~1s after
    request start. On Sarvam (or fast-path), the full reply is emitted
    as a single `delta` because those paths don't true-stream.
    """
    user_id = _auth(authorization)
    last_msg = _last_user_message(request.messages)
    if not last_msg:
        raise HTTPException(400, "no user message in payload")

    kite_token = _kite_token_for(db, user_id)
    holdings: list[dict] = []
    if request.include_portfolio_context:
        try:
            from backend.kite.portfolio import get_holdings
            holdings = get_holdings(kite_token)
        except Exception:
            holdings = []

    ctx = UserContext(user_id=user_id, kite_token=kite_token, db=db, holdings=holdings)
    conv_id = _conv_id(request, user_id)
    # Same per-session policy as the non-streaming path — see comment
    # above. FE-supplied messages list IS the session history.
    history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (request.messages or [])
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ][:-1]

    # Slash-command + indicator-backtest deterministic shortcut.
    # POST /chat runs this BEFORE the LLM (line ~841). The streaming
    # path used to skip it, so prompts like "How would a 50 SMA on
    # TCS have done over the past 3 years" went to the model — which
    # hallucinated period limits and burned 25s on an ASK_USER round
    # trip. Run the same shortcut here and surface its result as a
    # synthetic SSE sequence (start → delta → done) so the FE sees
    # the same shape as a normal stream.
    slash_result = await _maybe_run_slash(last_msg)

    async def gen():
        if slash_result is not None:
            yield f"data: {json.dumps({'type': 'start'})}\n\n"
            text = slash_result.get("response") or ""
            if text:
                yield f"data: {json.dumps({'type': 'delta', 'text': text})}\n\n"
            # Build a /chat-shaped raw_data block from the slash
            # result so the FE's render-hint dispatch fires the same
            # card it would have on the non-streaming path.
            raw = slash_result.get("raw_data") or {}
            for key in (
                "expr_backtest_data", "backtest_data", "screen_data",
                "chart_data",
            ):
                payload = slash_result.get(key)
                if isinstance(payload, dict) and not raw.get("_render_hint"):
                    raw = {**raw, **payload}
            done_event = {
                "type": "done",
                "response": text,
                "tools_called": [],
                "logiccard": slash_result.get("logiccard"),
                "raw_data": raw or None,
                "latency_ms": 0,
                "latency_breakdown": {},
            }
            yield f"data: {json.dumps(done_event, default=str)}\n\n"
            return
        try:
            async for event in _chat_service.handle_stream(
                last_msg, conv_id, ctx,
                history_override=history,  # always honour FE-sent window
                mode_override=request.mode,
            ):
                # Hoist nested-tool render hints up to top level so the
                # FE consumes the same shape as POST /chat. We only need
                # to do this on the `done` event.
                if event.get("type") == "done":
                    raw_data = event.get("raw_data") or {}
                    if isinstance(raw_data, dict) and not raw_data.get("_render_hint"):
                        for _key, val in list(raw_data.items()):
                            if isinstance(val, dict) and val.get("_render_hint"):
                                raw_data = {**raw_data, **val}
                                break
                    if event.get("logiccard") and not (raw_data or {}).get("_render_hint"):
                        raw_data = {**(raw_data or {}), "_render_hint": "logic_card"}
                    event = {**event, "raw_data": raw_data or None}
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            logger.exception("chat_stream gen failed: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
            "Connection": "keep-alive",
        },
    )
