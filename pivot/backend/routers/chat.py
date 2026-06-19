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
from backend.kite.auth import read_kite_access_token
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
        return read_kite_access_token(user.kite_session) or "mock_token"
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

    # Reply-by-selecting: weave the highlighted excerpt into the message
    # the LLM sees (slash shortcuts above already ran on the raw text).
    llm_msg = _with_reply_context(last_msg, request.quoted_text)

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
            # Reply-by-selecting: inline the highlighted excerpt for the
            # LLM (the slash shortcut above ran on the raw text).
            llm_msg = _with_reply_context(last_msg, request.quoted_text)
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
