"""Populate quarterly results for the whole universe from Moneycontrol's appfeeds host.

WHY THIS EXISTS, AND WHY THE EARLIER ATTEMPT FAILED

`mc.statement_lines` holds 18.3M rows and every one of them is
`period_kind='annual'`. There was no quarterly data anywhere in the stack.

The obvious API — `api.moneycontrol.com/mcapi/v1/quarterly-earning/...`, the one
`pivot/scripts/backfill_financials_from_mcapi.py` already uses — is named
"quarterly-earning" but returns `"noofmonths":"12 mths"` on every period. It is
annual data behind a misleading name, which is exactly why the annual table
looks the way it does. Guessing sibling paths on that host found nothing: the
param allowlist is strict (`"frequency" is not allowed`) and ~17 path variants
404'd.

The quarterly grid lives on a DIFFERENT HOST:

    https://appfeeds.moneycontrol.com/jsonapi/stocks/quarterly_results_responsive
        ?sc_id=RI&type_format=quarterly&start=0&limit=200

That host never appears in the page HTML (0 occurrences of "appfeeds",
"jsonapi", "mcapi"), and its two JS chunks do not reference it either, so it is
not discoverable by scraping the page — which is why the earlier hunt, and a
Playwright run that hit Akamai's "Access Denied", both came up empty.

WHAT IT GIVES (measured, RELIANCE)

    117 quarters, Jun '26 back to Jun '97, ZERO gaps
    ~0.15s per request, ~200KB, 42 real metric rows per period
    type_format=quarterly       -> standalone (117 periods)
    type_format=cons_quarterly  -> consolidated (53 periods)
    `limit` saturates at the company's full history; `start` paginates.

Those are the only two type_format values that return anything; ~17 others
(halfyearly, nine_months, yearly, cons_annual, ...) return an empty envelope.

MATCHING — THE ALIAS TRAP

The sc_id we hold in `company_identity.mc_sc_id` is frequently an ALIAS, and the
quarterly feed only answers to the CANONICAL id. This is silent: the feed returns
an empty envelope for an alias, or — worse — a different company's statements.

    company_info?sc_id=BA10  ->  main[0].sc_id = "BA06"   (Bajaj Auto)
    quarterly ... sc_id=BA10 ->  0 periods
    quarterly ... sc_id=BA06 ->  77 periods, 16,461.69 Rs cr

Measured on the companies that failed to reconcile: BA10->BA06, AP31->API
(Asian Paints), AS19->AS28 (DMART: 14.22 -> 18,343.49 Rs cr, i.e. the alias was
returning a company ~1000x smaller), ACC06->ACC, AC18->GAC (Ambuja),
AT14->AT, AW->AW01, AP11->AP26, AL05->AL16. RELIANCE's RI is already canonical,
which is exactly why the first spot-check looked fine and hid the problem.

So every company is resolved through `company_info` first: `main[0].sc_id` is the
key used for the fetch, and `main[0].sc_isinid` is checked against our verified
ISIN in the same call. A bad sc_id silently attaching another company's
financials is the same failure class as AUDIT_screen.md F1, so this verifies
rather than assumes.

`isin_state` records the verdict per row rather than dropping the data, so a
disagreement is visible in SQL instead of being a silent gap. On a 200-company
sample the ISIN agreed 90% of the time, with 2 genuine mismatches
(BHARTIARTL->BAP is Airtel's partly-paid line; SIMPLXMIL differs by share
series) — both kept, both flagged.

The feed does NOT fall back: an unknown sc_id returns an empty envelope rather
than someone else's data, so nothing is fabricated. `start` and `limit` are
both required — omitting them returns 0 rows even for a valid id.

STRUCTURE

Mirrors `mc.statement_lines` column-for-column (statement/basis/period_label/
period_end/period_kind/section/line_item/line_order/value_text/value_numeric/
unit) so quarterly is queryable exactly like the annual data, plus isin/symbol
for joining. Written to pivot_db — `mc` is a shared scraper-owned schema and is
never written to.

    pivot/.venv/bin/python pivotted/load_mc_quarterly.py --limit 25   # pilot
    pivot/.venv/bin/python pivotted/load_mc_quarterly.py              # all
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

PIVOT = Path(__file__).resolve().parent.parent / "pivot"
sys.path.insert(0, str(PIVOT))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
FEED = "https://appfeeds.moneycontrol.com/jsonapi/stocks/"
QUARTERLY = FEED + "quarterly_results_responsive?sc_id={sc}&type_format={tf}&start=0&limit=200"
INFO = FEED + "company_info?sc_id={sc}"

# The two type_format values that return data, mapped to mc.statement_lines' basis.
BASES = {"quarterly": "standalone", "cons_quarterly": "consolidated"}

# Keys that are layout, not data: blank on every period, and they name the
# section the rows beneath them belong to.
SECTION_HEADERS = {
    "EXPENDITURE", "EPS Before Extra Ordinary", "EPS After Extra Ordinary",
    "Public Share Holding", "Promoters and Promoter Group Shareholding",
    "a) Pledged/Encumbered", "b) Non-encumbered",
}
META_KEYS = {"yrc", "yrc0"}

# Moneycontrol opens a block with a header but never closes one, so a naive
# forward-fill drags EXPENDITURE across the result lines beneath it — "P/L
# Before Tax" and "Other Income" are not expenditure. A line that starts a
# result/subtotal closes the open block instead.
def _closes_block(line_item: str) -> bool:
    s = (line_item or "").strip()
    return s.startswith("P/L") or s.startswith("Operating Profit")

DDL = """
CREATE TABLE IF NOT EXISTS quarterly_statement_lines (
  sc_id         TEXT NOT NULL,
  alias_sc_id   TEXT,
  isin          TEXT,
  symbol        TEXT,
  statement     TEXT NOT NULL,
  basis         TEXT NOT NULL,
  period_label  TEXT NOT NULL,
  period_end    DATE NOT NULL,
  period_kind   TEXT NOT NULL,
  section       TEXT,
  line_item     TEXT NOT NULL,
  line_order    INTEGER,
  value_text    TEXT,
  value_numeric NUMERIC,
  unit          TEXT,
  isin_state    TEXT NOT NULL,
  source_url    TEXT,
  scraped_at    TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (sc_id, basis, period_end, line_item)
);
CREATE INDEX IF NOT EXISTS qsl_isin_idx    ON quarterly_statement_lines (isin);
CREATE INDEX IF NOT EXISTS qsl_symbol_idx  ON quarterly_statement_lines (symbol);
CREATE INDEX IF NOT EXISTS qsl_period_idx  ON quarterly_statement_lines (period_end DESC);
CREATE INDEX IF NOT EXISTS qsl_item_idx    ON quarterly_statement_lines (line_item);

