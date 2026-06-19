#!/usr/bin/env python3
"""
Enrich Pivot's company universe with yfinance company profiles.

Reads the company/ticker list from the EXISTING read-only `financials` DB
(mc.companies) and writes enriched profiles into a SEPARATE, newly-created
`pivot_enrich` DB on the same Azure server. The source DB is never written to;
`pivot_db` is never touched at all.

What we enrich (mapped from the user's ask):
  - company profile  -> long_business_summary, website, employees, address
  - sector division  -> sector / sector_key / industry / industry_key
  - promoter holding  -> held_percent_insiders (PROXY; see note), institutions
  - ticker            -> ticker + yf_symbol ({ticker}.NS)

NOTE on "promoter holding": yfinance exposes no true SEBI promoter category.
`heldPercentInsiders` is the closest proxy. We store it labeled as such and
also dump the full `.info` blob into raw_info (JSONB) so a better source can be
back-filled later without re-fetching.

Design properties:
  - RESUMABLE: a row is upserted per sc_id; on restart we skip sc_ids already
    marked fetch_status='ok'. Safe to Ctrl-C / reboot / re-run.
  - DURABLE: each company is committed immediately (no big in-memory buffer).
  - POLITE: a delay between requests + exponential backoff on transient errors,
    so the datacenter IP doesn't get rate-limited (429).
  - HONEST: companies without a ticker are recorded as fetch_status='no_ticker'
    rather than silently dropped.

Run (detached, survives SSH disconnect / laptop power-off):
  cd pivot
  nohup setsid .venv/bin/python scripts/enrich_company_profiles.py \
      > /home/azureuser/enrich.log 2>&1 &
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

# --- config ----------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ENV = dotenv_values(os.path.join(HERE, "..", ".env"))
SRC_DSN = ENV["FINANCIALS_DSN"]                       # read-only source
ENRICH_DSN = SRC_DSN.replace("/financials?", "/pivot_enrich?")  # target

REQUEST_DELAY_S = float(os.environ.get("ENRICH_DELAY", "1.5"))   # polite gap
MAX_RETRIES = 4
BACKOFF_BASE_S = 5.0
PROGRESS_EVERY = 25

_stop = False


def _handle_sigterm(signum, frame):
    global _stop
    _stop = True
    print(f"[{_now()}] received signal {signum}; finishing current row then stopping…", flush=True)


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


DDL = """
CREATE SCHEMA IF NOT EXISTS enrich;

