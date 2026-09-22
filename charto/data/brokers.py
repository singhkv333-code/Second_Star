"""Live broker connections for Charto — pivot's connectors, charto's user plane.

WHY THIS FILE EXISTS, AND WHY IT IS NOT A SECOND SET OF CONNECTORS.

Pivot already knows how to speak to six brokers: the endpoints, the auth
dances, the per-broker order vocabularies, the TOTP mints. That knowledge is
`pivot/backend/brokers/*.py` and there must be exactly one copy of it — a
second implementation of "how do you place an Angel One order" is a second
thing to get wrong on a day the first one was right.

What Pivot's connectors CANNOT be reused wholesale is their storage. They
persist through `backend.brokers.sessions`, which writes `broker_sessions` in
`pivot_db`, keyed by a *Pivot* user id. Charto's users live in
`charto_users.db` and the two tables are disjoint — a charto user_id means a
different human in pivot_db, so writing there would attach a real person's
broker tokens to a stranger's account.

So this module does what `ChartoDataAccessor` does for the DSL evaluator: it
implements the contract the borrowed code calls, against Charto's own store.
The connectors' module globals are rebound to the four functions below, so
`complete_auth`/`mint_access_token` persist into `charto_users.db` while every
byte of broker protocol stays Pivot's.

Secrets are Fernet-encrypted at rest with the same key material Pivot uses
(`BROKER_TOKEN_ENC_KEY`, falling back to `KITE_TOKEN_ENC_KEY`). With no key
configured the columns are plaintext and `connection_status()` says so, because
a silent downgrade to plaintext token storage is not a thing to discover later.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

import dataserver as ds

logger = logging.getLogger("charto.brokers")

PIVOT_ROOT = Path(__file__).resolve().parents[2] / "pivot"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_connections (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL REFERENCES users(id),
  broker        TEXT NOT NULL,
  access_token  TEXT,
  refresh_token TEXT,
  api_key       TEXT,
  api_secret    TEXT,
  totp_secret   TEXT,
  request_token TEXT,
  broker_user_id TEXT,
  login_time    INTEGER,
  token_expires_at INTEGER,
  persistence_mode TEXT NOT NULL DEFAULT 'daily_oauth',
  auto_login_opt_in INTEGER NOT NULL DEFAULT 0,
  -- Explicit per-connection arming for REAL money. Connecting a broker only
  -- grants read + the ability to place; it does not by itself route a single
  -- strategy fill to the exchange. The user flips this on knowingly.
  live_enabled  INTEGER NOT NULL DEFAULT 0,
  is_active     INTEGER NOT NULL DEFAULT 1,
  last_error    TEXT NOT NULL DEFAULT '',
  created       INTEGER NOT NULL,
  updated       INTEGER NOT NULL,
  UNIQUE(user_id, broker)
);

-- Every live order Charto sends, recorded BEFORE the broker call returns so a
-- crash mid-flight leaves evidence rather than a silent gap.
CREATE TABLE IF NOT EXISTS broker_orders (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL,
  broker      TEXT NOT NULL,
  strategy_id INTEGER,
  symbol      TEXT NOT NULL,
  side        TEXT NOT NULL,
  quantity    REAL NOT NULL,
  order_type  TEXT NOT NULL DEFAULT 'MARKET',
  price       REAL,
  status      TEXT NOT NULL DEFAULT 'sent',
  order_id    TEXT NOT NULL DEFAULT '',
  message     TEXT NOT NULL DEFAULT '',
  idem_key    TEXT NOT NULL DEFAULT '',
  created     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_broker_orders_user ON broker_orders(user_id, created);
-- The dedupe key for "this strategy, this bar, this side". A UNIQUE index is
-- the only thing that actually stops a double-send across two threads; the
-- application check above it is just a cheaper first pass.
CREATE UNIQUE INDEX IF NOT EXISTS ux_broker_orders_idem
  ON broker_orders(idem_key) WHERE idem_key <> '';
"""

_ready = False
_init_lock = threading.Lock()

_SECRET_COLS = ("access_token", "refresh_token", "api_key", "api_secret",
                "totp_secret", "request_token")
