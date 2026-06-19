#!/usr/bin/env python3
"""Map no-ticker companies to yfinance by NAME and enrich them.

Phase 2 of the enrichment. The first pass (enrich_company_profiles.py) used
mc.companies.ticker and left 8,236 rows as fetch_status='no_ticker' because the
source has no ticker for them. This pass resolves a yfinance symbol from the
company NAME (reconstructed from the truncated display name + full slug; see
scripts/_map_resolver.py), validates the match by fuzzy name similarity, then
fetches the same profile fields as phase 1.

Writes ONLY to pivot_enrich (never the source). Resumable: processes rows still
marked 'no_ticker'; a matched row becomes 'ok', an unmatched row becomes
'no_match'. Safe to re-run / reboot.

Run detached:
  cd pivot
  nohup setsid .venv/bin/python scripts/map_no_ticker_companies.py \
      > /home/azureuser/map.log 2>&1 < /dev/null &
"""
import os
import sys
import time
import json
import math
import signal
import datetime as dt
import warnings

warnings.filterwarnings("ignore")

import psycopg2
import psycopg2.extras
from dotenv import dotenv_values

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _map_resolver as R

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = dotenv_values(os.path.join(HERE, "..", ".env"))
SRC_DSN = ENV["FINANCIALS_DSN"]
ENRICH_DSN = SRC_DSN.replace("/financials?", "/pivot_enrich?")

SEARCH_DELAY_S = float(os.environ.get("MAP_DELAY", "0.7"))
THRESHOLD = float(os.environ.get("MAP_THRESHOLD", "0.62"))
PROGRESS_EVERY = 50

_stop = False


def _sig(signum, frame):
    global _stop
    _stop = True
    print(f"[{_now()}] signal {signum}; stopping after current row…", flush=True)


signal.signal(signal.SIGTERM, _sig)
signal.signal(signal.SIGINT, _sig)


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _f(v):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _i(v):
    f = _f(v)
    return int(f) if f is not None else None


def _jsonable(info: dict) -> dict:
    out = {}
    for k, v in info.items():
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)
    return out


DDL_EXTRA = """
ALTER TABLE enrich.company_profile ADD COLUMN IF NOT EXISTS match_score numeric;
ALTER TABLE enrich.company_profile ADD COLUMN IF NOT EXISTS matched_name text;
ALTER TABLE enrich.company_profile ADD COLUMN IF NOT EXISTS resolved_query text;
"""

UPDATE_OK = """
UPDATE enrich.company_profile SET
    ticker = %(ticker)s, yf_symbol = %(yf_symbol)s,
    long_name = %(long_name)s, long_business_summary = %(summary)s,
    website = %(website)s, full_time_employees = %(employees)s,
    address1 = %(address1)s, city = %(city)s, state = %(state)s,
    zip = %(zip)s, country = %(country)s, phone = %(phone)s,
    sector = %(sector)s, sector_key = %(sector_key)s,
    industry = %(industry)s, industry_key = %(industry_key)s,
    market_cap = %(market_cap)s, currency = %(currency)s,
    exchange = %(exchange)s, quote_type = %(quote_type)s,
    held_percent_insiders = %(ins)s, held_percent_institutions = %(inst)s,
    raw_info = %(raw_info)s,
    fetch_status = 'ok', fetch_error = NULL, source = 'yfinance_namematch',
    match_score = %(score)s, matched_name = %(matched_name)s,
    resolved_query = %(query)s, fetched_at = now(), updated_at = now()
WHERE sc_id = %(sc_id)s;
"""

UPDATE_MISS = """
UPDATE enrich.company_profile SET
    fetch_status = %(status)s, source = 'yfinance_namematch',
    resolved_query = %(query)s, fetch_error = %(err)s, updated_at = now()
WHERE sc_id = %(sc_id)s;
"""


