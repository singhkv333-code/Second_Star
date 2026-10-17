"""F&O P0 admin/debug surface — chain + universe inspection, manual refresh.

  GET  /admin/options/chain?underlying=NIFTY[&expiry=YYYY-MM-DD][&width=10]
       ATM-centered chain slice with quotes + IV(+status) + Greeks.
  GET  /admin/options/universe[?as_of=YYYY-MM-DD]
       The dynamic universe rows (selected / research-only verdicts).
  GET  /admin/options/expiries?underlying=NIFTY
       Tradable expiries from the instrument master.
  POST /admin/options/refresh
       Manual instrument-master refresh + universe re-selection (the
       scheduler runs this daily at 08:35 IST; this is the dev lever).

Same posture as routers/admin.py: debug/observability surface, JWT not
yet enforced (becomes admin-only when roles land). Read endpoints are
non-mutating; the refresh POST is idempotent.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.market.instrument_master import (
    list_expiries,
    refresh_instrument_master,
    select_active_universe,
)
from backend.market.option_chain import get_chain
from backend.models import OptionUniverse
from backend.routers._deps import require_admin

logger = logging.getLogger(__name__)

# 2026-07-04 (beta-prep): these endpoints previously had NO auth at all —
# any unauthenticated caller could trigger /refresh. Whole /admin/ prefix is
# now gated by require_admin (fail-closed via ADMIN_USER_IDS).
router = APIRouter(prefix="/admin/options", tags=["Admin — F&O"])


@router.get("/chain", summary="ATM-centered option-chain slice")
async def chain(
    underlying: str = Query(..., min_length=1, max_length=40),
    expiry: Optional[str] = Query(None, description="YYYY-MM-DD; nearest when omitted"),
    width: int = Query(10, ge=1, le=40, description="strikes each side of ATM"),
    db: Session = Depends(get_db),
    _admin: int = Depends(require_admin),
) -> dict[str, Any]:
    payload = get_chain(db, underlying, expiry, width=width)
    if payload is None:
        raise HTTPException(
            404,
            f"No option chain for '{underlying.upper()}'"
            + (f" expiry {expiry}" if expiry else "")
            + " — unknown underlying/expiry, or instrument master not refreshed.",
        )
    return payload


@router.get("/expiries", summary="Tradable option expiries for an underlying")
async def expiries(
    underlying: str = Query(..., min_length=1, max_length=40),
    db: Session = Depends(get_db),
    _admin: int = Depends(require_admin),
) -> dict[str, Any]:
    rows = list_expiries(db, underlying)
    if not rows:
        raise HTTPException(404, f"No expiries for '{underlying.upper()}'")
    return {"underlying": underlying.upper(), "expiries": rows}


@router.get("/universe", summary="Dynamic option universe (liquidity verdicts)")
async def universe(
    as_of: Optional[str] = Query(None, description="YYYY-MM-DD; latest when omitted"),
    db: Session = Depends(get_db),
    _admin: int = Depends(require_admin),
) -> dict[str, Any]:
    q = db.query(OptionUniverse)
    if as_of:
        q = q.filter(OptionUniverse.as_of == date.fromisoformat(as_of))
    else:
        latest = (
            db.query(OptionUniverse.as_of)
            .order_by(OptionUniverse.as_of.desc())
            .first()
        )
        if latest is None:
            return {"as_of": None, "underlyings": []}
        q = q.filter(OptionUniverse.as_of == latest[0])
    rows = q.order_by(OptionUniverse.liquidity_score.desc().nullslast()).all()
    return {
        "as_of": rows[0].as_of.isoformat() if rows else None,
        "underlyings": [
            {
                "underlying": r.underlying,
                "segment": r.segment,
                "exchange": r.exchange,
                "selected": r.selected,
                "research_only": r.research_only,
                "reason": r.reason,
                "liquidity_score": r.liquidity_score,
                "avg_oi": r.avg_oi,
                "avg_volume": r.avg_volume,
                "spread_pct_atm": r.spread_pct_atm,
            }
            for r in rows
        ],
    }


@router.post("/refresh", summary="Refresh instrument master + universe now")
async def refresh(
    db: Session = Depends(get_db),
    _admin: int = Depends(require_admin),
) -> dict[str, Any]:
    counts = refresh_instrument_master(db)
    selected = select_active_universe(db)
    return {
        **counts,
        "universe_scored": len(selected),
        "universe_selected": sum(1 for r in selected if r.selected),
        "research_only": sum(1 for r in selected if r.research_only),
    }
