"""Fetch every listed company's shareholding pattern and store it as rows.

    pivot/.venv/bin/python pivotted/shp_pipeline.py --create-table
    pivot/.venv/bin/python pivotted/shp_pipeline.py --build-universe
    pivot/.venv/bin/python pivotted/shp_pipeline.py --enqueue --quarters 8
    pivot/.venv/bin/python pivotted/shp_pipeline.py --run --workers 8

ROUTING, AND WHY IT IS BSE-FIRST (all measured 2026-08-08)

  BSE and NSE serve the SAME SEBI Reg-31 filing, but not the same way:

    BSE   106 quarters indexed (back to Mar-2001), 43-48 with XBRL (Jun-2016).
          Keyed by scripcode. Serves `TypeOfPromoterShareholding`.
    NSE   22 quarters, hard floor Sep-2021, keyed by symbol. MASKS both
          `PermanentAccountNumberOfShareholder` and, fatally,
          `TypeOfPromoterShareholding` to '******'.

  That second mask means an NSE-sourced filing cannot tell you which named
  holder is a promoter — only the category totals survive. So BSE is the
  route wherever a scripcode exists, and NSE is the fallback that keeps the
  512 NSE-SME and 126 NSE-only companies from being dropped entirely.
  `shp.filings.has_promoter_labels` records which you got.

COVERAGE, AND HOW WE GET TO ALL OF IT

  `enrich.bse_map` only reaches 3,960 scripcodes and got there by NAME
  matching. `company_identity.verified_bse_code` is verified but is only
  populated for the 2,212 rows whose verified exchange IS BSE — an
  NSE-verified company is still listed on BSE, it just has no code on its row.

  Joining `company_identity.isin` to BSE's own ListofScripData (4,949 active
  equity scrips, 4,948 with ISIN) lifts BSE reach from 44.0% to 87.3%. The
  638 that remain are exactly the NSE-SME and NSE-only names, which the NSE
  route covers. Union = 5,022/5,022 of the spine, plus 567 BSE scrips that
  are not in the spine at all and are stored anyway, keyed by ISIN, so they
  light up for free whenever the spine grows.

ISIN IS THE KEY. Pre-2018 filings carry no ISIN tag at all, so the universe
row's ISIN is what gets written — never the document's, which is absent
exactly where history is deepest.
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shp_parse  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BSE_API = "https://api.bseindia.com/BseIndiaAPI/api"
BSE_LIST = (f"{BSE_API}/ListofScripData/w"
            "?Group=&Scripcode=&industry=&segment=Equity&status=Active")
BSE_XBRL = "https://www.bseindia.com/XBRLFILES/SHPXBRLDataXML/"
NSE_MASTER = "https://www.nseindia.com/api/corporate-share-holdings-master"

ENV = Path(__file__).resolve().parent.parent / "pivot" / ".env"

_ctx = None
_tls = threading.local()


def ctx():
    global _ctx
    if _ctx is None:
        try:
            import certifi
            _ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _ctx = ssl.create_default_context()
    return _ctx


def dsn(key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} not found in env or {ENV}")


# ------------------------------------------------------------------ fetching

def _opener(referer, origin=None, seed=None):
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
        except Exception:                       # noqa: BLE001  403 is normal
            pass
    return op


def bse_op():
    """One cookie jar per thread — BSE and NSE both bind cookies to a session."""
    if not hasattr(_tls, "bse"):
        _tls.bse = _opener("https://www.bseindia.com/",
                           "https://www.bseindia.com", "https://www.bseindia.com/")
    return _tls.bse


def nse_op():
    if not hasattr(_tls, "nse"):
        _tls.nse = _opener(
            "https://www.nseindia.com/companies-listing/corporate-shareholdings-promoter",
            seed="https://www.nseindia.com/")
    return _tls.nse


def fetch(op, url, tries=3, binary=False):
    last = None
    for i in range(1, tries + 1):
        try:
            with op.open(url, timeout=60) as r:
                raw = r.read()
            return raw if binary else raw.decode("utf-8", "replace")
        except Exception as e:                  # noqa: BLE001
            last = e
            # A 403 on NSE means the cookie went stale; a fresh jar fixes it.
            if isinstance(e, urllib.error.HTTPError) and e.code == 403:
                for attr in ("nse", "bse"):
                    if hasattr(_tls, attr):
                        delattr(_tls, attr)
            if i < tries:
                time.sleep(0.7 * i)
    raise last


def getj(op, url, tries=3):
    return json.loads(fetch(op, url, tries))


# ------------------------------------------------------------------- schema

DDL = """
CREATE SCHEMA IF NOT EXISTS shp;

