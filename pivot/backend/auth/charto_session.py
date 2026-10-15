"""Accept a Charto session on Pivot's API, without merging the two stores.

WHY THIS EXISTS
---------------
Charto and Pivot authenticate differently and always have. Charto issues an
opaque bearer token backed by a `sessions` row in `charto_users.db` (SQLite,
on the VM). Pivot verifies a JWT it minted itself. Until now that did not
matter, because Pivot's API was never deployed — it ran only as a library
imported by `charto/data/execution_bridge.py`. Standing it up on :8000 beside
Charto means one signed-in person must be one signed-in person to both.

The obvious move — migrate Charto's users into Postgres — is the wrong one.
`charto_users.db` is not an auth database: it holds the accounts *and* the
sessions, the saved workspace, the layouts, the conversations, the alerts, the
paper book, the armed strategies and the journal, all through one shared
connection and lock (`ds._users`). Auth cannot be lifted out of it without
lifting the live product out with it. See `docs/DATA_MAP.md`.

So this module shares the SESSION and leaves the STORE alone. Charto stays the
system of record for who is signed in; Pivot reads that answer.

THE TRAP THIS MODULE EXISTS TO AVOID
------------------------------------
The two `users` tables are disjoint and their ids COLLIDE ON DIFFERENT PEOPLE.
Verified 2026-09-05:

    charto_users.db  id 2 -> a real beta user
    pivot_db.users   id 2 -> the operator's own account,
                             and ADMIN_USER_IDS is exactly "2"

Passing Charto's integer id through to Pivot would therefore not merely
mis-attribute a portfolio: it would hand a beta user the admin surface —
ticker control, chat-trace inspection, event simulation. Identity is resolved through explicit Charto-to-Pivot bindings. Email equality
alone is not proof of ownership and must never grant an existing identity.

Reads are strictly read-only. Charto owns `last_seen`; touching it from a
second process would race the writer that already serialises on `_users_lock`.
A Pivot request that never reaches Charto simply does not refresh the session,
which is correct — it did not touch the chart.
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

# Same default as `dataserver.py`, same override, so a box that moves the file
# moves it for both processes at once.
_DEFAULT = Path(__file__).resolve().parents[3] / "charto" / "data" / "charto_users.db"
_PATH = Path(os.environ.get("CHARTO_USERS_DB") or _DEFAULT)

# `dataserver.py` expires a session 30 days after it was CREATED (not after
# last use). Mirrored here rather than imported: importing the dataserver to
# read one constant would drag the whole 16k-line module, its Kite client and
# its bar store into Pivot's process.
_SESSION_TTL = 30 * 24 * 3600

_lock = threading.Lock()
_con: Optional[sqlite3.Connection] = None
_unavailable_logged = False

# WHY A TOKEN WAS REFUSED
# -----------------------
# The routers turn every None from here into a 401, and the app reads a 401
# as "your session is over" and signs the person out. That is right for an
# unknown or expired token and wrong for the other two ways this can fail:
#
#   "store_unavailable"  a store could not be read (Postgres unreachable, the
#                        SQLite file locked). The session may be perfectly
#                        good; the answer is "try again", a 503.
#   "link_required"      the session is good, but its email already belongs to
#                        a Pivot account that has not been linked to it. The
#                        person is signed in; this API refuses them, a 403.
#
# Both used to arrive as 401s: 263 sign-outs in two days for three linked-to-
# nothing accounts in production, and on a machine whose IP the database
# firewall does not allow, a sign-out on every request. The reason is kept
# here, keyed by a hash of the token, for the HTTPException handler in
# backend/main.py to read when it sees the 401. Nothing about who is signed
# in changes: the request is still refused.
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
    """Why this token was last refused, if it was within the last minute and
    for a reason other than being invalid; otherwise None (a plain 401)."""
    if not token:
        return None
    with _failures_lock:
        hit = _failures.get(_key(token))
    if hit is None or time.monotonic() - hit[1] > FAILURE_TTL:
        return None
    return hit[0]


def _connect() -> Optional[sqlite3.Connection]:
    """A read-only handle on Charto's account store, or None.

    `sqlite3.connect()` CREATES whatever it cannot find, so a mispointed
    CHARTO_USERS_DB would otherwise materialise an empty database and report
    every token as invalid — indistinguishable from "your session expired".
    The existence check makes a missing file loud instead of silent.
    """
    global _con, _unavailable_logged
    if _con is not None:
        return _con
    if not _PATH.exists():
        if not _unavailable_logged:
            logger.warning(
                "charto_session: %s does not exist — Charto sessions cannot be "
                "accepted. Set CHARTO_USERS_DB if the store moved.", _PATH,
            )
            _unavailable_logged = True
        return None
    try:
        # mode=ro on a WAL database needs the -shm file to already exist, which
        # it does whenever the dataserver is running. If it is not (a cold box,
        # a copy taken without -wal/-shm), fall back to a normal handle. This
        # module only ever issues SELECT, so the fallback is read-only by
        # construction rather than by flag.
        try:
            _con = sqlite3.connect(
                f"file:{_PATH}?mode=ro", uri=True,
                check_same_thread=False, timeout=5.0,
            )
        except sqlite3.OperationalError:
            _con = sqlite3.connect(str(_PATH), check_same_thread=False, timeout=5.0)
        _con.execute("PRAGMA query_only=ON")
        return _con
    except Exception as exc:  # noqa: BLE001
        if not _unavailable_logged:
            logger.warning("charto_session: cannot open %s: %s", _PATH, exc)
            _unavailable_logged = True
        return None


def resolve_token(token: str) -> Optional[tuple[int, str, Optional[str]]]:
    """`(charto_user_id, email, name)` behind a Charto bearer token, or None.

    Returns None for an unknown token, an expired one, or an unreachable
    store. An unknown and an expired token are not told apart (that would
    tell an attacker which tokens exist); an unreachable store is noted as
    "store_unavailable" (see `failure_reason`), which says nothing about the
    token.
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
        logger.warning("charto_session: lookup failed: %s", exc)
        _note(token, "store_unavailable")
        return None
    if not row:
        return None
    if int(time.time()) - int(row[3]) > _SESSION_TTL:
        # Expired. Charto deletes the row on its own next read; this process
        # does not write to a store it does not own.
        return None
    return int(row[0]), str(row[1]), row[2]


