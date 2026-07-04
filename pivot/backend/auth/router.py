"""Auth router — beta-hardened.

Surface (paths kept stable so the FE doesn't move):
    POST /auth/register          — signup + first tokens
    POST /auth/login             — login (rate-limited + lockout)
    POST /auth/logout            — revoke the current access token
    POST /auth/refresh           — mint a fresh access + refresh from a refresh
    GET  /auth/me                — current user profile
    POST /auth/request-verify    — mint + (deferred-)send email-verify link
    POST /auth/verify-email      — consume an email-verify token
    POST /auth/forgot-password   — mint + send password-reset link (no enum)
    POST /auth/reset-password    — consume a reset token, set new password
    GET  /auth/settings          — return user settings JSON
    PATCH /auth/settings         — deep-merge JSON into user settings

Existing register/login/me response shapes are preserved.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status,
)
from sqlalchemy.orm import Session

from backend.auth.audit import write_audit
from backend.auth.jwt_handler import (
    create_access_token,
    create_refresh_token,
    get_jti_from_token,
    get_token_remaining_seconds,
    get_user_id_from_token,
    hash_password,
    verify_password,
    verify_token,
)
from backend.auth.rate_limit import (
    LOCK_SECONDS,
    clear_failures,
    is_locked,
    record_failure,
)
from backend.auth.revocation import is_revoked, revoke
from backend.config import settings
from backend.database import get_db
from backend.security.throttle import rate_limit
from backend.models import (
    EmailVerificationToken,
    PasswordResetToken,
    User,
    UserSetting,
)
from backend.schemas import (
    EmailVerifyRequest,
    ForgotPasswordRequest,
    PasswordResetConfirm,
    TokenRefreshRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from backend.services.demo_seeder import seed_demo_data
from backend.services.email import send_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


# ─── helpers ────────────────────────────────────────────────────────

def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honours the first X-Forwarded-For hop when
    behind a reverse proxy; falls back to request.client.host. Used for
    rate-limit bucketing and audit rows — not a security claim."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # First entry is the original client; rest are proxies.
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "") or ""


def _hash_token(raw: str) -> str:
    """SHA-256 hex of a raw email/reset token — what we store in the DB.
    Never round-trip the raw token through the DB; only the user's email
    inbox / our logs (when EMAIL_ENABLED=false) ever sees the raw."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token",
        )
    return authorization.replace("Bearer ", "", 1)


