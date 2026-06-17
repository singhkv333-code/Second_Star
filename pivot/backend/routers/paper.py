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
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from backend.auth.jwt_handler import get_user_id_from_token
from backend.database import get_db
from backend.models import PAPER_ACCOUNT_MODES, PaperIpoAllocation
from backend.paper.accounts import get_or_create_account
from backend.paper.ipo_sim import serialize_paper_ipo_allocation
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


# ── Account trading mode (real/live vs paper) ─────────────────────────────
# The single source of truth for whether this user's orders fill in the
# paper book: ``should_use_paper`` (backend/paper/routing.py) reads
# ``account.mode``. The frontend mode toggle drives THIS endpoint, so a
# 'paper' mode genuinely routes buys/sells to the PaperBroker and a 'live'
# mode leaves the real/Kite path untouched. Read-only ``account.mode`` —
# no balances or positions are mutated here.

class AccountModeResponse(BaseModel):
    mode: str  # 'paper' | 'live'


class SetAccountModeRequest(BaseModel):
    mode: str

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in PAPER_ACCOUNT_MODES:  # {'paper', 'live'}
            raise ValueError(
                f"mode must be one of {sorted(PAPER_ACCOUNT_MODES)}"
            )
        return v


@router.get("/account/mode", response_model=AccountModeResponse)
def get_account_mode(
    user_id: int = Depends(get_user_id), db: Session = Depends(get_db)
):
    """Current trading mode for the user's paper account. Creates+seeds the
    account on first touch (default mode 'paper'); commit persists that
    lazy create, matching how ``should_use_paper`` materialises it."""
    acct = get_or_create_account(db, user_id)
    db.commit()
    return AccountModeResponse(mode=str(acct.mode))


@router.post("/account/mode", response_model=AccountModeResponse)
def set_account_mode(
    body: SetAccountModeRequest,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    """Set the trading mode. Only ``account.mode`` changes — this is the
    seam that makes subsequent buys/sells route to the paper book (mode
    'paper') or the real/Kite path (mode 'live'). Never touches balances."""
    acct = get_or_create_account(db, user_id)
    acct.mode = body.mode
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return AccountModeResponse(mode=str(acct.mode))


@router.get("/holdings")
def paper_holdings(
    user_id: int = Depends(get_user_id), db: Session = Depends(get_db)
):
    return holdings(db, user_id)


@router.get("/greeks")
def paper_greeks(
    user_id: int = Depends(get_user_id), db: Session = Depends(get_db)
):
    """F&O P2: live portfolio Greeks over open option positions (net
    delta/gamma/theta/vega + FutEq delta-notional + per-underlying and
    per-expiry breakdowns). Same payload shape as the chat card."""
    from backend.services.portfolio_greeks import portfolio_greeks_card

    return portfolio_greeks_card(db, user_id)


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


# ── P3: labelled IPO allocation ledger ────────────────────────────────────

@router.get("/ipo-allocations")
def paper_ipo_allocations(
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
) -> list[dict]:
    """The user's simulated IPO allocations (paper mode only).

    Returns a list of allocations sorted most-recent first; empty list
    when none exist. Each row is clearly labelled ``simulated: true``
    so the FE renderer can never confuse this set with real cash moves.
    No paginate / no filter in P3 — IPO intents are small in volume per
    user (a handful per quarter) and the FE shows them all in one block.
    """
    rows = (
        db.query(PaperIpoAllocation)
        .filter(PaperIpoAllocation.user_id == int(user_id))
        .order_by(PaperIpoAllocation.created_at.desc(), PaperIpoAllocation.id.desc())
        .all()
    )
    return [serialize_paper_ipo_allocation(r) for r in rows]
