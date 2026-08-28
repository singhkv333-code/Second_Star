"""Crawl quarterly result filings (with XBRL links) for every NSE-listed company.

WHY NSE FIRST, AGAINST THE EARLIER PLAN

The coverage analysis said BSE-primary: BSE lists 5,936 companies to NSE's
2,867, and 541 of our live filers are BSE-only. That was an argument about
BREADTH, and it was the wrong axis.

Probing both feeds:

  NSE  /api/corporates-financial-results   -> 130 records for RELIANCE, EVERY
       one carrying a structured INDAS XBRL url AND the company's ISIN.
  NSE  /api/integrated-filing-results      -> the 2025+ continuation, current
       to 28-Jul-2026 (the legacy feed stops at Dec-2024).
  BSE  /api/AnnSubCategoryGetData          -> works, but a "Result" row is an
       ANNOUNCEMENT: headline plus a .pdf attachment.
  BSE  XBRLResultData / Finresults / CorpFinancialResult / AnnGetData?strCat=Result
       -> 302, 302, connection failure, 503. None resolve.

BSE has mandated XBRL results filing since April 2017, so the data exists; the
public API path to it is not one of the obvious ones. Until that is found, BSE
offers PDFs, and pulling financials out of PDFs is a different project with a
different error profile. A structured feed covering 2,976 companies beats a
PDF feed covering 5,936.

So: NSE now, BSE parked as research. This file is the NSE half.

WHAT IT WRITES

`result_filings`, one row per (source, seq) — the exchange's own filing id, so
a re-run is idempotent and a re-filed quarter does not overwrite the original.
Every row keeps `xbrl_url` (the thing worth having), `isin` (the join key), and
the untouched `raw` payload, because the parse can be redone offline and the
fetch cannot.

    pivot/.venv/bin/python pivotted/extract_filings.py --limit 25   # pilot
    pivot/.venv/bin/python pivotted/extract_filings.py              # all NSE
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

PIVOT = Path(__file__).resolve().parent.parent / "pivot"
sys.path.insert(0, str(PIVOT))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
REFERER = ("https://www.nseindia.com/companies-listing/"
           "corporate-filings-financial-results")
LEGACY = ("https://www.nseindia.com/api/corporates-financial-results"
          "?index=equities&symbol={sym}&period=Quarterly")
INTEGRATED = ("https://www.nseindia.com/api/integrated-filing-results"
              "?index=equities&symbol={sym}&period=Quarterly")

DDL = """
CREATE TABLE IF NOT EXISTS result_filings (
  source        TEXT NOT NULL,
  source_seq    TEXT NOT NULL,
  symbol        TEXT NOT NULL,
  isin          TEXT,
  company_name  TEXT,
  period_from   DATE,
  period_to     DATE,
  financial_year TEXT,
  consolidated  TEXT,
  audited       TEXT,
  is_revision   BOOLEAN,
  broadcast_at  TIMESTAMPTZ,
  xbrl_url      TEXT,
  pdf_url       TEXT,
  raw           JSONB NOT NULL,
  fetched_at    TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (source, source_seq)
);
CREATE INDEX IF NOT EXISTS result_filings_symbol_idx ON result_filings (symbol);
CREATE INDEX IF NOT EXISTS result_filings_isin_idx   ON result_filings (isin);
CREATE INDEX IF NOT EXISTS result_filings_period_idx ON result_filings (period_to DESC);

COMMENT ON TABLE result_filings IS
 'Quarterly result filings from NSE, one row per exchange filing id. '
 'xbrl_url points at the structured INDAS document — the payload worth having; '
 'raw is the untouched API record so the parse can be redone without refetching. '
 'Join to company_identity on isin. NSE only: BSE publishes results as PDF '
 'announcements and its XBRL API path is not public (see module docstring).';
COMMENT ON COLUMN result_filings.source IS
 'nse_legacy = /corporates-financial-results (history, ends ~Dec-2024); '
 'nse_integrated = /integrated-filing-results (2025 onwards). Both are needed: '
 'neither covers the full span alone.';
COMMENT ON COLUMN result_filings.is_revision IS
 'The exchange re-published this quarter. Kept as a separate row, never an '
 'overwrite — the market reacted to the FIRST landing, and a study that '
 'silently adopts the revision has look-ahead in it.';
