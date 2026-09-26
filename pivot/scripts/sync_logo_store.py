"""Copy every company logo into our own table (company_logo_images).

    PYTHONPATH=. .venv/bin/python scripts/sync_logo_store.py [--only SYM,SYM] [--refresh]

Sources, in the resolver's order: SharePerks (by ISIN), curated overrides, the
company's own website via logo.dev (fallback=404, so no generated letter
tiles), then the precomputed column. Rows already stored are skipped unless
--refresh. Idempotent; creates the table if it is missing.
"""
from __future__ import annotations

import argparse
import hashlib
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import certifi  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.database import SessionLocal  # noqa: E402
from backend.market import company_logos as cl, logo_store  # noqa: E402

_CTX = ssl.create_default_context(cafile=certifi.where())
_MAX_BYTES = 512 * 1024


def universe(db) -> list[str]:
    rows = db.execute(text(
        "SELECT DISTINCT upper(verified_symbol) FROM company_identity "
        "WHERE verified_symbol IS NOT NULL AND verified_symbol <> ''")).fetchall()
    return sorted(r[0] for r in rows)


def sources(symbols: list[str]) -> dict[str, tuple[str, bool]]:
    """symbol -> (source URL, is SharePerks tile)."""
    out: dict[str, tuple[str, bool]] = {}
    rest = []
    for s in symbols:
        sp = cl.shareperks_logo_url(s)
        if sp:
            out[s] = (sp, True)
        else:
            rest.append(s)
    # The rest through the batched ladder, with our own store switched off so
    # the resolver returns the upstream URL rather than our copy of it.
    orig = logo_store.path_for
    logo_store.path_for = lambda _s: None
    try:
        for i in range(0, len(rest), 200):
            for s, url in cl.get_logo_urls(rest[i:i + 200]).items():
                if url and url.startswith("http"):
                    out[s] = (url, "shareperks.in" in url)
    finally:
        logo_store.path_for = orig
    return out


def fetch(url: str) -> tuple[bytes, str] | None:
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 PivotLogoSync"})
            with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
                body = r.read(_MAX_BYTES + 1)
            if not ctype.startswith("image/") or not body or len(body) > _MAX_BYTES:
                return None
            return body, ctype
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:     # logo.dev: 500 requests/min
                time.sleep(15)
                continue
            return None
        except Exception:  # noqa: BLE001
            if attempt == 1:
                time.sleep(1)
                continue
            return None
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    with SessionLocal() as db:
        db.execute(text(logo_store.DDL))
        db.commit()
        syms = [s.strip().upper() for s in args.only.split(",") if s.strip()] or universe(db)
        have = set() if args.refresh else {
            r[0] for r in db.execute(text("SELECT symbol FROM company_logo_images")).fetchall()}
    todo = [s for s in syms if s not in have]
    print(f"universe {len(syms)}, stored {len(have)}, to fetch {len(todo)}", flush=True)

    src = sources(todo)
    print(f"sources found for {len(src)}", flush=True)

    t0, done, stored = time.time(), 0, 0
    batch: list[dict] = []

    def flush() -> None:
        nonlocal batch
        if not batch:
            return
        with SessionLocal() as db:
            db.execute(text(
                "INSERT INTO company_logo_images (symbol, sha, content_type, body, source_url, tile, fetched_at) "
                "VALUES (:symbol, :sha, :content_type, :body, :source_url, :tile, now()) "
                "ON CONFLICT (symbol) DO UPDATE SET sha = EXCLUDED.sha, "
                "content_type = EXCLUDED.content_type, body = EXCLUDED.body, "
                "source_url = EXCLUDED.source_url, tile = EXCLUDED.tile, fetched_at = now()"),
                batch)
            db.commit()
        batch = []

    with ThreadPoolExecutor(args.workers) as ex:
        items = list(src.items())
        for (sym, (url, tile)), got in zip(items, ex.map(lambda kv: fetch(kv[1][0]), items)):
            done += 1
            if got:
                body, ctype = got
                batch.append({"symbol": sym, "sha": hashlib.sha256(body).hexdigest(),
                              "content_type": ctype, "body": body,
                              "source_url": url, "tile": tile})
                stored += 1
            if len(batch) >= 200:
                flush()
            if done % 500 == 0:
                print(f"  {done}/{len(items)} fetched, {stored} stored, {time.time() - t0:.0f}s", flush=True)
    flush()
    print(f"done: {stored} stored of {len(src)} sources in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
