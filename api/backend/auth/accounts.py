"""Pivot accounts, as this API sees them.

ONE ACCOUNT SYSTEM
------------------
A Pivot account is created, signed into and recovered in one place: the
account service behind `/auth/*` on the same origin (signup, sign-in, Google,
password reset, email confirmation, sign-out). It keeps the accounts and their
sessions in `users.db` beside the rest of the live account state (the
workspace, layouts, alerts, the paper book), which is why that file is read
here and never written. See `docs/DATA_MAP.md`.

This API issues no credentials. A request carries the account service's
session token; this module answers "which account is that?" and maps the
account to its row in Postgres, where this API keeps that account's data
(conversations, settings, the API's portfolio and workflow tables).

THE MAPPING
-----------
One email is one account. The account service holds every account (Pivot's
API once had sign-ups of its own; those accounts were moved into the account
service with their passwords on 2026-10-15), so an email there is the person,
and a Postgres row with the same email is that person's data:

    1. a recorded link (`charto_account_links`) whose row has the account's
       email: use it;
    2. otherwise the Postgres row with that email: record the link, use it;
    3. otherwise create the row and the link.

A link whose row has a DIFFERENT email is ignored and replaced. Postgres is
shared with development machines, whose account services number their
accounts independently, so a recorded link for account 121 may describe some
other machine's account 121. The email check keeps a production account from
ever landing on a stranger's data that way.

Account ids from the account service are never used as Postgres ids: the two
numberings collide on different people (account 2 is not row 2, and row 2 is
the admin). Only the mapping above turns one into the other.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# The account service's store. Same default and override as the service, so a
# box that moves the file moves it for both.
_DEFAULT = Path(__file__).resolve().parents[3] / "dataserver" / "users.db"
_PATH = Path(os.environ.get("PIVOT_USERS_DB") or _DEFAULT)

# The account service expires a session 30 days after it was created.
_SESSION_TTL = 30 * 24 * 3600

_lock = threading.Lock()
_con: Optional[sqlite3.Connection] = None
_unavailable_logged = False

# WHY A TOKEN WAS REFUSED
# -----------------------
# The routers turn every None from here into a 401, and the app reads a 401
# as "your session is over" and signs the person out. That is right for an
# unknown or expired token and wrong when a store could not be read
# (Postgres unreachable, the SQLite file locked): the session may be fine, and
# the answer is "try again", a 503. The reason is kept here, keyed by a hash
# of the token, for the HTTPException handler in backend/main.py.
FAILURE_TTL = 60.0
_failures: dict[str, tuple[str, float]] = {}
_failures_lock = threading.Lock()


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _note(token: str, reason: Optional[str]) -> None:
    now = time.monotonic()
    with _failures_lock:
        if reason is None:
            _failures.pop(_key(token), None)
        else:
            _failures[_key(token)] = (reason, now)
        if len(_failures) > 2048:
            for k in [k for k, (_, t) in _failures.items() if now - t > FAILURE_TTL]:
                del _failures[k]


def failure_reason(token: str) -> Optional[str]:
    """Why this token was last refused, if within the last minute and for a
    reason other than being invalid; otherwise None (a plain 401)."""
    if not token:
        return None
    with _failures_lock:
        hit = _failures.get(_key(token))
    if hit is None or time.monotonic() - hit[1] > FAILURE_TTL:
        return None
    return hit[0]


def _connect() -> Optional[sqlite3.Connection]:
    """A read-only handle on the account store, or None.

    `sqlite3.connect()` CREATES whatever it cannot find, so a mispointed path
    would otherwise materialise an empty database and report every token as
    invalid. The existence check makes a missing file loud instead.
    """
    global _con, _unavailable_logged
    if _con is not None:
        return _con
    if not _PATH.exists():
        if not _unavailable_logged:
            logger.warning("accounts: %s does not exist; no session can be accepted. "
                           "Set PIVOT_USERS_DB if the store moved.", _PATH)
            _unavailable_logged = True
        return None
    try:
        # mode=ro on a WAL database needs the -shm file, which exists while the
        # account service runs. Without it, a normal handle; this module only
        # issues SELECT, so it stays read-only by construction.
        try:
            _con = sqlite3.connect(f"file:{_PATH}?mode=ro", uri=True,
                                   check_same_thread=False, timeout=5.0)
        except sqlite3.OperationalError:
            _con = sqlite3.connect(str(_PATH), check_same_thread=False, timeout=5.0)
        _con.execute("PRAGMA query_only=ON")
        return _con
    except Exception as exc:  # noqa: BLE001
        if not _unavailable_logged:
            logger.warning("accounts: cannot open %s: %s", _PATH, exc)
            _unavailable_logged = True
        return None


def resolve_token(token: str) -> Optional[tuple[int, str, Optional[str]]]:
    """`(account_id, email, name)` behind a session token, or None.

    An unknown and an expired token are not told apart (that would tell an
    attacker which tokens exist); an unreadable store is noted as
    "store_unavailable", which says nothing about the token.
    """
    if not token:
        return None
    con = _connect()
    if con is None:
        _note(token, "store_unavailable")
        return None
    try:
        with _lock:
            row = con.execute(
                "SELECT u.id, u.email, u.name, s.created "
                "FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token = ?",
                (token,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("accounts: lookup failed: %s", exc)
        _note(token, "store_unavailable")
        return None
    if not row:
        return None
    if int(time.time()) - int(row[3]) > _SESSION_TTL:
        return None
    return int(row[0]), str(row[1]), row[2]


def pivot_user_id_for(email: str, name: Optional[str] = None, *,
                      account_id: Optional[int] = None) -> Optional[int]:
    """The Postgres row for this account (see THE MAPPING), or None."""
    return _bind(email, name, account_id)[0]


# A page load sends ~20 authenticated requests at once and every one asks
# this question; the answer changes only when an account is created or
# deactivated. Without a memo each request spent two Postgres round trips and
# a pooled connection on it, and on 2026-10-15 two users' page loads queued
# behind held connections until the pool timed out. Only a found, active
# account is remembered, so a deactivation takes effect within the TTL and a
# store failure is never cached.
BIND_TTL = 300.0
_bound: dict[tuple[int, str], tuple[int, float]] = {}
_bound_lock = threading.Lock()


def _bind(email: str, name: Optional[str],
          account_id: Optional[int]) -> tuple[Optional[int], Optional[str]]:
    """`pivot_user_id_for`, with the reason when there is no id."""
    if not email or account_id is None or account_id <= 0:
        return None, None
    memo = (int(account_id), email.strip().lower())
    now = time.monotonic()
    with _bound_lock:
        hit = _bound.get(memo)
    if hit is not None and now - hit[1] < BIND_TTL:
        return hit[0], None
    uid, reason = _bind_store(email, name, account_id)
    if uid is not None:
        with _bound_lock:
            if len(_bound) > 4096:
                for k in [k for k, (_, t) in _bound.items() if now - t >= BIND_TTL]:
                    del _bound[k]
            _bound[memo] = (uid, now)
    return uid, reason


def _bind_store(email: str, name: Optional[str],
                account_id: int) -> tuple[Optional[int], Optional[str]]:
    """The mapping as Postgres answers it (see THE MAPPING)."""
    from sqlalchemy import select, text
    from backend.database import SessionLocal
    from backend.models import User

    norm = email.strip().lower()
    try:
        db = SessionLocal()
    except Exception as exc:  # noqa: BLE001
        logger.warning("accounts: database unavailable for account %s: %s", account_id, exc)
        return None, "store_unavailable"

    def by_email():
        # Oldest row first: tolerant of a duplicate left by an old race.
        return db.execute(select(User).where(User.email == norm).order_by(User.id)).scalars().first()

    try:
        linked = db.execute(text(
            "SELECT pivot_user_id FROM charto_account_links WHERE charto_user_id=:aid"
        ), {"aid": account_id}).scalar_one_or_none()
        if linked is not None:
            user = db.get(User, int(linked))
            if user is not None and (user.email or "").strip().lower() == norm:
                result = (int(user.id), None) if user.is_active else (None, None)
                # This fast path bypasses the link-writing try/finally below.
                # Return its connection before answering: one page issues many
                # parallel authenticated requests and otherwise exhausts the pool.
                db.close()
                return result
        user = by_email()
        if user is None:
            # One page load sends ~20 requests at once, and each resolves the
            # account: they all race to create the row. A loser re-reads the
            # winner's row instead of failing the request.
            try:
                db.add(User(email=norm, hashed_password="!pivot-account",
                            full_name=name or None, is_active=True, is_verified=False))
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            user = by_email()
            if user is None:
                raise RuntimeError("account row could not be created")
        uid, active = int(user.id), bool(user.is_active)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        db.close()
        logger.warning("accounts: mapping unavailable for account %s: %s", account_id, exc)
        return None, "store_unavailable"

    # The email decides the account; the link only saves the lookup next
    # time. So writing it is best-effort and never fails the request:
    # parallel requests and other machines sharing this database
    # (development account services number accounts independently) may all
    # be writing it at once. One upsert, no delete-then-insert.
    try:
        db.execute(text(
            "DELETE FROM charto_account_links WHERE pivot_user_id=:pid AND charto_user_id<>:aid"
        ), {"aid": account_id, "pid": uid})
        db.execute(text(
            "INSERT INTO charto_account_links (charto_user_id, pivot_user_id) VALUES (:aid, :pid) "
            "ON CONFLICT (charto_user_id) DO UPDATE SET pivot_user_id = EXCLUDED.pivot_user_id"
        ), {"aid": account_id, "pid": uid})
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.info("accounts: link for account %s not written: %s", account_id, str(exc).splitlines()[0][:200])
    finally:
        db.close()
    return (uid, None) if active else (None, None)


def user_id_from_bearer(token: str) -> Optional[int]:
    """The Postgres user id for this session, or None."""
    resolved = resolve_token(token)
    if resolved is None:
        return None
    account_id, email, name = resolved
    pid, reason = _bind(email, name, account_id)
    _note(token, reason)
    return pid
