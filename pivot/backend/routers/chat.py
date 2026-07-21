"""Chat router — thin, delegates to backend.services.chat_service.

This is a deliberate rewrite: the previous router had ~600 lines of intent
routing, regex shortcuts, classifier calls, and canned responses. Those
concerns now live behind ``ChatService``. The router's job is auth, request
shape, slash-command shortcuts, and serialising the response.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from backend.security.throttle import rate_limit
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.jwt_handler import get_user_id_from_token
from backend.database import get_db
from backend.kite.auth import read_kite_access_token
from backend.models import User
from backend.posthog_client import get_posthog
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
    # Reply-by-selecting: when the user highlights a snippet of a prior
    # assistant answer and replies to it, the FE sends the highlighted
    # excerpt here. We weave it into the current user message so the LLM
    # knows precisely what is being replied to. None / empty = no quote.
    quoted_text: Optional[str] = None
    # "Chat edits the draft open in the editor": when the user has an
    # unsaved workflow draft open in the editor panel, the FE attaches
    # the current on-screen draft here so chat amendments base off
    # exactly what the user sees — not whatever happens to be in Redis.
    # Same shape as workflow_draft_card / propose_workflow output
    # (name, description, steps:[{step_type,label,config}], ...).
    # None / absent = legacy Redis active_draft flow, byte-for-byte
    # unchanged.
    editor_draft: Optional[dict] = None
    # Composer context attachments — the "+" menu and "@" mentions in the
    # FE composer. Each item is one of:
    #   {"kind": "security", "symbol": "TCS", "name": "Tata Consultancy…"}
    #   {"kind": "position", "symbol": "RELIANCE", "quantity": 10,
    #    "avg_price": 1300.5, "last_price": 1321.2, "pnl": 207.0,
    #    "book": "portfolio"|"paper"}
    #   {"kind": "agent", "workflow_id": "…", "name": "…",
    #    "description": "…", "status": "active"}
    #   {"kind": "basket", "basket_id": 12, "name": "Renewables",
    #    "members": [{"symbol": "SUZLON", "weight": 27.0}, …]}
    # They are woven into the prompt as a labelled context block (same
    # mechanism as quoted_text) so the LLM treats them as the subject of
    # the message. None / absent = no attachments, prompt unchanged.
    attachments: Optional[list[dict]] = None


# ---- Helpers -----------------------------------------------------------


# Hard cap on the quoted excerpt we inline into the prompt — a guard
# against a runaway selection blowing up the context window.
_MAX_QUOTE_CHARS = 2000


def _with_reply_context(message: str, quoted_text: Optional[str]) -> str:
    """Prefix `message` with the assistant excerpt the user is replying to.

    Returns `message` unchanged when there's no quote. The excerpt is
    rendered as a markdown blockquote so the model reads it as "the thing
    being replied to", not as a new instruction.
    """
    quote = (quoted_text or "").strip()
    if not quote:
        return message
    if len(quote) > _MAX_QUOTE_CHARS:
        quote = quote[:_MAX_QUOTE_CHARS].rstrip() + " …"
    quoted_block = "\n".join(f"> {line}" for line in quote.splitlines())
    return (
        "The user highlighted this excerpt from your previous reply and is "
        "responding to it specifically:\n\n"
        f"{quoted_block}\n\n"
        f"Their reply:\n{message}"
    )


# Bounds for the attachment context block — a guard against a crafted
# payload blowing up the prompt. Attachments beyond the cap are dropped.
_MAX_ATTACHMENTS = 8
_MAX_ATTACH_FIELD = 300


def _fmt_attachment(att: dict) -> Optional[str]:
    """One human-readable line per attachment, or None for junk."""
    if not isinstance(att, dict):
        return None

    def _s(key: str) -> str:
        v = att.get(key)
        return str(v)[:_MAX_ATTACH_FIELD].strip() if v is not None else ""

    kind = _s("kind").lower()
    if kind == "security":
        sym, name = _s("symbol").upper(), _s("name")
        if not sym:
            return None
        return f"- Security: {sym}" + (f" ({name})" if name else "")
    if kind == "position":
        sym = _s("symbol").upper()
        if not sym:
            return None
        bits = [f"- Position: {sym}"]
        qty, avg, ltp, pnl = (att.get(k) for k in ("quantity", "avg_price", "last_price", "pnl"))
        try:
            if qty is not None:
                bits.append(f"{float(qty):g} sh")
            if avg is not None:
                bits.append(f"avg ₹{float(avg):,.2f}")
            if ltp is not None:
                bits.append(f"LTP ₹{float(ltp):,.2f}")
            if pnl is not None:
                bits.append(f"P&L ₹{float(pnl):+,.2f}")
        except (TypeError, ValueError):
            pass
        book = _s("book")
        if book:
            bits.append(f"[{book} book]")
        return " · ".join([bits[0]] + bits[1:]) if len(bits) > 1 else bits[0]
    if kind == "basket":
        name = _s("name")
        if not name:
            return None
        line = f"- Saved basket: “{name}”"
        bid = _s("basket_id")
        if bid:
            line += f" [basket_id={bid}]"
        # Carry the exact legs so an edit ("drop SUZLON", "make it equal
        # weight") amends THIS basket's holdings rather than a guess.
        legs = []
        for m in (att.get("members") or [])[:40]:
            if not isinstance(m, dict):
                continue
            sym = str(m.get("symbol") or "").strip().upper()[:20]
            if not sym:
                continue
            try:
                legs.append(f"{sym} {float(m.get('weight')):g}%")
            except (TypeError, ValueError):
                legs.append(sym)
        if legs:
            line += " — holdings: " + ", ".join(legs)
        return line
    if kind == "agent":
        name = _s("name")
        if not name:
            return None
        status, desc, wf_id = _s("status"), _s("description"), _s("workflow_id")
        line = f"- Agent: “{name}”"
        if status:
            line += f" ({status})"
        if wf_id:
            line += f" [workflow_id={wf_id}]"
        if desc:
            line += f" — {desc}"
        return line
    return None


def _with_attachment_context(message: str, attachments: Optional[list]) -> str:
    """Prefix `message` with the composer's context attachments.

    Same mechanism as `_with_reply_context`: the block reads as grounding
    ("the user tagged these"), never as a new instruction. Returns the
    message unchanged when there are no valid attachments.
    """
    lines = []
    for att in (attachments or [])[:_MAX_ATTACHMENTS]:
        line = _fmt_attachment(att)
        if line:
            lines.append(line)
    if not lines:
        return message
    block = "\n".join(lines)
    return (
        "The user attached the following context to this message (tagged via "
        "the composer). Treat these as the specific subject(s) being discussed "
        "— resolve pronouns like 'it'/'this' to them, and use their exact "
        "symbols/ids when calling tools:\n"
        f"{block}\n\n"
        f"User message:\n{message}"
    )


# ---- Conversation persistence -------------------------------------------

# Client session ids are UUID-ish strings; the conversations PK is a
# String(36). Anything else (legacy "u{id}" keys, forged payloads) is
# simply not persisted — chat still works, it just won't appear in the
# sidebar history.
_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,36}$")


# Cards above this JSON size used to be persisted as hint-only (dropping
# the whole card) — a resumed conversation then re-hydrated `{_render_hint}`
# with no `card` body, and the FE's card components (which assume their
# required arrays exist, no defensive checks) crashed on render. The error
# boundary caught it and showed "This card couldn't be shown" (reported
# 2026-07-14) for exactly the common case of a multi-year backtest, whose
# bulk is almost always its own chart series, not its metrics/summary.
# Down-sample those series first so the card that matters (metrics intact,
# chart slightly coarser) still fits, instead of vanishing outright.
_PERSIST_CARD_MAX_CHARS = 60_000
_DOWNSAMPLE_KEYS = frozenset({
    "equity_curve", "benchmark_curve", "price_curve", "indicator_curve",
})
_DOWNSAMPLE_CAP = 200


def _downsample_series(points: list, cap: int = _DOWNSAMPLE_CAP) -> list:
    if not isinstance(points, list) or len(points) <= cap:
        return points
    step = len(points) / cap
    sampled = [points[int(i * step)] for i in range(cap - 1)]
    sampled.append(points[-1])
    return sampled


def _downsample_large_arrays(obj):
    """Recursively down-sample any list value keyed by a known chart-series
    field name, wherever it sits in the payload (some cards nest their
    series a level deep under a tool-name key)."""
    if isinstance(obj, dict):
        return {
            k: (_downsample_series(v)
                if k in _DOWNSAMPLE_KEYS and isinstance(v, list)
                else _downsample_large_arrays(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_downsample_large_arrays(v) for v in obj]
    return obj


def _bounded_card(raw_data: Optional[dict]) -> Optional[dict]:
    """The turn's card payload if it is render-hinted and reasonably sized,
    down-sampling large chart series first rather than dropping the card
    outright."""
    if not isinstance(raw_data, dict) or not raw_data.get("_render_hint"):
        return None
    try:
        shrunk = _downsample_large_arrays(raw_data)
        if len(json.dumps(shrunk, default=str)) <= _PERSIST_CARD_MAX_CHARS:
            return shrunk
    except Exception:
        return None
    return None


def _persist_turn(
    user_id: int,
    raw_conv_id: Optional[str],
    user_msg: str,
    assistant_text: str,
    render_hint: Optional[str] = None,
    card: Optional[dict] = None,
) -> None:
    """Write the (user, assistant) turn into the Postgres conversations
    tables so the sidebar history survives reloads and re-logins.

    ``card`` (the render-hinted raw_data) rides along in ``tool_payload``
    so a resumed conversation re-renders its widgets instead of showing
    bare text (user-reported 2026-07-10).

    Uses its OWN session — the streaming generator outlives the request-
    scoped Depends(get_db) session. Failures are logged and swallowed:
    persistence must never break a chat turn.
    """
    conv_id = (raw_conv_id or "").strip()
    if not _CONV_ID_RE.match(conv_id) or not user_msg:
        return
    try:
        from backend.database import SessionLocal
        from backend.models import Conversation, ConversationMessage

        db = SessionLocal()
        try:
            convo = (
                db.query(Conversation).filter(Conversation.id == conv_id).first()
            )
            if convo is not None and convo.user_id != user_id:
                # Forged/colliding id — never write into another user's thread.
                return
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            if convo is None:
                convo = Conversation(
                    id=conv_id,
                    user_id=user_id,
                    # First user message doubles as the sidebar title.
                    title=user_msg[:80] or None,
                )
                db.add(convo)
            db.add(ConversationMessage(
                conversation_id=conv_id, role="user", content=user_msg[:8000],
            ))
            if assistant_text or render_hint:
                payload = None
                if render_hint:
                    payload = {"_render_hint": render_hint}
                    bounded = _bounded_card(card)
                    if bounded is not None:
                        payload["card"] = bounded
                db.add(ConversationMessage(
                    conversation_id=conv_id,
                    role="assistant",
                    content=(assistant_text or "")[:16000],
                    tool_payload=payload,
                ))
            convo.last_message_at = now
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("conversation persistence failed for conv %s", conv_id)


def _auth(authorization: str) -> int:
    if not authorization:
        # Local-dev convenience ONLY: fall back to the default dev user so the
        # chat UI works without a login flow. This is gated behind an explicit
        # opt-in flag that defaults to FALSE — beta/production MUST require a
        # real token (set dev_auth_bypass=false / leave unset). Without the
        # gate, any unauthenticated request was silently treated as user 1.
        from backend.config import settings as _cfg
        if getattr(_cfg, "dev_auth_bypass", False):
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
    if user and getattr(user, "active_broker_session", None):
        return read_kite_access_token(user.active_broker_session) or "mock_token"
    return "mock_token"


def _conv_id(req: ChatRequest, user_id: int) -> str:
    """Per-user Redis conversation key.

    SECURITY: a client-supplied ``conversation_id`` is ALWAYS namespaced under
    the AUTHENTICATED user id, so a forged value can never address another
    user's chat state (history / pending tool calls / in-flight order drafts).
    Before this, ``return req.conversation_id`` used the raw client value, so
    User A could pass ``conversation_id="u2"`` and read User B's session — a
    multi-tenant isolation breach. The store is keyed only by the value we
    return here, so prefixing it is sufficient and fully isolates tenants.
    """
    base = (req.conversation_id or "").strip()
    if base:
        return f"u{user_id}::{base}"
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

async def _maybe_run_slash(text: str) -> Optional[dict]:
    """Match explicit slash commands ONLY. Everything else — including
    natural-language phrasings of backtest / screen intents — falls
    through to the LLM hop, which composes the right tool call (and
    can chain multi-indicator strategies via propose_workflow + the
    workflow backtester).

    History note: this function used to auto-route a half-dozen NL
    backtest patterns (indicator / fundamentals / screen / open-close /
    weekly-swing / unsupported-msg) into deterministic backend tools.
    Those shortcuts silently dropped detail in compound queries — e.g.
    'rsi crosses 30 AND macd signal crosses' got backtested as RSI-only
    because the regex matched the first indicator and discarded the
    rest. Removing them sends every NL backtest query through the LLM,
    which costs +1 round-trip per query but fixes the truncation bug
    and unlocks multi-condition strategies for free.
    """
    body = (text or "").strip()
    if not body or not body.startswith("/"):
        return None

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



async def _run_expr_screen(*, expression: str, as_of: Optional[str]) -> dict:
    import asyncpg
    import datetime as _dt
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
    import asyncpg
    import datetime as _dt
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


async def _refresh_summary_bg(user_id: int, raw_conv_id) -> None:
    """A2: refresh the ChatSummary AFTER the response is sent (FastAPI
    background task — zero user-facing latency). ensure_summary is
    internally gated to regenerate only every REFRESH_EVERY_N messages,
    so most invocations are a cheap staleness check."""
    raw = (raw_conv_id or "").strip()
    if not raw:
        return
    try:
        from backend.database import SessionLocal
        from backend.services.conversation_summary import ensure_summary

        _db = SessionLocal()
        try:
            await ensure_summary(_db, raw, user_id)
        finally:
            _db.close()
    except Exception as e:  # noqa: BLE001 — background-only, never surfaces
        logger.warning("background summary refresh failed: %s", e)


@router.post("", dependencies=[Depends(rate_limit("chat", 40, 60))])
async def chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
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
            # Paper-AWARE: reads the simulated paper book in paper mode
            # (the default), else Kite — the SAME resolver the Portfolio page
            # uses. Without this the injected "## User context" block showed
            # "no holdings" for paper users who actually hold a real book,
            # so the LLM refused portfolio-grounded asks ("hedge my portfolio").
            from backend.services.portfolio_source import resolve_holdings
            holdings = list(resolve_holdings(db, user_id, kite_token))
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

    # Reply-by-selecting: weave the highlighted excerpt into the message
    # the LLM sees (slash shortcuts above already ran on the raw text).
    llm_msg = _with_reply_context(last_msg, request.quoted_text)
    # Composer context attachments (+ menu / @ mentions) — prepend as a
    # labelled grounding block, same mechanism as the reply quote.
    llm_msg = _with_attachment_context(llm_msg, request.attachments)

    turn = await _chat_service.handle(
        llm_msg, conv_id, ctx,
        # Always pass the FE's history (even when empty) — this is the
        # session boundary signal. None would re-hydrate from Redis.
        history_override=history,
        mode_override=request.mode,
        editor_draft=request.editor_draft,
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

    # Persist the turn so the sidebar conversation history survives
    # reloads/re-login. Uses the RAW client conversation id (the FE lists
    # conversations by it); failures never break the response.
    _persist_turn(
        user_id, request.conversation_id, last_msg, turn.response or "",
        render_hint=(raw_data or {}).get("_render_hint"),
        card=raw_data if isinstance(raw_data, dict) else None,
    )
    # A2: keep the conversation summary fresh off the hot path so the
    # summary bridge in chat_service has something to inject once the
    # conversation outgrows the 6-turn window.
    background_tasks.add_task(
        _refresh_summary_bg, user_id, request.conversation_id,
    )

    _ph = get_posthog()
    if _ph:
        _ph.capture("chat_message_sent", distinct_id=str(user_id), properties={
            "mode": request.mode,
            "has_attachments": bool(request.attachments),
            "message_length": len(last_msg),
            "tools_called_count": len(turn.tools_called or []),
            "render_hint": (raw_data or {}).get("_render_hint"),
            "latency_ms": turn.latency_ms,
        })

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


@router.post("/stream", dependencies=[Depends(rate_limit("chat", 40, 60))])
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

    On the OpenAI / Azure providers, `delta` events come straight from
    the Responses API stream — first token typically lands ~1s after
    request start. On the fast-path (slash-command shortcuts), the full
    reply is emitted as a single `delta` because that path doesn't
    true-stream.
    """
    user_id = _auth(authorization)
    last_msg = _last_user_message(request.messages)
    if not last_msg:
        raise HTTPException(400, "no user message in payload")

    kite_token = _kite_token_for(db, user_id)
    holdings: list[dict] = []
    if request.include_portfolio_context:
        try:
            # Paper-AWARE: reads the simulated paper book in paper mode
            # (the default), else Kite — the SAME resolver the Portfolio page
            # uses. Without this the injected "## User context" block showed
            # "no holdings" for paper users who actually hold a real book,
            # so the LLM refused portfolio-grounded asks ("hedge my portfolio").
            from backend.services.portfolio_source import resolve_holdings
            holdings = list(resolve_holdings(db, user_id, kite_token))
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
            _persist_turn(
                user_id, request.conversation_id, last_msg, text,
                render_hint=(raw or {}).get("_render_hint"),
                card=raw if isinstance(raw, dict) else None,
            )
            return
        try:
            # Reply-by-selecting: inline the highlighted excerpt for the
            # LLM (the slash shortcut above ran on the raw text).
            llm_msg = _with_reply_context(last_msg, request.quoted_text)
            # Composer context attachments (+ menu / @ mentions) — same
            # grounding-block mechanism as the reply quote.
            llm_msg = _with_attachment_context(llm_msg, request.attachments)
            async for event in _chat_service.handle_stream(
                llm_msg, conv_id, ctx,
                history_override=history,  # always honour FE-sent window
                mode_override=request.mode,
                editor_draft=request.editor_draft,
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
                    # Persist the completed turn (raw client conv id, so
                    # the sidebar lists it). Never breaks the stream.
                    _persist_turn(
                        user_id, request.conversation_id, last_msg,
                        event.get("response") or "",
                        render_hint=(raw_data or {}).get("_render_hint"),
                        card=raw_data if isinstance(raw_data, dict) else None,
                    )
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
