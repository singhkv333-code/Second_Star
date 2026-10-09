"""Copy every company logo into our own table (company_logo_images).

    PYTHONPATH=. .venv/bin/python scripts/sync_logo_store.py [--only SYM,SYM] [--refresh]
    PYTHONPATH=. .venv/bin/python scripts/sync_logo_store.py --from-master /srv/pivot-data/charto_bars.db
    PYTHONPATH=. .venv/bin/python scripts/sync_logo_store.py --nse-csv EQUITY_L.csv --nse-csv SME_EQUITY_L.csv --processes 8
    PYTHONPATH=. .venv/bin/python scripts/sync_logo_store.py --from-master DB --link-only

Sources, in the resolver's order: SharePerks (by ISIN), curated overrides, the
company's own website via logo.dev (fallback=404, so no generated letter
tiles), then the precomputed column. Rows already stored are skipped unless
--refresh. Idempotent; creates the table if it is missing.

--from-master covers the listed equities charto's instrument_master added
beyond company_identity (the full Kite universe: NSE and BSE ids such as
20MICRONS and BSE:ANDHRAPET). Those come from SharePerks by ISIN only — the
master carries the ISIN, so no domain is guessed — and each stored logo is
linked into charto's `instrument_logo` map under the chart's own id, so the
chart, search and screener show it without a code change. SharePerks answers
200 with its own default mark for an ISIN it does not know; that image is
learned by probing an ISIN that cannot exist, and never stored.
"""
from __future__ import annotations

import argparse
import csv
import re
import hashlib
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import certifi  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.database import SessionLocal  # noqa: E402
from backend.market import company_logos as cl, logo_store  # noqa: E402

_CTX = ssl.create_default_context(cafile=certifi.where())
_MAX_BYTES = 512 * 1024
_ISIN_IN_URL = re.compile(r"/logo/([A-Z]{2}[A-Z0-9]{9}\d)/")


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


def master_equities(path: str) -> dict[str, str]:
    """chart id -> ISIN for every listed equity in charto's instrument_master."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {i.upper(): isin for i, isin in con.execute(
            "SELECT id, isin FROM instrument_master WHERE kind='equity' "
            "AND exchange IN ('NSE','BSE') AND isin IS NOT NULL AND isin<>''")}
    finally:
        con.close()


def link_to_chart(path: str, ids: list[str]) -> int:
    """Point charto's logo map at our stored copy for each master id: by id,
    else by ISIN (read back from the stored SharePerks URL), so a company's
    NSE and BSE listings share one image. A row already in company_profile
    wins the chart's map, so only the rest are written."""
    master = master_equities(path)
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT symbol, left(sha, 10), tile, source_url FROM company_logo_images")).fetchall()
    ver = {s: (v, t) for s, v, t, _u in rows}
    by_isin = {}
    for s, _v, _t, u in rows:
        m = _ISIN_IN_URL.search(u or "")
        if m:
            by_isin.setdefault(m.group(1), s)
    con = sqlite3.connect(path, timeout=30)
    try:
        profiled = {s for (s,) in con.execute(
            "SELECT symbol FROM company_profile WHERE logo_url<>''")}
        out = []
        for i in ids:
            src = i if i in ver else by_isin.get(master.get(i, ""))
            if not src or i in profiled:
                continue
            v, t = ver[src]
            out.append((i, logo_store.PUBLIC_PATH.format(symbol=src, ver=v, tile="&tile=1" if t else ""),
                        "equity", "store:shareperks"))
        con.executemany("INSERT OR REPLACE INTO instrument_logo VALUES (?,?,?,?)", out)
        con.commit()
    finally:
        con.close()
    return len(out)


def listed_isins(nse_csvs: list[str]) -> dict[str, str]:
    """Store id -> ISIN for every listed equity we can name without the
    charto master: NSE's own lists (main board + SME) by symbol, then the
    BSE-only companies in company_identity as BSE:<scrip id>. Each ISIN is
    taken once — a dual-listed company is fetched under its NSE symbol."""
    out: dict[str, str] = {}
    for path in nse_csvs:
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                r = {k.strip().upper(): (v or "").strip() for k, v in r.items()}
                if r.get("SYMBOL") and r.get("ISIN NUMBER"):
                    out[r["SYMBOL"].upper()] = r["ISIN NUMBER"]
    seen = set(out.values())
    with SessionLocal() as db:
        for sym, isin in db.execute(text(
                "SELECT upper(verified_symbol), isin FROM company_identity "
                "WHERE verified_exchange='BSE' AND isin<>'' AND verified_symbol<>''")):
            if isin not in seen:
                out[f"BSE:{sym}"] = isin
                seen.add(isin)
    return out


def _fetch_one(item: tuple[str, str]) -> tuple[str, str, tuple[bytes, str] | None]:
    sym, url = item
    return sym, url, fetch(url)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--from-master", default="", help="charto_bars.db holding instrument_master")
    ap.add_argument("--nse-csv", action="append", default=[],
                    help="NSE EQUITY_L / SME_EQUITY_L csv; fetch by ISIN without the master")
    ap.add_argument("--link-only", action="store_true",
                    help="with --from-master: only link stored logos into the chart's map")
    ap.add_argument("--processes", type=int, default=0,
                    help="fetch in this many processes instead of threads")
    args = ap.parse_args()

    if args.link_only:
        ids = sorted(master_equities(args.from_master))
        print(f"linked into the chart's logo map: {link_to_chart(args.from_master, ids)}", flush=True)
        return
    master = master_equities(args.from_master) if args.from_master else (
        listed_isins(args.nse_csv) if args.nse_csv else {})
    with SessionLocal() as db:
        # Only when missing: a least-privilege role (the clean host's) may
        # write rows but not issue DDL, even an IF NOT EXISTS one.
        if db.execute(text("SELECT to_regclass('company_logo_images')")).scalar() is None:
            db.execute(text(logo_store.DDL))
            db.commit()
        syms = [s.strip().upper() for s in args.only.split(",") if s.strip()] \
            or (sorted(master) if master else universe(db))
        have = set() if args.refresh else {
            r[0] for r in db.execute(text("SELECT symbol FROM company_logo_images")).fetchall()}
    todo = [s for s in syms if s not in have]
    print(f"universe {len(syms)}, stored {len(have)}, to fetch {len(todo)}", flush=True)

    placeholder = None
    if master:
        src = {s: (cl._SHAREPERKS_URL.format(isin=master[s]), True) for s in todo if s in master}
        probe = fetch(cl._SHAREPERKS_URL.format(isin="INE000X00000"))
        placeholder = hashlib.sha256(probe[0]).hexdigest() if probe else None
    else:
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

    pool = ProcessPoolExecutor(args.processes) if args.processes else ThreadPoolExecutor(args.workers)
    with pool as ex:
        items = list(src.items())
        work = [(sym, url) for sym, (url, _t) in items]
        for (sym, (url, tile)), (_s, _u, got) in zip(items, ex.map(_fetch_one, work, chunksize=8)):
            done += 1
            if got and hashlib.sha256(got[0]).hexdigest() == placeholder:
                got = None          # SharePerks' default mark, not this company's
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
    if args.from_master:
        print(f"linked into the chart's logo map: {link_to_chart(args.from_master, syms)}", flush=True)


if __name__ == "__main__":
    main()
