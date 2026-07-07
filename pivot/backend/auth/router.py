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
from backend.posthog_client import get_posthog
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
    GoogleAuthRequest,
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

    _ph = get_posthog()
    if _ph:
        _ph.set(distinct_id=str(user.id), properties={"email": user.email})
        _ph.capture("user_signed_up", distinct_id=str(user.id), properties={"signup_method": "email"})

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

    _ph = get_posthog()
    if _ph:
        _ph.set(distinct_id=str(user.id), properties={"email": user.email})

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

    _ph = get_posthog()
    if _ph:
        _ph.set(distinct_id=str(user.id), properties={"email": user.email})
        _ph.capture("user_logged_in", distinct_id=str(user.id), properties={"login_method": "email"})

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user.id,
        email=user.email,
    )


# ─── /google ────────────────────────────────────────────────────────

# Google's token-introspection + profile endpoints. tokeninfo lets us verify
# the access token was minted for OUR client (audience) and carries a verified
# email; userinfo gives a display name for brand-new accounts.
_GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def _verify_google_access_token(access_token: str) -> dict[str, Any]:
    """Introspect a Google access token and return {email, name}.

    Security: we do NOT trust the browser's identity claims. tokeninfo tells
    us (a) the token's audience — which MUST equal our own client id, or a
    token minted for a different app could be replayed here — and (b) that
    Google has verified the email. Only then do we treat the email as an
    identity. Raises HTTPException(401) on any failure.
    """
    import httpx  # local import: only this endpoint needs it

    client_id = settings.google_client_id
    try:
        with httpx.Client(timeout=6.0) as client:
            info = client.get(
                _GOOGLE_TOKENINFO_URL, params={"access_token": access_token}
            )
    except httpx.HTTPError as e:
        logger.warning("google tokeninfo request failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not verify Google sign-in",
        )
    if info.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google credential",
        )
    data = info.json()

    # Audience check — the token must have been issued to OUR client id.
    # Google returns the client id in `aud` (and `azp` for the authorized
    # party); accept a match on either.
    aud = data.get("aud")
    azp = data.get("azp")
    if client_id not in (aud, azp):
        logger.warning(
            "google token audience mismatch: aud=%r azp=%r", aud, azp
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google credential was not issued for this app",
        )

    email = (data.get("email") or "").strip().lower()
    email_verified = str(data.get("email_verified", "")).lower() == "true"
    if not email or not email_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account has no verified email",
        )

    # Display name is best-effort — fetch it from userinfo, else derive from
    # the email local-part. Never blocks the sign-in.
    name = ""
    try:
        with httpx.Client(timeout=6.0) as client:
            prof = client.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if prof.status_code == 200:
            name = (prof.json().get("name") or "").strip()
    except httpx.HTTPError:
        name = ""
    if not name:
        name = email.split("@", 1)[0].replace(".", " ").title()

    return {"email": email, "name": name}


@router.post(
    "/google",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("google_auth", 20, 3600))],
)
def google_auth(
    body: GoogleAuthRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Sign in (or sign up) with Google.

    The browser obtains a Google access token via Google Identity Services and
    posts it here; we verify it with Google (audience + verified email — see
    :func:`_verify_google_access_token`), then find-or-create the local user by
    that verified email and issue Pivot tokens.

    Account linking is by verified email: a user who first signed up with a
    password and later clicks "Login with Google" (same email) is logged into
    that same account — safe because Google has verified the address. New
    Google users get an unusable random password (they can set a real one via
    "forgot password" if they ever want email login) and are marked verified.
    """
    ip = _client_ip(request)
    ua = _user_agent(request)

    if not settings.google_client_id:
        # Not configured — honest 503 rather than a broken sign-in.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured",
        )

    profile = _verify_google_access_token(body.access_token)
    email = profile["email"]

    user = db.query(User).filter(User.email == email).first()
    is_new = user is None

    if user is None:
        user = User(
            email=email,
            # Unusable password: a random secret they don't know. Google is
            # the credential; email login stays closed until they reset it.
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            full_name=profile["name"],
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        import os as _os
        if _os.environ.get("DEMO_SEED_ON_REGISTER", "1") != "0":
            try:
                seed_demo_data(db, user.id)
            except Exception as e:  # noqa: BLE001 — seeding never blocks auth
                logger.warning("Demo seed raised for google user %s: %s", user.id, e)
    elif not user.is_active:
        write_audit(
            db, event="login_failed", success=False, email=email,
            user_id=user.id, ip=ip, user_agent=ua, detail="inactive_google",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account is not available",
        )

    access_token = create_access_token(user.id, user.email)
    refresh_token = create_refresh_token(user.id)

    write_audit(
        db, event="signup" if is_new else "login", success=True, email=email,
        user_id=user.id, ip=ip, user_agent=ua, detail="google",
    )

    # Same fire-and-forget cache warm as password login.
    try:
        from backend.services.cache_warm import warm_user_cache
        background_tasks.add_task(warm_user_cache, int(user.id))
    except Exception as e:  # noqa: BLE001 — cache warm must never break auth
        logger.debug("failed to schedule cache warm for google user %s: %s", user.id, e)

    _ph = get_posthog()
    if _ph:
        _ph.set(distinct_id=str(user.id), properties={"email": user.email})
        event = "user_signed_up_google" if is_new else "user_logged_in_google"
        _ph.capture(event, distinct_id=str(user.id), properties={"login_method": "google"})

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