def _require_user_with_jti(
    authorization: Optional[str],
    db: Session,
) -> tuple[User, Optional[str]]:
    """Resolve the bearer token to (user, jti) for endpoints that need
    both the user row AND the token id (logout, request-verify). Raises
    401 on miss/invalid/revoked. Mirrors `_deps.require_user` but
    returns the loaded user row for convenience."""
    token = _bearer_token(authorization)
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    jti = get_jti_from_token(token)
    if jti and is_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user, jti


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `patch` into `base`. Patch values WIN; nested
    dicts merge key-by-key; lists and scalars are REPLACED wholesale
    (the FE settings page replaces a notification list, never appends)."""
    out = dict(base)
    for k, v in patch.items():
        if (
            k in out
            and isinstance(out[k], dict)
            and isinstance(v, dict)
        ):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ─── /register ──────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
    # Throttle account creation per-IP: caps the "register-in-a-loop → mint
    # tokens → hammer the LLM" abuse path (5 new accounts / hour / IP).
    dependencies=[Depends(rate_limit("register", 5, 3600))],
)
def register(
    user_data: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Register a new user and return JWT tokens."""
    ip = _client_ip(request)
    ua = _user_agent(request)

    existing = db.query(User).filter(User.email == user_data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        full_name=user_data.full_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Seed demo data so a fresh account doesn't land on empty Agents /
    # Portfolio / Order-history tabs. Failures here are logged but never
    # block registration. Test suites disable seeding via env so a
    # registered test user starts truly empty.
    import os as _os
    if _os.environ.get("DEMO_SEED_ON_REGISTER", "1") != "0":
        try:
            seed_result = seed_demo_data(db, user.id)
            if not seed_result.get("skipped"):
                logger.info(
                    "Seeded demo data for user %s: %d workflows, %d trades",
                    user.id, seed_result.get("workflows", 0),
                    seed_result.get("trades", 0),
                )
        except Exception as e:
            logger.warning("Demo seed raised for user %s: %s", user.id, e)

    access_token = create_access_token(user.id, user.email)
    refresh_token = create_refresh_token(user.id)

    write_audit(
        db, event="signup", success=True,
        email=user.email, user_id=user.id, ip=ip, user_agent=ua,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user.id,
        email=user.email,
    )


# ─── /me ────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserResponse)
def me(
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Return the authenticated user's profile.

    Used by the frontend dashboard ("Good Evening {name}!" greeting)
    and anywhere we need the current user's display name without
    re-decoding the JWT in the browser.
    """
    user, _jti = _require_user_with_jti(authorization, db)
    return UserResponse.model_validate(user)


# ─── /login ─────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
def login(
    credentials: UserLogin,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Login with email/password and return JWT tokens.

    Rate-limited per (email, ip): 5 failed attempts within 15 min lock
    the pair out for 15 min and return 429 with a Retry-After header.
    All bad-credential paths return the same uniform 401 message so the
    response can't be used to enumerate accounts.

    On success, schedules a fire-and-forget background task that proactively
    warms the user's portfolio/views/markets Redis caches (see
    :mod:`services.cache_warm`) so the FE's dashboard-mount burst hits warm
    entries instead of paying the first-request compute/network cost. Failure
    or slowness inside the warm task NEVER affects this response — it is
    strictly additive.
    """
    ip = _client_ip(request)
    ua = _user_agent(request)
    email = credentials.email  # already normalised by the schema validator

    locked, retry_after = is_locked(email, ip)
    if locked:
        write_audit(
            db, event="login_failed", success=False, email=email,
            ip=ip, user_agent=ua, detail="locked",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(retry_after or LOCK_SECONDS)},
        )

    user = db.query(User).filter(User.email == email).first()

    if not user or not verify_password(credentials.password, user.hashed_password):
        record_failure(email, ip)
        write_audit(
            db, event="login_failed", success=False, email=email,
            user_id=user.id if user else None,
            ip=ip, user_agent=ua, detail="bad_credentials",
        )
        # Uniform message — no enumeration.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        # Deactivated accounts: don't reveal "deactivated vs nonexistent"
        # to a stranger; respond with the uniform 401. (We still audit it
        # so support can see what happened.)
        write_audit(
            db, event="login_failed", success=False, email=email,
            user_id=user.id, ip=ip, user_agent=ua, detail="inactive",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    clear_failures(email, ip)
    access_token = create_access_token(user.id, user.email)
    refresh_token = create_refresh_token(user.id)

    write_audit(
        db, event="login", success=True, email=email,
        user_id=user.id, ip=ip, user_agent=ua,
    )

    # Fire-and-forget cache warm. Runs AFTER the response is sent (that's
    # BackgroundTasks' contract), so a slow yfinance / broker fetch inside
    # the warmer NEVER shows up as login latency to the user. The task
    # itself is broadly-except'd (see cache_warm.warm_user_cache), so any
    # failure is logged and swallowed — the login response has already been
    # committed by then either way.
    try:
        from backend.services.cache_warm import warm_user_cache
        background_tasks.add_task(warm_user_cache, int(user.id))
    except Exception as e:  # noqa: BLE001 — cache warm must never break login
        logger.debug("failed to schedule cache warm for user %s: %s", user.id, e)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user.id,
        email=user.email,
    )


# ─── /logout ────────────────────────────────────────────────────────

@router.post("/logout", status_code=200)
def logout(
    request: Request,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Revoke the current access token's jti. Subsequent requests with
    the same token are rejected by `_deps.require_user`. Legacy tokens
    without a jti claim succeed silently (the FE clears local storage
    either way) — the user must wait for natural expiry."""
    token = _bearer_token(authorization)
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    jti = get_jti_from_token(token)
    remaining = get_token_remaining_seconds(token)
    if jti and remaining > 0:
        revoke(jti, remaining)
    write_audit(
        db, event="logout", success=True, user_id=user_id,
        ip=_client_ip(request), user_agent=_user_agent(request),
    )
    return {"ok": True}


# ─── /refresh ───────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
def refresh(
    body: TokenRefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Trade a refresh token for a fresh access + refresh pair.

    Returning a NEW refresh token (rotation) means a stolen refresh is
    burned the moment the legitimate client refreshes. 401 on any
    invalid / expired / wrong-type refresh."""
    payload = verify_token(body.refresh_token, "refresh")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    # Honour revocation on refresh tokens too — a logged-out refresh
    # token must not mint fresh access tokens.
    jti = payload.get("jti")
    if isinstance(jti, str) and is_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    sub = payload.get("sub")
    try:
        user_id = int(sub) if sub is not None else 0
    except (TypeError, ValueError):
        user_id = 0
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # Rotate: revoke the old refresh's jti (TTL = its remaining lifetime)
    # before minting the new pair.
    remaining = get_token_remaining_seconds(body.refresh_token, "refresh")
    if isinstance(jti, str) and remaining > 0:
        revoke(jti, remaining)

    access_token = create_access_token(user.id, user.email)
    refresh_token = create_refresh_token(user.id)

    write_audit(
        db, event="refresh", success=True, email=user.email,
        user_id=user.id, ip=_client_ip(request), user_agent=_user_agent(request),
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user.id,
        email=user.email,
    )


# ─── /request-verify ────────────────────────────────────────────────

@router.post("/request-verify", status_code=200)
def request_verify(
    request: Request,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Mint a single-use email-verify token (24h) and send it to the
    user's email. Stored only as a sha256 hash; raw token only ever
    leaves via the email body (or the deferred-send log line)."""
    user, _jti = _require_user_with_jti(authorization, db)
    if user.is_verified:
        return {"ok": True, "already_verified": True}

    raw = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    row = EmailVerificationToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires,
    )
    db.add(row)
    db.commit()

    link = f"{settings.frontend_url.rstrip('/')}/verify-email?token={raw}"
    send_email(
        to=user.email,
        subject="Verify your Pivot email",
        body=(
            "Welcome to Pivot. Click the link below to verify this email "
            "address. The link expires in 24 hours."
        ),
        link=link,
    )

    write_audit(
        db, event="email_verify_requested", success=True,
        email=user.email, user_id=user.id,
        ip=_client_ip(request), user_agent=_user_agent(request),
    )
    return {"ok": True}


# ─── /verify-email ──────────────────────────────────────────────────

@router.post("/verify-email", status_code=200)
def verify_email(
    body: EmailVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Consume an email-verify token. Sets user.is_verified = True and
    marks the row used. 400 on invalid / expired / already-used token."""
    token_hash = _hash_token(body.token)
    now = datetime.now(timezone.utc)
    row = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.token_hash == token_hash)
        .first()
    )
    if not row or row.used_at is not None or row.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    user.is_verified = True
    row.used_at = now
    db.commit()

    write_audit(
        db, event="email_verified", success=True,
        email=user.email, user_id=user.id,
        ip=_client_ip(request), user_agent=_user_agent(request),
    )
    return {"ok": True}


# ─── /forgot-password ───────────────────────────────────────────────

@router.post("/forgot-password", status_code=200)
def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Always returns 200 — never reveals whether the email exists. If
    a user is found we mint a single-use 1h reset token and (deferred-)
    send the reset link."""
    email = body.email  # normalised by schema
    user = db.query(User).filter(User.email == email).first()
    ip = _client_ip(request)
    ua = _user_agent(request)

    if user is not None:
        raw = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        row = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires,
        )
        db.add(row)
        db.commit()

        link = f"{settings.frontend_url.rstrip('/')}/reset-password?token={raw}"
        send_email(
            to=user.email,
            subject="Reset your Pivot password",
            body=(
                "Use the link below to set a new password. The link "
                "expires in 1 hour. If you didn't request this, ignore "
                "this email."
            ),
            link=link,
        )
        write_audit(
            db, event="password_reset_requested", success=True,
            email=user.email, user_id=user.id, ip=ip, user_agent=ua,
        )
    else:
        # Still write an audit row so we can spot enumeration attempts.
        write_audit(
            db, event="password_reset_requested", success=False,
            email=email, ip=ip, user_agent=ua, detail="unknown_email",
        )

    return {"ok": True}


# ─── /reset-password ────────────────────────────────────────────────

@router.post("/reset-password", status_code=200)
def reset_password(
    body: PasswordResetConfirm,
    request: Request,
    db: Session = Depends(get_db),
):
    """Consume a password-reset token; set the new password. Password
    strength enforced by the Pydantic model so this can't be bypassed."""
    token_hash = _hash_token(body.token)
    now = datetime.now(timezone.utc)
    row = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == token_hash)
        .first()
    )
    if not row or row.used_at is not None or row.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    user.hashed_password = hash_password(body.new_password)
    row.used_at = now
    db.commit()

    write_audit(
        db, event="password_reset", success=True,
        email=user.email, user_id=user.id,
        ip=_client_ip(request), user_agent=_user_agent(request),
    )
    return {"ok": True}


