#!/usr/bin/env python3
"""
scrape_tijori_revmix_marketshare.py — enrich our companies with Tijori's
REVENUE MIX and MARKET SHARE data, and nothing else.

What it does
------------
1. Reads our enriched universe from `enrich.company_profile` (the ~5.8k rows
   that carry an NSE `ticker`).
2. For each, resolves the matching Tijori company via its public search
   (`/api/v1/ind/company_search/?q=`) and CONFIRMS the match by fetching the
   candidate page and checking the Tijori `symbol` == our `ticker` and/or a
   high name-similarity score. Ambiguous rows are flagged, never guessed.
3. Scrapes ONLY:
     • market share  — the in-page `ms-charts` JSON blob (name / methodology /
       sample_size / full time series).
     • revenue mix   — every `rmix_graph_block` (breakdown title + current mix)
       plus its full historic series from `/api/rmix/historic/graph/{chart_id}`.
   No prices, no ratios, no financials — just the two requested surfaces.
4. Upserts into `enrich.tijori_enrichment`, keyed by our `sc_id`, storing both
   names so the alignment is auditable.

Concurrency: a thread pool (default 8 workers) fans out the scrape; DB writes go
through an 8-connection psycopg2 pool ("8 connections, max").

Politeness: per-request timeout + retry + small jitter. robots.txt allows all,
but we stay a good citizen.

Usage
-----
  python scrape_tijori_revmix_marketshare.py --create-table
  python scrape_tijori_revmix_marketshare.py --sample 25            # test + ETA
  python scrape_tijori_revmix_marketshare.py --sample 25 --dry-run  # no inserts
  python scrape_tijori_revmix_marketshare.py --full                 # whole universe
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import unescape

import psycopg2
import psycopg2.pool
import requests

BASE = "https://www.tijorifinance.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
REQ_TIMEOUT = 15
MAX_CANDIDATES = 4          # how many search hits we'll verify per company
NAME_ACCEPT = 0.92          # name-only acceptance when symbol doesn't confirm

# ── env / DB ────────────────────────────────────────────────────────────────

def load_enrich_dsn() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(here, "..", ".env")
    for line in open(env_path):
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "ENRICH_DSN":
            return v.strip()
    raise SystemExit("ENRICH_DSN not found in pivot/.env")


DDL = """
CREATE TABLE IF NOT EXISTS enrich.tijori_enrichment (
    sc_id              text PRIMARY KEY,
    source             text DEFAULT 'tijori',   -- data provider (future multi-source)
    ticker             text NOT NULL,
    our_name           text,
    tijori_slug        text,
    tijori_company_id  integer,
    tijori_name        text,
    match_kind         text,          -- 'symbol' | 'name' | 'unmatched'
    match_score        numeric,       -- 0..1 name similarity
    dup_of_sc_id       text,          -- set when another sc_id already owns this Tijori company
    has_revenue_mix    boolean DEFAULT false,
    has_market_share   boolean DEFAULT false,
    revenue_mix        jsonb,         -- [{breakdown, chart_id, current:[[seg,val]], segments:[{fieldname, series:[[ts,val]]}]}]
    market_share       jsonb,         -- [{name, id, methodology, sample_size, series:[[ts,val]]}]
    status             text,          -- 'ok' | 'duplicate' | 'unmatched' | 'no_data' | 'error:<msg>'
    scraped_at         timestamptz
);
"""

# Applied on --create-table so an EXISTING table gains the new columns/guards.
MIGRATE = [
    "ALTER TABLE enrich.tijori_enrichment ADD COLUMN IF NOT EXISTS source text DEFAULT 'tijori'",
    "ALTER TABLE enrich.tijori_enrichment ADD COLUMN IF NOT EXISTS dup_of_sc_id text",
    # One 'ok' payload row per Tijori company → the company's data is stored ONCE.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_tijori_company_owner "
    "ON enrich.tijori_enrichment (tijori_company_id) "
    "WHERE status='ok' AND tijori_company_id IS NOT NULL",
]

UPSERT = """
INSERT INTO enrich.tijori_enrichment
  (sc_id, source, ticker, our_name, tijori_slug, tijori_company_id, tijori_name,
   match_kind, match_score, dup_of_sc_id, has_revenue_mix, has_market_share,
   revenue_mix, market_share, status, scraped_at)