_COLS = _SECRET_COLS + ("broker_user_id", "login_time", "token_expires_at",
                        "persistence_mode", "auto_login_opt_in", "live_enabled",
                        "is_active", "last_error")

# Added after the table shipped; same ALTER-not-declare shape strategies.py
# uses for `last_eval_bar`.
_LIVE_COL = ("ALTER TABLE broker_connections ADD COLUMN "
             "live_enabled INTEGER NOT NULL DEFAULT 0")


def _db():
    return ds._users


def init_db() -> None:
    global _ready
    with _init_lock:
        if _ready:
            return
        with ds._users_lock:
            _db().executescript(_SCHEMA)
            try:
                _db().execute(_LIVE_COL)
            except sqlite3.OperationalError:
                pass                      # already there
            _db().commit()
        _ready = True


# ── at-rest crypto ───────────────────────────────────────────────────────────

_cipher: Any = None
_cipher_loaded = False


def _get_cipher():
    """Fernet cipher from the same env var Pivot uses, or None (plaintext)."""
    global _cipher, _cipher_loaded
    if _cipher_loaded:
        return _cipher
    _cipher_loaded = True
    key = (os.environ.get("BROKER_TOKEN_ENC_KEY")
           or os.environ.get("KITE_TOKEN_ENC_KEY") or "").strip()
    if not key:
        logger.warning("broker tokens stored PLAINTEXT — set BROKER_TOKEN_ENC_KEY")
        return None
    try:
        from cryptography.fernet import Fernet
        _cipher = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:  # noqa: BLE001
        logger.error("broker token encryption unavailable: %s", exc)
        _cipher = None
    return _cipher


