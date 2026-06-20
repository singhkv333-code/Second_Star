"""Read/write helpers for ``BrokerSession`` rows + at-rest secret crypto.

Single source of truth for "which session does this user / data-read use" and
for encrypting the secret columns. Connectors and the routing/scheduler layers
go through here so encryption + lookup semantics stay consistent.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from backend.models import BrokerSession, User
from backend.security.encryption import get_cipher

# Columns that hold secrets and must be encrypted at rest when a cipher is set.
_SECRET_FIELDS = (
    "access_token",
    "request_token",
    "refresh_token",
    "api_key",
    "api_secret",
    "totp_secret",
)


def _enc(value: Optional[str]) -> Optional[str]:
    """Encrypt a secret for storage (pass-through in dev when no key set)."""
    if value is None:
        return None
    cipher = get_cipher()
    return cipher.encrypt(value) if cipher is not None else value


def read_secret(value: Optional[str]) -> str:
    """Decrypt an at-rest secret column. Tolerates legacy plaintext rows (the
    cipher short-circuits when the Fernet prefix is absent). Returns "" for
    NULL so callers can route to the mock path."""
    if not value:
        return ""
    cipher = get_cipher()
    if cipher is None:
        return str(value)
    return cipher.decrypt(str(value)) or ""


def read_broker_access_token(session: Optional[BrokerSession]) -> str:
    """Plaintext access token from a session row (or "" if none)."""
    if session is None:
        return ""
    return read_secret(getattr(session, "access_token", None))


def get_broker_session(
    db: Session, user_id: int, broker: str
) -> Optional[BrokerSession]:
    """The user's session for a specific broker (active or not)."""
    return (
        db.query(BrokerSession)
        .filter(BrokerSession.user_id == user_id, BrokerSession.broker == broker)
        .first()
    )


def get_active_broker_session(
    db: Session, user_id: int
) -> Optional[BrokerSession]:
    """The user's active trading-broker session, most-recently-updated first.
    Mirrors ``User.active_broker_session`` for call sites that only hold a
    db + user_id."""
    rows = (
        db.query(BrokerSession)
        .filter(BrokerSession.user_id == user_id, BrokerSession.is_active.is_(True))
        .order_by(
            BrokerSession.updated_at.desc().nullslast(),
            BrokerSession.id.desc(),
        )
        .all()
    )
    return rows[0] if rows else None


def get_active_kite_session(db: Session) -> Optional[BrokerSession]:
    """The most-recent active *Kite* session, app-level (any user). Kite stays
    the market-data provider, so historical/ticker/system reads pull a Kite
    token regardless of which user owns it. Replaces the old
    ``db.query(KiteSession).filter(is_active)`` pattern."""
    return (
        db.query(BrokerSession)
        .filter(
            BrokerSession.broker == "kite",
            BrokerSession.is_active.is_(True),
            BrokerSession.access_token.isnot(None),
        )
        .order_by(
            BrokerSession.updated_at.desc().nullslast(),
            BrokerSession.id.desc(),
        )
        .first()
    )


def upsert_broker_session(
    db: Session, user_id: int, broker: str, *, commit: bool = True, **fields
) -> BrokerSession:
    """Create or update a ``(user_id, broker)`` session, encrypting any secret
    fields before write. Unknown keys are ignored. Returns the refreshed row.
    """
    session = get_broker_session(db, user_id, broker)
    if session is None:
        session = BrokerSession(user_id=user_id, broker=broker)
        db.add(session)
    for key, value in fields.items():
        if not hasattr(session, key):
            continue
        if key in _SECRET_FIELDS:
            value = _enc(value)
        setattr(session, key, value)
    if commit:
        db.commit()
        db.refresh(session)
    return session