CREATE TABLE IF NOT EXISTS shp.universe (
  isin         text PRIMARY KEY,
  name         text,
  route        text NOT NULL,          -- bse | nse
  scripcode    text,
  nse_symbol   text,
  nse_index    text,                   -- equities | sme
  in_spine     boolean NOT NULL DEFAULT false,
  resolved_by  text,
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shp.queue (
  id            bigserial PRIMARY KEY,
  isin          text NOT NULL,
  route         text NOT NULL,
  scripcode     text,
  nse_symbol    text,
  nse_index     text,
  quarter_label text,
  quarter_end   date,
  filed_at      timestamptz,
  url           text NOT NULL,
  state         text NOT NULL DEFAULT 'pending',   -- pending|running|done|error
  attempts      int  NOT NULL DEFAULT 0,
  error         text,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (isin, url)
);
CREATE INDEX IF NOT EXISTS shp_queue_state ON shp.queue (state, id);

CREATE TABLE IF NOT EXISTS shp.filings (
  id            bigserial PRIMARY KEY,
  isin          text NOT NULL,
  scripcode     text,
  symbol        text,
  company_name  text,
  quarter_end   date NOT NULL,
  filed_at      timestamptz,
  taxonomy      text,
  source        text NOT NULL,         -- bse | nse
  url           text NOT NULL UNIQUE,
  sha256        text,
  -- headline numbers, so the common query needs no join
  total_shares            numeric,
  promoter_pct            numeric,
  public_pct              numeric,
  npnp_pct                numeric,
  pct_sum                 numeric,     -- promoter+public+npnp, an audit handle
  promoter_shares         numeric,
  promoter_encumbered     numeric,
  promoter_encumbered_pct numeric,     -- of the PROMOTER stake, not the company
  promoter_pledged        numeric,
  promoter_ndu            numeric,
  promoter_other_enc      numeric,
  has_promoter_labels     boolean NOT NULL DEFAULT false,
  n_categories  int, n_holders int, n_sbo int,
  meta          jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (isin, quarter_end, source)
);
CREATE INDEX IF NOT EXISTS shp_filings_isin_q ON shp.filings (isin, quarter_end DESC);

CREATE TABLE IF NOT EXISTS shp.category (
  filing_id     bigint NOT NULL REFERENCES shp.filings(id) ON DELETE CASCADE,
  category      text NOT NULL,
  shareholders  numeric, shares numeric, shares_fully_paid numeric,
  pct numeric, voting_rights numeric, pct_voting numeric,
  shares_diluted numeric, pct_diluted numeric, demat numeric,
  locked_in numeric, locked_in_pct numeric,
  enc_total numeric, enc_pledged numeric, enc_ndu numeric, enc_other numeric,
  enc_pct numeric, convertibles numeric, esop_outstanding numeric,
  depository_receipts numeric,
  PRIMARY KEY (filing_id, category)
);

CREATE TABLE IF NOT EXISTS shp.holder (
  id            bigserial PRIMARY KEY,
  filing_id     bigint NOT NULL REFERENCES shp.filings(id) ON DELETE CASCADE,
  name          text NOT NULL,
  bucket        text,
  promoter_type text,                  -- Promoter | Promoter Group | NULL on NSE
  holder_category text,
  shareholders numeric, shares numeric, pct numeric,
  voting_rights numeric, pct_voting numeric,
  shares_diluted numeric, pct_diluted numeric, demat numeric,
  locked_in numeric, locked_in_pct numeric,
  enc_total numeric, enc_pledged numeric, enc_ndu numeric, enc_other numeric,
  enc_pct numeric
);
CREATE INDEX IF NOT EXISTS shp_holder_filing ON shp.holder (filing_id);
CREATE INDEX IF NOT EXISTS shp_holder_name   ON shp.holder (lower(name));

CREATE TABLE IF NOT EXISTS shp.sbo (
  id            bigserial PRIMARY KEY,
  filing_id     bigint NOT NULL REFERENCES shp.filings(id) ON DELETE CASCADE,
  sbo_name      text NOT NULL,
  sbo_nationality text,
  registered_owner text,
  registered_owner_nationality text,
  held_since    text,
  by_shares text, by_voting_rights text, by_dividend text,
  by_control text, by_significant_influence text
);
CREATE INDEX IF NOT EXISTS shp_sbo_filing ON shp.sbo (filing_id);

COMMENT ON COLUMN shp.filings.promoter_encumbered_pct IS
 'Encumbered shares as a percentage of the PROMOTER holding, not of the '
 'company. Sums pledge + non-disposal undertaking + other encumbrances: '
 'Vedanta Jun-2026 files pledged=false while carrying 2,032,309,058 shares '
 'under other encumbrances, so reading pledge alone reports zero.';
COMMENT ON COLUMN shp.filings.has_promoter_labels IS
 'False for NSE-sourced filings: NSE masks TypeOfPromoterShareholding to '
 '******, so per-holder promoter/promoter-group labels are unavailable and '
 'only the category totals can be trusted.';
COMMENT ON COLUMN shp.category.pct IS
 'PERCENT (0-100), normalised. The 2016 taxonomy wrote 6360.00 for 63.60% '
 'and the 2025 ones write 0.5048 for 50.48%; shp_parse anchors on '
 'promoter+public+npnp to pick the scale.';
"""


# ------------------------------------------------------------------ universe

def build_universe(conn, verbose=True):
    """Resolve every company we can reach to a fetch route, keyed on ISIN."""
    if verbose:
        print("fetching BSE active-equity master…", flush=True)
    bse = getj(bse_op(), BSE_LIST)
    by_isin, by_name = {}, {}
    for r in bse:
        isin = (r.get("ISIN_NUMBER") or "").strip().upper()
        if not isin:
            continue
        by_isin.setdefault(isin, r)
        nm = (r.get("Issuer_Name") or r.get("Scrip_Name") or "").strip()
        if nm:
            by_name.setdefault(nm.upper(), r)
    if verbose:
        print(f"  BSE scrips {len(bse)}  with ISIN {len(by_isin)}")

    spine = []
    try:
        pv = psycopg2.connect(dsn("DATABASE_URL"))
        c = pv.cursor()
        c.execute("SELECT isin, verified_symbol, verified_exchange, "
                  "verified_bse_code, verified_name FROM company_identity "
                  "WHERE mc_is_primary")
        spine = c.fetchall()
        pv.close()
    except Exception as e:                       # noqa: BLE001
        print(f"  WARN company_identity unavailable ({str(e)[:70]}); "
              f"universe will be BSE-only", file=sys.stderr)
    if verbose:
        print(f"  spine rows {len(spine)}")

    rows, seen = [], set()
    for isin, sym, exch, code, name in spine:
        isin = (isin or "").strip().upper()
        if not isin or isin in seen:
            continue
        seen.add(isin)
        hit = by_isin.get(isin)
        if hit:
            rows.append((isin, name or hit.get("Issuer_Name"), "bse",
                         str(hit["SCRIP_CD"]), sym, None, True, "isin->bse_master"))
        elif (code or "").strip():
            rows.append((isin, name, "bse", str(code).strip(), sym, None,
                         True, "company_identity.verified_bse_code"))
        else:
            rows.append((isin, name, "nse", None, sym,
                         "sme" if exch == "NSE_SME" else "equities",
                         True, f"nse:{exch}"))
    # BSE scrips outside the spine: cheap to carry, and they cost nothing
    # until the spine grows to meet them.
    for isin, r in by_isin.items():
        if isin in seen:
            continue
        seen.add(isin)
        rows.append((isin, r.get("Issuer_Name") or r.get("Scrip_Name"), "bse",
                     str(r["SCRIP_CD"]), None, None, False, "bse_master_only"))

    cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO shp.universe
          (isin,name,route,scripcode,nse_symbol,nse_index,in_spine,resolved_by)
        VALUES %s
        ON CONFLICT (isin) DO UPDATE SET
          name=EXCLUDED.name, route=EXCLUDED.route,
          scripcode=COALESCE(EXCLUDED.scripcode, shp.universe.scripcode),
          nse_symbol=COALESCE(EXCLUDED.nse_symbol, shp.universe.nse_symbol),
          nse_index=EXCLUDED.nse_index, in_spine=EXCLUDED.in_spine,
          resolved_by=EXCLUDED.resolved_by, updated_at=now()
    """, rows, page_size=500)
    conn.commit()
    cur.execute("SELECT route, in_spine, count(*) FROM shp.universe "
                "GROUP BY 1,2 ORDER BY 1,2")
    if verbose:
        print("\nuniverse written:")
        for route, in_spine, n in cur.fetchall():
            print(f"   {route:4s} in_spine={str(in_spine):5s} {n:5d}")
    return len(rows)


