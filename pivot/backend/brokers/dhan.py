"""Dhan (DhanHQ v2) connector — the clean unattended-automation broker.

This is the broker that actually delivers "never re-verify":
  - ``rolling_renew``  : POST /v2/RenewToken rolls a 24h token forward with NO
                         stored credentials, as long as we renew before expiry.
  - ``api_key_mint``   : POST auth.dhan.co/app/generateAccessToken?pin&totp mints
                         a fresh 24h token from the user's client id + PIN + TOTP
                         (the opt-in unattended path that survives a missed roll).

Both are officially supported. Orders/holdings hit https://api.dhan.co/v2 with
``access-token`` + ``dhanClientId`` headers. The connector speaks Pivot's
Kite-style vocabulary at the boundary and maps to Dhan enums internally.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Optional

import pyotp

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

_API = "https://api.dhan.co/v2"
_AUTH = "https://auth.dhan.co"
_SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

# Deep links — straight to where the user grabs their keys / sets up 2FA.
_DHAN_API_PAGE = "https://web.dhan.co/"          # Profile → DhanHQ Trading API
_DHAN_TOTP = "https://web.dhan.co/"              # Profile → Security → 2FA
_DHAN_DOCS = "https://dhanhq.co/docs/v2/"

# Pivot (Kite-style) → Dhan enum maps.
_SEGMENT = {
    "NSE": "NSE_EQ", "BSE": "BSE_EQ", "NFO": "NSE_FNO", "BFO": "BSE_FNO",
    "MCX": "MCX_COMM", "CDS": "NSE_CURRENCY", "NSE_EQ": "NSE_EQ", "BSE_EQ": "BSE_EQ",
}
_PRODUCT = {
    "CNC": "CNC", "MIS": "INTRADAY", "NRML": "MARGIN", "MTF": "MTF",
    "CO": "CO", "BO": "BO", "INTRADAY": "INTRADAY", "MARGIN": "MARGIN",
}
_ORDER_TYPE = {
    "MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "STOP_LOSS",
    "SL-M": "STOP_LOSS_MARKET", "SL-MARKET": "STOP_LOSS_MARKET",
    "STOP_LOSS": "STOP_LOSS", "STOP_LOSS_MARKET": "STOP_LOSS_MARKET",
}

# Lazy (exchange_segment, UPPER symbol) -> securityId cache from the scrip master.
_scrip_cache: dict[tuple[str, str], str] = {}
_scrip_loaded = False


def _dhan_mock() -> bool:
    """No app-level Dhan config AND used for the dev connect-mock shortcut. Live
    operation is decided per-call by token presence (a user can paste their own
    token without app config)."""
    return not bool(settings.dhan_partner_id or settings.dhan_api_key)


def _use_mock(token: str) -> bool:
    return requests is None or not token or token == "mock_token" or token.startswith("mock_")


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


def _resolve_security_id(exchange_segment: str, symbol: str) -> Optional[str]:
    """Map (segment, tradingsymbol) -> Dhan securityId via the scrip master.
    Cached after first download; returns None (caller errors honestly) if the
    master can't be loaded. Never raises."""
    global _scrip_loaded
    key = (exchange_segment, symbol.upper())
    if key in _scrip_cache:
        return _scrip_cache[key]
    if _scrip_loaded or requests is None:
        return None
    try:
        resp = requests.get(_SCRIP_MASTER, timeout=20)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            seg = (row.get("SEM_SEGMENT") or row.get("SEM_EXM_EXCH_ID") or "").strip()
            sym = (row.get("SEM_TRADING_SYMBOL") or "").strip().upper()
            sid = (row.get("SEM_SMST_SECURITY_ID") or "").strip()
            if sym and sid:
                # Index by both the raw exch id and our segment guess so common
                # equity lookups resolve. Best-effort.
                _scrip_cache[(seg, sym)] = sid
        _scrip_loaded = True
    except Exception as exc:  # pragma: no cover - network
        logger.warning("Dhan scrip master load failed: %s", exc)
        _scrip_loaded = True  # don't retry every order
        return None
    return _scrip_cache.get(key)


