"""
Kite Connect integration endpoints.

Flow:
  1. Frontend (authenticated) calls GET /kite/login_url
     → backend returns Kite OAuth URL containing a short-lived state JWT
       that encodes the user_id. In mock mode the response signals the UI
       to call POST /kite/connect-mock instead.
  2. User signs in with Zerodha; Kite redirects to GET /kite/callback
     with ?request_token=...&state=...
  3. Backend verifies state JWT → exchanges request_token → upserts
     KiteSession row → redirects browser back to FRONTEND_URL with
     ?kite=connected (or ?kite=error&reason=...).
  4. Frontend polls GET /kite/status to render the connection state and
     can call DELETE /kite/session to disconnect.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.auth.jwt_handler import get_user_id_from_token
from backend.kite.auth import (
    KITE_MOCK_MODE,
    exchange_request_token,
    get_login_url,
    verify_token_valid,
)
from backend.models import KiteSession, User

router = APIRouter(prefix="/kite", tags=["Kite"])


KITE_STATE_PURPOSE = "kite_oauth_state"
KITE_STATE_TTL_SECONDS = 600  # 10 minutes


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


def _make_state_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "purpose": KITE_STATE_PURPOSE,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(seconds=KITE_STATE_TTL_SECONDS),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _read_state_token(state: str) -> int | None:
    try:
        payload = jwt.decode(
            state, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None
    if payload.get("purpose") != KITE_STATE_PURPOSE:
        return None
    sub = payload.get("sub")
    return int(sub) if sub else None


def _frontend_redirect(params: dict) -> RedirectResponse:
    base = settings.frontend_url.rstrip("/")
    return RedirectResponse(f"{base}/?{urlencode(params)}", status_code=302)


def _upsert_session(db: Session, user_id: int, *, access_token: str,
                    request_token: str | None, kite_user_id: str | None) -> KiteSession:
    session = (
        db.query(KiteSession)
        .filter(KiteSession.user_id == user_id)
        .first()
    )
    now = datetime.now(timezone.utc)
    if session is None:
        session = KiteSession(user_id=user_id)
        db.add(session)
    session.access_token = access_token
    session.request_token = request_token
    session.kite_user_id = kite_user_id
    session.login_time = now
    # Kite tokens expire daily at 06:00 IST, but we don't try to be precise —
    # we just record when we received it; the scheduler / verify_token_valid
    # is the source of truth for expiry decisions.
    session.token_expires_at = now + timedelta(hours=20)
    session.is_active = True
    db.commit()
    db.refresh(session)
    return session


def _session_payload(session: KiteSession | None) -> dict:
    if not session or not session.is_active:
        return {
            "connected": False,
            "mock_mode": KITE_MOCK_MODE,
            "kite_user_id": None,
            "login_time": None,
        }
    return {
        "connected": True,
        "mock_mode": KITE_MOCK_MODE,
        "kite_user_id": session.kite_user_id,
        "login_time": session.login_time.isoformat() if session.login_time else None,
        "expires_at": session.token_expires_at.isoformat() if session.token_expires_at else None,
    }


@router.get("/status")
def kite_status(authorization: str | None = Header(None), db: Session = Depends(get_db)):
    """Return the current Kite connection state for the authenticated user."""
    user = _require_user(authorization, db)
    return _session_payload(user.kite_session)


@router.get("/login_url")
def kite_login_url(authorization: str | None = Header(None), db: Session = Depends(get_db)):
    """
    Return the Kite OAuth URL embedded with a signed state JWT, plus a flag
    indicating whether the backend is in mock mode (UI should then call
    POST /kite/connect-mock instead of redirecting to Kite).
    """
    user = _require_user(authorization, db)
    state = _make_state_token(user.id)

    if KITE_MOCK_MODE:
        return {
            "mock_mode": True,
            "login_url": None,
            "state": state,
        }

    base_url = get_login_url()
    redirect_params = urlencode({"state": state})
    sep = "&" if "?" in base_url else "?"
    login_url = f"{base_url}{sep}redirect_params={redirect_params}"
    return {"mock_mode": False, "login_url": login_url, "state": state}


@router.post("/connect-mock")
def kite_connect_mock(
    authorization: str | None = Header(None), db: Session = Depends(get_db)
):
    """
    Mock-mode shortcut: skips the Kite browser flow and writes a fake
    KiteSession so the rest of the app behaves as if connected.
    """
    user = _require_user(authorization, db)
    if not KITE_MOCK_MODE:
        raise HTTPException(
            status_code=400,
            detail="Mock connect is only available when KITE_API_KEY is unset",
        )
    session = _upsert_session(
        db,
        user.id,
        access_token=f"mock_access_token_{user.id}",
        request_token=None,
        kite_user_id="MOCK001",
    )
    return _session_payload(session)


@router.get("/callback")
def kite_callback(
    request_token: str = Query(...),
    state: str | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Public callback hit by Zerodha after the user logs in. We can't require an
    auth header here, so the caller is identified via the signed state JWT we
    issued from /kite/login_url.
    """
    if status and status.lower() != "success":
        return _frontend_redirect({"kite": "error", "reason": status})

    user_id = _read_state_token(state) if state else None
    if user_id is None:
        return _frontend_redirect({"kite": "error", "reason": "invalid_state"})

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return _frontend_redirect({"kite": "error", "reason": "user_not_found"})

    try:
        session_data = exchange_request_token(request_token)
    except Exception as exc:
        return _frontend_redirect({"kite": "error", "reason": f"exchange_failed:{exc}"})

    _upsert_session(
        db,
        user.id,
        access_token=session_data.get("access_token", ""),
        request_token=request_token,
        kite_user_id=session_data.get("user_id"),
    )
    return _frontend_redirect({"kite": "connected"})


@router.delete("/session")
def kite_disconnect(
    authorization: str | None = Header(None), db: Session = Depends(get_db)
):
    """Disconnect Kite for the authenticated user (deletes the session row)."""
    user = _require_user(authorization, db)
    session = (
        db.query(KiteSession).filter(KiteSession.user_id == user.id).first()
    )
    if session is None:
        return {"connected": False, "mock_mode": KITE_MOCK_MODE}
    db.delete(session)
    db.commit()
    return {"connected": False, "mock_mode": KITE_MOCK_MODE}


@router.get("/verify")
def kite_verify(
    authorization: str | None = Header(None), db: Session = Depends(get_db)
):
    """
    Live-check the stored access token against Kite. Marks session inactive if
    the token has expired so the frontend can prompt re-connect.
    """
    user = _require_user(authorization, db)
    session = user.kite_session
    if not session or not session.is_active:
        return {"valid": False, "connected": False, "mock_mode": KITE_MOCK_MODE}
    valid = verify_token_valid(session.access_token)
    if not valid:
        session.is_active = False
        db.commit()
    return {"valid": valid, "connected": valid, "mock_mode": KITE_MOCK_MODE,
            "kite_user_id": session.kite_user_id}
