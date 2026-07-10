"""Zerodha Kite connector — wraps the existing ``backend.kite.*`` modules
behind the broker-agnostic :class:`BrokerConnector` interface.

Kite is OAuth-first and has NO official unattended refresh: the access token
dies ~6 AM IST daily and the user must re-auth. ``mint_access_token`` therefore
raises :class:`NeedsManualLogin` unless the user has opted into the gray
stored-credential TOTP login (persistence_mode=``totp_login`` +
``auto_login_opt_in``) — in which case it replays the Kite web-login with the
encrypted user_id/password/TOTP secret and mints a fresh access token with no
manual step.
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
from backend.brokers.sessions import (
    read_broker_access_token,
    read_secret,
    upsert_broker_session,
)
from backend.kite import auth as kite_auth
from backend.kite import orders as kite_orders
from backend.kite import portfolio as kite_portfolio
from backend.models import BrokerSession

# Deep links — send the user straight to where their input is needed.
_KITE_APP_CREATE = "https://developers.kite.trade/apps"
_KITE_TOTP_SETUP = "https://kite.zerodha.com/settings/password"
_KITE_DOCS = "https://kite.trade/docs/connect/v3/"


def _build_kite_info() -> BrokerInfo:
    """Construct the Kite ``BrokerInfo``. ``supports_oauth`` is a field P2b adds
    to ``BrokerInfo`` (default False); set it True for Kite either via the
    constructor (once the field exists) or by post-assignment, so this stream is
    independently importable regardless of merge ordering."""
    info = BrokerInfo(
        id="kite",
        name="Zerodha Kite",
        logo="/brokers/kite.svg",
        persistence_kind=PersistenceKind.daily_oauth,
        supports_unattended=False,  # only via the opt-in TOTP login below
        needs_api_key=False,        # pure OAuth; api_key/secret are app-level
        accent="#F6461A",  # Kite logo red (matches /brokers/kite.svg)
        blurb="India's largest broker. One-click OAuth login.",
        tags=["OAuth login", "Daily re-login"],
    )
    # Kite IS an OAuth broker; flows to GET /brokers via dataclasses.asdict.
    info.supports_oauth = True  # type: ignore[attr-defined]
    return info


class KiteConnector(BrokerConnector):
    broker = "kite"
    info = _build_kite_info()

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

        opt_in = bool(payload.get("auto_login_opt_in"))
        totp_secret = payload.get("totp_secret")
        password = payload.get("password")
        client_id = payload.get("client_id")

        # ── Advanced opt-in (the gray "stay connected without daily login")
        # path: the user supplies their Kite user id + password + TOTP secret so
        # we can replay the web-login each morning. We store the creds ENCRYPTED
        # (upsert encrypts api_secret/totp_secret) and IMMEDIATELY attempt one
        # login+exchange so bad credentials fail fast at connect time.
        is_advanced = bool(password) or (opt_in and client_id and totp_secret)
        if is_advanced:
            kite_user_id = client_id or payload.get("broker_user_id")
            if not (kite_user_id and password and totp_secret):
                raise ValueError(
                    "Kite auto-login needs your Kite user id, password, and "
                    "TOTP secret."
                )

            access_token = ""
            captured_user_id = kite_user_id
            login_error: Exception | None = None
            try:
                rt = kite_auth.totp_login(kite_user_id, password, totp_secret)
                data = kite_auth.exchange_request_token(rt)
                access_token = data.get("access_token", "")
                captured_user_id = data.get("user_id") or kite_user_id
            except Exception as exc:  # store the opt-in but surface the failure
                login_error = exc

            session = upsert_broker_session(
                db,
                user_id,
                self.broker,
                access_token=access_token,
                broker_user_id=captured_user_id,
                # api_secret carries the (encrypted) Kite *password*; totp_secret
                # the (encrypted) TOTP seed — both used only to replay login.
                api_secret=password,
                totp_secret=totp_secret,
                auto_login_opt_in=True,
                persistence_mode=PersistenceKind.totp_login.value,
                login_time=now if access_token else None,
                token_expires_at=(now + timedelta(hours=20)) if access_token else None,
                is_active=bool(access_token),
            )
            if login_error is not None:
                # Opt-in recorded (so the user can fix creds), but be honest the
                # first login failed — do not claim success.
                raise ValueError(
                    f"Saved your auto-login preference, but the first Kite "
                    f"login failed: {login_error}"
                )
            return session

        # ── Standard OAuth (request_token) / mock connect path (unchanged). ──
        if request_token:
            session_data = kite_auth.exchange_request_token(request_token)
            access_token = session_data.get("access_token", "")
            broker_user_id = session_data.get("user_id")
        else:
            # Mock connect / credential-only path (no real OAuth round-trip).
            access_token = payload.get("access_token") or f"mock_access_token_{user_id}"
            broker_user_id = client_id or "MOCK001"

        # A bare totp_secret (no password) only records the intent — the gray
        # replay needs a password, so this stays daily_oauth (honest).
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
        """Silently mint a fresh Kite access token.

        Kite has no official unattended refresh, so this works ONLY when the
        user opted into the gray stored-credential path
        (``persistence_mode == "totp_login"`` and ``auto_login_opt_in``): we
        replay the Kite web-login with the encrypted user_id/password/TOTP
        secret, exchange the resulting request_token, persist the new token, and
        return it. Any other session raises :class:`NeedsManualLogin` (honest:
        the user must reconnect in the browser).
        """
        if (
            session.persistence_mode == PersistenceKind.totp_login.value
            and session.auto_login_opt_in
        ):
            # Credential source: when unattended auto-login is enabled, prefer
            # the current env creds (post-reset) over the encrypted DB session,
            # which can hold stale creds after a credential reset. Falls back to
            # the DB secrets field-by-field so a partial env config still works.
            from backend.config import settings as _cfg
            if _cfg.kite_unattended_autologin:
                kite_user_id = _cfg.kite_user_id or session.broker_user_id
                password = _cfg.kite_password or read_secret(session.api_secret)
                totp_secret = _cfg.permanent_token or read_secret(session.totp_secret)
            else:
                kite_user_id = session.broker_user_id
                password = read_secret(session.api_secret)
                totp_secret = read_secret(session.totp_secret)
            if kite_user_id and password and totp_secret:
                # Raises NeedsManualLogin on any failure (bad creds / network).
                request_token = kite_auth.totp_login(
                    kite_user_id, password, totp_secret
                )
                data = kite_auth.exchange_request_token(request_token)
                access_token = data.get("access_token", "")
                now = datetime.now(timezone.utc)
                upsert_broker_session(
                    db,
                    session.user_id,
                    self.broker,
                    access_token=access_token,
                    broker_user_id=data.get("user_id") or kite_user_id,
                    login_time=now,
                    token_expires_at=now + timedelta(hours=20),
                    is_active=True,
                )
                return access_token

        raise NeedsManualLogin(
            "Zerodha requires a fresh login — Kite has no unattended token "
            "refresh unless you enable 'stay connected' (stored credentials). "
            "Reconnect from the app (or enable Dhan for hands-off automation)."
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

    def place_gtt_oco(
        self,
        session: BrokerSession,
        *,
        tradingsymbol: str,
        exchange: str = "NSE",
        transaction_type: str,
        quantity: int,
        stoploss_trigger: float,
        target_trigger: float,
        last_price: Optional[float] = None,
    ) -> dict:
        return kite_orders.place_gtt_oco_order(
            access_token=read_broker_access_token(session) or "mock_token",
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            stoploss_trigger=stoploss_trigger,
            target_trigger=target_trigger,
            last_price=(
                last_price
                if last_price is not None
                else (stoploss_trigger + target_trigger) / 2
            ),
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

    def get_available_cash(self, session: BrokerSession) -> Optional[float]:
        """Live equity cash balance from Kite's margins API (₹). Returns None
        if the shape is unexpected so the funds guard fails open, never closed."""
        margins = kite_portfolio.get_margins(
            read_broker_access_token(session) or "mock_token"
        )
        try:
            return float(margins["equity"]["available"]["live_balance"])
        except (KeyError, TypeError, ValueError):
            return None
