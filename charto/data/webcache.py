"""A small persistent cache for the Browser widget's network reads.

Search results, readable pages, Wikipedia articles, site icons and fetched
PDFs used to live in per-process dicts: unbounded, and gone on every backend
deploy. This keeps them in their own SQLite file beside the code (never in
charto_users.db, which is live user state), bounded by total size, with the
oldest-read rows evicted first.

    put(ns, key, value: bytes, ttl)      get(ns, key) -> bytes | None
    put_json(ns, key, obj, ttl)          get_json(ns, key) -> obj | None
    get(..., stale=True) also returns an expired row — the caller's fallback
    when the network fails.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

_PATH = Path(os.environ.get("CHARTO_WEBCACHE_DB") or Path(__file__).parent / "charto_web.db")
_CAP = int(os.environ.get("CHARTO_WEBCACHE_MB") or 256) * 1_000_000
_lock = threading.Lock()
_db: sqlite3.Connection | None = None
_puts = 0


def _conn() -> sqlite3.Connection | None:
    global _db
    if _db is None:
        try:
            _db = sqlite3.connect(str(_PATH), check_same_thread=False, timeout=5)
            _db.execute("PRAGMA journal_mode=WAL")
            _db.execute("PRAGMA synchronous=NORMAL")
            _db.execute("CREATE TABLE IF NOT EXISTS cache (ns TEXT, k TEXT, v BLOB, size INT, "
                        "created REAL, expires REAL, atime REAL, PRIMARY KEY (ns, k))")
            _db.execute("CREATE INDEX IF NOT EXISTS cache_atime ON cache (atime)")
            _db.commit()
        except sqlite3.Error:
            _db = None
    return _db


def get(ns: str, key: str, stale: bool = False) -> bytes | None:
    with _lock:
        db = _conn()
        if db is None:
            return None
        try:
            row = db.execute("SELECT v, expires FROM cache WHERE ns=? AND k=?", (ns, key)).fetchone()
            if not row or (row[1] < time.time() and not stale):
                return None
            db.execute("UPDATE cache SET atime=? WHERE ns=? AND k=?", (time.time(), ns, key))
            db.commit()
            return bytes(row[0])
        except sqlite3.Error:
            return None


def put(ns: str, key: str, value: bytes, ttl: float) -> None:
    global _puts
    now = time.time()
    with _lock:
        db = _conn()
        if db is None:
            return
        try:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,?,?)",
                       (ns, key, sqlite3.Binary(value), len(value), now, now + ttl, now))
            _puts += 1
            if _puts % 50 == 1:
                _evict(db, now)
            db.commit()
        except sqlite3.Error:
            pass


def _evict(db: sqlite3.Connection, now: float) -> None:
    # expired rows a week past their time are no use even as a fallback
    db.execute("DELETE FROM cache WHERE expires < ?", (now - 7 * 86400,))
    total = db.execute("SELECT COALESCE(SUM(size), 0) FROM cache").fetchone()[0]
    if total <= _CAP:
        return
    drop, cut = total - int(_CAP * 0.8), 0
    for ns, k, size in db.execute("SELECT ns, k, size FROM cache ORDER BY atime").fetchall():
        if cut >= drop:
            break
        db.execute("DELETE FROM cache WHERE ns=? AND k=?", (ns, k))
        cut += size


def get_json(ns: str, key: str, stale: bool = False):
    raw = get(ns, key, stale)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def put_json(ns: str, key: str, obj, ttl: float) -> None:
    put(ns, key, json.dumps(obj, separators=(",", ":")).encode(), ttl)


def stats() -> dict:
    with _lock:
        db = _conn()
        if db is None:
            return {"ok": False}
        rows = db.execute("SELECT ns, COUNT(*), SUM(size) FROM cache GROUP BY ns").fetchall()
    return {"ok": True, "cap_mb": _CAP // 1_000_000,
            "namespaces": {ns: {"rows": n, "mb": round((s or 0) / 1e6, 2)} for ns, n, s in rows}}
