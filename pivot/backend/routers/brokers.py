"""Unified multi-broker connection endpoints.

Replaces the old Kite-only ``routers/kite.py``. Every broker (Kite, Dhan, …)
onboards, connects, persists, and disconnects through ONE surface here; the
broker-specific behaviour lives behind the :class:`BrokerConnector` resolved
from ``brokers.registry``.

Flow (OAuth brokers, e.g. Kite):
  1. Frontend (authenticated) calls GET /brokers/{broker}/login_url
     → backend returns the broker OAuth URL embedded with a short-lived state
       JWT encoding the user_id. In mock mode the response signals the UI to
       call POST /brokers/{broker}/connect-mock instead.
  2. User signs in; the broker redirects to GET /brokers/{broker}/callback
     (or the bare /callback alias for Kite apps) with ?request_token=&state=.
  3. Backend verifies the state JWT → connector.complete_auth exchanges the
     token and upserts the BrokerSession → redirects the browser back to
     FRONTEND_URL with ?broker=connected (or ?broker=error&reason=...).
  4. Frontend polls GET /brokers/{broker}/status and can DELETE the session.

Credential-only brokers (Dhan PIN+TOTP / pasted token) skip the redirect and
POST to /brokers/{broker}/credentials.
"""
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth.jwt_handler import get_user_id_from_token
from backend.brokers.registry import get_connector, is_supported, list_connectors
from backend.brokers.sessions import get_broker_session
from backend.config import settings
from backend.database import get_db
from backend.models import BrokerSession, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brokers", tags=["Brokers"])

# Compatibility alias for OAuth brokers (Kite) whose configured Redirect URL is
# `/callback` (without the `/brokers/{broker}/` prefix). Mounted in main.py.
callback_alias_router = APIRouter(tags=["Brokers"])


BROKER_STATE_PURPOSE = "broker_oauth_state"
# 30 min: a short TTL expired during slow logins (broker login + 2FA + the user
# reading the modal) → callback rejected with `invalid_state` AFTER the broker
# had already issued a request_token, so the token was discarded.
BROKER_STATE_TTL_SECONDS = 1800