VALUES (%(sc_id)s, 'tijori', %(ticker)s, %(our_name)s, %(tijori_slug)s, %(tijori_company_id)s,
        %(tijori_name)s, %(match_kind)s, %(match_score)s, %(dup_of_sc_id)s,
        %(has_revenue_mix)s, %(has_market_share)s, %(revenue_mix)s, %(market_share)s,
        %(status)s, %(scraped_at)s)
ON CONFLICT (sc_id) DO UPDATE SET
   ticker=EXCLUDED.ticker, our_name=EXCLUDED.our_name, tijori_slug=EXCLUDED.tijori_slug,
   tijori_company_id=EXCLUDED.tijori_company_id, tijori_name=EXCLUDED.tijori_name,
   match_kind=EXCLUDED.match_kind, match_score=EXCLUDED.match_score,
   dup_of_sc_id=EXCLUDED.dup_of_sc_id,
   has_revenue_mix=EXCLUDED.has_revenue_mix, has_market_share=EXCLUDED.has_market_share,
   revenue_mix=EXCLUDED.revenue_mix, market_share=EXCLUDED.market_share,
   status=EXCLUDED.status, scraped_at=EXCLUDED.scraped_at;
"""

# ── name normalisation / similarity ─────────────────────────────────────────

_SUFFIX = re.compile(
    r"\b(ltd|limited|ltd\.|the|company|co|corp|corporation|india|indian|"
    r"industries|enterprises|&|and)\b",
    re.I,
)


def norm_name(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[.\-,/()]", " ", s)
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_score(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_name(a), norm_name(b)).ratio()


# ── HTTP (thread-local session) ─────────────────────────────────────────────

# Cross-company dedup: the FIRST sc_id to resolve a given Tijori company owns
# its scraped payload; later sc_ids that resolve to the same company become
# lightweight duplicate refs (no re-scrape, no second copy of the data). Single
# process + lock makes this authoritative; the partial unique index is a
# belt-and-suspenders guard for re-runs.
_claimed: dict[int, str] = {}      # tijori_company_id -> owning sc_id
_claimed_lock = threading.Lock()

_tls = threading.local()


def session() -> requests.Session:
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Referer": BASE + "/"})
        _tls.s = s
    return s


def get(url: str, tries: int = 3) -> requests.Response | None:
    for i in range(tries):
        try:
            r = session().get(url, timeout=REQ_TIMEOUT)
            if r.status_code == 200:
                return r
            if r.status_code in (401, 403, 404):
                return r  # definitive, don't retry
        except requests.RequestException:
            pass
        time.sleep(0.4 * (i + 1) + random.random() * 0.3)
    return None


# ── Tijori parsing ──────────────────────────────────────────────────────────

def blob(html: str, blob_id: str):
    m = re.search(
        r'<script id="%s" type="application/json">(.*?)</script>' % re.escape(blob_id),
        html, re.S,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return None


def search_tijori(q: str) -> list[dict]:
    r = get(f"{BASE}/api/v1/ind/company_search/?q={requests.utils.quote(q)}")
    if not r or r.status_code != 200:
        return []
    try:
        data = json.loads(r.text)
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict) and d.get("type") == "companies"]


def parse_market_share(html: str) -> list[dict]:
    ms = blob(html, "ms-charts") or []
    out = []
    for c in ms:
        out.append({
            "name": c.get("name"),
            "id": c.get("id"),
            "methodology": c.get("methodology"),
            "sample_size": c.get("sample_size"),
            "series": c.get("data") or [],
        })
    return out


_RMIX_BLOCK = re.compile(r'class="rmix_graph_block"(.*?)(?=class="rmix_graph_block"|</section|$)', re.S)
_H4 = re.compile(r"<h4[^>]*>(.*?)</h4>", re.S)
_PIE = re.compile(r'chart-data="([^"]*)"\s+chart-id="(\d+)"')


def parse_rmix_blocks(html: str) -> list[dict]:
    """Return [{breakdown, chart_id, current:[[seg,val]]}] from the page."""
    # Scope to the revenue-mix section so we never grab an unrelated chart-id.
    start = html.find("rmix_graph_wrapper")
    section = html[start:] if start != -1 else html
    blocks = []
    for chunk in _RMIX_BLOCK.split(section):
        pie = _PIE.search(chunk)
        if not pie:
            continue
        h4 = _H4.search(chunk)
        title = re.sub(r"<[^>]+>", "", h4.group(1)).strip() if h4 else None
        try:
            current = json.loads(unescape(pie.group(1)))
        except json.JSONDecodeError:
            current = None
        blocks.append({
            "breakdown": title,
            "chart_id": int(pie.group(2)),
            "current": current,
        })
    # de-dupe by chart_id (the split can re-emit a trailing fragment)
    seen, uniq = set(), []
    for b in blocks:
        if b["chart_id"] in seen:
            continue
        seen.add(b["chart_id"])
        uniq.append(b)
    return uniq


def fetch_rmix_historic(chart_id: int) -> list[dict]:
    r = get(f"{BASE}/api/rmix/historic/graph/{chart_id}")
    if not r or r.status_code != 200:
        return []
    try:
        payload = json.loads(r.text)
    except json.JSONDecodeError:
        return []
    if not payload.get("success"):
        return []
    segs = []
    for item in payload.get("context", []):
        try:
            series = json.loads(item.get("graphdata") or "[]")
        except json.JSONDecodeError:
            series = []
        segs.append({"fieldname": item.get("fieldname"), "series": series})
    return segs


# ── resolve one company ─────────────────────────────────────────────────────

def resolve_and_scrape(row: dict) -> dict:
    """row: {sc_id, ticker, our_name}. Returns the upsert payload dict."""
    sc_id, ticker = row["sc_id"], row["ticker"]
    our_name = row["our_name"]
    base = {
        "sc_id": sc_id, "ticker": ticker, "our_name": our_name,
        "tijori_slug": None, "tijori_company_id": None, "tijori_name": None,
        "match_kind": "unmatched", "match_score": None, "dup_of_sc_id": None,
        "has_revenue_mix": False, "has_market_share": False,
        "revenue_mix": None, "market_share": None,
        "status": "unmatched", "scraped_at": datetime.now(timezone.utc),
    }

    # Candidate set: search by ticker (often exact) then by name, dedup by slug.
    cands: list[dict] = []
    seen = set()
    for q in (ticker, our_name):
        if not q:
            continue
        for c in search_tijori(q):
            slug = c.get("slug")
            if slug and slug not in seen:
                seen.add(slug)
                cands.append(c)
    if not cands:
        return base
    # Rank by name similarity to our name; verify the best few by fetching pages.
    cands.sort(key=lambda c: name_score(our_name, c.get("name", "")), reverse=True)

    best = None
    for c in cands[:MAX_CANDIDATES]:
        r = get(f"{BASE}/company/{c['slug']}/")
        if not r or r.status_code != 200:
            continue
        details = blob(r.text, "company_details_data") or {}
        t_symbol = (details.get("symbol") or "").upper()
        t_name = details.get("company") or c.get("name") or ""
        score = name_score(our_name, t_name)
        # Symbol confirmation is decisive, but require a minimal name overlap so
        # a company mis-tagged with the wrong ticker in OUR db (e.g. an entity
        # carrying ticker 'RELIANCE') can't be confidently attached to an
        # unrelated Tijori name.
        symbol_ok = bool(t_symbol) and t_symbol == ticker.upper() and score >= 0.5
        cand = {
            "slug": c["slug"], "html": r.text, "details": details,
            "t_name": t_name, "score": score,
            "kind": "symbol" if symbol_ok else "name",
        }
        if symbol_ok:
            best = cand
            break  # symbol confirmation is decisive
        if best is None or score > best["score"]:
            best = cand

    if best is None:
        return base
    if best["kind"] != "symbol" and best["score"] < NAME_ACCEPT:
        # Not confident enough — record the near-miss for audit, don't scrape.
        base.update({
            "tijori_slug": best["slug"], "tijori_name": best["t_name"],
            "match_score": round(best["score"], 3), "status": "unmatched",
        })
        return base

    details = best["details"]
    tcid = details.get("company_id")

    # ── Cross-company dedup: claim this Tijori company. ──────────────────────
    # If another sc_id already owns it, store a compact duplicate ref and skip
    # the (expensive) re-scrape — the company's data is never stored twice.
    if tcid is not None:
        with _claimed_lock:
            owner = _claimed.get(tcid)
            if owner is None:
                _claimed[tcid] = sc_id
            elif owner != sc_id:
                base.update({
                    "tijori_slug": best["slug"], "tijori_company_id": tcid,
                    "tijori_name": best["t_name"], "match_kind": best["kind"],
                    "match_score": round(best["score"], 3),
                    "dup_of_sc_id": owner, "status": "duplicate",
                })
                return base

    html = best["html"]
    market_share = parse_market_share(html)
    rmix = parse_rmix_blocks(html)
    for b in rmix:
        b["segments"] = fetch_rmix_historic(b["chart_id"])
        time.sleep(0.05 + random.random() * 0.1)  # gentle spacing

    has_rev = any(b.get("segments") or b.get("current") for b in rmix)
    has_ms = len(market_share) > 0

    base.update({
        "tijori_slug": best["slug"],
        "tijori_company_id": details.get("company_id"),
        "tijori_name": best["t_name"],
        "match_kind": best["kind"],
        "match_score": round(best["score"], 3),
        "has_revenue_mix": has_rev,
        "has_market_share": has_ms,
        "revenue_mix": json.dumps(rmix) if rmix else None,
        "market_share": json.dumps(market_share) if market_share else None,
        "status": "ok" if (has_rev or has_ms) else "no_data",
    })
    return base


# ── universe ────────────────────────────────────────────────────────────────

def fetch_universe(dsn: str, limit: int | None, offset: int) -> list[dict]:
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    q = """
        SELECT sc_id, ticker, COALESCE(long_name, company_name) AS our_name
        FROM enrich.company_profile
        WHERE ticker IS NOT NULL AND ticker <> '' AND fetch_status = 'ok'
        ORDER BY market_cap DESC NULLS LAST
    """
    if limit is not None:
        q += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    cur.execute(q)
    rows = [{"sc_id": r[0], "ticker": r[1], "our_name": r[2]} for r in cur.fetchall()]
    conn.close()
    return rows


# ── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create-table", action="store_true")
    ap.add_argument("--sample", type=int, help="scrape N companies (test + ETA)")
    ap.add_argument("--full", action="store_true", help="scrape the whole universe")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true", help="scrape but do not insert")
    args = ap.parse_args()

    dsn = load_enrich_dsn()

    if args.create_table:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        cur.execute(DDL)
        for stmt in MIGRATE:
            cur.execute(stmt)
        conn.commit()
        conn.close()
        print("enrich.tijori_enrichment ready (source column + dedup index applied).")
        if not (args.sample or args.full or args.limit):
            return

    limit = args.sample if args.sample else args.limit
    if args.full:
        limit = None
    if limit is None and not args.full:
        print("Nothing to do. Pass --sample N, --limit N, or --full.")
        return

    universe = fetch_universe(dsn, limit, args.offset)
    total = len(universe)
    print(f"Universe to process: {total} companies | workers={args.workers} | "
          f"dry_run={args.dry_run}")

    pool = None
    if not args.dry_run:
        pool = psycopg2.pool.ThreadedConnectionPool(2, 8, dsn)

    def write(payload: dict) -> None:
        if args.dry_run or pool is None:
            return
        conn = pool.getconn()
        try:
            cur = conn.cursor()
            try:
                cur.execute(UPSERT, payload)
                conn.commit()
            except psycopg2.errors.UniqueViolation:
                # A prior run already owns this Tijori company → downgrade to a
                # duplicate ref so the data is never stored twice.
                conn.rollback()
                cur.execute("SELECT sc_id FROM enrich.tijori_enrichment "
                            "WHERE tijori_company_id=%s AND status='ok' LIMIT 1",
                            (payload["tijori_company_id"],))
                r = cur.fetchone()
                dup = dict(payload)
                dup.update({"status": "duplicate", "dup_of_sc_id": r[0] if r else None,
                            "revenue_mix": None, "market_share": None,
                            "has_revenue_mix": False, "has_market_share": False})
                cur.execute(UPSERT, dup)
                conn.commit()
        finally:
            pool.putconn(conn)

    t0 = time.time()
    stats = {"ok": 0, "duplicate": 0, "unmatched": 0, "no_data": 0, "error": 0,
             "symbol": 0, "name": 0, "has_rev": 0, "has_ms": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(resolve_and_scrape, row): row for row in universe}
        for fut in as_completed(futs):
            row = futs[fut]
            try:
                payload = fut.result()
            except Exception as e:  # noqa: BLE001
                payload = None
                stats["error"] += 1
                print(f"  ERR {row['ticker']}: {e}")
            if payload:
                write(payload)
                st = payload["status"]
                stats[st if st in stats else "error"] = stats.get(
                    st if st in stats else "error", 0) + 1
                if payload["match_kind"] in ("symbol", "name"):
                    stats[payload["match_kind"]] += 1
                stats["has_rev"] += int(payload["has_revenue_mix"])
                stats["has_ms"] += int(payload["has_market_share"])
            done += 1
            if done % 10 == 0 or done == total:
                el = time.time() - t0
                rate = done / el if el else 0
                eta = (total - done) / rate if rate else 0
                print(f"  {datetime.now().strftime('%H:%M:%S')} "
                      f"[{done}/{total}] {el:6.1f}s  ok={stats['ok']} "
                      f"dup={stats['duplicate']} unmatched={stats['unmatched']} "
                      f"no_data={stats['no_data']} err={stats['error']}  "
                      f"({rate:.1f}/s, ETA {eta/60:.1f}m)", flush=True)

    if pool:
        pool.closeall()

    el = time.time() - t0
    per = el / total if total else 0
    print("\n──────── RESULT ────────")
    print(f"processed         {total}")
    print(f"matched (ok)      {stats['ok']}  "
          f"[symbol-confirmed {stats['symbol']}, name-confirmed {stats['name']}]")
    print(f"  with revenue mix  {stats['has_rev']}")
    print(f"  with market share {stats['has_ms']}")
    print(f"duplicate (same co, deduped)  {stats['duplicate']}")
    print(f"no_data (matched, empty)  {stats['no_data']}")
    print(f"unmatched         {stats['unmatched']}")
    print(f"errors            {stats['error']}")
    print(f"wall clock        {el:.1f}s  ({per:.2f}s/company at {args.workers} workers)")

    # ETA extrapolation to the full tickered universe.
    conn = psycopg2.connect(dsn)
    c = conn.cursor()
    c.execute("SELECT count(*) FROM enrich.company_profile "
              "WHERE ticker IS NOT NULL AND ticker<>'' AND fetch_status='ok'")
    full_n = c.fetchone()[0]
    conn.close()
    eta = per * full_n
    print(f"\nFULL UNIVERSE       {full_n} companies")
    print(f"ESTIMATED FULL RUN  {eta/60:.1f} min  (~{eta:.0f}s) at {args.workers} workers")


if __name__ == "__main__":
    main()