def main():
    print(f"[{_now()}] name-mapping pass starting", flush=True)
    import yfinance as yf

    src = psycopg2.connect(SRC_DSN)
    src.set_session(readonly=True, autocommit=True)
    tgt = psycopg2.connect(ENRICH_DSN)
    tgt.autocommit = False
    with tgt.cursor() as cur:
        cur.execute(DDL_EXTRA)
    tgt.commit()

    # rows still pending name resolution
    with tgt.cursor() as cur:
        _lim = int(os.environ.get("MAP_LIMIT", "0"))
        cur.execute("SELECT sc_id, company_name FROM enrich.company_profile "
                    "WHERE fetch_status = 'no_ticker' ORDER BY sc_id"
                    + (f" LIMIT {_lim}" if _lim > 0 else "") + ";")
        pending = cur.fetchall()
    ids = [r[0] for r in pending]
    # slugs from the source (full names)
    slug = {}
    with src.cursor() as cur:
        for chunk_start in range(0, len(ids), 1000):
            chunk = ids[chunk_start:chunk_start + 1000]
            cur.execute("SELECT sc_id, company_name, company_slug FROM mc.companies "
                        "WHERE sc_id = ANY(%s);", (chunk,))
            for sc_id, cname, cslug in cur.fetchall():
                slug[sc_id] = (cname, cslug or "")

    n = len(pending)
    print(f"[{_now()}] {n} no_ticker rows to resolve (threshold={THRESHOLD})", flush=True)

    processed = matched = 0
    for sc_id, cname in pending:
        if _stop:
            print(f"[{_now()}] stop requested at {processed}", flush=True)
            break
        disp, cslug = slug.get(sc_id, (cname, ""))
        m = None
        try:
            m = R.resolve(disp or cname or "", cslug, yf, threshold=THRESHOLD)
        except Exception as e:  # noqa: BLE001
            print(f"[{_now()}]   resolve error {sc_id}: {str(e)[:80]}", flush=True)

        if m:
            sym = m["symbol"]
            base = sym.rsplit(".", 1)[0]
            try:
                info = yf.Ticker(sym).info or {}
            except Exception as e:  # noqa: BLE001
                info = {}
            ins = info.get("heldPercentInsiders")
            inst = info.get("heldPercentInstitutions")
            params = {
                "sc_id": sc_id, "ticker": base, "yf_symbol": sym,
                "long_name": info.get("longName") or info.get("shortName") or m["yahoo_name"],
                "summary": info.get("longBusinessSummary"),
                "website": info.get("website"), "employees": _i(info.get("fullTimeEmployees")),
                "address1": info.get("address1"), "city": info.get("city"),
                "state": info.get("state"), "zip": info.get("zip"),
                "country": info.get("country"), "phone": info.get("phone"),
                "sector": info.get("sectorDisp") or info.get("sector"),
                "sector_key": info.get("sectorKey"),
                "industry": info.get("industryDisp") or info.get("industry"),
                "industry_key": info.get("industryKey"),
                "market_cap": _f(info.get("marketCap")), "currency": info.get("currency"),
                "exchange": info.get("exchange"), "quote_type": info.get("quoteType"),
                "ins": _f(ins), "inst": _f(inst),
                "raw_info": json.dumps(_jsonable(info)) if info else None,
                "score": m["score"], "matched_name": m["yahoo_name"], "query": m["query"],
            }
            with tgt.cursor() as cur:
                cur.execute(UPDATE_OK, params)
            tgt.commit()
            matched += 1
        else:
            with tgt.cursor() as cur:
                cur.execute(UPDATE_MISS, {"sc_id": sc_id, "status": "no_match",
                                          "query": R.spaced_name(disp or "", cslug),
                                          "err": None})
            tgt.commit()

        processed += 1
        if processed % PROGRESS_EVERY == 0:
            print(f"[{_now()}] {processed}/{n}  matched={matched} "
                  f"({matched/processed*100:.0f}%)  last={cname!r}", flush=True)
        time.sleep(SEARCH_DELAY_S)

    print(f"[{_now()}] DONE. processed={processed} matched={matched}", flush=True)
    src.close()
    tgt.close()


if __name__ == "__main__":
    main()
