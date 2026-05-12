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
from pydantic import BaseModel, Field

from backend.kite import auth as kite_auth
from backend.kite import orders as kite_orders
from backend.kite.auth import (
    KITE_MOCK_MODE,
    clear_kite_credentials,
    exchange_request_token,
    get_login_url,
    masked_credentials_status,
    set_kite_credentials,
    verify_token_valid,
)
from backend.models import KiteSession, User

router = APIRouter(prefix="/kite", tags=["Kite"])

# Compatibility alias for Kite developer apps whose configured Redirect URL is
# `/callback` (without the `/kite/` prefix). Mounted on the app in main.py.
callback_alias_router = APIRouter(tags=["Kite"])


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


class KiteCredentialsBody(BaseModel):
    """Body for POST /kite/credentials — both fields required by Kite policy."""
    api_key: str = Field(..., min_length=1, description="Kite Connect API key")
    api_secret: str = Field(..., min_length=1, description="Kite Connect API secret")


@router.get("/credentials")
def kite_get_credentials(
    authorization: str | None = Header(None), db: Session = Depends(get_db)
):
    """Return masked Kite API credential state (never the raw secret)."""
    _require_user(authorization, db)
    return masked_credentials_status()


@router.post("/credentials")
def kite_set_credentials(
    body: KiteCredentialsBody,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """
    Inject Kite API key + secret at runtime. Flips the backend out of mock mode
    immediately — subsequent /kite/login_url and order placements will hit the
    real Kite Connect API.
    """
    _require_user(authorization, db)
    try:
        status = set_kite_credentials(body.api_key, body.api_secret)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return status


@router.delete("/credentials")
def kite_delete_credentials(
    authorization: str | None = Header(None), db: Session = Depends(get_db)
):
    """Wipe runtime Kite credentials and revert to mock mode."""
    _require_user(authorization, db)
    return clear_kite_credentials()


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


def _handle_kite_callback(
    request_token: str,
    state: str | None,
    status: str | None,
    db: Session,
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


@router.get("/callback")
def kite_callback(
    request_token: str = Query(...),
    state: str | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Kite OAuth redirect target (canonical path under /kite)."""
    return _handle_kite_callback(request_token, state, status, db)


@callback_alias_router.get("/callback")
def kite_callback_alias(
    request_token: str = Query(...),
    state: str | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Same as GET /kite/callback. Mounted at /callback for Kite developer apps
    whose configured Redirect URL is `<host>/callback` instead of
    `<host>/kite/callback`.
    """
    return _handle_kite_callback(request_token, state, status, db)


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


class KiteCancelBody(BaseModel):
    order_id: str = Field(..., min_length=1)
    variety: str = Field("regular", description="regular | amo | co")


@router.post("/test-order")
def kite_place_test_order(
    authorization: str | None = Header(None), db: Session = Depends(get_db)
):
    """
    Place a hard-coded safe test order: LIMIT BUY 1 TCS @ ₹3500.
    Well below market, won't fill — proves the wire end-to-end through this
    backend (credentials → access_token → kite.place_order).
    Falls back from `regular` to `amo` variety when markets are closed.
    """
    user = _require_user(authorization, db)
    if kite_auth.KITE_MOCK_MODE:
        raise HTTPException(
            status_code=400,
            detail="Backend is in mock mode. Save real Kite credentials first.",
        )
    session = user.kite_session
    if not session or not session.access_token:
        raise HTTPException(
            status_code=400,
            detail="No Kite session — click 'Connect to Zerodha' first.",
        )

    common = dict(
        access_token=session.access_token,
        tradingsymbol="TCS",
        exchange="BSE",
        transaction_type="BUY",
        quantity=1,
        order_type="LIMIT",
        price=3500.0,
        product="CNC",
        tag="pivot-test",
    )

    try:
        result = kite_orders.place_order(**common, variety="regular")
        return {**result, "variety": "regular"}
    except Exception as regular_exc:
        # Markets closed (after 15:30 IST) → regular orders are rejected.
        # Retry as AMO so we still demonstrate end-to-end placement.
        try:
            result = kite_orders.place_order(**common, variety="amo")
            return {**result, "variety": "amo", "regular_error": str(regular_exc)}
        except Exception as amo_exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Kite rejected both regular and AMO. "
                    f"regular={regular_exc}; amo={amo_exc}"
                ),
            )


@router.post("/test-order/cancel")
def kite_cancel_test_order(
    body: KiteCancelBody,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Cancel a previously-placed test order (variety must match)."""
    user = _require_user(authorization, db)
    if kite_auth.KITE_MOCK_MODE:
        raise HTTPException(status_code=400, detail="Backend is in mock mode.")
    session = user.kite_session
    if not session or not session.access_token:
        raise HTTPException(status_code=400, detail="No Kite session.")
    try:
        result = kite_orders.cancel_order(
            access_token=session.access_token,
            order_id=body.order_id,
            variety=body.variety,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cancel failed: {exc}")


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
