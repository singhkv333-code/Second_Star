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
from backend.security.encryption import get_cipher

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
_MIRRORED_MODULES = (
    "backend.kite.orders",
    "backend.routers.kite",
    "backend.kite.ticker",
)


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


# Kite web-login (the same flow the browser performs) — used ONLY for the
# opt-in unattended TOTP login replay. Endpoints are stable & documented across
# the community (kite.zerodha.com is the web client's own backend).
_KITE_WEB_LOGIN = "https://kite.zerodha.com/api/login"
_KITE_WEB_TWOFA = "https://kite.zerodha.com/api/twofa"
_KITE_WEB_TIMEOUT = 12  # seconds


def totp_login(kite_user_id: str, password: str, totp_secret: str) -> str:
    """Replay the Kite web-login flow to mint a fresh ``request_token`` with no
    manual step — the engine behind the opt-in "stay connected" path.

    Steps (all on one cookie-bearing ``requests.Session``):
      1. POST /api/login  {user_id, password}            -> data.request_id
      2. POST /api/twofa  {user_id, request_id, twofa_value=<current TOTP>,
                           twofa_type="totp"}             -> sets the auth cookie
      3. GET  /connect/login?api_key=...&v=3 (follow redirects) -> the final
         redirected URL (or a Location header) carries ?request_token=...

    Returns the ``request_token`` string; the caller exchanges it for an
    access_token via :func:`exchange_request_token`. Raises
    :class:`backend.brokers.base.NeedsManualLogin` with a clear, secret-free
    message on any failure. NEVER logs ``password`` or ``totp_secret``.
    """
    # Imported here (and from base lazily) to keep auth.py importable in mock
    # mode / minimal installs that don't carry ``requests``.
    from backend.brokers.base import NeedsManualLogin

    try:
        import requests  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only sans requests
        raise RuntimeError(
            "the `requests` package is required for Kite TOTP auto-login; "
            "run `pip install requests`"
        ) from exc

    from urllib.parse import urlparse, parse_qs

    if not (kite_user_id and password and totp_secret):
        raise NeedsManualLogin(
            "Kite auto-login is missing stored credentials — reconnect and "
            "re-enable 'stay connected'."
        )

    api_key = settings.kite_api_key
    if not api_key:
        raise NeedsManualLogin(
            "Kite API key is not configured on the server; auto-login is "
            "unavailable. Reconnect from the app."
        )

    sess = requests.Session()
    try:
        # 1) password step → request_id
        r1 = sess.post(
            _KITE_WEB_LOGIN,
            data={"user_id": kite_user_id, "password": password},
            timeout=_KITE_WEB_TIMEOUT,
        )
        r1.raise_for_status()
        login_data = r1.json().get("data") or {}
        request_id = login_data.get("request_id")
        if not request_id:
            raise NeedsManualLogin(
                "Kite rejected the stored login (no 2FA challenge returned). "
                "Re-check your Kite user id / password and reconnect."
            )

        # 2) TOTP 2FA step (sets the session auth cookie)
        # The login response's own `data.twofa_type` names the type THIS
        # account currently expects — it isn't a fixed "totp" string. It's
        # been observed to read "app_code" while 2FA enrollment is pending
        # confirmation and "totp" once active, so echo back whatever step 1
        # reported rather than hardcoding either (a hardcoded "totp" got a
        # hard "requested 2FA type is not available" error regardless of a
        # correct code — found 2026-07-08 wiring up auto-login for the
        # first time). Generate the code as late as possible (immediately
        # before sending) to avoid drifting past a short-lived request_id.
        twofa_type = login_data.get("twofa_type") or "totp"
        r2 = sess.post(
            _KITE_WEB_TWOFA,
            data={
                "user_id": kite_user_id,
                "request_id": request_id,
                "twofa_value": generate_totp(totp_secret),
                "twofa_type": twofa_type,
            },
            timeout=_KITE_WEB_TIMEOUT,
        )
        r2.raise_for_status()

        # 3) hit the connect login → Kite 302s to the redirect URL carrying the
        #    request_token. Capture it from the final URL or any Location header.
        r3 = sess.get(
            "https://kite.zerodha.com/connect/login",
            params={"api_key": api_key, "v": "3"},
            timeout=_KITE_WEB_TIMEOUT,
            allow_redirects=True,
        )

        def _extract(url: str) -> Optional[str]:
            if not url:
                return None
            qs = parse_qs(urlparse(url).query)
            tok = qs.get("request_token")
            return tok[0] if tok else None

        request_token = _extract(r3.url)
        if not request_token:
            # Fall back to scanning the redirect chain's Location headers.
            for hop in r3.history:
                loc = hop.headers.get("Location", "")
                request_token = _extract(loc)
                if request_token:
                    break
        if not request_token:
            request_token = _extract(r3.headers.get("Location", ""))

        if not request_token:
            raise NeedsManualLogin(
                "Kite auto-login completed 2FA but no request_token came back "
                "(the app may need re-authorising). Reconnect from the app."
            )
        return request_token
    except NeedsManualLogin:
        raise
    except Exception as exc:  # network / HTTP / parse — surface as a re-login.
        # Deliberately do NOT include credentials or the raw response body.
        raise NeedsManualLogin(
            f"Kite auto-login failed ({type(exc).__name__}). Reconnect from "
            "the app and re-enable 'stay connected'."
        ) from exc
    finally:
        sess.close()


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


def read_kite_access_token(session: Optional["object"]) -> str:
    """Return the plaintext access_token from a KiteSession row.

    Decrypts at-rest ciphertext through the process-wide
    :class:`TokenCipher` when one is configured. Tolerates legacy
    plaintext rows (the cipher's ``decrypt`` short-circuits when the
    Fernet prefix is missing), so this is safe to call both before and
    after the encryption migration runs.

    Returns an empty string when ``session`` is falsy or the column is
    NULL — callers can then route to mock mode via the existing
    ``"mock_token"`` shim.
    """
    if session is None:
        return ""
    raw = getattr(session, "access_token", None)
    if not raw:
        return ""
    cipher = get_cipher()
    if cipher is None:
        return str(raw)
    plain = cipher.decrypt(str(raw))
    return plain or ""


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
