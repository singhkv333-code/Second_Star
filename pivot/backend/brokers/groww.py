"""Groww Trade API connector.

Groww is India's largest broker by user count, so it matters that its
onboarding is short. It has two auth shapes and they are NOT equivalent:

  - a pasted **access token**, which the user regenerates by hand daily; and
  - an **API key + TOTP seed**, which mints a fresh token unattended.

Only the second survives contact with an execution agent, so the UI asks for
the TOTP pair and treats a pasted token as the fallback. That is the whole
difference between "connect once" and "log in every morning", which is why
`complete_auth` branches on which pair it was given rather than accepting
either silently.

Tokens die at 06:00 IST. The mint endpoint is rate-limited to 150 calls per
24h per key — generous for one mint a day, but the reason nothing here retries
a mint in a loop.
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
from backend.brokers.transport import http, next_expiry_at
from backend.models import BrokerSession

logger = logging.getLogger(__name__)

_API = "https://api.groww.in/v1"
_TOKEN = f"{_API}/token/api/access"

_API_KEY_PAGE = "https://groww.in/trade-api/api-keys"
_DOCS = "https://groww.in/trade-api/docs"

_PRODUCT = {"CNC": "CNC", "MIS": "MIS", "NRML": "NRML"}
_ORDER_TYPE = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "SL", "SL-M": "SL_M"}


class GrowwConnector(BrokerConnector):
    broker = "groww"
    info = BrokerInfo(
        id="groww",
        name="Groww",
        logo="/brokers/groww.svg",
        persistence_kind=PersistenceKind.api_key_mint,
        supports_unattended=True,
        needs_api_key=True,
        accent="#00D09C",
        blurb="Stays connected. No daily login.",
        tags=["Auto-login", "No daily login"],
    )

    # ── mode ──────────────────────────────────────────────────────────────────
    def mock_mode(self) -> bool:
        return False  # credential broker: the user's own key is always a real path

    def deep_links(self, *, state: Optional[str] = None) -> DeepLinks:
        return DeepLinks(
            api_key_page=_API_KEY_PAGE,
            app_create=_API_KEY_PAGE,
            totp_setup=_API_KEY_PAGE,
            docs=_DOCS,
        )

    def get_login_url(self, state: str) -> Optional[str]:
        return None  # credential broker — no hosted redirect

    # ── onboarding ────────────────────────────────────────────────────────────
    def _mint(self, api_key: str, totp_secret: str) -> str:
        totp = pyotp.TOTP(totp_secret).now()
        data = http(
            "POST",
            _TOKEN,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "X-API-VERSION": "1.0",
                     "Authorization": f"Bearer {api_key}"},
            json={"key_type": "approval", "totp": totp},
        )
        token = data.get("token") or data.get("access_token") or ""
        if not token:
            raise ValueError(data.get("message") or "Groww returned no access token")
        return token

    def complete_auth(self, db: Session, user_id: int, payload: dict) -> BrokerSession:
        api_key = (payload.get("api_key") or "").strip()
        totp_secret = (payload.get("totp_secret") or "").strip().replace(" ", "")
        pasted = (payload.get("access_token") or "").strip()

        # Preferred shape: key + seed, which can re-mint unattended.
        if api_key and totp_secret:
            token = self._mint(api_key, totp_secret)
            mode = PersistenceKind.api_key_mint.value
            unattended = True
        elif pasted:
            # Honest fallback: a pasted token works today and dies at 6 AM with
            # no way for us to renew it. Recorded as daily_oauth so the UI shows
            # the reconnect prompt instead of promising it stays alive.
            token = pasted
            mode = PersistenceKind.daily_oauth.value
            unattended = False
        else:
            raise ValueError(
                "Groww needs your API key and TOTP secret (or a pasted access token)."
            )

        return upsert_broker_session(
            db,
            user_id,
            self.broker,
            access_token=token,
            api_key=api_key or None,
            totp_secret=totp_secret or None,
            broker_user_id=payload.get("client_id") or None,
            login_time=datetime.now(timezone.utc),
            token_expires_at=next_expiry_at(6, 0),
            persistence_mode=mode,
            auto_login_opt_in=unattended,
            is_active=True,
        )

    # ── token persistence ─────────────────────────────────────────────────────
    def mint_access_token(self, db: Session, session: BrokerSession) -> str:
        api_key = read_secret(session.api_key)
        totp_secret = read_secret(session.totp_secret)
        if not (api_key and totp_secret):
            raise NeedsManualLogin(
                "Groww: this connection was made with a pasted token, which "
                "cannot be renewed. Reconnect."
            )
        try:
            token = self._mint(api_key, totp_secret)
        except Exception as exc:
            raise NeedsManualLogin(f"Groww auto-login failed: {exc}") from exc

        upsert_broker_session(
            db,
            int(session.user_id),
            self.broker,
            access_token=token,
            token_expires_at=next_expiry_at(6, 0),
            is_active=True,
        )
        return token

    def verify_token(self, session: BrokerSession) -> bool:
        token = read_broker_access_token(session)
        if not token:
            return False
        try:
            http("GET", f"{_API}/order/list",
                 headers=self._headers(token), params={"segment": "CASH",
                                                       "page": 0, "page_size": 1})
            return True
        except Exception:
            return False

    # ── trading ───────────────────────────────────────────────────────────────
    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "X-API-VERSION": "1.0",
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
        if not token:
            return {"order_id": "", "status": "error", "message": "Groww not connected"}

        body = {
            "trading_symbol": tradingsymbol.upper(),
            "quantity": int(quantity),
            "exchange": exchange.upper(),
            "segment": "CASH",
            "product": _PRODUCT.get(product.upper(), "CNC"),
            "order_type": _ORDER_TYPE.get(order_type.upper(), "MARKET"),
            "transaction_type": transaction_type.upper(),
            "validity": "DAY",
        }
        if price:
            body["price"] = float(price)
        if trigger_price:
            body["trigger_price"] = float(trigger_price)
        if client_request_id:
            body["order_reference_id"] = client_request_id[:20]

        data = http("POST", f"{_API}/order/create", headers=self._headers(token), json=body)
        payload = data.get("payload") or data.get("data") or data
        order_id = payload.get("groww_order_id") or payload.get("order_id") or ""
        if not order_id:
            return {"order_id": "", "status": "error",
                    "message": data.get("message") or "Groww rejected the order",
                    "raw": data}
        return {"order_id": order_id, "status": "success", "message": "", "raw": data}

    def cancel_order(
        self, session: BrokerSession, order_id: str, variety: str = "regular"
    ) -> dict:
        token = read_broker_access_token(session)
        if not token:
            return {"order_id": order_id, "status": "error", "message": "Groww not connected"}
        data = http(
            "POST",
            f"{_API}/order/cancel",
            headers=self._headers(token),
            json={"groww_order_id": order_id, "segment": "CASH"},
        )
        return {"order_id": order_id, "status": "success", "raw": data}

    # ── data ──────────────────────────────────────────────────────────────────
    def get_holdings(self, session: BrokerSession):
        token = read_broker_access_token(session)
        if not token:
            return []
        data = http("GET", f"{_API}/holdings/user", headers=self._headers(token))
        return (data.get("payload") or {}).get("holdings") or []

    def get_positions(self, session: BrokerSession):
        token = read_broker_access_token(session)
        if not token:
            return []
        data = http("GET", f"{_API}/positions/user",
                    headers=self._headers(token), params={"segment": "CASH"})
        return (data.get("payload") or {}).get("positions") or []

    def get_available_cash(self, session: BrokerSession) -> Optional[float]:
        token = read_broker_access_token(session)
        if not token:
            return None
        try:
            data = http("GET", f"{_API}/margins/detail/user",
                        headers=self._headers(token))
            payload = data.get("payload") or {}
            return float(payload.get("clear_cash") or payload.get("net_margin_used", 0))
        except Exception:
            return None