def _require_user(authorization: str | None, db: Session) -> User:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.replace("Bearer ", "")
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _make_state_token(user_id: int, broker: str) -> str:
    payload = {
        "sub": str(user_id),
        "broker": broker,
        "purpose": BROKER_STATE_PURPOSE,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc)
        + timedelta(seconds=BROKER_STATE_TTL_SECONDS),
    }
    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def _read_state_token(state: str) -> tuple[int | None, str | None]:
    """Return ``(user_id, broker)`` from a signed state JWT, or ``(None, None)``."""
    try:
        payload = jwt.decode(
            state, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None, None
    if payload.get("purpose") != BROKER_STATE_PURPOSE:
        return None, None
    sub = payload.get("sub")
    return (int(sub) if sub else None), payload.get("broker")


def _frontend_redirect(params: dict) -> RedirectResponse:
    base = settings.frontend_url.rstrip("/")
    return RedirectResponse(f"{base}/?{urlencode(params)}", status_code=302)


def _status_payload(connector, session: BrokerSession | None) -> dict:
    """The connection-status object the FE renders per broker."""
    if not session or not session.is_active:
        return {
            "connected": False,
            "mock_mode": connector.mock_mode(),
            "broker_user_id": None,
            "persistence_mode": None,
            "auto_login_opt_in": False,
            "expires_at": None,
        }
    return {
        "connected": True,
        "mock_mode": connector.mock_mode(),
        "broker_user_id": session.broker_user_id,
        "persistence_mode": session.persistence_mode,
        "auto_login_opt_in": bool(session.auto_login_opt_in),
        "expires_at": (
            session.token_expires_at.isoformat()
            if session.token_expires_at
            else None
        ),
    }


def _broker_entry(connector, session: BrokerSession | None) -> dict:
    """One element of GET /brokers — static metadata + deep links + status."""
    info = asdict(connector.info)
    links = connector.deep_links()  # static links only — omit `login`
    deep_links = {
        k: v
        for k, v in asdict(links).items()
        if v is not None and k != "login"
    }
    return {
        "id": info["id"],
        "name": info["name"],
        "logo": info["logo"],
        # PersistenceKind is a str-Enum; asdict yields the enum — coerce to str.
        "persistence_kind": str(getattr(
            info["persistence_kind"], "value", info["persistence_kind"]
        )),
        "supports_unattended": info["supports_unattended"],
        "needs_api_key": info["needs_api_key"],
        "accent": info["accent"],
        "blurb": info["blurb"],
        "tags": info["tags"],
        "deep_links": deep_links,
        "status": _status_payload(connector, session),
    }


def _resolve(broker: str):
    """Resolve a connector or 404 on unknown broker."""
    if not is_supported(broker):
        raise HTTPException(status_code=404, detail=f"Unknown broker: {broker}")
    return get_connector(broker)


# ── pydantic bodies ────────────────────────────────────────────────────────
class CredentialsBody(BaseModel):
    """Connect via submitted credentials (Dhan client_id/PIN/TOTP, pasted
    token, or Kite advanced api_key/secret). All optional — the connector
    decides which path the supplied fields select."""

    api_key: str | None = None
    api_secret: str | None = None
    client_id: str | None = None
    pin: str | None = None
    totp_secret: str | None = None
    access_token: str | None = None
    auto_login_opt_in: bool = False


class AutomationBody(BaseModel):
    auto_login_opt_in: bool = Field(..., description="Opt in/out of unattended auto-login")


# ── listing + status ───────────────────────────────────────────────────────
@router.get("")
def list_brokers(
    authorization: str | None = Header(None), db: Session = Depends(get_db)
):
    """All supported brokers with static metadata, deep links, and this user's
    per-broker connection status."""
    user = _require_user(authorization, db)
    brokers = [
        _broker_entry(
            connector, get_broker_session(db, user.id, connector.broker)
        )
        for connector in list_connectors()
    ]
    return {"brokers": brokers}


@router.get("/{broker}/status")
def broker_status(
    broker: str,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Connection status for one broker for the authenticated user."""
    connector = _resolve(broker)
    user = _require_user(authorization, db)
    return _status_payload(connector, get_broker_session(db, user.id, broker))


@router.get("/{broker}/login_url")
def broker_login_url(
    broker: str,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """The broker OAuth URL embedded with a signed state JWT, plus a flag for
    mock mode (UI calls POST /brokers/{broker}/connect-mock instead)."""
    connector = _resolve(broker)
    user = _require_user(authorization, db)
    state = _make_state_token(user.id, broker)
    return {
        "mock_mode": connector.mock_mode(),
        "login_url": connector.get_login_url(state),
        "state": state,
    }


# ── connecting ─────────────────────────────────────────────────────────────
@router.post("/{broker}/connect-mock")
def broker_connect_mock(
    broker: str,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Mock-mode shortcut: skips the broker browser flow and writes a fake
    BrokerSession so the rest of the app behaves as if connected."""
    connector = _resolve(broker)
    user = _require_user(authorization, db)
    if not connector.mock_mode():
        raise HTTPException(
            status_code=400,
            detail="Mock connect is only available when the broker has no real credentials configured",
        )
    connector.complete_auth(db, user.id, {})
    return _status_payload(connector, get_broker_session(db, user.id, broker))


@router.post("/{broker}/credentials")
def broker_credentials(
    broker: str,
    body: CredentialsBody,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Connect via submitted credentials (Dhan client_id/PIN/TOTP or pasted
    token; Kite advanced api_key/secret). The connector validates + upserts."""
    connector = _resolve(broker)
    user = _require_user(authorization, db)
    try:
        connector.complete_auth(db, user.id, body.dict())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Connect failed: {exc}")
    return _status_payload(connector, get_broker_session(db, user.id, broker))


@router.post("/{broker}/automation")
def broker_automation(
    broker: str,
    body: AutomationBody,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Toggle unattended auto-login for this broker's session."""
    connector = _resolve(broker)
    user = _require_user(authorization, db)
    from backend.brokers.sessions import upsert_broker_session

    upsert_broker_session(
        db, user.id, broker, auto_login_opt_in=body.auto_login_opt_in
    )
    return _status_payload(connector, get_broker_session(db, user.id, broker))


# ── data ───────────────────────────────────────────────────────────────────
@router.get("/{broker}/holdings")
def broker_holdings(
    broker: str,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Holdings for this broker (401 when the user has no session for it)."""
    connector = _resolve(broker)
    user = _require_user(authorization, db)
    session = get_broker_session(db, user.id, broker)
    if session is None:
        raise HTTPException(status_code=401, detail=f"No {broker} session")
    return {"holdings": connector.get_holdings(session)}


@router.delete("/{broker}/session")
def broker_disconnect(
    broker: str,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Disconnect this broker (deletes the BrokerSession row)."""
    _resolve(broker)
    user = _require_user(authorization, db)
    session = get_broker_session(db, user.id, broker)
    if session is not None:
        db.delete(session)
        db.commit()
    return {"connected": False}


# ── OAuth callback (public — identity comes from the state JWT) ─────────────
def _handle_broker_callback(
    broker: str | None,
    request_token: str | None,
    state: str | None,
    status: str | None,
    token_id: str | None,
    db: Session,
):
    """Public callback hit by the broker after the user logs in. No auth header
    is available, so the caller is identified via the signed state JWT we issued
    from /brokers/{broker}/login_url. ``broker`` from the path (when present)
    cross-checks the broker encoded in the state."""
    if status and status.lower() != "success":
        return _frontend_redirect({"broker": "error", "reason": status})

    state_user_id, state_broker = (
        _read_state_token(state) if state else (None, None)
    )
    if state_user_id is None:
        logger.warning("broker callback rejected: invalid_state")
        return _frontend_redirect({"broker": "error", "reason": "invalid_state"})

    broker = broker or state_broker
    if broker is None or not is_supported(broker):
        return _frontend_redirect({"broker": "error", "reason": "unknown_broker"})

    user = db.query(User).filter(User.id == state_user_id).first()
    if user is None:
        return _frontend_redirect({"broker": "error", "reason": "user_not_found"})

    connector = get_connector(broker)
    payload: dict = {}
    if request_token:
        payload["request_token"] = request_token
    if token_id:
        payload["tokenId"] = token_id
    try:
        connector.complete_auth(db, user.id, payload)
    except Exception as exc:
        return _frontend_redirect(
            {"broker": "error", "reason": f"exchange_failed:{exc}"}
        )
    return _frontend_redirect({"broker": "connected"})


@router.get("/{broker}/callback")
def broker_callback(
    broker: str,
    request_token: str | None = Query(None),
    state: str | None = Query(None),
    status: str | None = Query(None),
    tokenId: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Broker OAuth redirect target (canonical path under /brokers/{broker})."""
    return _handle_broker_callback(broker, request_token, state, status, tokenId, db)


@callback_alias_router.get("/callback")
def broker_callback_alias(
    request_token: str | None = Query(None),
    state: str | None = Query(None),
    status: str | None = Query(None),
    tokenId: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Same as GET /brokers/{broker}/callback. Mounted at /callback for OAuth
    brokers (Kite) whose configured Redirect URL is ``<host>/callback``. The
    broker is identified from the signed state JWT."""
    return _handle_broker_callback(None, request_token, state, status, tokenId, db)
