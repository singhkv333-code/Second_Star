"""POST /chat/v2 — non-streaming endpoint backed by chat_v2.pipeline.

Request/response shape mirrors /chat exactly so the test bank and the
FE can switch with one URL change. Streaming endpoint added in a later
day if needed; for now the bank uses non-streaming.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.jwt_handler import get_user_id_from_token
from backend.chat_v2.pipeline import process_turn
from backend.config import settings as _cfg
from backend.database import get_db
from backend.models import User
from backend.services.chat_service import UserContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat_v2"])


class ChatV2Request(BaseModel):
    messages: list
    include_portfolio_context: bool = True
    conversation_id: Optional[str] = None
    mode: Optional[str] = None  # "agent" | "automation" | "backtest"


def _auth(authorization: Optional[str]) -> int:
    if not authorization:
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


def _conv_id(req: ChatV2Request, user_id: int) -> str:
    if req.conversation_id:
        return req.conversation_id
    return f"u{user_id}"


@router.post("/v2")
async def chat_v2(
    request: ChatV2Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """v2 chat endpoint. Same I/O shape as /chat for test-bank compat."""
    user_id = _auth(authorization)
    last_msg = _last_user_message(request.messages)
    if not last_msg:
        raise HTTPException(400, "no user message in payload")

    kite_token = _kite_token_for(db, user_id)
    user_ctx = UserContext(
        user_id=user_id, kite_token=kite_token, db=db, holdings=[]
    )
    conv_id = _conv_id(request, user_id)

    history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (request.messages or [])
        if isinstance(m, dict)
        and m.get("role") in {"user", "assistant"}
        and m.get("content")
    ][:-1]  # drop the just-arrived user msg

    turn = await process_turn(
        message=last_msg,
        conv_id=conv_id,
        user_ctx=user_ctx,
        history_override=history,
        mode_override=request.mode,
    )

    return {
        "response": turn.response,
        "tools_called": turn.tools_called,
        "raw_data": turn.raw_data or None,
        "logiccard": turn.logiccard,
        "latency_ms": turn.latency_ms,
        "latency_breakdown": turn.latency_breakdown,
        "state": turn.final_state,
    }