# ─── /settings ──────────────────────────────────────────────────────

@router.get("/settings", status_code=200)
def get_settings_endpoint(
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Return the user's settings JSON. Empty dict when no row exists
    (FE treats that as "all defaults")."""
    user, _jti = _require_user_with_jti(authorization, db)
    row = (
        db.query(UserSetting)
        .filter(UserSetting.user_id == user.id)
        .first()
    )
    return {"settings": (row.settings if row and row.settings else {})}


@router.patch("/settings", status_code=200)
def patch_settings_endpoint(
    body: dict[str, Any],
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Deep-merge the request body into the user's settings JSON. Lists
    and scalars are REPLACED; nested dicts are merged key-by-key. Upserts
    the row when no settings exist yet."""
    user, _jti = _require_user_with_jti(authorization, db)
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body must be a JSON object",
        )

    row = (
        db.query(UserSetting)
        .filter(UserSetting.user_id == user.id)
        .first()
    )
    if row is None:
        merged = _deep_merge({}, body)
        row = UserSetting(user_id=user.id, settings=merged)
        db.add(row)
    else:
        current = row.settings if isinstance(row.settings, dict) else {}
        row.settings = _deep_merge(current, body)
    db.commit()
    db.refresh(row)
    return {"settings": row.settings or {}}
