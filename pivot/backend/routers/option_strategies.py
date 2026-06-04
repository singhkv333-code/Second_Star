"""Option strategies REST surface — F&O P1 (register-not-execute).

Endpoints:
  POST /option-strategies                  register a multi-leg intent
  POST /option-strategies/{id}/withdraw    withdraw a registered intent
  GET  /users/option-strategies            list current user's strategies
  GET  /option-strategies/{id}             one strategy with legs

Mounted BARE (like /orders, /paper, /ipo-applications) — the FE's
``requestLegacy`` helper hits these without the /api prefix.

REGISTER-NOT-EXECUTE, both books: the server RE-RESOLVES the strategy
against the live chain (fresh mids/Greeks/margin — client economics are
discarded, IPO pattern), runs the fail-closed pre-trade gate
(safety.run_option_pretrade_gate: MCX block, disclosure ack, expiry-day
naked-short block, liquidity, lot caps), then persists.
  book='paper' → P2's paper broker picks the row up and simulates fills.
  book='live'  → stays a registered intent forever; the user executes
                 in their broker app. Pivot NEVER places a live F&O order.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth.jwt_handler import get_user_id_from_token
from backend.database import get_db
from backend.models import OptionStrategy
from backend.safety import run_option_pretrade_gate
from backend.services.option_strategies import (
    StrategyResolutionError,
    resolve_strategy,
)
from backend.services.option_strategy_service import (
    find_open_duplicate,
    persist_option_strategy,
    serialize_option_strategy,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Options"])


# ── Auth dependency (matches ipo_applications.py / orders.py shape) ──

def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    user_id = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


# ── Request / response models ────────────────────────────────────────

class StrategyLegIn(BaseModel):
    option_type: Literal["CE", "PE"]
    side: Literal["BUY", "SELL"]
    strike: float = Field(..., gt=0)


class OptionStrategyRegisterRequest(BaseModel):
    """Body for POST /option-strategies. Only the STRUCTURE is taken
    from the client (underlying/expiry/template/legs/lots/book); every
    economic number is recomputed server-side from the live chain."""
    underlying: str = Field(..., min_length=1, max_length=40)
    expiry: str = Field(..., description="ISO date — must exist in the master")
    template: str = Field(..., max_length=40)
    book: Literal["paper", "live"] = "paper"
    qty_lots: int = Field(1, ge=1)
    legs: list[StrategyLegIn] = Field(..., min_length=1, max_length=6)
    acknowledge_disclosure: bool = Field(
        False,
        description="Must be true — SEBI risk disclosure acknowledgement.",
    )
    conversation_id: Optional[str] = None


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/option-strategies")
async def register_option_strategy(
    req: OptionStrategyRegisterRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_user_id),
) -> dict:
    # 1. SERVER re-resolution against the live chain — fresh mids,
    #    Greeks, margin; throws on unknown/illiquid structure.
    try:
        payload = resolve_strategy(
            db,
            req.underlying,
            req.template,
            expiry=req.expiry,
            qty_lots=req.qty_lots,
            explicit_legs=[leg.model_dump() for leg in req.legs],
        )
    except StrategyResolutionError as exc:
        return {"success": False, "strategy": None, "error": str(exc)}
    payload["editable"]["book"] = req.book

    # 2. Fail-closed pre-trade gate (single source of truth — safety.py).
    ok, reason = run_option_pretrade_gate(
        payload, acknowledged=req.acknowledge_disclosure,
    )
    if not ok:
        return {"success": False, "strategy": None, "error": reason}

    # 3. Duplicate guard (double-click protection, IPO pattern).
    from datetime import date as _date

    dup = find_open_duplicate(
        db, user_id, payload["locked"]["underlying"], req.template,
        _date.fromisoformat(payload["locked"]["expiry"]),
    )
    if dup is not None:
        return {
            "success": True,
            "strategy": serialize_option_strategy(dup),
            "duplicate": True,
            "error": None,
        }

    # 4. Persist (server numbers only).
    strategy = persist_option_strategy(
        db,
        user_id=user_id,
        payload=payload,
        book=req.book,
        qty_lots=req.qty_lots,
        conversation_id=req.conversation_id,
        source="chat",
    )
    return {
        "success": True,
        "strategy": serialize_option_strategy(strategy),
        "error": None,
    }


@router.post("/option-strategies/{strategy_id}/withdraw")
async def withdraw_option_strategy(
    strategy_id: str,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_user_id),
) -> dict:
    strategy = (
        db.query(OptionStrategy)
        .filter(
            OptionStrategy.id == strategy_id,
            OptionStrategy.user_id == user_id,
        )
        .first()
    )
    if strategy is None:
        raise HTTPException(404, "Strategy not found")
    if strategy.status not in ("registered", "intent_armed"):
        return {
            "success": False,
            "strategy": serialize_option_strategy(strategy),
            "error": f"Cannot withdraw a strategy in status '{strategy.status}'.",
        }
    strategy.status = "withdrawn"
    db.commit()
    db.refresh(strategy)
    return {
        "success": True,
        "strategy": serialize_option_strategy(strategy),
        "error": None,
    }


@router.get("/users/option-strategies")
async def list_option_strategies(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_user_id),
) -> dict:
    rows = (
        db.query(OptionStrategy)
        .filter(OptionStrategy.user_id == user_id)
        .order_by(OptionStrategy.created_at.desc())
        .limit(100)
        .all()
    )
    return {"strategies": [serialize_option_strategy(s) for s in rows]}


@router.get("/option-strategies/{strategy_id}")
async def get_option_strategy(
    strategy_id: str,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_user_id),
) -> dict:
    strategy = (
        db.query(OptionStrategy)
        .filter(
            OptionStrategy.id == strategy_id,
            OptionStrategy.user_id == user_id,
        )
        .first()
    )
    if strategy is None:
        raise HTTPException(404, "Strategy not found")
    return {"strategy": serialize_option_strategy(strategy)}
