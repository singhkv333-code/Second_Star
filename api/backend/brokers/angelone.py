"""Angel One SmartAPI connector.

The interesting property: SmartAPI's login is a **TOTP exchange, not an OAuth
code exchange** — the user's own API key plus a TOTP generated from their own
seed mints a fresh JWT. No API secret ever crosses the wire, and crucially
there is no hosted redirect to bounce the user through. That makes Angel One
one of the two brokers here that can genuinely re-connect itself every morning
with zero human input: we hold the TOTP seed, so `mint_access_token` works.

It also returns a ``refreshToken``, so there are two unattended paths; the
refresh is tried first because it is cheaper and does not re-derive a TOTP.

SmartAPI demands a fixed set of client-identity headers on EVERY request
(``X-ClientLocalIP``/``X-ClientPublicIP``/``X-MACAddress``). They are not
validated for correctness but the call 400s when they are absent, which is an
unusually silent way to fail — hence `_headers` always sends them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import pyotp
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
from backend.brokers.transport import http, next_expiry_at, use_mock
from backend.models import BrokerSession

logger = logging.getLogger(__name__)

_API = "https://apiconnect.angelone.in"
_LOGIN = f"{_API}/rest/auth/angelbroking/user/v1/loginByPassword"
_GENERATE = f"{_API}/rest/auth/angelbroking/jwt/v1/generateTokens"
_PLACE = f"{_API}/rest/secure/angelbroking/order/v1/placeOrder"
_CANCEL = f"{_API}/rest/secure/angelbroking/order/v1/cancelOrder"
_HOLDINGS = f"{_API}/rest/secure/angelbroking/portfolio/v1/getHolding"
_POSITIONS = f"{_API}/rest/secure/angelbroking/order/v1/getPosition"
_RMS = f"{_API}/rest/secure/angelbroking/user/v1/getRMS"
_PROFILE = f"{_API}/rest/secure/angelbroking/user/v1/getProfile"

_APP_CREATE = "https://smartapi.angelbroking.com/signup"
_API_KEY_PAGE = "https://smartapi.angelbroking.com/apps"
_TOTP_SETUP = "https://smartapi.angelbroking.com/enable-totp"
_DOCS = "https://smartapi.angelbroking.com/docs"

_PRODUCT = {"CNC": "DELIVERY", "MIS": "INTRADAY", "NRML": "CARRYFORWARD"}
_ORDER_TYPE = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "STOPLOSS_LIMIT",
               "SL-M": "STOPLOSS_MARKET"}


class AngelOneConnector(BrokerConnector):
    broker = "angelone"
    info = BrokerInfo(
        id="angelone",
        name="Angel One",
        logo="/brokers/angelone.svg",
        persistence_kind=PersistenceKind.totp_login,
        supports_unattended=True,
        needs_api_key=True,
        accent="#E84C2F",
        blurb="Stays connected. No daily login.",
        tags=["Auto-login", "No daily login"],
    )

    # ── mode ──────────────────────────────────────────────────────────────────
    def mock_mode(self) -> bool:
        # Credential broker: the user brings their own key, so there is always
        # a real path. Never mock at the connector level.
        return False

    def deep_links(self, *, state: Optional[str] = None) -> DeepLinks:
        return DeepLinks(
            app_create=_APP_CREATE,
            api_key_page=_API_KEY_PAGE,
            totp_setup=_TOTP_SETUP,
            docs=_DOCS,
        )

    def get_login_url(self, state: str) -> Optional[str]:
        return None  # no hosted redirect — credential broker

    # ── onboarding ────────────────────────────────────────────────────────────
    def _login(self, api_key: str, client_code: str, password: str,
               totp_secret: str) -> dict:
        totp = pyotp.TOTP(totp_secret).now()
        data = http(
            "POST",
            _LOGIN,
            headers=self._base_headers(api_key),
            json={"clientcode": client_code, "password": password, "totp": totp},
        )
        if not data.get("status"):
            raise ValueError(data.get("message") or "Angel One login failed")
        return data.get("data") or {}

    def complete_auth(self, db: Session, user_id: int, payload: dict) -> BrokerSession:
        api_key = (payload.get("api_key") or "").strip()
        client_code = (payload.get("client_id") or payload.get("client_code") or "").strip()
        # Angel One calls it the PIN/MPIN in the app but the field is `password`.
        password = (payload.get("password") or payload.get("pin") or "").strip()
        totp_secret = (payload.get("totp_secret") or "").strip().replace(" ", "")

        if not (api_key and client_code and password and totp_secret):
            raise ValueError(
                "Angel One needs your API key, client code, PIN and TOTP secret."
            )

        # Attempt the login immediately so bad credentials fail at connect time
        # rather than silently at 6 AM tomorrow.
        data = self._login(api_key, client_code, password, totp_secret)
        token = data.get("jwtToken") or ""
        if not token:
            raise ValueError("Angel One returned no jwtToken")

        return upsert_broker_session(
            db,
            user_id,
            self.broker,
            access_token=token.replace("Bearer ", ""),
            refresh_token=data.get("refreshToken"),
            api_key=api_key,
            api_secret=password,
            totp_secret=totp_secret,
            broker_user_id=client_code,
            login_time=datetime.now(timezone.utc),
            token_expires_at=next_expiry_at(6, 0),
            persistence_mode=PersistenceKind.totp_login.value,
            auto_login_opt_in=True,
            is_active=True,
        )

    # ── token persistence ─────────────────────────────────────────────────────
    def mint_access_token(self, db: Session, session: BrokerSession) -> str:
        api_key = read_secret(session.api_key)
        client_code = session.broker_user_id or ""
        password = read_secret(session.api_secret)
        totp_secret = read_secret(session.totp_secret)

        if not api_key:
            raise NeedsManualLogin("Angel One: no API key stored — reconnect")

        token = ""
        refresh = read_secret(session.refresh_token)

        # Cheapest path first: trade the refresh token for a new JWT.
        if refresh:
            try:
                data = http(
                    "POST",
                    _GENERATE,
                    headers=self._base_headers(api_key),
                    json={"refreshToken": refresh},
                )
                token = ((data.get("data") or {}).get("jwtToken") or "").replace("Bearer ", "")
                refresh = (data.get("data") or {}).get("refreshToken") or refresh
            except Exception as exc:
                logger.info("angelone: refresh failed, falling back to TOTP (%s)", exc)

        # Fall back to replaying the TOTP login.
        if not token:
            if not (client_code and password and totp_secret):
                raise NeedsManualLogin(
                    "Angel One: refresh failed and no stored TOTP — reconnect"
                )
            try:
                data = self._login(api_key, client_code, password, totp_secret)
            except Exception as exc:
                raise NeedsManualLogin(f"Angel One auto-login failed: {exc}") from exc
            token = (data.get("jwtToken") or "").replace("Bearer ", "")
            refresh = data.get("refreshToken") or refresh

        if not token:
            raise NeedsManualLogin("Angel One: no token minted — reconnect")

        upsert_broker_session(
            db,
            int(session.user_id),
            self.broker,
            access_token=token,
            refresh_token=refresh,
            token_expires_at=next_expiry_at(6, 0),
            is_active=True,
        )
        return token

    def verify_token(self, session: BrokerSession) -> bool:
        token = read_broker_access_token(session)
        if not token:
            return False
        try:
            data = http("GET", _PROFILE, headers=self._headers(session, token))
            return bool(data.get("status"))
        except Exception:
            return False

    # ── trading ───────────────────────────────────────────────────────────────
    def _base_headers(self, api_key: str) -> dict:
        """SmartAPI 400s when the client-identity headers are missing, so they
        are always sent. The values are not validated by Angel One."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": api_key,
        }

    def _headers(self, session: BrokerSession, token: str) -> dict:
        headers = self._base_headers(read_secret(session.api_key))
        headers["Authorization"] = f"Bearer {token}"
        return headers

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
        if not token:
            return {"order_id": "", "status": "error",
                    "message": "Angel One not connected"}

        # SmartAPI addresses instruments by numeric symboltoken, which the
        # caller resolves upstream and may pass through the symbol field.
        symbol_token = (client_request_id or "").strip() if (client_request_id or "").isdigit() else ""

        body = {
            "variety": "NORMAL",
            "tradingsymbol": tradingsymbol.upper(),
            "symboltoken": symbol_token,
            "transactiontype": transaction_type.upper(),
            "exchange": exchange.upper(),
            "ordertype": _ORDER_TYPE.get(order_type.upper(), "MARKET"),
            "producttype": _PRODUCT.get(product.upper(), "DELIVERY"),
            "duration": "DAY",
            "price": str(price or 0),
            "triggerprice": str(trigger_price or 0),
            "quantity": str(int(quantity)),
        }
        data = http("POST", _PLACE, headers=self._headers(session, token), json=body)
        if not data.get("status"):
            return {"order_id": "", "status": "error",
                    "message": data.get("message") or "Angel One rejected the order",
                    "raw": data}
        inner = data.get("data") or {}
        return {
            "order_id": inner.get("orderid") or "",
            "status": "success",
            "message": "",
            "raw": data,
        }

    def cancel_order(
        self, session: BrokerSession, order_id: str, variety: str = "regular"
    ) -> dict:
        token = read_broker_access_token(session)
        if not token:
            return {"order_id": order_id, "status": "error",
                    "message": "Angel One not connected"}
        data = http(
            "POST",
            _CANCEL,
            headers=self._headers(session, token),
            json={"variety": "NORMAL", "orderid": order_id},
        )
        return {"order_id": order_id,
                "status": "success" if data.get("status") else "error",
                "message": data.get("message") or "", "raw": data}

    # ── data ──────────────────────────────────────────────────────────────────
    def get_holdings(self, session: BrokerSession):
        token = read_broker_access_token(session)
        if not token:
            return []
        data = http("GET", _HOLDINGS, headers=self._headers(session, token))
        return data.get("data") or []

    def get_positions(self, session: BrokerSession):
        token = read_broker_access_token(session)
        if not token:
            return []
        data = http("GET", _POSITIONS, headers=self._headers(session, token))
        return data.get("data") or []

    def get_available_cash(self, session: BrokerSession) -> Optional[float]:
        token = read_broker_access_token(session)
        if not token:
            return None
        try:
            data = http("GET", _RMS, headers=self._headers(session, token))
            return float((data.get("data") or {}).get("availablecash"))
        except Exception:
            return None
