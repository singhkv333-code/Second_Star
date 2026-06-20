"""Zerodha Kite connector — wraps the existing ``backend.kite.*`` modules
behind the broker-agnostic :class:`BrokerConnector` interface.

Kite is OAuth-first and has NO official unattended refresh: the access token
dies ~6 AM IST daily and the user must re-auth. ``mint_access_token`` therefore
raises :class:`NeedsManualLogin` unless the user has opted into the gray
stored-credential TOTP login (persistence_mode=``totp_login``) — that replay is
wired in P2; for now the opt-in is recorded but mint still asks for re-login.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from backend.brokers.sessions import read_broker_access_token, upsert_broker_session
from backend.kite import auth as kite_auth
from backend.kite import orders as kite_orders
from backend.kite import portfolio as kite_portfolio
from backend.models import BrokerSession

# Deep links — send the user straight to where their input is needed.
_KITE_APP_CREATE = "https://developers.kite.trade/apps"
_KITE_TOTP_SETUP = "https://kite.zerodha.com/settings/password"
_KITE_DOCS = "https://kite.trade/docs/connect/v3/"


class KiteConnector(BrokerConnector):
    broker = "kite"
    info = BrokerInfo(
        id="kite",
        name="Zerodha Kite",
        logo="/brokers/kite.svg",
        persistence_kind=PersistenceKind.daily_oauth,
        supports_unattended=False,  # only via the P2 opt-in TOTP login
        needs_api_key=False,        # pure OAuth; api_key/secret are app-level
        accent="#F6461A",  # Kite logo red (matches /brokers/kite.svg)
        blurb="India's largest broker. One-click OAuth login.",
        tags=["OAuth login", "Daily re-login"],
    )

    # ── mode ──────────────────────────────────────────────────────────────────
    def mock_mode(self) -> bool:
        return kite_auth.KITE_MOCK_MODE

    def deep_links(self, *, state: Optional[str] = None) -> DeepLinks:
        return DeepLinks(
            login=self.get_login_url(state) if state else None,
            app_create=_KITE_APP_CREATE,
            totp_setup=_KITE_TOTP_SETUP,
            docs=_KITE_DOCS,
        )

    # ── onboarding ────────────────────────────────────────────────────────────
    def get_login_url(self, state: str) -> Optional[str]:
        if kite_auth.KITE_MOCK_MODE:
            return None
        base_url = kite_auth.get_login_url()
        sep = "&" if "?" in base_url else "?"
        # Kite forwards redirect_params back on the callback as the `state`.
        return f"{base_url}{sep}redirect_params={urlencode({'state': state})}"

    def complete_auth(self, db: Session, user_id: int, payload: dict) -> BrokerSession:
        now = datetime.now(timezone.utc)
        request_token = payload.get("request_token")

        if request_token:
            session_data = kite_auth.exchange_request_token(request_token)
            access_token = session_data.get("access_token", "")
            broker_user_id = session_data.get("user_id")
        else:
            # Mock connect / credential-only path (no real OAuth round-trip).
            access_token = payload.get("access_token") or f"mock_access_token_{user_id}"
            broker_user_id = payload.get("client_id") or "MOCK001"

        # Optional gray opt-in: record the intent to keep Kite alive via stored
        # TOTP login. The actual login replay lands in P2; until then the
        # scheduler will still ask the user to re-auth (honest).
        opt_in = bool(payload.get("auto_login_opt_in"))
        totp_secret = payload.get("totp_secret")
        persistence = (
            PersistenceKind.totp_login.value
            if (opt_in and totp_secret)
            else PersistenceKind.daily_oauth.value
        )

        return upsert_broker_session(
            db,
            user_id,
            self.broker,
            access_token=access_token,
            request_token=request_token,
            broker_user_id=broker_user_id,
            totp_secret=totp_secret,
            auto_login_opt_in=opt_in,
            persistence_mode=persistence,
            login_time=now,
            # Kite expires ~6 AM IST; we record receipt + a conservative window
            # and let verify_token be the source of truth.
            token_expires_at=now + timedelta(hours=20),
            is_active=True,
        )

    # ── token persistence ─────────────────────────────────────────────────────
    def mint_access_token(self, db: Session, session: BrokerSession) -> str:
        # Kite offers no official unattended refresh. The opt-in TOTP login
        # replay (persistence_mode == totp_login) is implemented in P2.
        raise NeedsManualLogin(
            "Zerodha requires a fresh login — Kite has no unattended token "
            "refresh. Reconnect from the app (or enable Dhan for hands-off "
            "automation)."
        )

    def verify_token(self, session: BrokerSession) -> bool:
        return kite_auth.verify_token_valid(read_broker_access_token(session))

    # ── trading ───────────────────────────────────────────────────────────────
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
        return kite_orders.place_order(
            access_token=read_broker_access_token(session) or "mock_token",
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type=order_type,
            price=price,
            product=product,
            trigger_price=trigger_price,
            tag=tag,
            variety=variety,
            client_request_id=client_request_id,
        )

    def place_gtt(
        self,
        session: BrokerSession,
        *,
        tradingsymbol: str,
        exchange: str = "NSE",
        transaction_type: str,
        quantity: int,
        trigger_price: float,
        limit_price: float,
        last_price: Optional[float] = None,
    ) -> dict:
        return kite_orders.place_gtt_order(
            access_token=read_broker_access_token(session) or "mock_token",
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            trigger_price=trigger_price,
            limit_price=limit_price,
            last_price=last_price if last_price is not None else limit_price,
        )

    def cancel_order(
        self, session: BrokerSession, order_id: str, variety: str = "regular"
    ) -> dict:
        return kite_orders.cancel_order(
            access_token=read_broker_access_token(session) or "mock_token",
            order_id=order_id,
            variety=variety,
        )

    # ── data ──────────────────────────────────────────────────────────────────
    def get_holdings(self, session: BrokerSession):
        return kite_portfolio.get_holdings(read_broker_access_token(session) or "mock_token")

    def get_positions(self, session: BrokerSession):
        return kite_portfolio.get_positions(read_broker_access_token(session) or "mock_token")
