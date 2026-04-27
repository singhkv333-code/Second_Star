from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session
import json
from backend.database import get_db
from backend.models import Strategy, StrategyStatus
from backend.auth.jwt_handler import get_user_id_from_token

router = APIRouter(prefix="/strategies", tags=["Strategies"])


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


class StrategyCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    strategy_type: str = Field(..., description="price_drop, price_cross, rsi, scheduled")
    trigger_symbol: Optional[str] = None
    trigger_condition: dict = Field(default_factory=dict)
    action_config: dict = Field(default_factory=dict)
    max_budget: Optional[float] = Field(default=None, le=200_000)


@router.post("", status_code=201)
def create_strategy(
    request: StrategyCreateRequest,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    strategy = Strategy(
        user_id=user_id,
        name=request.name,
        description=request.description,
        strategy_type=request.strategy_type,
        trigger_symbol=request.trigger_symbol,
        trigger_condition=json.dumps(request.trigger_condition),
        action_config=json.dumps(request.action_config),
        max_budget=request.max_budget,
        status=StrategyStatus.active,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return {"id": strategy.id, "status": "created", "name": strategy.name}


@router.get("")
def list_strategies(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    strategies = db.query(Strategy).filter(Strategy.user_id == user_id).all()
    return [{"id": s.id, "name": s.name, "type": s.strategy_type,
             "status": s.status, "trigger_symbol": s.trigger_symbol,
             "max_budget": s.max_budget,
             "last_triggered": s.last_triggered_at.isoformat() if s.last_triggered_at else None}
            for s in strategies]


@router.patch("/{strategy_id}/pause")
def pause_strategy(strategy_id: int, user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    s = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.user_id == user_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    s.status = StrategyStatus.paused
    db.commit()
    return {"id": strategy_id, "status": "paused"}


@router.patch("/{strategy_id}/resume")
def resume_strategy(strategy_id: int, user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    s = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.user_id == user_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    s.status = StrategyStatus.active
    db.commit()
    return {"id": strategy_id, "status": "active"}


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: int, user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    s = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.user_id == user_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    s.status = StrategyStatus.completed
    db.commit()
    return {"id": strategy_id, "status": "deleted"}