# ------------------------------------------------------------------- enqueue

_Q = re.compile(r"^([A-Za-z]+)\s+(\d{4})$")
_MON = {"march": "03-31", "june": "06-30", "september": "09-30",
        "december": "12-31"}


def _qend(label, iso=None):
    """'June 2026' -> date(2026,6,30). BSE also emits '18 Feb 2017' one-offs."""
    if iso:
        return iso[:10]
    m = _Q.match((label or "").strip())
    if m and m.group(1).lower() in _MON:
        return f"{m.group(2)}-{_MON[m.group(1).lower()]}"
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%d %B %Y"):
        try:
            return datetime.strptime(label.strip(), fmt).date().isoformat()
        except Exception:                        # noqa: BLE001
            pass
    return None


def index_bse(scripcode, quarters):
    j = getj(bse_op(), f"{BSE_API}/SHPQNewFormat/w?scripcode={scripcode}")
    out = []
    for r in (j or {}).get("Table", []):
        f = (r.get("XbrlFile") or "").strip()
        if not f:
            continue
        out.append(dict(label=r.get("qtr"),
                        qend=_qend(r.get("qtr")),
                        filed_at=r.get("filing_date_time"),
                        url=BSE_XBRL + f))
    out = [r for r in out if r["qend"]]
    return out[:quarters] if quarters else out