def pivot_user_id_for(email: str, name: Optional[str] = None, *,
                      charto_user_id: Optional[int] = None) -> Optional[int]:
    """Resolve an explicit server-owned binding, never adopt an email match.

    New Charto identities may create an unverified, password-disabled Pivot
    row. Existing Pivot accounts require a separately approved binding.
    The migration creates the binding table; absence fails closed.
    """
    return _bind(email, name, charto_user_id)[0]


def _bind(email: str, name: Optional[str],
          charto_user_id: Optional[int]) -> tuple[Optional[int], Optional[str]]:
    """`pivot_user_id_for`, with the reason when there is no id."""
    if not email or charto_user_id is None or charto_user_id <= 0:
        return None, None
    from sqlalchemy import select, text
    from backend.database import SessionLocal
    from backend.models import User

    try:
        db = SessionLocal()
    except Exception:
        logger.warning("Charto identity linking unavailable for id %s", charto_user_id)
        return None, "store_unavailable"
    try:
        linked = db.execute(text(
            "SELECT pivot_user_id FROM charto_account_links WHERE charto_user_id=:cid"
        ), {"cid": charto_user_id}).scalar_one_or_none()
        if linked is not None:
            user = db.get(User, int(linked))
            return (int(user.id), None) if user is not None and user.is_active else (None, None)
        norm = email.strip().lower()
        user = db.execute(select(User).where(User.email == norm)).scalar_one_or_none()
        if user is not None:
            logger.warning("Charto identity %s requires explicit account linking", charto_user_id)
            return None, "link_required"
        user = User(email=norm, hashed_password="!charto-session",
                    full_name=name or None, is_active=True, is_verified=False)
        db.add(user)
        db.flush()
        db.execute(text(
            "INSERT INTO charto_account_links (charto_user_id, pivot_user_id) "
            "VALUES (:cid, :pid)"
        ), {"cid": charto_user_id, "pid": user.id})
        db.commit()
        return int(user.id), None
    except Exception:
        db.rollback()
        logger.warning("Charto identity linking unavailable for id %s", charto_user_id)
        return None, "store_unavailable"
    finally:
        db.close()


def user_id_from_bearer(token: str) -> Optional[int]:
    """Pivot identity bound to this Charto session, or None."""
    resolved = resolve_token(token)
    if resolved is None:
        return None
    charto_id, email, name = resolved
    pid, reason = _bind(email, name, charto_id)
    _note(token, reason)
    return pid
