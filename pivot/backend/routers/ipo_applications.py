"""IPO applications REST surface — P0 (register-not-execute).

Endpoints:
  POST /ipo-applications              register intent (writes a row)
  POST /ipo-applications/{id}/withdraw  withdraw an existing intent
  GET  /users/ipo-applications        list current user's applications

Mounted BARE (like /orders, /paper) — the FE's ``requestLegacy`` helper
hits these without the /api prefix; the canonical /api error envelope
in main.py is therefore NOT applied here, by design.

No broker / ASBA / UPI-mandate call EVER. P0 persists the user's stated
intent so the UI can recall the application card and a later P2 reminder
worker can email them at the IPO open / close. The user places + funds
the bid themselves, in their broker app.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.auth.jwt_handler import get_user_id_from_token
from backend.database import get_db
from backend.models import IPOApplication
from backend.services.ipo_application_service import (
    compute_amount_estimate,
    find_open_duplicate,
    mask_upi_id,
    persist_ipo_application,
)
from backend.services.ipo_feed import (
    get_ipo_details,
    list_upcoming_ipos,
    parse_price_band,
)
from backend.utils.time_utils import format_ist


logger = logging.getLogger(__name__)

router = APIRouter(tags=["IPO"])


# ── Auth dependency (matches sip.py / orders.py shape) ──────────────────

def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    user_id = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


# ── Request / response models ───────────────────────────────────────────

class IPOApplicationRegisterRequest(BaseModel):
    """Body for POST /ipo-applications.

    The chat-confirm path passes these from the application card's editable
    block. Amount is recomputed SERVER-SIDE — the client's amount, if any,
    is discarded.
    """
    ipo_symbol: str = Field(..., description="NSE IPO symbol, case-insensitive")
    category: str = Field(
        ..., description="retail | snii | bnii | shareholder | employee",
    )
    quantity_lots: int = Field(..., ge=1)
    bid_price_mode: str = Field(..., description="cutoff | fixed")
    bid_price: Optional[float] = Field(
        default=None,
        description="Required when bid_price_mode == 'fixed'. Must lie in band.",
    )
    upi_id_masked: Optional[str] = Field(
        default=None,
        description=(
            "User's UPI id. Stored MASKED only — never the raw handle. "
            "The field name reflects what we PERSIST; the client may pass "
            "the raw value here and the service masks it on the way in."
        ),
    )
    conversation_id: Optional[str] = None


class IPOApplicationOut(BaseModel):
    """Serialised IPOApplication row for the list + register responses."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    ipo_symbol: str
    ipo_name: Optional[str]
    ipo_type: str
    category: str
    quantity_lots: int
    lot_size: int
    bid_price_mode: str
    bid_price: Optional[float]
    amount_estimate: float
    upi_id_masked: Optional[str]
    status: str
    autonomous: bool
    paper_mode: bool
    stale: bool
    conversation_id: Optional[str]
    source: Optional[str]
    created_at: str
    updated_at: Optional[str]


def _serialize(row: IPOApplication) -> dict:
    return {
        "id": row.id,
        "ipo_symbol": row.ipo_symbol,
        "ipo_name": row.ipo_name,
        "ipo_type": row.ipo_type,
        "category": row.category,
        "quantity_lots": row.quantity_lots,
        "lot_size": row.lot_size,
        "bid_price_mode": row.bid_price_mode,
        "bid_price": row.bid_price,
        "amount_estimate": float(row.amount_estimate),
        "upi_id_masked": row.upi_id_masked,
        "status": row.status,
        "autonomous": bool(row.autonomous),
        "paper_mode": bool(row.paper_mode),
        "stale": bool(row.stale),
        "conversation_id": row.conversation_id,
        "source": row.source,
        "created_at": format_ist(row.created_at) if row.created_at else "—",
        "updated_at": format_ist(row.updated_at) if row.updated_at else None,
    }


# ── POST /ipo-applications ─────────────────────────────────────────────

