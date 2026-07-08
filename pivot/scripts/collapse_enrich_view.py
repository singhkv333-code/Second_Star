#!/usr/bin/env python3
"""Collapse the confusingly-paired holding columns in enrich.v_company_enriched.

The view exposed the SAME datum twice in two units:
    held_percent_insiders        -> promoter_holding_proxy   (raw 0..1 fraction)
    held_percent_insiders*100    -> promoter_holding_pct     (percentage)
    held_percent_institutions    -> institution_holding      (raw 0..1 fraction)
    held_percent_institutions*100-> institution_holding_pct  (percentage)

The app (backend/market/enrich_db.py) reads ONLY the *_pct percentages, so the
raw-fraction columns are dead redundancy that reads as "two different values"
in a schema audit. This drops them and keeps one documented percentage column
each. Idempotent, transactional (DROP+CREATE in one txn), read-only view — safe.

Usage:
  cd pivot && .venv/bin/python scripts/collapse_enrich_view.py [--apply]
"""
from __future__ import annotations

import argparse
import os
from sqlalchemy import create_engine, text

HERE = os.path.dirname(os.path.abspath(__file__))

NEW_VIEW = """
CREATE VIEW enrich.v_company_enriched AS
 SELECT sc_id,
        ticker,
        yf_symbol,
        company_name,
        long_name,
        sector,
        industry,
        long_business_summary,
        website,
        full_time_employees,
        city,
        state,
        country,
        market_cap,
        currency,
        round(held_percent_insiders * 100::numeric, 2)     AS promoter_holding_pct,
        round(held_percent_institutions * 100::numeric, 2) AS institution_holding_pct,
        institutions_count,
        fetched_at,
        updated_at
   FROM enrich.company_profile
  WHERE fetch_status = 'ok'::text;
"""

COMMENTS = [
    "COMMENT ON VIEW enrich.v_company_enriched IS "
    "'Read surface (successful fetches only). One promoter/institution holding "
    "column each, as a PERCENTAGE (0-100). promoter_holding_pct is a yfinance "
    "insiders proxy, not the SEBI promoter field.'",
    "COMMENT ON COLUMN enrich.v_company_enriched.promoter_holding_pct IS "
    "'held_percent_insiders * 100 (percent). Proxy for promoter stake.'",
    "COMMENT ON COLUMN enrich.v_company_enriched.institution_holding_pct IS "
    "'held_percent_institutions * 100 (percent).'",
]


def _env() -> dict:
    env = {}
    with open(os.path.join(HERE, "..", ".env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    eng = create_engine(_env()["ENRICH_DSN"], pool_pre_ping=True)

    with eng.connect() as c:
        cols = [r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='enrich' AND table_name='v_company_enriched' "
            "ORDER BY ordinal_position"))]
    print("current view columns:", cols)
    redundant = [x for x in ("promoter_holding_proxy", "institution_holding") if x in cols]
    print("redundant fraction columns to drop:", redundant or "(none — already collapsed)")

    if not args.apply:
        print("\n(dry-run — pass --apply to recreate the view)")
        return
    if not redundant:
        print("nothing to do.")
        return

    with eng.begin() as c:
        c.execute(text("DROP VIEW enrich.v_company_enriched"))
        c.execute(text(NEW_VIEW))
        for stmt in COMMENTS:
            c.execute(text(stmt))
    with eng.connect() as c:
        cols2 = [r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='enrich' AND table_name='v_company_enriched' "
            "ORDER BY ordinal_position"))]
        n = c.execute(text("SELECT count(*) FROM enrich.v_company_enriched")).scalar()
    print("APPLIED. new columns:", cols2)
    print("row count preserved:", n)


if __name__ == "__main__":
    main()
