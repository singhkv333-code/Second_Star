"""Unit tests for the broker-agnostic connection layer.

These exercise the new ``BrokerSession``-backed abstraction that replaced the
old Kite-only connection system. They import ``brokers.*`` + ``models`` +
``sessions`` directly (no full-app import needed) and use the shared sqlite
``db`` fixture from the project conftest.

What's covered:
  - ``registry``: connector lookup by broker id, the supported list, and the
    ``is_supported`` guard (unknown broker -> ``ValueError``).
  - Static ``BrokerInfo`` contracts that the FE picker + scheduler rely on
    (Kite = daily_oauth / attended; Dhan = unattended / needs api key).
  - Kite has NO unattended refresh -> ``mint_access_token`` raises
    ``NeedsManualLogin``.
  - Dhan ``place_order`` on a token-less session takes the deterministic mock
    path and returns a mock order id.
  - ``upsert_broker_session`` -> ``read_broker_access_token`` round-trip works
    whether or not a Fernet cipher key is configured (dev plaintext path).
  - ``User.active_broker_session`` returns the most-recently-updated active
    session when a user has more than one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.brokers import registry
from backend.brokers.base import (
    BrokerConnector,
    NeedsManualLogin,
    PersistenceKind,
)
from backend.brokers.dhan import DhanConnector
from backend.brokers.kite import KiteConnector
from backend.brokers.sessions import (
    read_broker_access_token,
    upsert_broker_session,
)
from backend.models import BrokerSession, User


# ── helpers ──────────────────────────────────────────────────────────────────
def _make_user(db, email: str = "broker_test@pivot.com") -> User:
    """Create + flush a User so we have a real ``users.id`` to hang sessions on
    (mirrors the existing DB-test pattern; flush, not commit, so the fixture's
    rollback cleans up)."""
    user = User(email=email, hashed_password="h")
    db.add(user)
    db.flush()
    return user


# ── registry ─────────────────────────────────────────────────────────────────
def test_registry_get_connector_kite_is_kite_connector():
    conn = registry.get_connector("kite")
    assert isinstance(conn, KiteConnector)
    assert isinstance(conn, BrokerConnector)
    assert conn.broker == "kite"


def test_registry_get_connector_dhan_is_dhan_connector():
    conn = registry.get_connector("dhan")
    assert isinstance(conn, DhanConnector)
    assert isinstance(conn, BrokerConnector)
    assert conn.broker == "dhan"


def test_registry_get_connector_unknown_raises_valueerror():
    with pytest.raises(ValueError):
        registry.get_connector("robinhood")


def test_registry_supported_brokers_list():
    assert registry.SUPPORTED_BROKERS == ["kite", "dhan", "fyers"]


def test_registry_is_supported():
    assert registry.is_supported("kite") is True
    assert registry.is_supported("dhan") is True
    assert registry.is_supported("robinhood") is False


def test_registry_list_connectors_covers_supported():
    connectors = registry.list_connectors()
    brokers = {c.broker for c in connectors}
    assert {"kite", "dhan"}.issubset(brokers)
    assert all(isinstance(c, BrokerConnector) for c in connectors)


# ── static BrokerInfo contracts ──────────────────────────────────────────────
def test_kite_info_is_daily_oauth_and_attended():
    info = KiteConnector.info
    assert info.persistence_kind == PersistenceKind.daily_oauth
    assert info.supports_unattended is False


def test_dhan_info_is_unattended_and_needs_api_key():
    info = DhanConnector.info
    assert info.supports_unattended is True
    assert info.needs_api_key is True


# ── Kite has no unattended refresh ────────────────────────────────────────────
def test_kite_mint_access_token_raises_needs_manual_login(db):
    user = _make_user(db, "kite_mint@pivot.com")
    session = BrokerSession(user_id=user.id, broker="kite", access_token="tok")
    db.add(session)
    db.flush()
    with pytest.raises(NeedsManualLogin):
        KiteConnector().mint_access_token(db, session)


# ── Dhan mock order path ──────────────────────────────────────────────────────
def test_dhan_place_order_mock_path_returns_mock_order_id():
    # No access_token -> read token is "" -> _use_mock True -> deterministic
    # mock order id (no network, no scrip-master download).
    session = BrokerSession(broker="dhan")
    result = DhanConnector().place_order(
        session,
        tradingsymbol="RELIANCE",
        transaction_type="BUY",
        quantity=1,
    )
    assert isinstance(result, dict)
    assert result.get("order_id")
    assert str(result["order_id"]).startswith("MOCK-DHAN-")


# ── Dhan real (non-mock) credential connect ──────────────────────────────────
def test_dhan_complete_auth_access_token_is_real_session(db):
    # The onboarding form posts Client ID + access token to /credentials. That
    # MUST create a REAL, active rolling_renew session (kept alive via
    # RenewToken) — NOT a mock — distinct from the connect-mock dev shortcut.
    user = _make_user(db, "dhan_real@pivot.com")
    session = DhanConnector().complete_auth(
        db, user.id, {"client_id": "1000000001", "access_token": "live-token-xyz"},
    )
    assert session.is_active is True
    assert session.persistence_mode == "rolling_renew"
    assert session.broker_user_id == "1000000001"
    assert read_broker_access_token(session) == "live-token-xyz"


# ── session crypto round-trip ─────────────────────────────────────────────────
def test_upsert_broker_session_access_token_round_trips(db):
    user = _make_user(db, "roundtrip@pivot.com")
    session = upsert_broker_session(
        db, user.id, "dhan", access_token="abc", commit=False,
    )
    # Reads back as plaintext whether or not a cipher key is configured
    # (Fernet layer passes plaintext through transparently in dev).
    assert read_broker_access_token(session) == "abc"


# ── User.active_broker_session recency ────────────────────────────────────────
def test_active_broker_session_picks_most_recently_updated(db):
    user = _make_user(db, "recency@pivot.com")
    now = datetime.now(timezone.utc)

    # Two active sessions; the newer (kite) should win. ``updated_at`` is
    # onupdate-only (NULL on insert), so we set ``login_time`` explicitly to
    # drive the deterministic recency tiebreak in ``active_broker_session``.
    older = BrokerSession(
        user_id=user.id, broker="dhan", is_active=True,
        login_time=now - timedelta(hours=2),
    )
    newer = BrokerSession(
        user_id=user.id, broker="kite", is_active=True,
        login_time=now,
    )
    db.add_all([older, newer])
    db.flush()
    db.refresh(user)

    chosen = user.active_broker_session
    assert chosen is not None
    assert chosen.broker == "kite"


def test_active_broker_session_none_when_no_active(db):
    user = _make_user(db, "noactive@pivot.com")
    inactive = BrokerSession(user_id=user.id, broker="kite", is_active=False)
    db.add(inactive)
    db.flush()
    db.refresh(user)
    assert user.active_broker_session is None