@router.post("/ipo-applications", status_code=201)
def register_ipo_application(
    request: IPOApplicationRegisterRequest,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    """Register a user's IPO application intent.

    Pipeline:
      1. Re-validate the IPO via ``get_ipo_details``.
         - Unreachable feed → register anyway with ``stale=true`` + note.
         - status == 'closed' → reject 422, honestly.
      2. Compute ``amount_estimate`` SERVER-SIDE from the live record's
         price band + lot size. The client's amount (if any) is ignored.
      3. Soft duplicate check on (user_id, ipo_symbol).
      4. Persist row with status="registered", source="chat-confirm".
         No broker call.
    """
    symbol = request.ipo_symbol.strip().upper()
    if not symbol:
        raise HTTPException(
            status_code=422,
            detail="ipo_symbol is required",
        )

    # ── 1. Re-validate against the live feed ───────────────────────────
    feed_data = get_ipo_details(symbol)
    stale = False
    ipo_name: Optional[str] = None
    ipo_type: str = "mainboard"
    lot_size: Optional[int] = None
    price_band: Optional[dict] = None

    if feed_data.get("source") == "unreachable":
        # Honest fallback: register but mark stale, and require the client
        # to have included enough context for the math. Without a live
        # band we have no way to compute the amount; reject 422.
        raise HTTPException(
            status_code=503,
            detail=(
                "Live IPO feed unreachable — cannot validate the IPO right "
                "now. Try again in a minute."
            ),
        )

    if not feed_data.get("found"):
        raise HTTPException(
            status_code=404,
            detail=f"IPO {symbol!r} is not in the current live feed.",
        )

    ipo = feed_data.get("ipo") or {}
    ipo_name = ipo.get("name")
    ipo_type = "sme" if ipo.get("type") == "sme" else "mainboard"

    status_ = (ipo.get("status") or "").lower()
    if status_ == "closed":
        raise HTTPException(
            status_code=422,
            detail=(
                f"IPO {symbol!r} is closed — cannot register a new intent. "
                "The subscription window has ended."
            ),
        )

    # Coerce lot_size. NSE returns it as int or string.
    raw_lot = ipo.get("lot_size")
    try:
        lot_size = int(raw_lot) if raw_lot not in (None, "") else None
    except (TypeError, ValueError):
        lot_size = None

    price_band = parse_price_band(ipo.get("price_band"))

    if lot_size is None or lot_size < 1:
        raise HTTPException(
            status_code=422,
            detail="IPO lot size is not available — cannot compute amount.",
        )
    if price_band is None:
        raise HTTPException(
            status_code=422,
            detail="IPO price band is not available — cannot compute amount.",
        )

    # ── 2. Validate the editable block ─────────────────────────────────
    mode = request.bid_price_mode
    if mode not in {"cutoff", "fixed"}:
        raise HTTPException(
            status_code=422,
            detail="bid_price_mode must be 'cutoff' or 'fixed'.",
        )

    if ipo_type == "sme" and mode == "cutoff":
        raise HTTPException(
            status_code=422,
            detail="SME issues do not allow cut-off bidding.",
        )
    if mode == "cutoff" and request.category not in {"retail", "employee"}:
        raise HTTPException(
            status_code=422,
            detail=(
                "Cut-off price is only allowed for retail or employee "
                "categories; provide an explicit in-band bid_price."
            ),
        )

    if mode == "fixed":
        if request.bid_price is None:
            raise HTTPException(
                status_code=422,
                detail="bid_price is required when bid_price_mode == 'fixed'.",
            )
        if not (price_band["min"] <= request.bid_price <= price_band["max"]):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"bid_price must lie in [{price_band['min']}, "
                    f"{price_band['max']}]."
                ),
            )

    min_lots = 2 if ipo_type == "sme" else 1
    if request.quantity_lots < min_lots:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Minimum lots for a {ipo_type} IPO is {min_lots}."
            ),
        )

    # ── 3. Server-side amount math + cap enforcement ───────────────────
    try:
        amount_estimate = compute_amount_estimate(
            quantity_lots=request.quantity_lots,
            lot_size=lot_size,
            bid_price_mode=mode,
            bid_price=request.bid_price,
            price_band_max=price_band["max"],
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Retail cap (mainboard retail only): amount-AT-CUTOFF (cap) <= 2L.
    if ipo_type == "mainboard" and request.category == "retail":
        cap_amount = (
            request.quantity_lots * lot_size * float(price_band["max"])
        )
        if cap_amount > 200_000:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Retail mainboard cap (₹2,00,000 at cut-off) exceeded — "
                    "either reduce lots or switch category to S-NII."
                ),
            )

    # UPI hard cap.
    if amount_estimate > 500_000:
        raise HTTPException(
            status_code=422,
            detail=(
                "Amount exceeds the ₹5,00,000 UPI mandate cap — "
                "use bank-ASBA via your broker for this size."
            ),
        )

    # ── 4. Soft duplicate check ────────────────────────────────────────
    duplicate = find_open_duplicate(db, user_id, symbol)

    # ── 5. Persist ─────────────────────────────────────────────────────
    upi_masked = mask_upi_id(request.upi_id_masked)
    row = persist_ipo_application(
        db, user_id,
        ipo_symbol=symbol,
        ipo_name=ipo_name,
        ipo_type=ipo_type,
        category=request.category,
        quantity_lots=request.quantity_lots,
        lot_size=lot_size,
        bid_price_mode=mode,
        bid_price=request.bid_price,
        amount_estimate=amount_estimate,
        upi_id_masked=upi_masked,
        conversation_id=request.conversation_id,
        source="chat-confirm",
        stale=stale,
    )
    db.commit()
    db.refresh(row)

    response: dict = {
        "application": _serialize(row),
        "duplicate": bool(duplicate),
    }
    if duplicate is not None:
        response["replace_offer"] = {
            "previous_id": duplicate.id,
            "note": (
                "You already have an open application for this IPO. "
                "This new row is registered alongside it — withdraw the "
                "old one if you meant to replace it."
            ),
        }
    return response


