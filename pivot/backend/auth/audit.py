"""Best-effort AuthAudit writer.

Auth flows MUST never break because an audit insert failed. This helper
wraps the DB write in a try/except, rolls back on error, and continues.
Caller owns the session; we don't open or close one.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from backend.models import AuthAudit

logger = logging.getLogger(__name__)


def write_audit(
    db: Session,
    *,
    event: str,
    success: bool,
    email: Optional[str] = None,
    user_id: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    """Insert one AuthAudit row. Swallows every error (logs at WARNING).

    `event` is one of: signup, login, login_failed, refresh, logout,
    email_verify_requested, email_verified, password_reset_requested,
    password_reset.
    """
    try:
        row = AuthAudit(
            user_id=user_id,
            email=email,
            event=event,
            success=success,
            ip_address=ip,
            user_agent=(user_agent or "")[:512] if user_agent else None,
            detail=detail,
        )
        db.add(row)
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("auth_audit insert failed (event=%s): %s", event, e)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
