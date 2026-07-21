#!/usr/bin/env python3
"""
scrape_bse_documents.py — build a FOCUSED document-link catalog per company
from BSE: annual reports, financial results, investor presentations, and
earnings-concall audio/video. Links/metadata only (no PDFs downloaded).

Universe = distinct BSE scripcodes from `enrich.bse_map` (name-matched earlier).
Stores into `enrich.company_documents`, deduped by (scripcode, url).

Usage:
  python scrape_bse_documents.py --create-table
  python scrape_bse_documents.py --sample 15          # test + ETA
  python scrape_bse_documents.py --full                # whole universe
"""
from __future__ import annotations
import argparse, json, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import os
import psycopg2
from psycopg2.extras import execute_values
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
API = "https://api.bseindia.com/BseIndiaAPI/api"
# AttachHis serves the modern (UUID-named) archive back to ~2015; the AR
# endpoint returns its own working URLs. Pre-~2015 name-based attachments live
# in a BSE archive path that is no longer served — we tag those 'legacy'.
ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/"
FROM_DATE = "19970101"       # BSE electronic archive floor
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-", re.I)

def attach(name):
    """(url, link_status) for a corpfiling attachment filename."""
    return ATTACH + name, ("ok" if _UUID.match(name or "") else "legacy")

def dsn():
    here = os.path.dirname(os.path.abspath(__file__))
    for line in open(os.path.join(here, "..", ".env")):
        line = line.strip()
        if line.startswith("ENRICH_DSN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("ENRICH_DSN not found")

DDL = """
CREATE TABLE IF NOT EXISTS enrich.company_documents (
    id             bigserial PRIMARY KEY,
    bse_scripcode  text NOT NULL,
    doc_type       text NOT NULL,   -- annual_report | financial_result | investor_presentation | concall_av
    category       text,
    subcategory    text,
    title          text,
    doc_date       date,
    fin_year       int,
    quarter        text,
    url            text NOT NULL,
    attach_size    bigint,
    link_status    text DEFAULT 'ok',   -- ok | legacy (pre-2015 name-based link may be stale)
    source         text DEFAULT 'bse',
    fetched_at     timestamptz,
    UNIQUE (bse_scripcode, url)
);
CREATE INDEX IF NOT EXISTS ix_docs_scrip ON enrich.company_documents (bse_scripcode);
CREATE INDEX IF NOT EXISTS ix_docs_type  ON enrich.company_documents (doc_type);
"""

_tls = threading.local()
def sess() -> requests.Session:
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Referer": "https://www.bseindia.com/"})
        _tls.s = s
    return s

def get(url, tries=3):
    for i in range(tries):
        try:
            r = sess().get(url, timeout=20)
            if r.status_code == 200 and r.text.strip():
                try: return r.json()
                except json.JSONDecodeError: return None
        except requests.RequestException:
            pass
        time.sleep(0.4 * (i + 1))
    return None

def _date(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z", "")[:19]).date().isoformat()
    except (ValueError, TypeError): return None

def _pages(scrip, cat, subcat):
    """Yield announcement rows for a category/subcategory across full history."""
    for pg in range(1, 60):
        url = (f"{API}/AnnSubCategoryGetData/w?pageno={pg}&strCat={cat}"
               f"&strPrevDate={FROM_DATE}&strScrip={scrip}&strSearch=P"
               f"&strToDate={datetime.now(timezone.utc).strftime('%Y%m%d')}"
               f"&strType=C&subcategory={subcat}")
        d = get(url)
        t = d.get("Table", []) if isinstance(d, dict) else []
        if not t: return
        yield from t
        if len(t) < 50: return
        time.sleep(0.2)

def fetch_company(scrip):
    """Return list of doc-rows for one scripcode. Only the 4 focused types."""
    out = []
    now = datetime.now(timezone.utc)
    # 1) Annual reports
    d = get(f"{API}/AnnualReport_New/w?scripcode={scrip}")
    for r in (d.get("Table", []) if isinstance(d, dict) else []):
        u = r.get("PDFDownload")
        if not u: continue
        yr = int(r["Year"]) if str(r.get("Year", "")).isdigit() else None
        out.append((scrip, "annual_report", "Annual Report", None,
                    f"Annual Report {r.get('Year','')}", _date(r.get("Fld_AuthoriseDate")),
                    yr, None, u, None, "ok", "bse", now))
    # 2) Financial results
    for r in _pages(scrip, "Result", "-1"):
        an = r.get("ATTACHMENTNAME")
        if not an: continue
        url, st = attach(an)
        out.append((scrip, "financial_result", r.get("CATEGORYNAME"), r.get("SUBCATNAME"),
                    (r.get("NEWSSUB") or "")[:400], _date(r.get("NEWS_DT") or r.get("DT_TM")),
                    None, str(r.get("QUARTER_ID") or "") or None, url,
                    r.get("Fld_Attachsize"), st, "bse", now))
    # 3) Investor presentations + concall A/V (Analyst / Investor Meet subcat)
    for r in _pages(scrip, "Company%20Update", "Analyst%20%2F%20Investor%20Meet"):
        dt = _date(r.get("NEWS_DT") or r.get("DT_TM"))
        title = (r.get("NEWSSUB") or "")[:400]
        ip = r.get("Investor_Presentation")
        if ip:
            url, st = attach(ip)
            out.append((scrip, "investor_presentation", r.get("CATEGORYNAME"), r.get("SUBCATNAME"),
                        title, dt, None, None, url, None, st, "bse", now))
        av = r.get("AUDIO_VIDEO_FILE")
        if av and str(av).startswith("http"):
            out.append((scrip, "concall_av", r.get("CATEGORYNAME"), r.get("SUBCATNAME"),
                        title, dt, None, None, av, None, "ok", "bse", now))
    return out

INSERT = """INSERT INTO enrich.company_documents
   (bse_scripcode,doc_type,category,subcategory,title,doc_date,fin_year,quarter,url,attach_size,link_status,source,fetched_at)
   VALUES %s ON CONFLICT (bse_scripcode, url) DO NOTHING"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--create-table", action="store_true")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    d = dsn()

    if a.create_table:
        c = psycopg2.connect(d); c.cursor().execute(DDL); c.commit(); c.close()
        print("enrich.company_documents ready.")
        if not (a.sample or a.full): return

    conn = psycopg2.connect(d); cur = conn.cursor()
    cur.execute("SELECT DISTINCT bse_scripcode FROM enrich.bse_map WHERE bse_scripcode IS NOT NULL "
                "ORDER BY bse_scripcode" + (f" LIMIT {int(a.sample)}" if a.sample else ""))
    scrips = [r[0] for r in cur.fetchall()]
    conn.close()
    if not a.full and not a.sample:
        print("pass --sample N or --full"); return

    print(f"companies: {len(scrips)}  workers={a.workers}  dry_run={a.dry_run}", flush=True)
    wconn = None if a.dry_run else psycopg2.connect(d)
    lock = threading.Lock()
    t0 = time.time(); done = 0
    tally = {"annual_report": 0, "financial_result": 0, "investor_presentation": 0, "concall_av": 0}

    def work(scrip):
        try: return scrip, fetch_company(scrip)
        except Exception as e:  # noqa: BLE001
            return scrip, e

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, s) for s in scrips]
        for f in as_completed(futs):
            scrip, rows = f.result()
            done += 1
            if isinstance(rows, Exception):
                print(f"  ERR {scrip}: {rows}", flush=True); continue
            for r in rows: tally[r[1]] = tally.get(r[1], 0) + 1
            if wconn and rows:
                with lock:
                    execute_values(wconn.cursor(), INSERT, rows, page_size=500)
                    wconn.commit()
            if done % 5 == 0 or done == len(scrips):
                el = time.time() - t0
                print(f"  {datetime.now().strftime('%H:%M:%S')} [{done}/{len(scrips)}] {el:.0f}s "
                      f"AR={tally['annual_report']} Res={tally['financial_result']} "
                      f"IP={tally['investor_presentation']} AV={tally['concall_av']} "
                      f"({done/el:.1f}/s)", flush=True)
    if wconn: wconn.close()
    el = time.time() - t0
    print("\n──── RESULT ────")
    print(f"companies={len(scrips)}  wall={el:.1f}s  per-co={el/len(scrips):.2f}s")
    for k, v in tally.items(): print(f"  {k:22} {v}")
    total = sum(tally.values())
    print(f"  TOTAL doc links: {total}  (avg {total/len(scrips):.1f}/company)")
    per = el / len(scrips)
    print(f"\n  EXTRAPOLATION 3960 companies @ {a.workers}w: {per*3960/60:.1f} min, "
          f"~{int(total/len(scrips)*3960):,} doc links")

if __name__ == "__main__":
    main()
