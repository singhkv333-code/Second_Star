"""Download a real sample of NSE + BSE filings so we can see what is actually in them.

This is the RECONNAISSANCE step before building scrapers/extractors: pull annual
reports, investor presentations and announcements for 10 companies (5 NSE-listed,
5 BSE-only), store the PDFs plus per-page text, and index everything in SQLite so
the analysis agents read identical inputs.

WHAT EACH EXCHANGE GIVES (measured, 2026-08-07)

  NSE  /api/annual-reports              17 yrs for RELIANCE. Has broadcast_dttm
                                        (the look-ahead-safe date). Some years
                                        are .ZIP, not .pdf.
  NSE  /api/corporate-announcements     ENTIRE history in ONE call (3,326 rows
                                        for RELIANCE, 0.2s). Free-text `desc`.
  BSE  AnnualReport_New                 30 yrs — deeper than NSE. Some URLs carry
                                        a stray backslash that must be stripped.
  BSE  AnnSubCategoryGetData            Paginated 50/page, but has a LABELLED
                                        two-level taxonomy including an explicit
                                        'Investor Presentation' subcategory,
                                        which NSE does not have.

Segment revenue is deliberately NOT sought here — it is already machine-readable
in the quarterly XBRL (`SegmentRevenue`, `ReportableSegmentsAxis`), so it needs no
PDF and no model. What the PDFs are for is what XBRL lacks: geography,
contingencies, and narrative.

    pivot/.venv/bin/python pivotted/fetch_filings_sample.py --reports 2 --pres 2
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "filings_sample"          # overridden by --out
PDFS = OUT / "pdf"
TEXT = OUT / "text"
DB = OUT / "index.db"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 5 NSE-listed majors spanning templates (conglomerate / IT / bank / auto / pharma)
# and 5 BSE-only names — deliberately small, because BSE-only IS the small end.
NSE_SAMPLE = ["RELIANCE", "TCS", "HDFCBANK", "MARUTI", "SUNPHARMA"]
BSE_SAMPLE = [("JYOTI", "504076"), ("KPGEL", "544150"), ("FABCLEAN", "544332"),
              ("CAMEXLTD", "524440"), ("GTV", "539479")]

DDL = """
CREATE TABLE IF NOT EXISTS docs (
  sha256      TEXT PRIMARY KEY,
  symbol      TEXT NOT NULL,
  exchange    TEXT NOT NULL,
  doc_kind    TEXT NOT NULL,
  title       TEXT,
  category    TEXT,
  subcategory TEXT,
  period      TEXT,
  filed_at    TEXT,
  url         TEXT NOT NULL,
  bytes       INTEGER,
  pages       INTEGER,
  chars       INTEGER,
  pdf_path    TEXT,
  text_path   TEXT,
  fetch_state TEXT NOT NULL,
  raw         TEXT
);
CREATE INDEX IF NOT EXISTS docs_sym ON docs(symbol);
CREATE INDEX IF NOT EXISTS docs_kind ON docs(doc_kind);
"""

_ctx = None


def ctx():
    global _ctx
    if _ctx is None:
        try:
            import certifi
            _ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _ctx = ssl.create_default_context()
    return _ctx


def opener(referer, origin=None, seed=None):
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=ctx()))
    h = [("User-Agent", UA), ("Accept", "application/json, text/plain, */*"),
         ("Accept-Language", "en-US,en;q=0.9"), ("Referer", referer)]
    if origin:
        h.append(("Origin", origin))
    op.addheaders = h
    if seed:
        try:
            op.open(seed, timeout=20).read()
        except Exception:                      # noqa: BLE001  403 here is normal
            pass
    return op


def getj(op, url, tries=3):
    for i in range(1, tries + 1):
        try:
            with op.open(url, timeout=40) as r:
                return json.loads(r.read())
        except Exception:                      # noqa: BLE001
            if i == tries:
                return None
            time.sleep(0.8 * i)
    return None


def clean_url(u: str | None) -> str | None:
    """BSE emits '.../AttachHis/\\b55b5dfc-...pdf' — a stray backslash. Strip it."""
    if not u:
        return None
    u = u.strip().replace("\\", "")
    return u if u.lower().startswith("http") else None


def download(op, url, tries=2):
    for i in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "*/*",
                "Referer": "https://www.nseindia.com/"
                if "nseindia" in url else "https://www.bseindia.com/"})
            with urllib.request.urlopen(req, timeout=180, context=ctx()) as r:
                return r.read()
        except Exception:                      # noqa: BLE001
            if i == tries:
                return None
            time.sleep(1.2 * i)
    return None


# ---------------------------------------------------------------- collectors

def nse_annual(sym, limit):
    op = opener("https://www.nseindia.com/companies-listing/corporate-filings-annual-reports",
                seed="https://www.nseindia.com/")
    j = getj(op, f"https://www.nseindia.com/api/annual-reports?index=equities&symbol={sym}")
    rows = (j or {}).get("data") or []
    out = []
    for r in rows[:limit]:
        u = clean_url(r.get("fileName"))
        if u:
            out.append(dict(symbol=sym, exchange="NSE", doc_kind="annual_report",
                            title=f"{r.get('companyName')} AR {r.get('fromYr')}-{r.get('toYr')}",
                            category="Annual Report", subcategory=None,
                            period=f"{r.get('fromYr')}-{r.get('toYr')}",
                            filed_at=r.get("broadcast_dttm"), url=u, raw=json.dumps(r)))
    return out


def nse_announcements(sym, limit):
    """One call returns the FULL history; we keep the presentation-ish ones."""
    op = opener("https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                seed="https://www.nseindia.com/")
    j = getj(op, f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={sym}")
    rows = j if isinstance(j, list) else []
    want = re.compile(r"present|investor|analyst|con\.?\s*call|earnings|media release|press release",
                      re.I)
    out = []
    for r in rows:
        blob = f"{r.get('desc','')} {str(r.get('attchmntText',''))[:200]}"
        if not want.search(blob):
            continue
        u = clean_url(r.get("attchmntFile"))
        if not u:
            continue
        out.append(dict(symbol=sym, exchange="NSE", doc_kind="announcement",
                        title=str(r.get("attchmntText") or r.get("desc"))[:220],
                        category=r.get("desc"), subcategory=None, period=None,
                        filed_at=r.get("an_dt"), url=u, raw=json.dumps(r)))
        if len(out) >= limit:
            break
    return out


def bse_op():
    return opener("https://www.bseindia.com/", "https://www.bseindia.com",
                  "https://www.bseindia.com/")


def bse_annual(sym, code, limit, op):
    j = getj(op, f"https://api.bseindia.com/BseIndiaAPI/api/AnnualReport_New/w?scripcode={code}")
    rows = (j or {}).get("Table") or []
    out = []
    for r in rows[:limit]:
        u = clean_url(r.get("PDFDownload"))
        if u:
            out.append(dict(symbol=sym, exchange="BSE", doc_kind="annual_report",
                            title=f"{r.get('scrip_name')} AR {r.get('Year')}",
                            category="Annual Report", subcategory=None,
                            period=str(r.get("Year")), filed_at=r.get("Fld_AuthoriseDate"),
                            url=u, raw=json.dumps(r)))
    return out


def bse_announcements(sym, code, limit, op):
    """BSE's labelled taxonomy — the reason to bother with BSE at all."""
    out = []
    for page in (1, 2, 3):
        u = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?"
             f"pageno={page}&strCat=-1&strPrevDate=20230101&strScrip={code}"
             "&strSearch=P&strToDate=20260807&strType=C&subcategory=-1")
        j = getj(op, u)
        rows = (j or {}).get("Table") or []
        if not rows:
            break
        for r in rows:
            sub = (r.get("SUBCATNAME") or "").strip()
            cat = (r.get("CATEGORYNAME") or "").strip()
            if not re.search(r"present|investor|analyst|press|media|annual report",
                             f"{cat} {sub}", re.I):
                continue
            att = (r.get("ATTACHMENTNAME") or "").strip()
            if not att:
                continue
            out.append(dict(
                symbol=sym, exchange="BSE", doc_kind="announcement",
                title=str(r.get("HEADLINE") or r.get("NEWSSUB"))[:220],
                category=cat, subcategory=sub, period=None,
                filed_at=r.get("NEWS_DT"),
                url=f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}",
                raw=json.dumps(r)))
            if len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------- text

