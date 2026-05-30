"""The /paper REST router (P4): READ-ONLY views over the paper book.

Each endpoint is a thin HTTP wrapper over the matching
``backend.paper.portfolio`` read function, which already returns JSON-ready
data (plain floats, never Decimal). This router NEVER writes/marks/commits —
marking is the scheduler's job (P3); a GET must not mutate the book.

Conventions mirror ``backend.routers.portfolio`` exactly: the same
``get_user_id`` dependency (dev falls back to user 1 when unauthenticated,
else 401) and ``db: Session = Depends(get_db)``.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.auth.jwt_handler import get_user_id_from_token
from backend.database import get_db
from backend.paper.portfolio import (
    account_summary,
    fills_journal,
    holdings,
    nav_curve,
    open_orders,
)
from backend.paper.scorecards import idea_detail, ideas_list

router = APIRouter(prefix="/paper", tags=["Paper Trading"])


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        # Mirror routers/portfolio.py: in development we fall back to the
        # default dev user so the FE works without a login flow. Production
        # (and test) still require a real token.
        from backend.config import settings as _cfg
        if getattr(_cfg, "app_env", "development") == "development":
            return 1
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


def _parse_date(value: Optional[str]) -> Optional[dt.date]:
    """Parse an ISO date query param to ``datetime.date`` (None passes through)."""
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid date (expected YYYY-MM-DD): {value}"
        )


@router.get("/summary")
def paper_summary(
    user_id: int = Depends(get_user_id), db: Session = Depends(get_db)
):
    return account_summary(db, user_id)


@router.get("/holdings")
def paper_holdings(
    user_id: int = Depends(get_user_id), db: Session = Depends(get_db)
):
    return holdings(db, user_id)


@router.get("/orders")
def paper_orders(
    user_id: int = Depends(get_user_id), db: Session = Depends(get_db)
):
    return open_orders(db, user_id)


@router.get("/fills")
def paper_fills(
    limit: int = 50,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    # Clamp the page size: a negative LIMIT is "unbounded" in SQL (would
    # dump the whole journal) and a huge value is a payload footgun.
    limit = max(1, min(int(limit), 500))
    return fills_journal(db, user_id, limit)


@router.get("/nav")
def paper_nav(
    start: Optional[str] = None,
    end: Optional[str] = None,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    s, e = _parse_date(start), _parse_date(end)
    if s is not None and e is not None and e < s:
        raise HTTPException(status_code=400, detail="end must be >= start")
    return nav_curve(db, user_id, s, e)


# ── forward-test scorecards (P6) ──────────────────────────────────────────

@router.get("/ideas")
def paper_ideas(
    user_id: int = Depends(get_user_id), db: Session = Depends(get_db)
):
    """The forward-test idea list — one scorecard headline per ForwardIdea."""
    return ideas_list(db, user_id)


@router.get("/ideas/{idea_id}")
def paper_idea(
    idea_id: str,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    """One idea's full scorecard: forward NAV curve + backtest baseline +
    stat gates. 404 when the idea doesn't exist or isn't this user's."""
    detail = idea_detail(db, user_id, idea_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Idea not found")
    return detail
