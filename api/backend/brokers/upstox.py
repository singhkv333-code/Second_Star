"""Upstox connector — OAuth 2.0 authorization-code flow.

Upstox is the cleanest onboarding of the six: a standard OAuth redirect, so the
user types NOTHING. The app-level ``client_id``/``client_secret`` belong to
Pivot, not the user.

The catch, and it is the whole reason ``mint_access_token`` refuses here:
Upstox issues **no refresh token**, and the access token dies at **03:30 IST**
regardless of when it was minted. Upstox support states it plainly. So there is
no unattended path — this is ``daily_oauth`` and the UI must show the one-tap
reconnect. Pretending otherwise would have the scheduler silently fail every
morning while the card still read "connected".

Extended tokens exist but are READ-ONLY; order placement rejects them with
403 UDAPI100067, so they are useless for the execution agent and not used here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

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
from backend.config import settings
from backend.models import BrokerSession

logger = logging.getLogger(__name__)

_AUTH_DIALOG = "https://api.upstox.com/v2/login/authorization/dialog"
_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
_API = "https://api.upstox.com/v2"
# Order placement lives on the HFT host in v3, NOT on api.upstox.com.
_HFT = "https://api-hft.upstox.com/v3"

_APP_CREATE = "https://account.upstox.com/developer/apps"
_DOCS = "https://upstox.com/developer/api-documentation/"

# Upstox speaks its own product vocabulary: I=intraday, D=delivery.
_PRODUCT = {"CNC": "D", "MIS": "I", "NRML": "D", "D": "D", "I": "I", "MTF": "MTF"}
_ORDER_TYPE = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "SL", "SL-M": "SL-M"}


class UpstoxConnector(BrokerConnector):
    broker = "upstox"
    info = BrokerInfo(
        id="upstox",
        name="Upstox",
        logo="/brokers/upstox.svg",
        persistence_kind=PersistenceKind.daily_oauth,
        supports_unattended=False,  # no refresh token exists — see module docstring
        needs_api_key=False,        # pure OAuth; app credentials are Pivot's
        accent="#7B3FE4",
        blurb="One-tap login. Nothing to type.",
        tags=["OAuth login", "Daily re-login"],
    )

    def __init__(self) -> None:
        self.info.supports_oauth = True  # type: ignore[attr-defined]

    # ── mode ──────────────────────────────────────────────────────────────────
    def mock_mode(self) -> bool:
        return not (settings.upstox_api_key and settings.upstox_api_secret)

    def _redirect_uri(self) -> str:
        return f"{settings.backend_url.rstrip('/')}/brokers/upstox/callback"

    def deep_links(self, *, state: Optional[str] = None) -> DeepLinks:
        return DeepLinks(
            login=self.get_login_url(state) if state else None,
            app_create=_APP_CREATE,
            docs=_DOCS,
        )

    # ── onboarding ────────────────────────────────────────────────────────────
    def get_login_url(self, state: str) -> Optional[str]:
        if self.mock_mode():
            return None
        return f"{_AUTH_DIALOG}?" + urlencode(
            {
                "client_id": settings.upstox_api_key,
                "redirect_uri": self._redirect_uri(),
                "response_type": "code",
                "state": state,
            }
        )

    def complete_auth(self, db: Session, user_id: int, payload: dict) -> BrokerSession:
        code = payload.get("code") or payload.get("request_token")
        if not code:
            raise ValueError("Upstox callback carried no authorization code")

        # Upstox's token endpoint takes form-encoded fields, not JSON.
        import requests as _rq

        resp = _rq.post(
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.upstox_api_key,
                "client_secret": settings.upstox_api_secret,
                "redirect_uri": self._redirect_uri(),
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json()

        token = data.get("access_token") or ""
        if not token:
            raise ValueError("Upstox token exchange returned no access_token")

        return upsert_broker_session(
            db,
            user_id,
            self.broker,
            access_token=token,
            broker_user_id=data.get("user_id") or data.get("client_id"),
            login_time=datetime.now(timezone.utc),
            token_expires_at=next_expiry_at(3, 30),
            persistence_mode=PersistenceKind.daily_oauth.value,
            is_active=True,
        )

    # ── token persistence ─────────────────────────────────────────────────────
    def mint_access_token(self, db: Session, session: BrokerSession) -> str:
        raise NeedsManualLogin(
            "Upstox issues no refresh token and the session ends at 3:30 AM IST. "
            "Reconnect in one tap."
        )

    def verify_token(self, session: BrokerSession) -> bool:
        token = read_broker_access_token(session)
        if use_mock(token):
            return True
        try:
            http("GET", f"{_API}/user/profile", headers=self._headers(token))
            return True
        except Exception:
            return False

    # ── trading ───────────────────────────────────────────────────────────────
    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _instrument_key(self, exchange: str, tradingsymbol: str) -> str:
        """Upstox addresses instruments by ``NSE_EQ|ISIN``-style keys. A caller
        may hand us either a ready-made key or a plain trading symbol."""
        if "|" in tradingsymbol:
            return tradingsymbol
        seg = "NSE_EQ" if exchange.upper() == "NSE" else "BSE_EQ"
        return f"{seg}|{tradingsymbol.upper()}"

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
        if use_mock(token):
            return {
                "order_id": f"mock-upstox-{tradingsymbol}",
                "status": "mock",
                "message": "Upstox not connected — simulated acknowledgement.",
            }

        body = {
            "quantity": int(quantity),
            "product": _PRODUCT.get(product.upper(), "D"),
            "validity": "DAY",
            "price": float(price or 0),
            "instrument_token": self._instrument_key(exchange, tradingsymbol),
            "order_type": _ORDER_TYPE.get(order_type.upper(), "MARKET"),
            "transaction_type": transaction_type.upper(),
            "disclosed_quantity": 0,
            "trigger_price": float(trigger_price or 0),
            "is_amo": False,
            # `tag` is how a fill is later reconciled back to the strategy that
            # caused it; Upstox caps it at 40 chars.
            "tag": tag[:40],
        }
        data = http("POST", f"{_HFT}/order/place", headers=self._headers(token), json=body)
        payload = data.get("data") or {}
        ids = payload.get("order_ids") or []
        return {
            "order_id": (ids[0] if ids else payload.get("order_id")) or "",
            "status": data.get("status") or "success",
            "message": "",
            "raw": data,
        }

    def cancel_order(
        self, session: BrokerSession, order_id: str, variety: str = "regular"
    ) -> dict:
        token = read_broker_access_token(session)
        if use_mock(token):
            return {"order_id": order_id, "status": "mock"}
        data = http(
            "DELETE",
            f"{_API}/order/cancel",
            headers=self._headers(token),
            params={"order_id": order_id},
        )
        return {"order_id": order_id, "status": data.get("status") or "success", "raw": data}

    # ── data ──────────────────────────────────────────────────────────────────
    def get_holdings(self, session: BrokerSession):
        token = read_broker_access_token(session)
        if use_mock(token):
            return []
        return (http("GET", f"{_API}/portfolio/long-term-holdings",
                     headers=self._headers(token)) or {}).get("data") or []

    def get_positions(self, session: BrokerSession):
        token = read_broker_access_token(session)
        if use_mock(token):
            return []
        return (http("GET", f"{_API}/portfolio/short-term-positions",
                     headers=self._headers(token)) or {}).get("data") or []

    def get_available_cash(self, session: BrokerSession) -> Optional[float]:
        token = read_broker_access_token(session)
        if use_mock(token):
            return None
        try:
            data = http("GET", f"{_API}/user/get-funds-and-margin",
                        headers=self._headers(token), params={"segment": "SEC"})
            equity = (data.get("data") or {}).get("equity") or {}
            return float(equity.get("available_margin"))
        except Exception:
            return None
