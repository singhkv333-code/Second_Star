"""Profile and settings for a signed-in Pivot account.

Pivot has ONE account system: the account service behind `/auth/*` on the
same origin (signup, sign-in, Google, password reset, email confirmation,
sign-out). This API issues no credentials of its own. It accepts that
service's session (backend/auth/accounts.py) and keeps the per-account data
that lives in Postgres:

    GET  /auth/me                — current user profile
    PATCH /auth/me               — edit name, username, date of birth, photo
    GET  /auth/settings          — return user settings JSON
    PATCH /auth/settings         — deep-merge JSON into user settings

Its own register / login / Google / refresh / link / email-verify / password
routes were removed on 2026-10-15: two sign-in layers meant two passwords per
person and refusals that signed people out.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.posthog_client import get_posthog
from backend.auth.jwt_handler import get_jti_from_token, get_user_id_from_token
from backend.auth.revocation import is_revoked
from backend.database import get_db
from backend.models import User, UserSetting
from backend.schemas import ProfileUpdate, UserResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ─── helpers ────────────────────────────────────────────────────────







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
    both the user row AND the token id. Raises
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

    return _user_response(user, _settings_row(db, user.id))


def _settings_row(db: Session, user_id: int) -> Optional[UserSetting]:
    return db.query(UserSetting).filter(UserSetting.user_id == user_id).first()


def _user_response(user: User, row: Optional[UserSetting]) -> UserResponse:
    """UserResponse with the profile extras folded in from user_settings."""
    out = UserResponse.model_validate(user)
    settings_json = row.settings if row and isinstance(row.settings, dict) else {}
    profile = settings_json.get("profile")
    if isinstance(profile, dict):
        out.username = profile.get("username") or None
        dob = profile.get("dob")
        out.dob = date.fromisoformat(dob) if isinstance(dob, str) and dob else None
        out.avatar = profile.get("avatar") or None
    return out


@router.patch("/me", response_model=UserResponse)
def update_me(
    body: ProfileUpdate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Edit the profile. `full_name` is a users column; username, date of
    birth and photo live in user_settings.settings["profile"] so no schema
    migration is needed. Only the fields present in the body change."""
    user, _jti = _require_user_with_jti(authorization, db)
    sent = body.model_fields_set

    if "full_name" in sent:
        user.full_name = body.full_name

    row = _settings_row(db, user.id)
    current = row.settings if row and isinstance(row.settings, dict) else {}
    profile = dict(current.get("profile") or {})

    if "username" in sent:
        if body.username:
            taken = (
                db.query(UserSetting.user_id)
                .filter(
                    UserSetting.user_id != user.id,
                    UserSetting.settings["profile"]["username"].as_string()
                    == body.username,
                )
                .first()
            )
            if taken:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="That username is taken",
                )
        profile["username"] = body.username
    if "dob" in sent:
        profile["dob"] = body.dob.isoformat() if body.dob else None
    if "avatar" in sent:
        profile["avatar"] = body.avatar

    merged = {**current, "profile": profile}
    if row is None:
        row = UserSetting(user_id=user.id, settings=merged)
        db.add(row)
    else:
        # A new dict, so SQLAlchemy sees the JSON column change.
        row.settings = merged
    db.commit()
    db.refresh(user)
    db.refresh(row)
    return _user_response(user, row)


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
