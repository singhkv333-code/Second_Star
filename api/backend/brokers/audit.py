"""Append-only broker audit trail writer.

Single entry point — ``record_audit`` — used by the order routing layer
(auto-exec gating: ``order_intent`` / ``order_placed`` / ``order_failed``)
and the scheduler's daily token sweep (``token_refresh`` /
``token_refresh_failed``) to land a ``BrokerAudit`` row.

The caller owns the transaction (we ``flush`` but never ``commit``), so an
audit row participates in the same unit of work as the order/token op it
records. The whole write is wrapped in try/except: auditing is best-effort
and MUST NEVER break the order it is describing — a failed audit only logs.
``BrokerAudit`` is imported lazily inside the function to avoid an import
cycle (models -> ... -> brokers).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def record_audit(
    db: Session,
    *,
    user_id: Optional[int],
    broker: Optional[str],
    event_type: str,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    quantity: Optional[int] = None,
    order_type: Optional[str] = None,
    price: Optional[float] = None,
    order_id: Optional[str] = None,
    status: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    """Append one ``BrokerAudit`` row (best-effort; never raises).

    ``db.add`` + ``db.flush`` only — the CALLER commits, so the audit shares
    the order/token transaction. Any failure here is swallowed (logged) so a
    bookkeeping problem can never fail the trade it is recording.
    """
    try:
        from backend.models import BrokerAudit

        row = BrokerAudit(
            user_id=int(user_id) if user_id is not None else None,
            broker=broker,
            event_type=event_type,
            symbol=symbol,
            side=side,
            quantity=int(quantity) if quantity is not None else None,
            order_type=order_type,
            price=float(price) if price is not None else None,
            order_id=str(order_id) if order_id is not None else None,
            status=status,
            detail=detail,
        )
        db.add(row)
        db.flush()
    except Exception:  # noqa: BLE001 — auditing must never break an order
        logger.warning(
            "record_audit failed (event_type=%s broker=%s user=%s); "
            "continuing without audit row",
            event_type, broker, user_id, exc_info=True,
        )
