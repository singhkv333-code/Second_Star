"""Company logos served from our own database.

Every logo used to be an <img> pointing at a third party (SharePerks, logo.dev),
so each browser paid a DNS + TLS + fetch to another host per logo, and a cold
table drew letters until they landed. The images are tiny (SVGs of ~0.3-2 KB),
so they live in one Postgres table keyed by symbol and are served from our API
with immutable caching: the first view fetches each once, every view after
that is the browser cache.

`scripts/sync_logo_store.py` fills the table. Everything here degrades to
"not stored" (the caller falls back to the third-party URL), never an error.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS company_logo_images (
    symbol       TEXT PRIMARY KEY,
    sha          CHAR(64)    NOT NULL,
    content_type TEXT        NOT NULL,
    body         BYTEA       NOT NULL,
    source_url   TEXT        NOT NULL,
    tile         BOOLEAN     NOT NULL DEFAULT FALSE,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# Both origins reach the API under this path: nginx on the VM rewrites
# /api/pivot/* to /api/*, and main.py registers the same handler at the alias
# for the local Next proxy (which forwards /api/* untouched).
PUBLIC_PATH = "/api/pivot/companies/logo/{symbol}?v={ver}{tile}"

_VERSIONS_TTL_S = 600
_lock = threading.Lock()
_versions: dict[str, tuple[str, bool]] = {}     # symbol -> (sha prefix, tile)
_versions_at = 0.0
_bodies: dict[str, tuple[bytes, str, str]] = {}  # symbol -> (body, type, sha)


def _session():
    from backend.database import SessionLocal

    return SessionLocal()


def _load_versions() -> dict[str, tuple[str, bool]]:
    """symbol -> version for every stored logo: one small query, cached."""
    global _versions, _versions_at
    now = time.monotonic()
    if now - _versions_at < _VERSIONS_TTL_S:
        return _versions
    try:
        with _session() as db:
            rows = db.execute(text(
                "SELECT symbol, left(sha, 10), tile FROM company_logo_images")).fetchall()
        fresh = {r[0]: (r[1], bool(r[2])) for r in rows}
    except Exception as exc:  # noqa: BLE001 — table missing / DB down: not stored
        logger.debug("[logo_store] versions unavailable: %s", exc)
        fresh = _versions
    with _lock:
        _versions, _versions_at = fresh, now
    return fresh


def path_for(symbol: str) -> Optional[str]:
    """Our own URL for a stored logo, versioned so it can be cached forever."""
    sym = (symbol or "").strip().upper()
    hit = _load_versions().get(sym)
    if not hit:
        return None
    ver, tile = hit
    return PUBLIC_PATH.format(symbol=sym, ver=ver, tile="&tile=1" if tile else "")


def image(symbol: str) -> Optional[tuple[bytes, str, str]]:
    """(body, content_type, sha) for a stored logo, from memory after first read."""
    sym = (symbol or "").strip().upper()
    with _lock:
        hit = _bodies.get(sym)
    if hit:
        return hit
    try:
        with _session() as db:
            row = db.execute(text(
                "SELECT body, content_type, sha FROM company_logo_images WHERE symbol = :s"),
                {"s": sym}).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[logo_store] read failed for %s: %s", sym, exc)
        return None
    if not row:
        return None
    got = (bytes(row[0]), row[1], row[2])
    with _lock:
        _bodies[sym] = got     # ~3,200 logos x ~1 KB: the whole set fits
    return got