def index_nse(symbol, index, quarters):
    j = getj(nse_op(), f"{NSE_MASTER}?index={index}&symbol={symbol}")
    out = []
    for r in (j or []):
        u = (r.get("xbrl") or "").strip()
        if not u:
            continue
        d = None
        try:
            d = datetime.strptime(r.get("date", ""), "%d-%b-%Y").date().isoformat()
        except Exception:                        # noqa: BLE001
            pass
        if not d:
            continue
        out.append(dict(label=r.get("date"), qend=d,
                        filed_at=r.get("submissionDate"), url=u))
    return out[:quarters] if quarters else out


def enqueue(conn, quarters, workers, limit=None, only_missing=True):
    cur = conn.cursor()
    q = "SELECT isin, route, scripcode, nse_symbol, nse_index FROM shp.universe"
    if only_missing:
        q += (" u WHERE NOT EXISTS (SELECT 1 FROM shp.queue k "
              "WHERE k.isin = u.isin)")
    q += " ORDER BY in_spine DESC, isin"
    if limit:
        q += f" LIMIT {int(limit)}"
    cur.execute(q)
    targets = cur.fetchall()
    print(f"enqueue: {len(targets)} companies, "
          f"{quarters or 'ALL'} quarters each, {workers} workers", flush=True)

    lock = threading.Lock()
    stats = {"ok": 0, "empty": 0, "fail": 0, "rows": 0}
    batch = []

    def one(t):
        isin, route, code, sym, idx = t
        try:
            if route == "bse" and code:
                found = index_bse(code, quarters)
                # A handful of companies carry a BSE code that files nothing —
                # the 59xxxx migrated-listing range (FACT, MADRASFERT,
                # BHARATRAS, ANDHRSUGAR all index 0 on BSE and 8 on NSE).
                # Fall back rather than drop a real company; the filing is
                # NSE-sourced so promoter labels will be masked, which
                # has_promoter_labels records.
                if not found and sym:
                    found = index_nse(sym, idx or "equities", quarters)
                    if found:
                        route = "nse"
            elif sym:
                found = index_nse(sym, idx or "equities", quarters)
            else:
                found = []
        except Exception as e:                   # noqa: BLE001
            with lock:
                stats["fail"] += 1
            return
        with lock:
            if not found:
                stats["empty"] += 1
            else:
                stats["ok"] += 1
                stats["rows"] += len(found)
                for f in found:
                    batch.append((isin, route, code, sym, idx, f["label"],
                                  f["qend"], f["filed_at"], f["url"]))
            n = stats["ok"] + stats["empty"] + stats["fail"]
            if n % 250 == 0:
                print(f"   indexed {n}/{len(targets)}  filings={stats['rows']}",
                      flush=True)
            if len(batch) >= 4000:
                _flush_queue(conn, batch)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, targets))
    _flush_queue(conn, batch)
    print(f"enqueue done: indexed={stats['ok']} empty={stats['empty']} "
          f"failed={stats['fail']} queued={stats['rows']}")