# ── POST /ipo-applications/{id}/withdraw ───────────────────────────────

@router.post("/ipo-applications/{application_id}/withdraw")
def withdraw_ipo_application(
    application_id: int,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    """Withdraw a previously-registered intent.

    Allowed while the issue is still open / upcoming. After close-date
    we 422 the request — withdrawing a closed-window intent is not
    meaningful (the broker would have ignored an unmade bid anyway).
    """
    row = (
        db.query(IPOApplication)
        .filter(
            IPOApplication.id == application_id,
            IPOApplication.user_id == user_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="IPO application not found.",
        )
    if row.status == "withdrawn":
        # Idempotent — return the existing row.
        return {"application": _serialize(row)}

    # Re-check the IPO state. We tolerate the feed being unreachable here
    # (the user's intention to withdraw shouldn't be blocked by NSE flaking).
    feed_data = get_ipo_details(row.ipo_symbol)
    if feed_data.get("found"):
        live_status = (
            (feed_data.get("ipo") or {}).get("status") or ""
        ).lower()
        if live_status == "closed":
            raise HTTPException(
                status_code=422,
                detail=(
                    "IPO subscription window has closed — withdrawal is "
                    "no longer meaningful."
                ),
            )

    row.status = "withdrawn"
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"application": _serialize(row)}


# ── GET /users/ipo-applications ────────────────────────────────────────

# ── GET /ipo-calendar ──────────────────────────────────────────────────


@router.get("/ipo-calendar")
def get_ipo_calendar(
    user_id: int = Depends(get_user_id),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
):
    """Return upcoming / open / recently-closed IPOs in a calendar-friendly
    shape, optionally filtered to [from, to] (inclusive).

    Used by the FE Calendar tab to render IPO open/close entries
    alongside scheduled workflows. Date filter is inclusive on both
    ends; rows missing either date are still included (the FE handles
    None / TBA display).

    Honest on failure: when the live feed is unreachable we surface the
    note verbatim (same shape as ``list_upcoming_ipos``) — never
    fabricate dates / bands. Bare-mount (no /api prefix) to match the
    other IPO routes; ``get_user_id`` auth kept for consistency.

    Note: FastAPI reserves the keyword ``from`` so we accept the query
    parameter via the ``from_`` parameter name; the OpenAPI spec
    exposes ``from`` to clients via FastAPI's standard alias handling
    on the parameter name. (Clients call ``/ipo-calendar?from=...&to=...``.)
    """
    listing = list_upcoming_ipos()
    if listing.get("source") == "unreachable":
        return {
            "count": 0,
            "items": [],
            "note": listing.get("note"),
            "source": "unreachable",
        }

    rows = listing.get("ipos") or []

    # Date window filtering. We compare lexicographically since
    # ipo_feed._normalize emits ISO dates ('YYYY-MM-DD') when parseable
    # — which is the standard branch for NSE responses. Rows with
    # un-parseable / missing dates pass through (Calendar tab handles
    # the TBA case).
    lo = (from_ or "").strip()
    hi = (to or "").strip()

    def _in_window(item: dict[str, object]) -> bool:
        if not lo and not hi:
            return True
        open_d = str(item.get("open_date") or "")
        close_d = str(item.get("close_date") or "")
        # Include rows where any of the dates intersect the window, OR
        # the dates are missing / un-parseable (don't accidentally drop
        # an upcoming-but-undated IPO from the calendar).
        if not open_d and not close_d:
            return True
        if lo and close_d and close_d < lo:
            return False
        if hi and open_d and open_d > hi:
            return False
        return True

    items: list[dict[str, object]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if not _in_window(r):
            continue
        items.append({
            "ipo_symbol": (r.get("symbol") or "").upper(),
            "name": r.get("name"),
            "open_date": r.get("open_date"),
            "close_date": r.get("close_date"),
            "price_band": r.get("price_band"),
            "status": r.get("status"),
            "type": r.get("type"),
        })

    return {
        "count": len(items),
        "items": items,
        "note": listing.get("note"),
        "source": listing.get("source"),
    }


@router.get("/users/ipo-applications")
def list_user_ipo_applications(
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
    limit: int = 50,
):
    """Return the user's IPO application history.

    Labelled "estimated amount you'll need" in the FE — NOT "blocked".
    Nothing is blocked; Pivot did not submit the bid.
    """
    limit = max(1, min(limit, 200))
    rows = (
        db.query(IPOApplication)
        .filter(IPOApplication.user_id == user_id)
        .order_by(IPOApplication.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "count": len(rows),
        "items": [_serialize(r) for r in rows],
        "amount_label": "estimated amount you'll need",
    }
