from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from backend.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: int, email: str) -> str:
    """Create a short-lived JWT access token.

    Carries a per-token ``jti`` (UUID4 hex) claim so individual tokens can
    be revoked via the Redis-backed revocation list (see
    ``backend/auth/revocation.py``). Old tokens minted before the jti claim
    shipped still validate — they simply can't be individually revoked.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "email": email,
        "type": "access",
        "jti": uuid4().hex,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int) -> str:
    """Create a long-lived JWT refresh token."""
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.refresh_token_expire_days
    )
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": uuid4().hex,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_token(token: str, token_type: str = "access") -> Optional[dict]:
    """Verify a JWT token and return the payload, or None if invalid/expired."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        if payload.get("type") != token_type:
            return None
        return payload
    except JWTError:
        return None


def get_user_id_from_token(token: str) -> Optional[int]:
    """Extract user_id from a valid access token."""
    payload = verify_token(token, "access")
    if not payload:
        return None
    return int(payload.get("sub"))


def get_jti_from_token(token: str, token_type: str = "access") -> Optional[str]:
    """Return the ``jti`` claim from a valid token, or None if missing/invalid.

    Tokens minted before the jti claim shipped will return None — callers
    treat that as "not individually revocable" rather than as an error."""
    payload = verify_token(token, token_type)
    if not payload:
        return None
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        return None
    return jti


def get_token_remaining_seconds(token: str, token_type: str = "access") -> int:
    """Seconds remaining until expiry for a valid token. Returns 0 for
    expired/invalid tokens or tokens missing ``exp``. Used to size the
    revocation entry's TTL so we don't keep revocation keys past expiry."""
    payload = verify_token(token, token_type)
    if not payload:
        return 0
    exp = payload.get("exp")
    if exp is None:
        return 0
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return 0
    now = int(datetime.now(timezone.utc).timestamp())
    remaining = exp_int - now
    return remaining if remaining > 0 else 0