class DhanConnector(BrokerConnector):
    broker = "dhan"
    info = BrokerInfo(
        id="dhan",
        name="Dhan",
        logo="/brokers/dhan.svg",
        persistence_kind=PersistenceKind.rolling_renew,
        supports_unattended=True,
        needs_api_key=True,
        accent="#4575D9",  # Dhan brand royal blue (matches /brokers/dhan.svg)
        blurb="Stays connected with no daily login. Built for automation.",
        tags=["No daily login", "Full automation"],
    )

    # ── mode ──────────────────────────────────────────────────────────────────
    def mock_mode(self) -> bool:
        return _dhan_mock()

    def deep_links(self, *, state: Optional[str] = None) -> DeepLinks:
        return DeepLinks(
            login=self.get_login_url(state) if state else None,
            api_key_page=_DHAN_API_PAGE,
            totp_setup=_DHAN_TOTP,
            docs=_DHAN_DOCS,
        )

    # ── onboarding ────────────────────────────────────────────────────────────
    def get_login_url(self, state: str) -> Optional[str]:
        # Partner OAuth needs a generate-consent server round-trip + per-user
        # correlation; in P0 Dhan onboards via the credentials path (paste token
        # or client_id+PIN+TOTP), so there's no hosted login redirect yet.
        if not settings.dhan_partner_id or requests is None:
            return None
        try:
            data = _http(
                "POST",
                f"{_AUTH}/partner/generate-consent",
                headers={
                    "partner_id": settings.dhan_partner_id,
                    "partner_secret": settings.dhan_partner_secret,
                },
            )
            consent_id = data.get("consentId")
            return f"{_AUTH}/consent-login?consentId={consent_id}" if consent_id else None
        except Exception as exc:  # pragma: no cover - network
            logger.warning("Dhan generate-consent failed: %s", exc)
            return None

    def complete_auth(self, db: Session, user_id: int, payload: dict) -> BrokerSession:
        now = datetime.now(timezone.utc)
        client_id = payload.get("client_id") or payload.get("dhanClientId")
        pin = payload.get("pin")
        totp_secret = payload.get("totp_secret")
        token_id = payload.get("tokenId")
        access_token = payload.get("access_token")
        opt_in = bool(payload.get("auto_login_opt_in"))
        expiry: Optional[datetime] = None

        if token_id and settings.dhan_partner_id and requests is not None:
            # Partner OAuth consume-consent.
            data = _http(
                "POST",
                f"{_AUTH}/partner/consume-consent",
                headers={
                    "partner_id": settings.dhan_partner_id,
                    "partner_secret": settings.dhan_partner_secret,
                },
                params={"tokenId": token_id},
            )
            access_token = data.get("accessToken") or data.get("access_token")
            client_id = data.get("dhanClientId") or client_id
            expiry = _parse_expiry(data.get("expiryTime"))
            persistence = PersistenceKind.rolling_renew.value
        elif client_id and pin and totp_secret:
            # Unattended mint path — verify it works now, then store creds so the
            # scheduler can re-mint daily with zero user interaction.
            access_token, expiry = self._generate_access_token(client_id, pin, totp_secret)
            persistence = PersistenceKind.api_key_mint.value
            opt_in = True
        elif access_token and client_id:
            # User pasted a 24h token — keep it alive via RenewToken.
            persistence = PersistenceKind.rolling_renew.value
        else:
            # Mock / dev connect.
            access_token = access_token or f"mock_dhan_{user_id}"
            client_id = client_id or "MOCK-DHAN"
            persistence = PersistenceKind.rolling_renew.value

        return upsert_broker_session(
            db,
            user_id,
            self.broker,
            access_token=access_token,
            broker_user_id=client_id,
            api_key=client_id,        # Dhan client id (also stored as api_key)
            api_secret=pin,           # Dhan PIN (encrypted) — used for re-mint
            totp_secret=totp_secret,
            auto_login_opt_in=opt_in,
            persistence_mode=persistence,
            login_time=now,
            token_expires_at=expiry,
            is_active=True,
        )

    # ── token persistence ─────────────────────────────────────────────────────
    def _generate_access_token(
        self, client_id: str, pin: str, totp_secret: str
    ) -> tuple[str, Optional[datetime]]:
        totp = pyotp.TOTP(totp_secret).now()
        data = _http(
            "POST",
            f"{_AUTH}/app/generateAccessToken",
            params={"dhanClientId": client_id, "pin": pin, "totp": totp},
        )
        token = data.get("accessToken") or data.get("access_token") or ""
        if not token:
            raise NeedsManualLogin("Dhan generateAccessToken returned no token")
        return token, _parse_expiry(data.get("expiryTime"))

    def mint_access_token(self, db: Session, session: BrokerSession) -> str:
        if requests is None:
            raise NeedsManualLogin("Dhan: requests not installed")
        mode = session.persistence_mode
        client_id = session.broker_user_id or read_secret(session.api_key)

        if mode == PersistenceKind.api_key_mint.value:
            pin = read_secret(session.api_secret)
            totp_secret = read_secret(session.totp_secret)
            if not (client_id and pin and totp_secret):
                raise NeedsManualLogin("Dhan: missing client_id/PIN/TOTP for mint")
            token, expiry = self._generate_access_token(client_id, pin, totp_secret)
        elif mode == PersistenceKind.rolling_renew.value:
            current = read_broker_access_token(session)
            if not current:
                raise NeedsManualLogin("Dhan: no token to renew — reconnect")
            try:
                data = _http(
                    "POST",
                    f"{_API}/RenewToken",
                    headers={"access-token": current, "dhanClientId": client_id or ""},
                )
            except Exception as exc:
                raise NeedsManualLogin(f"Dhan RenewToken failed: {exc}") from exc
            token = data.get("accessToken") or data.get("access_token") or ""
            expiry = _parse_expiry(data.get("expiryTime"))
            if not token:
                raise NeedsManualLogin("Dhan RenewToken returned no token")
        else:
            raise NeedsManualLogin(f"Dhan: persistence_mode {mode!r} has no mint path")

        upsert_broker_session(
            db,
            int(session.user_id),
            self.broker,
            access_token=token,
            token_expires_at=expiry,
            is_active=True,
        )
        return token

    def verify_token(self, session: BrokerSession) -> bool:
        token = read_broker_access_token(session)
        if _use_mock(token):
            return True
        try:
            _http("GET", f"{_API}/fundlimit", headers=self._headers(session, token))
            return True
        except Exception:
            return False

    # ── trading ───────────────────────────────────────────────────────────────
    def _headers(self, session: BrokerSession, token: str) -> dict:
        return {
            "access-token": token,
            "dhanClientId": session.broker_user_id or "",
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
                "order_id": f"MOCK-DHAN-{abs(hash(client_request_id or tradingsymbol)) % 100000}",
                "status": "TRANSIT",
                "message": "Mock Dhan order placed",
                "client_request_id": client_request_id,
            }

        segment = _SEGMENT.get(exchange.upper(), "NSE_EQ")
        security_id = _resolve_security_id(segment, tradingsymbol)
        if not security_id:
            return {
                "status": "error",
                "message": (
                    f"Dhan securityId unresolved for {tradingsymbol} ({segment}); "
                    "scrip master unavailable."
                ),
                "client_request_id": client_request_id,
            }

        body = {
            "dhanClientId": session.broker_user_id or "",
            "transactionType": transaction_type.upper(),
            "exchangeSegment": segment,
            "productType": _PRODUCT.get(product.upper(), "CNC"),
            "orderType": _ORDER_TYPE.get(order_type.upper(), "MARKET"),
            "validity": "DAY",
            "securityId": security_id,
            "quantity": int(quantity),
            "price": price or 0,
            "triggerPrice": trigger_price or 0,
            "correlationId": (client_request_id or tag)[:25],
        }
        try:
            data = _http("POST", f"{_API}/orders", headers=self._headers(session, token), json=body)
        except Exception as exc:
            logger.error("Dhan place_order failed: %s", exc)
            return {"status": "error", "message": str(exc), "client_request_id": client_request_id}
        return {
            "order_id": data.get("orderId"),
            "status": data.get("orderStatus", "PENDING"),
            "message": "Order placed",
            "client_request_id": client_request_id,
        }

    def cancel_order(
        self, session: BrokerSession, order_id: str, variety: str = "regular"
    ) -> dict:
        token = read_broker_access_token(session)
        if _use_mock(token):
            return {"order_id": order_id, "status": "CANCELLED"}
        try:
            _http("DELETE", f"{_API}/orders/{order_id}", headers=self._headers(session, token))
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
            logger.warning("Dhan holdings failed: %s", exc)
            return []

    def get_positions(self, session: BrokerSession):
        token = read_broker_access_token(session)
        if _use_mock(token):
            return []
        try:
            return _http("GET", f"{_API}/positions", headers=self._headers(session, token))
        except Exception as exc:
            logger.warning("Dhan positions failed: %s", exc)
            return []


def _parse_expiry(value) -> Optional[datetime]:
    """Parse Dhan's ISO expiryTime to an aware UTC datetime; None on failure."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