COMMENT ON TABLE quarterly_statement_lines IS
 'Quarterly results from appfeeds.moneycontrol.com/jsonapi/stocks/'
 'quarterly_results_responsive. Mirrors mc.statement_lines column-for-column so '
 'quarterly is queryable like the annual data, which it complements: every row '
 'of mc.statement_lines is period_kind=annual. Written to pivot_db because mc '
 'is a shared scraper-owned schema.';
COMMENT ON COLUMN quarterly_statement_lines.sc_id IS
 'Moneycontrol CANONICAL sc_id (company_info.main[0].sc_id) — the only id the '
 'quarterly feed answers to. TRUST THIS ONE for refetching.';
COMMENT ON COLUMN quarterly_statement_lines.alias_sc_id IS
 'The company_identity.mc_sc_id we started from. Often an ALIAS that the '
 'quarterly feed rejects (BA10 vs canonical BA06) or that returns a DIFFERENT '
 'company (AS19 gave a firm ~1000x smaller than DMART). Provenance only — never '
 'refetch with this.';
COMMENT ON COLUMN quarterly_statement_lines.basis IS
 'standalone (type_format=quarterly, the deeper history) or consolidated '
 '(cons_quarterly). Never mix the two in one series: for RELIANCE Jun-26 they '
 'are 163,631 and 309,468 Rs cr respectively.';
