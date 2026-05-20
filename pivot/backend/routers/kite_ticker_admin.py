"""Admin endpoints for the Kite ticker singleton.

Contract: PHASE2_CONTRACT.md §Layer 5 — Admin surface.

  - GET  /api/admin/kite-ticker/status
  - POST /api/admin/kite-ticker/start
  - POST /api/admin/kite-ticker/stop

Auth: same JWT bearer pattern as `backend/routers/admin.py`. We don't
gate to a specific role yet (none exist in v1); when roles ship, flip
the dependency to admin-only.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.kite.auth import (
    KITE_MOCK_MODE,
    read_kite_access_token,
)
from backend.kite.portfolio import get_holdings
from backend.kite.ticker import get_ticker_manager
from backend.models import KiteSession
from backend.routers._deps import require_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/kite-ticker", tags=["Admin"])


@router.get("/status")
def ticker_status(
    _user_id: int = Depends(require_user),
) -> dict[str, Any]:
    """Return the current ticker status (always — never 404)."""
    return get_ticker_manager().status()


@router.post("/start")
def ticker_start(
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Start the ticker under the calling user's Kite access token.

    Idempotent — if the ticker is already running, returns its status
    without trying to re-start.
    """
    manager = get_ticker_manager()
    if manager.status()["running"]:
        return manager.status()

    if KITE_MOCK_MODE:
        # In mock mode the manager intentionally refuses to start.
        # Return the status verbatim so the caller sees `running=False`
        # and can act on it.
        return manager.status()

    session = (
        db.query(KiteSession)
        .filter(KiteSession.user_id == user_id, KiteSession.is_active.is_(True))
        .order_by(KiteSession.updated_at.desc().nullslast(), KiteSession.id.desc())
        .first()
    )
    if session is None:
        raise HTTPException(404, "no active Kite session for user")

    access_token = read_kite_access_token(session)
    if not access_token or access_token.startswith("mock_"):
        raise HTTPException(409, "Kite access token unavailable or mocked")

    seed_symbols = _seed_symbols_from_holdings(access_token)
    try:
        return manager.start(
            access_token=access_token,
            user_id=user_id,
            seed_symbols=seed_symbols,
        )
    except Exception as exc:
        logger.exception("KiteTickerManager.start failed: %s", exc)
        raise HTTPException(503, f"ticker failed to start: {str(exc)[:160]}")


@router.post("/stop")
def ticker_stop(
    _user_id: int = Depends(require_user),
) -> dict[str, Any]:
    """Stop the ticker. Idempotent."""
    return get_ticker_manager().stop()


def _seed_symbols_from_holdings(access_token: str) -> list[str]:
    """Best-effort: pull holding tradingsymbols to seed the ticker.

    Failures here are non-fatal — we'll just start the ticker with
    only the default index basket. The user can hot-add anything else
    via the WS subscribe path.
    """
    try:
        holdings = get_holdings(access_token) or []
    except Exception as exc:
        logger.warning("holdings fetch for ticker seed failed: %s", exc)
        return []
    out: list[str] = []
    for h in holdings:
        sym = h.get("tradingsymbol") if isinstance(h, dict) else None
        if sym:
            out.append(str(sym))
    # Top 50 by market value if more than that — Kite caps the
    # subscription set at 3000 but our default budget is 500.
    return out[:50]
