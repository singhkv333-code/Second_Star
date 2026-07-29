"""Fyers (API v3) connector — the OAuth + 15-day refresh-token broker.

Fyers gives the smoothest "connect once, stay alive" path that is also fully
official:
  - ``refresh_token``  : a one-time hosted OAuth login mints an access token
                         (~24h) **and** a refresh token that lives ~15 days.
                         POST /validate-refresh-token silently re-mints the
                         daily access token from the refresh token + the user's
                         PIN, with zero browser interaction — so automation
                         keeps running for ~2 weeks before a reconnect.

The hosted login redirect (``get_login_url``) sends the user straight to Fyers'
consent screen; the ``/brokers/{broker}/callback`` forwards ``?auth_code``/
``?code`` + ``state`` back to :meth:`complete_auth`. Orders/holdings hit
``https://api-t1.fyers.in/api/v3`` with the ``Authorization: {app_id}:{token}``
header. The connector speaks Pivot's Kite-style vocabulary at the boundary and
maps to Fyers' numeric enums internally.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

try:  # requests is a standard dep; guard so import never hard-fails in mock dev
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore

from sqlalchemy.orm import Session

from backend.brokers.base import (
    BrokerConnector,
    BrokerInfo,
    DeepLinks,
    NeedsManualLogin,
    PersistenceKind,
)
from backend.brokers.sessions import (
    read_broker_access_token,
    read_secret,
    upsert_broker_session,
)
from backend.config import settings
from backend.models import BrokerSession

logger = logging.getLogger(__name__)

_API = "https://api-t1.fyers.in/api/v3"

# Deep links — straight to where the user grabs their app keys / reads the docs.
_FYERS_APP_CREATE = "https://myapi.fyers.in/dashboard/"
_FYERS_DOCS = "https://myapi.fyers.in/docsv3"

# Pivot (Kite-style) → Fyers numeric enum maps.
_SIDE = {"BUY": 1, "SELL": -1}
_ORDER_TYPE = {
    "MARKET": 2, "LIMIT": 1, "SL": 4, "SL-M": 3,
    "SL-MARKET": 3, "STOP_LOSS": 4, "STOP_LOSS_MARKET": 3,
}
_PRODUCT = {
    "CNC": "CNC", "MIS": "INTRADAY", "NRML": "MARGIN",
    "INTRADAY": "INTRADAY", "MARGIN": "MARGIN", "CO": "CO", "BO": "BO",
}


def _fyers_mock() -> bool:
    """No app-level Fyers config AND used for the dev connect-mock shortcut.
    Live operation is decided per-call by token presence (a user can paste their
    own token without app config)."""
    return not bool(settings.fyers_app_id)


def _use_mock(token: str) -> bool:
    return requests is None or not token or token == "mock_token" or token.startswith("mock_")


def _app_id_hash() -> str:
    """Fyers appIdHash = sha256 hex of ``"{app_id}:{secret_id}"``."""
    raw = f"{settings.fyers_app_id}:{settings.fyers_secret_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _http(method: str, url: str, *, headers=None, json=None, params=None) -> dict:
    """Thin JSON request with a sane timeout; raises for non-2xx so callers can
    translate to NeedsManualLogin / honest error dicts."""
    if requests is None:  # pragma: no cover
        raise RuntimeError("requests not installed")
    resp = requests.request(
        method, url, headers=headers or {}, json=json, params=params, timeout=12
    )
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {}


def _fyers_symbol(exchange: str, tradingsymbol: str) -> str:
    """Build a Fyers symbol like ``NSE:RELIANCE-EQ`` from exchange + symbol.
    Already-qualified symbols (containing ``:``) pass through untouched."""
    sym = (tradingsymbol or "").strip().upper()
    if ":" in sym:
        return sym
    exch = (exchange or "NSE").strip().upper()
    # Equity symbols take the -EQ suffix; derivatives already carry their own
    # contract suffix in the tradingsymbol, so only append for plain equities.
    if exch in ("NSE", "BSE") and "-" not in sym:
        return f"{exch}:{sym}-EQ"
    return f"{exch}:{sym}"


class FyersConnector(BrokerConnector):
    broker = "fyers"
    info = BrokerInfo(
        id="fyers",
        name="Fyers",
        logo="/brokers/fyers.svg",
        persistence_kind=PersistenceKind.refresh_token,
        supports_unattended=True,
        needs_api_key=False,
        supports_oauth=True,
        accent="#2C5FF6",  # Fyers brand blue (matches /brokers/fyers.svg)
        blurb="Connect once — stays alive for ~2 weeks.",
        tags=["No daily login", "Refresh token"],
    )

    # ── mode ──────────────────────────────────────────────────────────────────
    def mock_mode(self) -> bool:
        return _fyers_mock()

    def deep_links(self, *, state: Optional[str] = None) -> DeepLinks:
        return DeepLinks(
            login=self.get_login_url(state) if state else None,
            app_create=_FYERS_APP_CREATE,
            docs=_FYERS_DOCS,
        )

    # ── onboarding ────────────────────────────────────────────────────────────
    def get_login_url(self, state: str) -> Optional[str]:
        if self.mock_mode() or not settings.fyers_app_id:
            return None
        redirect = f"{settings.frontend_url}/"
        return (
            f"{_API}/generate-authcode?client_id={settings.fyers_app_id}"
            f"&redirect_uri={redirect}&response_type=code&state={state}"
        )

    def complete_auth(self, db: Session, user_id: int, payload: dict) -> BrokerSession:
        now = datetime.now(timezone.utc)
        auth_code = payload.get("code") or payload.get("auth_code")
        pin = payload.get("pin")
        pasted_refresh = payload.get("refresh_token")
        access_token = payload.get("access_token")
        broker_user_id = payload.get("client_id") or payload.get("fy_id")
        refresh_token: Optional[str] = None
        expiry: Optional[datetime] = now + timedelta(hours=24)

        if auth_code and settings.fyers_app_id and requests is not None:
            # OAuth callback — exchange the auth code for access + refresh tokens.
            data = _http(
                "POST",
                f"{_API}/validate-authcode",
                json={
                    "grant_type": "authorization_code",
                    "appIdHash": _app_id_hash(),
                    "code": auth_code,
                },
            )
            access_token = data.get("access_token") or access_token
            refresh_token = data.get("refresh_token")
            broker_user_id = data.get("fy_id") or broker_user_id
            if not access_token:
                raise NeedsManualLogin("Fyers validate-authcode returned no token")
        elif pasted_refresh and pin:
            # Paste path — user supplied a refresh token + PIN directly; mint the
            # first access token now so we verify the creds work.
            refresh_token = pasted_refresh
            access_token = self._mint_from_refresh(pasted_refresh, pin)
        elif access_token:
            # User pasted a 24h access token (no refresh) — usable until it dies.
            refresh_token = pasted_refresh
        else:
            # Mock / dev connect.
            access_token = f"mock_fyers_{user_id}"
            broker_user_id = broker_user_id or "MOCK-FYERS"

        return upsert_broker_session(
            db,
            user_id,
            self.broker,
            access_token=access_token,
            refresh_token=refresh_token,
            api_secret=pin,           # Fyers PIN (encrypted) — used for re-mint
            broker_user_id=broker_user_id,
            persistence_mode=PersistenceKind.refresh_token.value,
            login_time=now,
            token_expires_at=expiry,
            is_active=True,
        )

    # ── token persistence ─────────────────────────────────────────────────────
    def _mint_from_refresh(self, refresh_token: str, pin: str) -> str:
        """POST /validate-refresh-token → fresh access token. Raises
        :class:`NeedsManualLogin` on any failure."""
        if requests is None:
            raise NeedsManualLogin("Fyers: requests not installed")
        try:
            data = _http(
                "POST",
                f"{_API}/validate-refresh-token",
                json={
                    "grant_type": "refresh_token",
                    "appIdHash": _app_id_hash(),
                    "refresh_token": refresh_token,
                    "pin": pin,
                },
            )
        except Exception as exc:
            raise NeedsManualLogin(f"Fyers validate-refresh-token failed: {exc}") from exc
        token = data.get("access_token") or ""
        if not token:
            raise NeedsManualLogin("Fyers validate-refresh-token returned no token")
        return token

    def mint_access_token(self, db: Session, session: BrokerSession) -> str:
        refresh_token = read_secret(session.refresh_token)
        pin = read_secret(session.api_secret)
        if not refresh_token or not pin:
            raise NeedsManualLogin(
                "Fyers: missing refresh token / PIN — reconnect to refresh."
            )
        token = self._mint_from_refresh(refresh_token, pin)
        upsert_broker_session(
            db,
            int(session.user_id),
            self.broker,
            access_token=token,
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            is_active=True,
        )
        return token

    def verify_token(self, session: BrokerSession) -> bool:
        token = read_broker_access_token(session)
        if _use_mock(token):
            return True
        try:
            _http("GET", f"{_API}/profile", headers=self._headers(session, token))
            return True
        except Exception:
            return False

    # ── trading ───────────────────────────────────────────────────────────────
    def _headers(self, session: BrokerSession, token: str) -> dict:
        # Fyers authorises with the literal string "{app_id}:{access_token}".
        return {
            "Authorization": f"{settings.fyers_app_id}:{token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def place_order(
        self,
        session: BrokerSession,
        *,
        tradingsymbol: str,
        exchange: str = "NSE",
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        product: str = "CNC",
        trigger_price: Optional[float] = None,
        tag: str = "pivot",
        variety: str = "regular",
        client_request_id: Optional[str] = None,
    ) -> dict:
        token = read_broker_access_token(session)
        if _use_mock(token):
            return {
                "order_id": f"MOCK-FYERS-{abs(hash(client_request_id or tradingsymbol)) % 100000}",
                "status": "TRANSIT",
                "message": "Mock Fyers order placed",
                "client_request_id": client_request_id,
            }

        body = {
            "symbol": _fyers_symbol(exchange, tradingsymbol),
            "qty": int(quantity),
            "type": _ORDER_TYPE.get(order_type.upper(), 2),
            "side": _SIDE.get(transaction_type.upper(), 1),
            "productType": _PRODUCT.get(product.upper(), "CNC"),
            "limitPrice": price or 0,
            "stopPrice": trigger_price or 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }
        try:
            data = _http(
                "POST", f"{_API}/orders/sync",
                headers=self._headers(session, token), json=body,
            )
        except Exception as exc:
            logger.error("Fyers place_order failed: %s", exc)
            return {"status": "error", "message": str(exc), "client_request_id": client_request_id}
        return {
            "order_id": data.get("id"),
            "status": data.get("s", "PENDING"),
            "message": data.get("message", "Order placed"),
            "client_request_id": client_request_id,
        }

    def cancel_order(
        self, session: BrokerSession, order_id: str, variety: str = "regular"
    ) -> dict:
        token = read_broker_access_token(session)
        if _use_mock(token):
            return {"order_id": order_id, "status": "CANCELLED"}
        try:
            _http(
                "DELETE", f"{_API}/orders/sync",
                headers=self._headers(session, token), json={"id": order_id},
            )
            return {"order_id": order_id, "status": "CANCELLED"}
        except Exception as exc:
            return {"order_id": order_id, "status": "error", "message": str(exc)}

    # ── data ──────────────────────────────────────────────────────────────────
    def get_holdings(self, session: BrokerSession):
        token = read_broker_access_token(session)
        if _use_mock(token):
            return []
        try:
            return _http("GET", f"{_API}/holdings", headers=self._headers(session, token))
        except Exception as exc:
            logger.warning("Fyers holdings failed: %s", exc)
            return []

    def get_positions(self, session: BrokerSession):
        token = read_broker_access_token(session)
        if _use_mock(token):
            return []
        try:
            return _http("GET", f"{_API}/positions", headers=self._headers(session, token))
        except Exception as exc:
            logger.warning("Fyers positions failed: %s", exc)
            return []