def _flush_queue(conn, batch):
    if not batch:
        return
    cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO shp.queue
          (isin,route,scripcode,nse_symbol,nse_index,quarter_label,
           quarter_end,filed_at,url)
        VALUES %s ON CONFLICT (isin,url) DO NOTHING
    """, batch, page_size=1000)
    conn.commit()
    batch.clear()


# ----------------------------------------------------------------- the worker

CAT_COLS = ["shareholders", "shares", "shares_fully_paid", "pct",
            "voting_rights", "pct_voting", "shares_diluted", "pct_diluted",
            "demat", "locked_in", "locked_in_pct", "enc_total", "enc_pledged",
            "enc_ndu", "enc_other", "enc_pct", "convertibles",
            "esop_outstanding", "depository_receipts"]
HOLD_COLS = ["shareholders", "shares", "pct", "voting_rights", "pct_voting",
             "shares_diluted", "pct_diluted", "demat", "locked_in",
             "locked_in_pct", "enc_total", "enc_pledged", "enc_ndu",
             "enc_other", "enc_pct"]
SBO_COLS = ["sbo_name", "sbo_nationality", "registered_owner",
            "registered_owner_nationality", "held_since", "by_shares",
            "by_voting_rights", "by_dividend", "by_control",
            "by_significant_influence"]

PROMOTER = "ShareholdingOfPromoterAndPromoterGroupMember"
PUBLIC = "PublicShareholdingMember"
NPNP = "SharesHeldByNonPromoterNonPublicShareholdersMember"
WHOLE = "ShareholdingPatternMember"


def store(conn, job, doc, raw):
    """Write one parsed filing. ISIN comes from the queue, never the document."""
    jid, isin, route, code, sym, idx, label, qend, filed, url = job
    m = doc["meta"]
    C = {c["category"]: c for c in doc["categories"]}
    pr, pu, np_ = C.get(PROMOTER, {}), C.get(PUBLIC, {}), C.get(NPNP, {})
    whole = C.get(WHOLE, {})
    enc, shares = pr.get("enc_total"), pr.get("shares")
    labels = any(h.get("promoter_type") for h in doc["holders"])

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO shp.filings
          (isin,scripcode,symbol,company_name,quarter_end,filed_at,taxonomy,
           source,url,sha256,total_shares,promoter_pct,public_pct,npnp_pct,
           pct_sum,promoter_shares,promoter_encumbered,promoter_encumbered_pct,
           promoter_pledged,promoter_ndu,promoter_other_enc,
           has_promoter_labels,n_categories,n_holders,n_sbo,meta)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s)
        ON CONFLICT (isin,quarter_end,source) DO UPDATE SET
          url=EXCLUDED.url, sha256=EXCLUDED.sha256, meta=EXCLUDED.meta,
          promoter_pct=EXCLUDED.promoter_pct, public_pct=EXCLUDED.public_pct,
          promoter_encumbered=EXCLUDED.promoter_encumbered,
          promoter_encumbered_pct=EXCLUDED.promoter_encumbered_pct,
          has_promoter_labels=EXCLUDED.has_promoter_labels,
          n_holders=EXCLUDED.n_holders, n_sbo=EXCLUDED.n_sbo
        RETURNING id
    """, (isin, code or m.get("scripcode"), sym or m.get("symbol"),
          m.get("company_name"), qend, filed, m.get("taxonomy"), route, url,
          hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest(),
          whole.get("shares") or m.get("total_shares"),
          pr.get("pct"), pu.get("pct"), np_.get("pct"),
          (pr.get("pct") or 0) + (pu.get("pct") or 0) + (np_.get("pct") or 0),
          shares, enc,
          (100.0 * enc / shares) if (enc and shares) else None,
          pr.get("enc_pledged"), pr.get("enc_ndu"), pr.get("enc_other"),
          labels, len(doc["categories"]), len(doc["holders"]),
          len(doc["sbo"]), Json(m)))
    fid = cur.fetchone()[0]

    cur.execute("DELETE FROM shp.category WHERE filing_id=%s", (fid,))
    cur.execute("DELETE FROM shp.holder   WHERE filing_id=%s", (fid,))
    cur.execute("DELETE FROM shp.sbo      WHERE filing_id=%s", (fid,))

    if doc["categories"]:
        execute_values(cur, f"""INSERT INTO shp.category
            (filing_id,category,{','.join(CAT_COLS)}) VALUES %s
            ON CONFLICT (filing_id,category) DO NOTHING""",
            [(fid, c["category"], *[c.get(k) for k in CAT_COLS])
             for c in doc["categories"]], page_size=200)
    if doc["holders"]:
        execute_values(cur, f"""INSERT INTO shp.holder
            (filing_id,name,bucket,promoter_type,holder_category,
             {','.join(HOLD_COLS)}) VALUES %s""",
            [(fid, h["name"][:400], h.get("bucket"), h.get("promoter_type"),
              h.get("holder_category"), *[h.get(k) for k in HOLD_COLS])
             for h in doc["holders"]], page_size=500)
    if doc["sbo"]:
        execute_values(cur, f"""INSERT INTO shp.sbo
            (filing_id,{','.join(SBO_COLS)}) VALUES %s""",
            [(fid, *[s.get(k) for k in SBO_COLS]) for s in doc["sbo"]],
            page_size=200)
    return fid


