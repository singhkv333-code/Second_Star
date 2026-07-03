"""System-level (no request context) authenticated Kite access.

Scheduler jobs and admin endpoints need a KiteConnect instance without a
user request to borrow a token from. Dev/prod today is effectively a
single-operator deployment: borrow the most recently updated ACTIVE
KiteSession. Returns ``None`` in mock mode or when no session exists —
callers must degrade to their mock/fixture path, never crash.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from backend.brokers.sessions import get_active_kite_session
from backend.kite.auth import (
    KITE_MOCK_MODE,
    get_authenticated_kite,
    read_kite_access_token,
)

logger = logging.getLogger(__name__)


def get_system_kite(db: Session) -> Optional[object]:
    """Best-effort authenticated KiteConnect for background work."""
    if KITE_MOCK_MODE:
        return None
    session = get_active_kite_session(db)
    token = read_kite_access_token(session)
    if not token:
        logger.info("[kite-system] no active KiteSession; mock path engaged")
        return None
    return get_authenticated_kite(token)