COMMENT ON COLUMN quarterly_statement_lines.isin_state IS
 'verified  = Moneycontrol company_info.sc_isinid equals our company_identity '
 'ISIN, so this sc_id is provably the right company. '
 'mismatch = MC reports a DIFFERENT ISIN (usually a partly-paid or alternate '
 'share series). Data is kept but must not be trusted as this symbol. '
 'unverified = company_info returned nothing to check against. '
 'Filter on isin_state=''verified'' for anything analytical.';
COMMENT ON COLUMN quarterly_statement_lines.line_item IS
 'Moneycontrol''s OWN row label, stored verbatim. TWO TRAPS. '
 '(1) Depreciation arrives as the truncated string ''depreciat'' in the standard '
 'template — WHERE line_item=''Depreciation'' silently misses every '
 'non-financial company. (2) The vocabulary is TEMPLATE-dependent: banks get a '
 'different sheet entirely (Interest Earned / Interest Expended / Gross NPA / '
 'Capital Adequacy Ratio / Return on Assets %) and carry NO '
 '''Net Sales/Income from operations'' at all, so a revenue query on one '
 'line_item quietly drops the whole banking sector.';
COMMENT ON COLUMN quarterly_statement_lines.section IS
 'Nearest preceding block header (EXPENDITURE, EPS Before Extra Ordinary, ...). '
 'POSITIONAL, not semantic: Moneycontrol opens blocks but never closes them, so '
 'this is the header above the row, closed heuristically at the first P/L or '
 'Operating Profit line. Group on line_item, not on this.';
COMMENT ON COLUMN quarterly_statement_lines.unit IS
 'rs_crore — Moneycontrol reports this grid in Rs Crore. EPS and percentage '
 'rows are NOT in crore; check line_item before scaling.';
COMMENT ON COLUMN quarterly_statement_lines.period_end IS
 'Last calendar day of the quarter-ending month decoded from the feed''s yrc '
 '(202606 -> 2026-06-30). The feed carries no filing/availability date, so this '
 'is a PERIOD end, not a date the market knew the number — do not use it as an '
 'event date for a study without joining result_filings.broadcast_at.';
"""

_ctx = None


def _ssl_ctx():
    global _ctx
    if _ctx is None:
        try:
            import certifi
            _ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _ctx = ssl.create_default_context()
    return _ctx


def fetch(url: str, tries: int = 3):
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.moneycontrol.com/"})
        try:
            with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
                body = r.read()
            return json.loads(body) if body else None
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError):
            if attempt == tries:
                return None
            time.sleep(0.7 * attempt)
    return None


def resolve(sc: str) -> tuple[str | None, str | None]:
    """Alias sc_id -> (canonical sc_id, Moneycontrol's ISIN).

    The id we hold is often an alias the quarterly feed will not answer to, so
    this call is not optional — see the module docstring's alias table.
    """
    j = fetch(INFO.format(sc=urllib.parse.quote(sc)))
    main = (j or {}).get("main") if isinstance(j, dict) else None
    if isinstance(main, list) and main:
        m = main[0] or {}
        return ((m.get("sc_id") or "").strip() or None,
                (m.get("sc_isinid") or "").strip().upper() or None)
    return None, None


def period_end(yrc) -> date | None:
    """202606 -> 2026-06-30. The feed's own period key, not a filing date."""
    s = str(yrc or "").strip()
    if len(s) != 6 or not s.isdigit():
        return None
    y, m = int(s[:4]), int(s[4:])
    if not (1900 <= y <= 2100 and 1 <= m <= 12):
        return None
    return date(y, m, calendar.monthrange(y, m)[1])


def to_num(v):
    """'129,857.00' -> 129857.0 ; '--' and '' -> None. Never guesses a 0."""
    s = str(v or "").strip().replace(",", "")
    if s in ("", "--", "-", "N.A.", "NA"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def parse(sc, alias, isin, symbol, basis, periods, state, url):
    """Wide period dicts -> long rows, preserving section and display order."""
    rows = []
    now_keys = list(periods[0].keys()) if periods else []
    order = {k: i for i, k in enumerate(now_keys)}
    for rec in periods:
        pe = period_end(rec.get("yrc"))
        if pe is None:
            continue
        label = (rec.get("yrc0") or "").strip() or pe.isoformat()
        section = None
        for k in now_keys:
            if k in META_KEYS:
                continue
            if k in SECTION_HEADERS:
                section = k
                continue
            if _closes_block(k):
                section = None
            raw = rec.get(k)
            txt = str(raw).strip() if raw is not None else ""
            num = to_num(txt)
            if num is None and txt in ("", "--"):
                continue          # absent, not zero — do not store a hole
            rows.append((sc, alias, isin, symbol, "quarterly_results", basis,
                         label, pe, "quarterly", section, k, order.get(k), txt,
                         num, "rs_crore", state, url))
    return rows


def do_company(rec):
    sym, alias, our_isin = rec
    canon, their = resolve(alias)
    if canon is None:
        return sym, alias, "unresolved", []      # not on this host at all
    if their is None:
        state = "unverified"
    elif our_isin and their == (our_isin or "").strip().upper():
        state = "verified"
    else:
        state = "mismatch"
    out = []
    for tf, basis in BASES.items():
        url = QUARTERLY.format(sc=urllib.parse.quote(canon), tf=tf)
        j = fetch(url)
        data = (j or {}).get("data") if isinstance(j, dict) else None
        if isinstance(data, list) and data:
            out += parse(canon, alias, our_isin, sym, basis, data, state, url)
    return sym, canon, state, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(PIVOT / ".env")
    except ImportError:
        pass
    import psycopg2
    from psycopg2.extras import execute_values

    db = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = db.cursor()
    cur.execute(DDL)
    db.commit()

    cur.execute("""SELECT verified_symbol, mc_sc_id, isin FROM company_identity
                   WHERE mc_is_primary AND mc_sc_id IS NOT NULL
                   ORDER BY mc_metric_count DESC NULLS LAST""")
    todo = cur.fetchall()
    if a.limit:
        todo = todo[:a.limit]
    print(f"companies: {len(todo)}  workers={a.workers}", flush=True)

    t0 = time.time()
    states = {"verified": 0, "mismatch": 0, "unverified": 0, "unresolved": 0}
    n_rows = n_empty = 0
    SQL = ("INSERT INTO quarterly_statement_lines (sc_id,alias_sc_id,isin,symbol,"
           "statement,basis,period_label,period_end,period_kind,section,line_item,"
           "line_order,value_text,value_numeric,unit,isin_state,source_url,"
           "scraped_at) VALUES %s "
           "ON CONFLICT (sc_id,basis,period_end,line_item) DO UPDATE SET "
           "value_numeric=EXCLUDED.value_numeric, value_text=EXCLUDED.value_text,"
           "isin_state=EXCLUDED.isin_state, scraped_at=EXCLUDED.scraped_at")
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for i, (sym, sc, state, rows) in enumerate(pool.map(do_company, todo), 1):
            states[state] += 1
            if not rows:
                n_empty += 1
            elif not a.dry_run:
                execute_values(cur, SQL, [r + (time.strftime("%Y-%m-%d %H:%M:%S"),)
                                          for r in rows], page_size=1000)
                db.commit()
            n_rows += len(rows)
            if i % 50 == 0 or i == len(todo):
                el = time.time() - t0
                print(f"  {i}/{len(todo)}  rows={n_rows:,}  empty={n_empty}  "
                      f"{states}  {el:.0f}s ({i/max(el,1):.1f}/s)", flush=True)

    print(f"\ndone in {time.time()-t0:.0f}s  rows={n_rows:,}  empty={n_empty}  {states}")
    if not a.dry_run:
        cur.execute("""SELECT count(*), count(DISTINCT sc_id),
                              count(DISTINCT period_end), min(period_end), max(period_end)
                       FROM quarterly_statement_lines""")
        print("quarterly_statement_lines:", cur.fetchone())
        cur.execute("""SELECT isin_state, count(*) FROM quarterly_statement_lines
                       GROUP BY 1 ORDER BY 2 DESC""")
        print("by isin_state:", cur.fetchall())
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