def _enc(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    c = _get_cipher()
    if c is None:
        return str(value)
    return c.encrypt(str(value).encode()).decode()


def encryption_on() -> bool:
    return _get_cipher() is not None


# ── the contract Pivot's connectors call ─────────────────────────────────────
#
# These four names replace `backend.brokers.sessions`' versions inside each
# connector module. Same signatures, charto's store.

def read_secret(value: Optional[str]) -> str:
    """Decrypt an at-rest column. Tolerates plaintext rows written before a key
    was configured, so turning encryption on does not orphan live sessions."""
    if not value:
        return ""
    c = _get_cipher()
    if c is None:
        return str(value)
    try:
        return c.decrypt(str(value).encode()).decode()
    except Exception:
        return str(value)      # legacy plaintext


def read_broker_access_token(session) -> str:
    if session is None:
        return ""
    return read_secret(getattr(session, "access_token", None))


class Row:
    """Duck-type of Pivot's ``BrokerSession`` ORM row.

    The connectors only ever read attributes off it (`.access_token`,
    `.api_key`, `.broker_user_id`, `.persistence_mode`, `.user_id`), so a plain
    attribute bag satisfies them without dragging SQLAlchemy into Charto.
    """

    __slots__ = ("id", "user_id", "broker") + _COLS

    def __init__(self, **kw):
        for f in self.__slots__:
            setattr(self, f, kw.get(f))

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"<Row {self.broker} user={self.user_id} active={self.is_active}>"


def get_broker_session(db, user_id: int, broker: str) -> Optional[Row]:
    """Signature mirrors Pivot's; ``db`` is ignored (Charto has one store)."""
    return _load(int(user_id), broker)


def _load(user_id: int, broker: str) -> Optional[Row]:
    init_db()
    with ds._users_lock:
        cur = _db().execute(
            "SELECT id, user_id, broker, " + ", ".join(_COLS) +
            " FROM broker_connections WHERE user_id=? AND broker=?",
            (int(user_id), broker))
        row = cur.fetchone()
    if not row:
        return None
    keys = ("id", "user_id", "broker") + _COLS
    return Row(**dict(zip(keys, row)))


def upsert_broker_session(db, user_id: int, broker: str, *, commit: bool = True,
                          **fields) -> Row:
    """Create or update a (user, broker) connection, encrypting secrets.

    ``db``/``commit`` exist only so Pivot's connectors can call this unchanged.
    """
    init_db()
    now = int(time.time())
    sets, vals = [], []
    for key, value in fields.items():
        if key not in _COLS:
            continue
        if key in _SECRET_COLS:
            value = _enc(value)
        elif key in ("login_time", "token_expires_at") and value is not None:
            # Connectors hand us aware datetimes; Charto stores epoch seconds.
            value = int(value.timestamp()) if hasattr(value, "timestamp") else int(value)
        elif key in ("auto_login_opt_in", "is_active", "live_enabled") \
                and value is not None:
            value = 1 if value else 0
        sets.append(f"{key}=?")
        vals.append(value)

    with ds._users_lock:
        _db().execute(
            "INSERT INTO broker_connections (user_id, broker, created, updated) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id, broker) DO NOTHING",
            (int(user_id), broker, now, now))
        if sets:
            _db().execute(
                f"UPDATE broker_connections SET {', '.join(sets)}, updated=? "
                "WHERE user_id=? AND broker=?",
                (*vals, now, int(user_id), broker))
        _db().commit()
    return _load(int(user_id), broker)


# ── borrowing Pivot's connectors ─────────────────────────────────────────────

_state: dict[str, Any] = {"ok": False, "error": "", "mods": None}
_import_lock = threading.Lock()

# The connector modules whose storage calls get rebound to this module.
_CONNECTOR_MODULES = ("kite", "dhan", "fyers", "groww", "angelone", "upstox")


def _ensure_pivot() -> dict[str, Any]:
    """Import Pivot's broker registry once and rebind its storage layer.

    The rebinding is the whole trick and it is deliberate: each connector did
    `from backend.brokers.sessions import upsert_broker_session, ...` at import
    time, which copied those functions into the connector module's own globals.
    Replacing the attribute on the module therefore redirects every call the
    connector makes, without editing Pivot's source or shadowing its package.
    """
    if _state["ok"] or _state["error"]:
        return _state
    with _import_lock:
        if _state["ok"] or _state["error"]:
            return _state
        try:
            import sys
            root = str(PIVOT_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            # Pivot's config is imported by the connectors; it needs these to
            # construct Settings even though Charto never touches its DB.
            os.environ.setdefault("DATABASE_URL", "postgresql://unused/unused")
            os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
            os.environ.setdefault("JWT_SECRET_KEY", "charto-local")

            from backend.brokers import registry as _registry

            import importlib
            for name in _CONNECTOR_MODULES:
                try:
                    mod = importlib.import_module(f"backend.brokers.{name}")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("broker %s unavailable: %s", name, exc)
                    continue
                for fn in (upsert_broker_session, read_secret,
                           read_broker_access_token, get_broker_session):
                    if hasattr(mod, fn.__name__):
                        setattr(mod, fn.__name__, fn)

            _state["mods"] = {"registry": _registry}
            _state["ok"] = True
        except Exception as exc:  # noqa: BLE001
            _state["error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("broker layer unavailable: %s", _state["error"])
        return _state


def available() -> tuple[bool, str]:
    st = _ensure_pivot()
    return bool(st["ok"]), str(st["error"] or "")


def _registry():
    st = _ensure_pivot()
    if not st["ok"]:
        raise RuntimeError(st["error"] or "broker layer unavailable")
    return st["mods"]["registry"]


def connector(broker: str):
    return _registry().get_connector(broker)


def supported() -> list[str]:
    return list(_registry().SUPPORTED_BROKERS)


# ── what the UI reads ────────────────────────────────────────────────────────

def _expiry_state(row: Optional[Row]) -> tuple[bool, Optional[int]]:
    """(connected, seconds_until_expiry). A token past its wall-clock expiry is
    reported as disconnected even though the row still holds it, because that
    is the honest answer the reconnect button is keyed on."""
    if row is None or not row.is_active or not row.access_token:
        return False, None
    exp = row.token_expires_at
    if not exp:
        return True, None
    left = int(exp) - int(time.time())
    return (left > 0), left


def catalog(user_id: int) -> list[dict]:
    """Every broker + this user's connection state. One call powers the picker."""
    out = []
    for broker in supported():
        c = connector(broker)
        row = _load(int(user_id), broker) if user_id else None
        connected, left = _expiry_state(row)
        info = c.info
        links = c.deep_links()
        out.append({
            "id": info.id,
            "name": info.name,
            "logo": info.logo,
            "accent": info.accent,
            "blurb": info.blurb,
            "tags": list(info.tags),
            "needs_api_key": bool(info.needs_api_key),
            "supports_oauth": bool(getattr(info, "supports_oauth", False)),
            "supports_unattended": bool(info.supports_unattended),
            "persistence_kind": info.persistence_kind.value,
            "connected": connected,
            "expires_in": left,
            # `needs_reconnect` is the single flag the UI's amber banner keys
            # on: a session we HAVE but cannot renew ourselves.
            "needs_reconnect": bool(row is not None and row.access_token
                                    and not connected),
            "live_enabled": bool(row.live_enabled) if row else False,
            "broker_user_id": row.broker_user_id if row else None,
            "last_error": (row.last_error if row else "") or "",
            "links": {"app_create": links.app_create,
                      "api_key_page": links.api_key_page,
                      "totp_setup": links.totp_setup,
                      "docs": links.docs},
            "fields": _fields_for(broker),
        })
    return out


def _fields_for(broker: str) -> list[dict]:
    """The MINIMUM the user must type, per broker. OAuth brokers ask nothing.

    Kept here rather than in the UI so "what does Angel One need" has one
    answer that the backend and the form cannot drift apart on.
    """
    if broker in ("kite", "upstox", "fyers"):
        return []
    if broker == "groww":
        return [
            {"name": "api_key", "label": "API key", "type": "password"},
            {"name": "totp_secret", "label": "TOTP secret", "type": "password"},
        ]
    if broker == "angelone":
        return [
            {"name": "api_key", "label": "API key", "type": "password"},
            {"name": "client_id", "label": "Client code", "type": "text"},
            {"name": "password", "label": "PIN", "type": "password"},
            {"name": "totp_secret", "label": "TOTP secret", "type": "password"},
        ]
    if broker == "dhan":
        return [
            {"name": "client_id", "label": "Client ID", "type": "text"},
            {"name": "api_secret", "label": "PIN", "type": "password"},
            {"name": "totp_secret", "label": "TOTP secret", "type": "password"},
        ]
    return []


def connect(user_id: int, broker: str, payload: dict) -> dict:
    """Credential connect (or OAuth callback completion)."""
    c = connector(broker)
    row = c.complete_auth(None, int(user_id), dict(payload))
    connected, left = _expiry_state(row if isinstance(row, Row)
                                    else _load(int(user_id), broker))
    return {"broker": broker, "connected": connected, "expires_in": left}


def reconnect(user_id: int, broker: str) -> dict:
    """The one-tap daily reconnect. Succeeds silently where the broker allows
    an unattended mint; otherwise returns the login URL to bounce through."""
    from backend.brokers.base import NeedsManualLogin  # noqa: PLC0415

    c = connector(broker)
    row = _load(int(user_id), broker)
    if row is None:
        return {"ok": False, "reason": "not connected", "needs_login": True}
    try:
        c.mint_access_token(None, row)
    except NeedsManualLogin as exc:
        _note_error(int(user_id), broker, str(exc))
        return {"ok": False, "reason": str(exc), "needs_login": True}
    except Exception as exc:  # noqa: BLE001
        _note_error(int(user_id), broker, str(exc))
        return {"ok": False, "reason": str(exc), "needs_login": True}
    _note_error(int(user_id), broker, "")
    row = _load(int(user_id), broker)
    connected, left = _expiry_state(row)
    return {"ok": True, "connected": connected, "expires_in": left}


def disconnect(user_id: int, broker: str) -> dict:
    init_db()
    with ds._users_lock:
        _db().execute("DELETE FROM broker_connections WHERE user_id=? AND broker=?",
                      (int(user_id), broker))
        _db().commit()
    return {"ok": True}


def _note_error(user_id: int, broker: str, message: str) -> None:
    init_db()
    with ds._users_lock:
        _db().execute(
            "UPDATE broker_connections SET last_error=?, updated=? "
            "WHERE user_id=? AND broker=?",
            (message[:300], int(time.time()), int(user_id), broker))
        _db().commit()


# ── the live order path ──────────────────────────────────────────────────────

def active_connection(user_id: int) -> Optional[Row]:
    """The broker this user's live orders route to: the most recently updated
    connection that still holds an unexpired token."""
    init_db()
    with ds._users_lock:
        rows = _db().execute(
            "SELECT broker FROM broker_connections WHERE user_id=? AND is_active=1 "
            "AND access_token IS NOT NULL ORDER BY updated DESC",
            (int(user_id),)).fetchall()
    for (broker,) in rows:
        row = _load(int(user_id), broker)
        connected, _ = _expiry_state(row)
        if connected:
            return row
    return None


class LiveReject(Exception):
    """The broker refused, or there was no live path. Carries the reason."""


def place_live(user_id: int, symbol: str, side: str, quantity: int, *,
               price: Optional[float] = None, order_type: str = "MARKET",
               product: str = "CNC", strategy_id: Optional[int] = None,
               idem_key: str = "") -> dict:
    """Place ONE real order at the user's connected broker.

    Records the intent BEFORE the network call. If the process dies mid-flight
    the row survives as `sent` with no order id, which is a visible anomaly —
    the alternative (write after the call) loses the evidence exactly when an
    order may have reached the exchange.

    `idem_key` is enforced by a UNIQUE index, so two threads racing the same
    (strategy, bar, side) cannot both reach the broker.
    """
    init_db()
    row = active_connection(int(user_id))
    if row is None:
        raise LiveReject("no connected broker")

    now = int(time.time())
    try:
        with ds._users_lock:
            cur = _db().execute(
                "INSERT INTO broker_orders (user_id, broker, strategy_id, symbol, "
                "side, quantity, order_type, price, status, idem_key, created) "
                "VALUES (?,?,?,?,?,?,?,?,'sent',?,?)",
                (int(user_id), row.broker, strategy_id, symbol.upper(),
                 side.upper(), float(quantity), order_type, price, idem_key, now))
            _db().commit()
            local_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise LiveReject("duplicate order suppressed (same bar already sent)")

    try:
        res = connector(row.broker).place_order(
            row,
            tradingsymbol=symbol.upper(),
            exchange="NSE",
            transaction_type=side.upper(),
            quantity=int(quantity),
            order_type=order_type,
            price=price,
            product=product,
            tag=f"pivot-s{strategy_id or 0}",
        )
    except Exception as exc:  # noqa: BLE001
        _finish_order(local_id, "error", "", str(exc)[:300])
        raise LiveReject(f"{row.broker}: {exc}") from exc

    status = str(res.get("status") or "")
    order_id = str(res.get("order_id") or "")
    if status == "error" or not order_id:
        msg = str(res.get("message") or "broker returned no order id")
        _finish_order(local_id, "error", order_id, msg[:300])
        raise LiveReject(f"{row.broker}: {msg}")

    _finish_order(local_id, "placed", order_id, "")
    return {"broker": row.broker, "order_id": order_id, "status": "placed"}


def _finish_order(local_id: int, status: str, order_id: str, message: str) -> None:
    with ds._users_lock:
        _db().execute(
            "UPDATE broker_orders SET status=?, order_id=?, message=? WHERE id=?",
            (status, order_id, message, int(local_id)))
        _db().commit()


def recent_orders(user_id: int, limit: int = 50) -> list[dict]:
    init_db()
    with ds._users_lock:
        rows = _db().execute(
            "SELECT broker, strategy_id, symbol, side, quantity, order_type, "
            "price, status, order_id, message, created FROM broker_orders "
            "WHERE user_id=? ORDER BY created DESC LIMIT ?",
            (int(user_id), int(limit))).fetchall()
    keys = ("broker", "strategy_id", "symbol", "side", "quantity", "order_type",
            "price", "status", "order_id", "message", "created")
    return [dict(zip(keys, r)) for r in rows]
