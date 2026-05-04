"""
Kite Connect authentication.
MOCK_MODE when KITE_API_KEY is not set.
Real mode when API key present.
"""
import pyotp
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from backend.config import settings

logger = logging.getLogger(__name__)

KITE_MOCK_MODE = not bool(settings.kite_api_key)

if not KITE_MOCK_MODE:
    from kiteconnect import KiteConnect


def get_kite_instance():
    """Returns a KiteConnect instance or None in mock mode."""
    if KITE_MOCK_MODE:
        return None
    kite = KiteConnect(api_key=settings.kite_api_key)
    return kite


def generate_totp(totp_secret: str) -> str:
    """Generate current TOTP code from the base32 secret."""
    totp = pyotp.TOTP(totp_secret)
    return totp.now()


def get_login_url() -> str:
    """Returns Kite login URL for the user to authenticate."""
    if KITE_MOCK_MODE:
        return "https://mock.kite.trade/login?api_key=MOCK"
    kite = KiteConnect(api_key=settings.kite_api_key)
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
    kite = KiteConnect(api_key=settings.kite_api_key)
    session = kite.generate_session(request_token, api_secret=settings.kite_api_secret)
    return session


def get_authenticated_kite(access_token: str):
    """Returns a KiteConnect instance with access_token set."""
    if KITE_MOCK_MODE:
        return None
    kite = KiteConnect(api_key=settings.kite_api_key)
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