def run(dsn_fin, workers, limit=None, reset_stale=True):
    if reset_stale:
        c = psycopg2.connect(dsn_fin)
        cur = c.cursor()
        cur.execute("UPDATE shp.queue SET state='pending' WHERE state='running' "
                    "AND updated_at < now() - interval '20 minutes'")
        n = cur.rowcount
        c.commit()
        cur.execute("SELECT state, count(*) FROM shp.queue GROUP BY 1")
        print(f"requeued stale running: {n}   queue: {dict(cur.fetchall())}")
        c.close()

    stop = threading.Event()
    stats = {"done": 0, "err": 0, "t0": time.time()}
    lock = threading.Lock()
    cap = [int(limit) if limit else None]

    def worker(_):
        conn = psycopg2.connect(dsn_fin)
        conn.autocommit = False
        cur = conn.cursor()
        while not stop.is_set():
            with lock:
                if cap[0] is not None and cap[0] <= 0:
                    break
                if cap[0] is not None:
                    cap[0] -= 1
            cur.execute("""
                UPDATE shp.queue SET state='running', attempts=attempts+1,
                       updated_at=now()
                WHERE id = (SELECT id FROM shp.queue
                            WHERE state='pending' AND attempts < 3
                            ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
                RETURNING id,isin,route,scripcode,nse_symbol,nse_index,
                          quarter_label,quarter_end,filed_at,url
            """)
            row = cur.fetchone()
            conn.commit()
            if not row:
                break
            try:
                op = bse_op() if row[2] == "bse" else nse_op()
                raw = fetch(op, row[9])
                doc = shp_parse.parse(raw)
                store(conn, row, doc, raw)
                cur.execute("UPDATE shp.queue SET state='done', error=NULL, "
                            "updated_at=now() WHERE id=%s", (row[0],))
                conn.commit()
                with lock:
                    stats["done"] += 1
            except Exception as e:               # noqa: BLE001
                conn.rollback()
                cur.execute("UPDATE shp.queue SET state=CASE WHEN attempts>=3 "
                            "THEN 'error' ELSE 'pending' END, error=%s, "
                            "updated_at=now() WHERE id=%s",
                            (f"{type(e).__name__}: {str(e)[:400]}", row[0]))
                conn.commit()
                with lock:
                    stats["err"] += 1
            with lock:
                n = stats["done"] + stats["err"]
                if n % 100 == 0:
                    el = time.time() - stats["t0"]
                    print(f"   {n} filings  {stats['err']} err  "
                          f"{n/max(el,1)*60:.0f}/min", flush=True)
        conn.close()

    try:
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(worker, range(workers)))
    except KeyboardInterrupt:
        stop.set()
        print("\ninterrupted — queue state is durable, rerun --run to resume")
    el = time.time() - stats["t0"]
    print(f"\nrun done: {stats['done']} stored, {stats['err']} errors, "
          f"{el/60:.1f} min ({stats['done']/max(el,1)*60:.0f}/min)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--create-table", action="store_true")
    ap.add_argument("--build-universe", action="store_true")
    ap.add_argument("--enqueue", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--quarters", type=int, default=8,
                    help="newest N quarters per company; 0 = full history")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    fin = dsn("FINANCIALS_DSN")
    conn = psycopg2.connect(fin)

    if a.create_table:
        conn.cursor().execute(DDL)
        conn.commit()
        print("schema shp created")
    if a.build_universe:
        build_universe(conn)
    if a.enqueue:
        enqueue(conn, a.quarters, a.workers, a.limit)
    if a.run:
        conn.close()
        run(fin, a.workers, a.limit)
        conn = psycopg2.connect(fin)
    if a.stats or not any([a.create_table, a.build_universe, a.enqueue, a.run]):
        cur = conn.cursor()
        for label, q in [
            ("queue", "SELECT state, count(*) FROM shp.queue GROUP BY 1 ORDER BY 2 DESC"),
            ("filings by source", "SELECT source, count(*), count(distinct isin) "
                                  "FROM shp.filings GROUP BY 1"),
            ("rows", "SELECT (SELECT count(*) FROM shp.category), "
                     "(SELECT count(*) FROM shp.holder), "
                     "(SELECT count(*) FROM shp.sbo)"),
        ]:
            try:
                cur.execute(q)
                print(f"{label}: {cur.fetchall()}")
            except Exception as e:               # noqa: BLE001
                conn.rollback()
                print(f"{label}: n/a ({str(e)[:60]})")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
