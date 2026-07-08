from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session
from datetime import datetime
from backend.database import get_db
from backend.models import SIPSchedule
from backend.auth.jwt_handler import get_user_id_from_token
from backend.utils.time_utils import (
    next_monthly_execution,
    next_weekly_execution,
    next_daily_execution,
    format_ist,
    now_ist,
)

router = APIRouter(prefix="/sip", tags=["SIP"])


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    user_id = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


class SIPCreateRequest(BaseModel):
    name: str
    symbol: str = Field(..., description="NSE ticker or MF scheme code")
    instrument_type: str = Field(default="etf", description="etf, mutual_fund, stock")
    amount: float = Field(..., gt=100, description="Min ₹100 per SIP")
    frequency: str = Field(..., description="daily, weekly, monthly")
    day_of_month: Optional[int] = Field(default=None, ge=1, le=28)
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)


def compute_next_execution(
    frequency: str,
    day_of_month: int = None,
    day_of_week: int = None,
) -> datetime:
    """Compute the next execution time using IST-aware helpers."""
    if frequency == "monthly":
        return next_monthly_execution(day_of_month or 1)
    elif frequency == "weekly":
        return next_weekly_execution(day_of_week or 0)
    elif frequency == "daily":
        return next_daily_execution()
    return next_daily_execution()


# Backwards-compatible alias for any importers (e.g. older scheduler builds).
next_execution_date = compute_next_execution


@router.post("", status_code=201)
def create_sip(
    request: SIPCreateRequest,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    sip = SIPSchedule(
        user_id=user_id,
        name=request.name,
        symbol=request.symbol,
        instrument_type=request.instrument_type,
        amount=request.amount,
        frequency=request.frequency,
        day_of_month=request.day_of_month,
        day_of_week=request.day_of_week,
        next_execution_at=compute_next_execution(
            request.frequency, request.day_of_month, request.day_of_week
        ),
        is_active=True,
    )
    db.add(sip)
    db.commit()
    db.refresh(sip)

    return {
        "id": sip.id,
        "status": "created",
        "symbol": sip.symbol,
        "amount": sip.amount,
        "frequency": sip.frequency,
        "next_run": format_ist(sip.next_execution_at, include_seconds=False),
        "next_run_raw": sip.next_execution_at.isoformat() if sip.next_execution_at else None,
        "message": (
            f"SIP created. First execution: "
            f"{format_ist(sip.next_execution_at, include_seconds=False)}."
        ),
        "scheduled_at": format_ist(now_ist()),
    }


@router.get("")
def list_sips(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    sips = db.query(SIPSchedule).filter(SIPSchedule.user_id == user_id).all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "symbol": s.symbol,
            "amount": s.amount,
            "frequency": s.frequency,
            "is_active": s.is_active,
            "next_run": format_ist(s.next_execution_at, include_seconds=False),
            "total_invested": s.total_invested,
            "total_units_bought": s.total_units_bought,
            "created_at": format_ist(s.created_at),
        }
        for s in sips
    ]


@router.patch("/{sip_id}/pause")
def pause_sip(sip_id: int, user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    sip = db.query(SIPSchedule).filter(SIPSchedule.id == sip_id, SIPSchedule.user_id == user_id).first()
    if not sip:
        raise HTTPException(status_code=404, detail="SIP not found")
    sip.is_active = False
    db.commit()
    return {
        "id": sip_id,
        "status": "paused",
        "paused_at": format_ist(now_ist()),
    }


@router.patch("/{sip_id}/resume")
def resume_sip(sip_id: int, user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    sip = db.query(SIPSchedule).filter(SIPSchedule.id == sip_id, SIPSchedule.user_id == user_id).first()
    if not sip:
        raise HTTPException(status_code=404, detail="SIP not found")
    sip.is_active = True
    sip.next_execution_at = compute_next_execution(
        sip.frequency, sip.day_of_month, sip.day_of_week
    )
    db.commit()
    return {
        "id": sip_id,
        "status": "active",
        "next_run": format_ist(sip.next_execution_at, include_seconds=False),
        "resumed_at": format_ist(now_ist()),
    }


@router.delete("/{sip_id}")
def delete_sip(sip_id: int, user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    sip = db.query(SIPSchedule).filter(SIPSchedule.id == sip_id, SIPSchedule.user_id == user_id).first()
    if not sip:
        raise HTTPException(status_code=404, detail="SIP not found")
    db.delete(sip)
    db.commit()
    return {
        "id": sip_id,
        "status": "deleted",
        "deleted_at": format_ist(now_ist()),
    }
