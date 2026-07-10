"""
Kite Connect authentication.
MOCK_MODE when KITE_API_KEY is not set, but can be flipped at runtime via
`set_kite_credentials()` once the user submits real API key + secret from the
frontend Kite Credentials panel.
"""
import importlib
import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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

# --- Auto-login safety guards (2026-07-11) ---------------------------------
# The unattended TOTP replay locked a real Zerodha account: it fired on EVERY
# server restart with no attempt cap, and a 2FA-type / clock-skew bug meant
# each attempt failed. Zerodha locks an account after a few failed logins, so
# a restart-storm of failed auto-logins = a lockout. These guards make a
# re-lock structurally impossible:
#   1. a hard kill-switch env flag (KITE_AUTO_LOGIN_DISABLED),
#   2. a Redis-backed circuit-breaker that pauses auto-login after a couple of
#      failures and SURVIVES restarts (the actual lockout vector),
#   3. a clock-skew guard that refuses to send a TOTP the server clock will
#      make invalid (never adds a failed 2FA attempt).
_AUTO_LOGIN_MAX_FAILS = 2        # pause auto-login after this many failures
_AUTO_LOGIN_COOLDOWN_S = 1800    # 30-min pause window (Redis TTL)
_CLOCK_SKEW_LIMIT_S = 25         # refuse if local clock is >this far off Kite's


def _breaker_key(kite_user_id: str) -> str:
    return f"kite:autologin:fails:{kite_user_id}"


def auto_login_blocked_reason(kite_user_id: str) -> Optional[str]:
    """A human reason string if auto-login is currently blocked, else None.
    Checked before any network call so a paused breaker never touches Kite."""
    if os.getenv("KITE_AUTO_LOGIN_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        return "unattended auto-login is disabled (KITE_AUTO_LOGIN_DISABLED)"
    try:
        from backend.cache import redis_client
        raw = redis_client.get(_breaker_key(kite_user_id))
        n = int(raw) if raw is not None else 0
        if n >= _AUTO_LOGIN_MAX_FAILS:
            return (f"auto-login is paused after {n} failed attempts to avoid "
                    "locking your Kite account")
    except Exception:  # noqa: BLE001 — Redis is best-effort, never fatal
        pass
    return None


def _record_auto_login_failure(kite_user_id: str) -> None:
    """Increment the breaker; a short TTL means it self-clears after cooldown."""
    try:
        from backend.cache import redis_client
        key = _breaker_key(kite_user_id)
        redis_client.incr(key)
        redis_client.expire(key, _AUTO_LOGIN_COOLDOWN_S)
    except Exception:  # noqa: BLE001
        pass


def _clear_auto_login_failures(kite_user_id: str) -> None:
    try:
        from backend.cache import redis_client
        redis_client.delete(_breaker_key(kite_user_id))
    except Exception:  # noqa: BLE001
        pass


def _clock_skew_seconds(resp) -> Optional[float]:
    """Local-clock minus Kite's server clock (from the HTTP Date header), in
    seconds. None when unavailable. A large value means our TOTP codes will be
    rejected — the classic silent cause of repeated failed 2FA."""
    try:
        date_hdr = resp.headers.get("Date")
        if not date_hdr:
            return None
        server_dt = parsedate_to_datetime(date_hdr)
        if server_dt.tzinfo is None:
            server_dt = server_dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - server_dt).total_seconds()
    except Exception:  # noqa: BLE001
        return None


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

    # CIRCUIT-BREAKER / KILL-SWITCH: refuse to touch Kite at all when auto-login
    # is paused. This is what prevents a restart-storm of failed logins from
    # locking the account — the whole reason this guard exists.
    blocked = auto_login_blocked_reason(kite_user_id)
    if blocked:
        raise NeedsManualLogin(
            f"Kite {blocked}. Log in manually from the app (or `scripts/"
            "kite_connect.py`); auto-login stays paused until then."
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

        # CLOCK-SKEW GUARD: if our clock is off vs Kite's, the TOTP we generate
        # will be rejected — a silent, repeatable 2FA failure. Bail BEFORE the
        # 2FA POST so we never spend a failed attempt on an invalid code.
        skew = _clock_skew_seconds(r1)
        if skew is not None and abs(skew) > _CLOCK_SKEW_LIMIT_S:
            raise NeedsManualLogin(
                f"Server clock is ~{int(skew)}s off Kite's — TOTP codes will be "
                "rejected. Sync the clock (NTP) and log in manually."
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
        # Success → reset the breaker so a healthy account isn't held paused.
        _clear_auto_login_failures(kite_user_id)
        return request_token
    except NeedsManualLogin:
        # Pre-check bail-outs (breaker / clock / bad challenge) are NOT counted
        # as login failures — they never hit Kite's 2FA, so they can't lock it.
        raise
    except Exception as exc:  # a REAL failed login/2FA (this is what locks) —
        # trip the breaker so repeated restarts can't keep hammering Kite.
        _record_auto_login_failure(kite_user_id)
        # Deliberately do NOT include credentials or the raw response body.
        raise NeedsManualLogin(
            f"Kite auto-login failed ({type(exc).__name__}) — auto-login is now "
            "paused; log in manually from the app."
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
