"""
Kite Connect authentication.
MOCK_MODE when KITE_API_KEY is not set, but can be flipped at runtime via
`set_kite_credentials()` once the user submits real API key + secret from the
frontend Kite Credentials panel.
"""
import importlib
import logging
from datetime import datetime, timezone
from typing import Optional

import pyotp

from backend.config import settings

logger = logging.getLogger(__name__)

KITE_MOCK_MODE: bool = not bool(settings.kite_api_key)
# Lazily-loaded kiteconnect.KiteConnect class. Imported on demand so that
# starting the backend without the kiteconnect package installed in mock mode
# still works.
KiteConnect = None  # type: ignore[assignment]

if not KITE_MOCK_MODE:
    from kiteconnect import KiteConnect as _KiteConnect  # type: ignore
    KiteConnect = _KiteConnect  # type: ignore[assignment]


# Modules that captured KITE_MOCK_MODE by value at import time. When the flag
# flips at runtime we patch each one so they see the new value.
_MIRRORED_MODULES = ("backend.kite.orders", "backend.routers.kite")


def _propagate_mock_mode(value: bool) -> None:
    for mod_name in _MIRRORED_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        if hasattr(mod, "KITE_MOCK_MODE"):
            setattr(mod, "KITE_MOCK_MODE", value)


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def masked_credentials_status() -> dict:
    return {
        "mock_mode": KITE_MOCK_MODE,
        "has_api_key": bool(settings.kite_api_key),
        "has_api_secret": bool(settings.kite_api_secret),
        "api_key_masked": _mask(settings.kite_api_key),
    }


def set_kite_credentials(api_key: str, api_secret: str) -> dict:
    """Inject Kite API credentials at runtime and flip out of mock mode."""
    global KITE_MOCK_MODE, KiteConnect

    api_key = (api_key or "").strip()
    api_secret = (api_secret or "").strip()
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret are required")

    settings.kite_api_key = api_key
    settings.kite_api_secret = api_secret
    KITE_MOCK_MODE = False

    if KiteConnect is None:
        try:
            from kiteconnect import KiteConnect as _KiteConnect  # type: ignore
            KiteConnect = _KiteConnect  # type: ignore[assignment]
        except ImportError as exc:
            # Roll the flag back so the rest of the app stays consistent.
            KITE_MOCK_MODE = True
            raise RuntimeError(
                "kiteconnect package is not installed; run `pip install kiteconnect`"
            ) from exc

    _propagate_mock_mode(False)
    logger.info("Kite credentials updated at runtime; mock mode disabled.")
    return masked_credentials_status()


def clear_kite_credentials() -> dict:
    """Wipe credentials at runtime and revert to mock mode."""
    global KITE_MOCK_MODE
    settings.kite_api_key = ""
    settings.kite_api_secret = ""
    KITE_MOCK_MODE = True
    _propagate_mock_mode(True)
    logger.info("Kite credentials cleared; mock mode re-enabled.")
    return masked_credentials_status()


def get_kite_instance():
    """Returns a KiteConnect instance or None in mock mode."""
    if KITE_MOCK_MODE:
        return None
    return KiteConnect(api_key=settings.kite_api_key)  # type: ignore[misc]


def generate_totp(totp_secret: str) -> str:
    """Generate current TOTP code from the base32 secret."""
    totp = pyotp.TOTP(totp_secret)
    return totp.now()


def get_login_url() -> str:
    """Returns Kite login URL for the user to authenticate."""
    if KITE_MOCK_MODE:
        return "https://mock.kite.trade/login?api_key=MOCK"
    kite = KiteConnect(api_key=settings.kite_api_key)  # type: ignore[misc]
    return kite.login_url()


def exchange_request_token(request_token: str) -> dict:
    """
    Exchange request_token for access_token.
    Returns session data including access_token.
    """
    if KITE_MOCK_MODE:
        return {
            "access_token": "mock_access_token_" + request_token[:8],
            "user_id": "MOCK001",
            "login_time": datetime.now(timezone.utc).isoformat(),
        }
    kite = KiteConnect(api_key=settings.kite_api_key)  # type: ignore[misc]
    session = kite.generate_session(request_token, api_secret=settings.kite_api_secret)
    return session


def get_authenticated_kite(access_token: str):
    """Returns a KiteConnect instance with access_token set."""
    if KITE_MOCK_MODE:
        return None
    kite = KiteConnect(api_key=settings.kite_api_key)  # type: ignore[misc]
    kite.set_access_token(access_token)
    return kite


def verify_token_valid(access_token: str) -> bool:
    """Check if a Kite access token is still valid."""
    if KITE_MOCK_MODE:
        return True
    try:
        kite = get_authenticated_kite(access_token)
        kite.profile()
        return True
    except Exception:
        return False
