"""The ``BrokerConnector`` interface every broker implements.

Design goals:
  - one onboarding + token + trading surface, broker-agnostic;
  - the onboarding UI gets *deep links* (``DeepLinks``) that send the user
    straight to the exact broker page where their input is needed;
  - ``mint_access_token`` is the silent-refresh hook that kills daily
    re-verification where the broker allows it (Dhan rolling RenewToken,
    Fyers refresh token, Kite opt-in TOTP login).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.models import BrokerSession


class PersistenceKind(str, Enum):
    """How a broker session stays alive without daily human re-login."""

    daily_oauth = "daily_oauth"      # re-auth each day (Kite default)
    api_key_mint = "api_key_mint"    # 12-mo key mints a daily token (Dhan PIN+TOTP)
    rolling_renew = "rolling_renew"  # roll a 24h token forward before expiry (Dhan RenewToken)
    refresh_token = "refresh_token"  # silent refresh token (Fyers 15d)
    totp_login = "totp_login"        # gray opt-in: stored creds replay login (Kite)


class NeedsManualLogin(Exception):
    """Raised by :meth:`BrokerConnector.mint_access_token` when no unattended
    path is available and the user must re-authenticate in the browser. The
    scheduler catches this, marks the session, and the UI prompts a reconnect.
    """


@dataclass
class DeepLinks:
    """Links the onboarding UI sends the user straight to — the exact page
    where their input is needed, no hunting through broker menus.

    ``login`` is the OAuth/login redirect (the smoothest path: zero manual
    input). The others are for credential-only steps.
    """

    login: Optional[str] = None         # broker OAuth/login redirect
    app_create: Optional[str] = None    # create the developer app / API key
    api_key_page: Optional[str] = None  # where to copy api_key / api_secret
    totp_setup: Optional[str] = None    # where to enable TOTP / 2FA
    docs: Optional[str] = None          # API docs (help link)


@dataclass
class BrokerInfo:
    """Static metadata the FE uses to render the broker picker + onboarding."""

    id: str                         # "kite" | "dhan" | ...
    name: str                       # "Zerodha Kite"
    logo: str                       # FE asset path, e.g. "/brokers/kite.svg"
    persistence_kind: PersistenceKind
    supports_unattended: bool       # can we keep it alive with no daily human step?
    needs_api_key: bool             # user supplies api_key/secret (Dhan) vs pure OAuth (Kite)
    accent: str = "#000000"         # brand colour for the card
    blurb: str = ""                 # one line shown under the name
    tags: list[str] = field(default_factory=list)  # e.g. ["Full automation", "No daily login"]
    supports_oauth: bool = False    # hosted login redirect (Kite, Fyers); Dhan = False


class BrokerConnector(abc.ABC):
    """Stateless connector: every method takes the ``BrokerSession`` row it
    operates on (tokens are decrypted internally). Implementations live in
    ``brokers/kite.py``, ``brokers/dhan.py``, ... and are registered in
    ``brokers/registry.py``.
    """

    broker: str
    info: BrokerInfo

    # ── mode ────────────────────────────────────────────────────────────────
    @abc.abstractmethod
    def mock_mode(self) -> bool:
        """True when this broker has no real credentials configured and runs
        against deterministic mock data (dev path)."""

    def deep_links(self, *, state: Optional[str] = None) -> DeepLinks:
        """Deep links for the onboarding UI. ``state`` lets OAuth brokers embed
        the signed CSRF/login state in the ``login`` redirect."""
        return DeepLinks()

    # ── onboarding ──────────────────────────────────────────────────────────
    @abc.abstractmethod
    def get_login_url(self, state: str) -> Optional[str]:
        """OAuth/login URL the user is redirected to. ``None`` for
        credential-only brokers that don't have a hosted login redirect."""

    @abc.abstractmethod
    def complete_auth(self, db: Session, user_id: int, payload: dict) -> BrokerSession:
        """Consume an OAuth callback (request_token / auth_code / tokenId) OR
        submitted credentials (api_key/secret/pin/totp), then upsert and return
        the ``BrokerSession`` with all secrets encrypted at rest."""

    # ── token persistence ─────────────────────────────────────────────────────
    @abc.abstractmethod
    def mint_access_token(self, db: Session, session: BrokerSession) -> str:
        """Silently obtain a fresh access token with no user interaction and
        persist it on ``session``. Raise :class:`NeedsManualLogin` when the
        broker offers no unattended path for this session."""

    @abc.abstractmethod
    def verify_token(self, session: BrokerSession) -> bool:
        """Live-check the stored access token against the broker."""

    # ── trading ───────────────────────────────────────────────────────────────
    @abc.abstractmethod
    def place_order(
        self,
        session: BrokerSession,
        *,
        tradingsymbol: str,
        exchange: str = "NSE",
        transaction_type: str,           # BUY | SELL
        quantity: int,
        order_type: str = "MARKET",      # MARKET | LIMIT | SL | SL-M
        price: Optional[float] = None,
        product: str = "CNC",            # CNC | MIS | NRML
        trigger_price: Optional[float] = None,
        tag: str = "pivot",
        variety: str = "regular",
        client_request_id: Optional[str] = None,
    ) -> dict:
        """Place a single order. Returns ``{order_id, status, message, ...}``.
        Normalises broker-specific enums internally (the caller speaks the
        Kite-style vocabulary used across Pivot)."""

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
        raise NotImplementedError(f"{self.broker}: GTT not supported")

    @abc.abstractmethod
    def cancel_order(
        self, session: BrokerSession, order_id: str, variety: str = "regular"
    ) -> dict:
        ...

    # ── data ──────────────────────────────────────────────────────────────────
    def get_holdings(self, session: BrokerSession) -> Any:
        raise NotImplementedError(f"{self.broker}: holdings not supported")

    def get_positions(self, session: BrokerSession) -> Any:
        raise NotImplementedError(f"{self.broker}: positions not supported")