CREATE TABLE IF NOT EXISTS enrich.company_profile (
    sc_id                       text PRIMARY KEY,
    company_name                text,
    ticker                      text,
    yf_symbol                   text,
    -- profile
    long_name                   text,
    long_business_summary       text,
    website                     text,
    full_time_employees         integer,
    address1                    text,
    city                        text,
    state                       text,
    zip                         text,
    country                     text,
    phone                       text,
    -- sector division
    sector                      text,
    sector_key                  text,
    industry                    text,
    industry_key                text,
    -- market context
    market_cap                  numeric,
    currency                    text,
    exchange                    text,
    quote_type                  text,
    -- promoter holding (insiders = proxy for promoter; see module docstring)
    held_percent_insiders       numeric,
    held_percent_institutions   numeric,
    institutions_float_percent  numeric,
    institutions_count          integer,
    -- provenance
    raw_info                    jsonb,
    fetch_status                text NOT NULL DEFAULT 'pending',  -- ok|no_data|no_ticker|error
    fetch_error                 text,
    source                      text NOT NULL DEFAULT 'yfinance',
    fetched_at                  timestamptz,
    updated_at                  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_company_profile_ticker  ON enrich.company_profile (ticker);
CREATE INDEX IF NOT EXISTS ix_company_profile_sector  ON enrich.company_profile (sector);
CREATE INDEX IF NOT EXISTS ix_company_profile_status  ON enrich.company_profile (fetch_status);

COMMENT ON COLUMN enrich.company_profile.held_percent_insiders IS
  'yfinance heldPercentInsiders. PROXY for SEBI promoter holding (no true promoter field in yfinance). 0..1 fraction.';
COMMENT ON TABLE enrich.company_profile IS
  'yfinance-enriched company profiles for Pivot. Linked to financials.mc.companies via sc_id. Source DB is read-only.';
"""


def _f(v):
    """Coerce yfinance numerics to float or None (drop NaN/inf)."""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _i(v):
    f = _f(v)
    return int(f) if f is not None else None


def _jsonable(info: dict) -> dict:
    """Strip non-JSON-serialisable values from the info blob."""
    out = {}
    for k, v in info.items():
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)
    return out


def load_universe(conn):
    """Distinct companies from the source DB. One row per sc_id.

    Picks the best ticker per sc_id (non-null preferred). Returns list of
    dicts with sc_id, company_name, ticker.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT sc_id,
                   max(company_name) AS company_name,
                   max(NULLIF(ticker, '')) AS ticker
            FROM mc.companies
            WHERE is_active
            GROUP BY sc_id
            """
        )
        return cur.fetchall()


def already_done(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT sc_id FROM enrich.company_profile WHERE fetch_status = 'ok';")
        return {r[0] for r in cur.fetchall()}


UPSERT = """
INSERT INTO enrich.company_profile (
    sc_id, company_name, ticker, yf_symbol,
    long_name, long_business_summary, website, full_time_employees,
    address1, city, state, zip, country, phone,
    sector, sector_key, industry, industry_key,
    market_cap, currency, exchange, quote_type,
    held_percent_insiders, held_percent_institutions,
    institutions_float_percent, institutions_count,
    raw_info, fetch_status, fetch_error, fetched_at, updated_at
) VALUES (
    %(sc_id)s, %(company_name)s, %(ticker)s, %(yf_symbol)s,
    %(long_name)s, %(long_business_summary)s, %(website)s, %(full_time_employees)s,
    %(address1)s, %(city)s, %(state)s, %(zip)s, %(country)s, %(phone)s,
    %(sector)s, %(sector_key)s, %(industry)s, %(industry_key)s,
    %(market_cap)s, %(currency)s, %(exchange)s, %(quote_type)s,
    %(held_percent_insiders)s, %(held_percent_institutions)s,
    %(institutions_float_percent)s, %(institutions_count)s,
    %(raw_info)s, %(fetch_status)s, %(fetch_error)s, %(fetched_at)s, now()
)
ON CONFLICT (sc_id) DO UPDATE SET
    company_name = EXCLUDED.company_name,
    ticker = EXCLUDED.ticker,
    yf_symbol = EXCLUDED.yf_symbol,
    long_name = EXCLUDED.long_name,
    long_business_summary = EXCLUDED.long_business_summary,
    website = EXCLUDED.website,
    full_time_employees = EXCLUDED.full_time_employees,
    address1 = EXCLUDED.address1, city = EXCLUDED.city, state = EXCLUDED.state,
    zip = EXCLUDED.zip, country = EXCLUDED.country, phone = EXCLUDED.phone,
    sector = EXCLUDED.sector, sector_key = EXCLUDED.sector_key,
    industry = EXCLUDED.industry, industry_key = EXCLUDED.industry_key,
    market_cap = EXCLUDED.market_cap, currency = EXCLUDED.currency,
    exchange = EXCLUDED.exchange, quote_type = EXCLUDED.quote_type,
    held_percent_insiders = EXCLUDED.held_percent_insiders,
    held_percent_institutions = EXCLUDED.held_percent_institutions,
    institutions_float_percent = EXCLUDED.institutions_float_percent,
    institutions_count = EXCLUDED.institutions_count,
    raw_info = EXCLUDED.raw_info,
    fetch_status = EXCLUDED.fetch_status,
    fetch_error = EXCLUDED.fetch_error,
    fetched_at = EXCLUDED.fetched_at,
    updated_at = now();
"""


def fetch_one(yf, sc_id, company_name, ticker):
    """Fetch a single company's yfinance profile. Returns a params dict."""
    base = {
        "sc_id": sc_id, "company_name": company_name, "ticker": ticker,
        "yf_symbol": None, "long_name": None, "long_business_summary": None,
        "website": None, "full_time_employees": None, "address1": None,
        "city": None, "state": None, "zip": None, "country": None, "phone": None,
        "sector": None, "sector_key": None, "industry": None, "industry_key": None,
        "market_cap": None, "currency": None, "exchange": None, "quote_type": None,
        "held_percent_insiders": None, "held_percent_institutions": None,
        "institutions_float_percent": None, "institutions_count": None,
        "raw_info": None, "fetch_status": "pending", "fetch_error": None,
        "fetched_at": dt.datetime.now(dt.timezone.utc),
    }
    if not ticker:
        base["fetch_status"] = "no_ticker"
        return base

    yf_symbol = f"{ticker}.NS"
    base["yf_symbol"] = yf_symbol

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            info = yf.Ticker(yf_symbol).info or {}
            # yfinance returns a near-empty dict for unknown symbols
            if not info or info.get("quoteType") in (None, "NONE") and len(info) < 5:
                base["fetch_status"] = "no_data"
                base["raw_info"] = json.dumps(_jsonable(info)) if info else None
                return base

            base.update({
                "long_name": info.get("longName") or info.get("shortName"),
                "long_business_summary": info.get("longBusinessSummary"),
                "website": info.get("website"),
                "full_time_employees": _i(info.get("fullTimeEmployees")),
                "address1": info.get("address1"),
                "city": info.get("city"),
                "state": info.get("state"),
                "zip": info.get("zip"),
                "country": info.get("country"),
                "phone": info.get("phone"),
                "sector": info.get("sectorDisp") or info.get("sector"),
                "sector_key": info.get("sectorKey"),
                "industry": info.get("industryDisp") or info.get("industry"),
                "industry_key": info.get("industryKey"),
                "market_cap": _f(info.get("marketCap")),
                "currency": info.get("currency"),
                "exchange": info.get("exchange"),
                "quote_type": info.get("quoteType"),
                "held_percent_insiders": _f(info.get("heldPercentInsiders")),
                "held_percent_institutions": _f(info.get("heldPercentInstitutions")),
                "raw_info": json.dumps(_jsonable(info)),
                "fetch_status": "ok",
            })
            return base
        except Exception as e:  # noqa: BLE001 - network/parse errors vary widely
            last_err = f"{type(e).__name__}: {e}"
            wait = BACKOFF_BASE_S * (2 ** attempt)
            print(f"[{_now()}]   retry {attempt+1}/{MAX_RETRIES} {yf_symbol}: {last_err} (sleep {wait:.0f}s)", flush=True)
            time.sleep(wait)

    base["fetch_status"] = "error"
    base["fetch_error"] = last_err
    return base


def main():
    print(f"[{_now()}] starting enrichment", flush=True)
    print(f"[{_now()}] source (read-only): financials.mc.companies", flush=True)
    print(f"[{_now()}] target            : pivot_enrich.enrich.company_profile", flush=True)

    import yfinance as yf

    src = psycopg2.connect(SRC_DSN)
    src.set_session(readonly=True, autocommit=True)
    tgt = psycopg2.connect(ENRICH_DSN)
    tgt.autocommit = False

    with tgt.cursor() as cur:
        cur.execute(DDL)
    tgt.commit()

    universe = load_universe(src)
    done = already_done(tgt)
    todo = [c for c in universe if c["sc_id"] not in done]

    _limit = int(os.environ.get("ENRICH_LIMIT", "0"))
    if _limit > 0:
        # prefer ticker'd rows for a meaningful smoke test
        todo = sorted(todo, key=lambda c: c["ticker"] is None)[:_limit]
        print(f"[{_now()}] ENRICH_LIMIT={_limit} (smoke test)", flush=True)

    n_total = len(universe)
    n_done = len(done)
    n_todo = len(todo)
    n_with_ticker = sum(1 for c in todo if c["ticker"])
    print(f"[{_now()}] universe={n_total} already_ok={n_done} todo={n_todo} "
          f"(with_ticker={n_with_ticker}, no_ticker={n_todo - n_with_ticker})", flush=True)

    processed = 0
    ok = 0
    for c in todo:
        if _stop:
            print(f"[{_now()}] stop requested — exiting cleanly at {processed} processed", flush=True)
            break
        params = fetch_one(yf, c["sc_id"], c["company_name"], c["ticker"])
        with tgt.cursor() as cur:
            cur.execute(UPSERT, params)
        tgt.commit()
        processed += 1
        if params["fetch_status"] == "ok":
            ok += 1
        if processed % PROGRESS_EVERY == 0:
            print(f"[{_now()}] progress {processed}/{n_todo}  ok={ok}  "
                  f"last={params['yf_symbol']} [{params['fetch_status']}]", flush=True)
        # only sleep when we actually hit the network
        if c["ticker"] and params["fetch_status"] != "no_ticker":
            time.sleep(REQUEST_DELAY_S)

    print(f"[{_now()}] DONE. processed={processed} newly_ok={ok}", flush=True)
    src.close()
    tgt.close()


if __name__ == "__main__":
    main()