"""

_ctx = None
_cookie_lock = threading.Lock()
_opener = None


def _ssl_ctx():
    global _ctx
    if _ctx is None:
        try:
            import certifi
            _ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _ctx = ssl.create_default_context()
    return _ctx


def _get_opener():
    """One cookie jar for the process. NSE hands out a session on first touch."""
    global _opener
    with _cookie_lock:
        if _opener is None:
            import http.cookiejar
            jar = http.cookiejar.CookieJar()
            _opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(jar),
                urllib.request.HTTPSHandler(context=_ssl_ctx()))
            _opener.addheaders = [("User-Agent", UA), ("Referer", REFERER),
                                  ("Accept", "application/json, text/plain, */*"),
                                  ("Accept-Language", "en-US,en;q=0.9")]
            try:                       # seed cookies; 403 here is fine
                _opener.open("https://www.nseindia.com/", timeout=20).read()
            except Exception:          # noqa: BLE001
                pass
    return _opener


def fetch(url: str, tries: int = 3):
    for attempt in range(1, tries + 1):
        try:
            with _get_opener().open(url, timeout=30) as r:
                body = r.read()
            return json.loads(body) if body else None
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError):
            if attempt == tries:
                return None
            time.sleep(0.8 * attempt)
    return None


def _date(s):
    for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return datetime.strptime((s or "").strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


def rows_legacy(sym: str, recs) -> list:
    out = []
    for r in recs or []:
        seq = str(r.get("seqNumber") or "").strip()
        if not seq:
            continue
        out.append(("nse_legacy", seq, sym, (r.get("isin") or "").strip() or None,
                    r.get("companyName"),
                    _date(r.get("fromDate")), _date(r.get("toDate")),
                    r.get("financialYear"), r.get("consolidated"),
                    r.get("audited"),
                    (r.get("reInd") or "").upper() == "Y",
                    _date(r.get("broadCastDate")),
                    r.get("xbrl") or None, r.get("resultDetailedDataLink") or None,
                    json.dumps(r)))
    return out


def rows_integrated(sym: str, payload) -> list:
    out = []
    for r in (payload or {}).get("data") or []:
        seq = str(r.get("seq_Id") or "").strip()
        if not seq:
            continue
        qe = _date(r.get("qe_Date"))
        out.append(("nse_integrated", seq, sym, None, r.get("cmName"),
                    None, qe, None, r.get("consolidated"), r.get("audited"),
                    False, _date((r.get("broadcast_Date") or "").split(".")[0]),
                    r.get("xbrl") or r.get("ixbrl") or None,
                    r.get("pdf_attach") or None, json.dumps(r)))
    return out


def crawl_symbol(sym: str) -> tuple[str, list, str]:
    rows = []
    leg = fetch(LEGACY.format(sym=urllib.parse.quote(sym)))
    if leg:
        rows += rows_legacy(sym, leg)
    integ = fetch(INTEGRATED.format(sym=urllib.parse.quote(sym)))
    if integ:
        rows += rows_integrated(sym, integ)
    state = "ok" if rows else ("empty" if (leg is not None or integ is not None)
                               else "FAILED")
    return sym, rows, state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(PIVOT / ".env")
    except ImportError:
        pass
    import psycopg2

    db = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = db.cursor()
    cur.execute(DDL)
    db.commit()
    cur.execute("""SELECT verified_symbol FROM company_identity
                   WHERE verified_exchange IN ('NSE','NSE_SME')
                     AND mc_is_primary
                   ORDER BY mc_metric_count DESC NULLS LAST""")
    syms = [r[0] for r in cur.fetchall()]
    if a.limit:
        syms = syms[:a.limit]
    print(f"NSE symbols to crawl: {len(syms)} (workers={a.workers})", flush=True)

    t0 = time.time()
    done = {"ok": 0, "empty": 0, "FAILED": 0}
    total_rows = 0
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for i, (sym, rows, state) in enumerate(pool.map(crawl_symbol, syms), 1):
            done[state] += 1
            if rows and not a.dry_run:
                cur.executemany(
                    "INSERT INTO result_filings (source,source_seq,symbol,isin,"
                    "company_name,period_from,period_to,financial_year,"
                    "consolidated,audited,is_revision,broadcast_at,xbrl_url,"
                    "pdf_url,raw,fetched_at) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) "
                    "ON CONFLICT (source,source_seq) DO NOTHING", rows)
                db.commit()
            total_rows += len(rows)
            if i % 25 == 0 or i == len(syms):
                el = time.time() - t0
                print(f"  {i}/{len(syms)}  rows={total_rows}  "
                      f"ok={done['ok']} empty={done['empty']} fail={done['FAILED']}  "
                      f"{el:.0f}s  ({i/max(el,1):.1f} sym/s)", flush=True)

    print(f"\n{done}  rows={total_rows}  in {time.time()-t0:.0f}s")
    if not a.dry_run:
        cur.execute("SELECT count(*), count(DISTINCT symbol), count(xbrl_url), "
                    "min(period_to), max(period_to) FROM result_filings")
        print("result_filings:", cur.fetchone())
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
