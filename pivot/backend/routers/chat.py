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


# ---- Helpers -----------------------------------------------------------


def _auth(authorization: str) -> int:
    if not authorization:
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


async def _maybe_run_slash(text: str) -> Optional[dict]:
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
    from backtester.engine import BacktestConfig, run_backtest as _run_bt
    from backtester.metrics import compute_metrics
    from backtester.universe import universe_at
    from backtester.expr.validator import ValidationError

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
    return {
        "response": text, "intent": "EXPR_BACKTEST",
        "expr_backtest_data": {
            "expression": expression, "start": start, "end": end,
            "rebalance": cfg.rebalance, "metrics": metrics,
            "equity_curve": result.equity_curve,
            "benchmark_curve": result.benchmark_curve,
            "rebalances": result.rebalances[:50],
            "n_trades": len(result.trades),
            "warnings": result.warnings[:5],
            "symbol_mapping": mapping_summary,
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

    # The frontend currently sends the rolling history in `messages`. Until
    # the client switches to using ``conversation_id`` we honour that.
    history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (request.messages or [])
        if isinstance(m, dict)
        and m.get("role") in {"user", "assistant"}
        and m.get("content")
    ][:-1]                                    # drop the just-arrived user msg

    turn = await _chat_service.handle(
        last_msg, conv_id, ctx,
        history_override=history if history else None,
    )

    if turn.sanitised:
        logger.warning("post-processor stripped output for user %s conv %s",
                       user_id, conv_id)

    return {
        "response": turn.response,
        "intent": None,                       # intent classifier removed
        "tools_called": turn.tools_called,
        "logiccard": turn.logiccard,
        "requires_clarification": False,
        "missing_params": [],
        "tool_call": None,                    # legacy field — never populated now
        "raw_data": turn.raw_data or None,
        "latency_ms": turn.latency_ms,
    }


# ---- Streaming (kept lean — used by the streaming chat UI path) --------


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """SSE wrapper: runs the same handler, then word-streams the final reply.

    We don't stream tokens from Sarvam (it doesn't true-stream). We stream
    *the deterministic reply we already got* word-by-word so the UI feels
    live. Tool calls happen before the first word ships.
    """
    user_id = _auth(authorization)
    last_msg = _last_user_message(request.messages)
    if not last_msg:
        raise HTTPException(400, "no user message in payload")

    kite_token = _kite_token_for(db, user_id)
    ctx = UserContext(user_id=user_id, kite_token=kite_token, db=db)
    conv_id = _conv_id(request, user_id)
    history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (request.messages or [])
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ][:-1]
    turn = await _chat_service.handle(last_msg, conv_id, ctx,
                                      history_override=history if history else None)

    async def gen():
        words = (turn.response or "").split(" ")
        for i, w in enumerate(words):
            chunk = w + (" " if i < len(words) - 1 else "")
            yield f"data: {json.dumps({'token': chunk})}\n\n"
            await asyncio.sleep(0.02)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
