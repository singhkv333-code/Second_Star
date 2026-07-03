"""IPO application persistence helper.

The router (``backend/routers/ipo_applications.py``) calls this to write
the user's REGISTERED intent into ``ipo_applications``. There is no
broker / ASBA / UPI-mandate call — P0 is "register intent only", spelt
out in the chat disclaimer the FE renders.

Why a separate service: mirrors ``_persist_leg`` in routers/orders.py —
keeps the route handler thin, makes the test that asserts NO broker
function is called trivial (we monkeypatch this helper or import it
directly), and keeps the amount-estimate math + UPI-masking + status
validation behind one named entry point.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from backend.models import (
    IPO_APPLICATION_STATUSES,
    IPO_BID_PRICE_MODES,
    IPO_CATEGORIES,
    IPO_TYPES,
    IPOApplication,
)


def mask_upi_id(upi_id: str | None) -> str | None:
    """Mask a UPI handle for storage. ``alice@okhdfcbank`` -> ``a***@okhdfcbank``.

    Never returns the raw handle. Returns None for empty / falsy input —
    UPI is optional at intent-registration time (the user can fill it in
    later in the broker app).
    """
    if not upi_id:
        return None
    raw = str(upi_id).strip()
    if not raw or "@" not in raw:
        # Garbage-but-non-empty: still mask conservatively.
        if not raw:
            return None
        first = raw[0]
        return f"{first}***"
    local, _, handle = raw.partition("@")
    if not local:
        return f"***@{handle}"
    first = local[0]
    return f"{first}***@{handle}"


def compute_amount_estimate(
    *,
    quantity_lots: int,
    lot_size: int,
    bid_price_mode: str,
    bid_price: float | None,
    price_band_max: float | None,
) -> float:
    """Server-side amount math.

    effective_price = cutoff ? price_band.max : bid_price
    amount_estimate = quantity_lots * lot_size * effective_price

    Raises ``ValueError`` when the inputs don't permit computation — the
    router converts that into a 422 with an honest field-level message.
    The FE also enforces these rules in-card; this is the second wall.
    """
    if quantity_lots < 1:
        raise ValueError("quantity_lots must be >= 1")
    if lot_size < 1:
        raise ValueError("lot_size must be a positive integer")
    if bid_price_mode == "cutoff":
        if price_band_max is None:
            raise ValueError(
                "cannot compute amount: price band missing for cut-off bid"
            )
        effective = float(price_band_max)
    elif bid_price_mode == "fixed":
        if bid_price is None:
            raise ValueError("fixed bid mode requires bid_price")
        effective = float(bid_price)
    else:
        raise ValueError(
            f"unknown bid_price_mode: {bid_price_mode!r}"
        )
    return float(quantity_lots * lot_size * effective)


def persist_ipo_application(
    db: Session,
    user_id: int,
    *,
    ipo_symbol: str,
    ipo_name: str | None,
    ipo_type: str,
    category: str,
    quantity_lots: int,
    lot_size: int,
    bid_price_mode: str,
    bid_price: float | None,
    amount_estimate: float,
    upi_id_masked: str | None,
    conversation_id: str | None = None,
    workflow_id: int | None = None,
    source: str = "chat-confirm",
    stale: bool = False,
    autonomous: bool = False,
    paper_mode: bool = False,
    status: str = "registered",
) -> IPOApplication:
    """Persist an IPO intent row.

    Validates enum-like fields before INSERT so an upstream typo surfaces
    here, not as a check-constraint violation deep in the DB driver.
    No broker call. No external state mutation.

    ``status`` defaults to ``"registered"`` for the router register path;
    the autonomous workflow ``action.arm_ipo_intent`` executor passes
    ``status="intent_armed"`` so the row clearly shows up as a
    workflow-armed intent (vs a chat-confirmed registration). Any value
    must be in ``IPO_APPLICATION_STATUSES`` (matches the DB
    CheckConstraint).
    """
    if ipo_type not in IPO_TYPES:
        raise ValueError(f"ipo_type must be one of {sorted(IPO_TYPES)}")
    if category not in IPO_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(IPO_CATEGORIES)}")
    if bid_price_mode not in IPO_BID_PRICE_MODES:
        raise ValueError(
            f"bid_price_mode must be one of {sorted(IPO_BID_PRICE_MODES)}"
        )
    if status not in IPO_APPLICATION_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(IPO_APPLICATION_STATUSES)}"
        )

    row = IPOApplication(
        user_id=user_id,
        ipo_symbol=ipo_symbol.upper(),
        ipo_name=ipo_name,
        ipo_type=ipo_type,
        category=category,
        quantity_lots=int(quantity_lots),
        lot_size=int(lot_size),
        bid_price_mode=bid_price_mode,
        bid_price=bid_price,
        amount_estimate=float(amount_estimate),
        upi_id_masked=upi_id_masked,
        status=status,
        autonomous=bool(autonomous),
        paper_mode=bool(paper_mode),
        stale=bool(stale),
        conversation_id=conversation_id,
        workflow_id=workflow_id,
        source=source,
    )
    db.add(row)
    return row


def find_open_duplicate(
    db: Session, user_id: int, ipo_symbol: str,
) -> Optional[IPOApplication]:
    """Return an existing non-withdrawn application for the same IPO, if any.

    "Soft duplicate" check: P0 doesn't hard-fail on a duplicate (the user
    might want to bump category / lots after the first registration).
    The route surfaces ``duplicate: true`` + a replace-offer in the
    response; the FE shows a banner.
    """
    return (
        db.query(IPOApplication)
        .filter(
            IPOApplication.user_id == user_id,
            IPOApplication.ipo_symbol == ipo_symbol.upper(),
            IPOApplication.status != "withdrawn",
        )
        .order_by(IPOApplication.id.desc())
        .first()
    )