def extract_text(pdf_bytes, dest: Path):
    """Per-page text. Page numbers are the provenance anchor for any extraction."""
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:                          # noqa: BLE001
        return None, 0, 0
    parts = []
    for i, page in enumerate(doc, 1):
        try:
            t = page.get_text()
        except Exception:                      # noqa: BLE001
            t = ""
        parts.append(f"\n\n===== [PAGE {i}] =====\n{t}")
    n = doc.page_count
    doc.close()
    body = "".join(parts)
    dest.write_text(body, encoding="utf-8")
    return dest, n, len(body)


def main() -> int:
    # `global` must precede every use of these names in this scope — the
    # argparse defaults below read them.
    global OUT, PDFS, TEXT, DB, NSE_SAMPLE, BSE_SAMPLE
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", type=int, default=2, help="annual reports per company")
    ap.add_argument("--pres", type=int, default=3, help="presentation-ish docs per company")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--out", default="filings_sample",
                    help="output dir under pivotted/ (use a NEW name for a "
                         "held-out validation sample)")
    ap.add_argument("--nse", default=",".join(NSE_SAMPLE))
    ap.add_argument("--bse", default=",".join(f"{s}:{c}" for s, c in BSE_SAMPLE))
    a = ap.parse_args()

    OUT = HERE / a.out
    PDFS, TEXT, DB = OUT / "pdf", OUT / "text", OUT / "index.db"
    NSE_SAMPLE = [x for x in a.nse.split(",") if x]
    BSE_SAMPLE = [tuple(x.split(":")) for x in a.bse.split(",") if x]

    for p in (OUT, PDFS, TEXT):
        p.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.executescript(DDL)
    db.commit()

    print("collecting metadata ...", flush=True)
    todo = []
    bo = bse_op()
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = []
        for s in NSE_SAMPLE:
            futs.append(pool.submit(nse_annual, s, a.reports))
            futs.append(pool.submit(nse_announcements, s, a.pres))
        for s, code in BSE_SAMPLE:
            futs.append(pool.submit(bse_annual, s, code, a.reports, bo))
            futs.append(pool.submit(bse_announcements, s, code, a.pres, bo))
        for f in futs:
            todo += f.result() or []
    print(f"  {len(todo)} documents to fetch")
    by = {}
    for d in todo:
        by.setdefault((d["exchange"], d["doc_kind"]), 0)
        by[(d["exchange"], d["doc_kind"])] += 1
    print("  ", by)

    print("\ndownloading ...", flush=True)
    t0 = time.time()
    ok = fail = skip = 0

    def work(d):
        blob = download(None, d["url"])
        if not blob or len(blob) < 800:
            return d, None, "FAILED"
        return d, blob, "ok"

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for i, (d, blob, state) in enumerate(pool.map(work, todo), 1):
            if state != "ok":
                fail += 1
                db.execute("INSERT OR REPLACE INTO docs (sha256,symbol,exchange,doc_kind,"
                           "title,category,subcategory,period,filed_at,url,fetch_state,raw)"
                           " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           ("FAIL:" + hashlib.sha1(d["url"].encode()).hexdigest(),
                            d["symbol"], d["exchange"], d["doc_kind"], d["title"],
                            d["category"], d["subcategory"], d["period"], d["filed_at"],
                            d["url"], "FAILED", d["raw"]))
                db.commit()
                continue
            sha = hashlib.sha256(blob).hexdigest()
            cur = db.execute("SELECT 1 FROM docs WHERE sha256=?", (sha,))
            if cur.fetchone():
                skip += 1
                continue
            ext = ".zip" if blob[:2] == b"PK" else ".pdf"
            pdf_path = PDFS / f"{d['symbol']}_{d['doc_kind']}_{sha[:12]}{ext}"
            pdf_path.write_bytes(blob)
            tpath, pages, chars = (None, 0, 0)
            if ext == ".pdf":
                tpath, pages, chars = extract_text(
                    blob, TEXT / f"{d['symbol']}_{d['doc_kind']}_{sha[:12]}.txt")
            db.execute("INSERT OR REPLACE INTO docs (sha256,symbol,exchange,doc_kind,title,"
                       "category,subcategory,period,filed_at,url,bytes,pages,chars,"
                       "pdf_path,text_path,fetch_state,raw) VALUES "
                       "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (sha, d["symbol"], d["exchange"], d["doc_kind"], d["title"],
                        d["category"], d["subcategory"], d["period"], d["filed_at"],
                        d["url"], len(blob), pages, chars,
                        str(pdf_path), str(tpath) if tpath else None,
                        "ok" if ext == ".pdf" else "zip", d["raw"]))
            db.commit()
            ok += 1
            if i % 10 == 0:
                print(f"  {i}/{len(todo)}  ok={ok} fail={fail} skip={skip} "
                      f"{time.time()-t0:.0f}s", flush=True)

    print(f"\ndone in {time.time()-t0:.0f}s  ok={ok} failed={fail} dup={skip}")
    for row in db.execute("""SELECT exchange,doc_kind,count(*),sum(bytes)/1048576,
                                    sum(pages),sum(chars)/1000
                             FROM docs WHERE fetch_state='ok'
                             GROUP BY 1,2 ORDER BY 1,2"""):
        print(f"  {row[0]:4s} {row[1]:14s} n={row[2]:>3}  {row[3] or 0:>5.0f}MB  "
              f"{row[4] or 0:>6} pages  {row[5] or 0:>7,}k chars")
    print(f"\nindex: {DB}\npdfs : {PDFS}\ntext : {TEXT}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
