#!/usr/bin/env python3
"""Sync result-filing dates from Azure Postgres into the local Charto store.

Charto's request path is stdlib-only and offline by design — bars live in
SQLite and every tool reads from there. Result dates are the same kind of
fact, so they are synced once rather than fetched per question.

ONE EVENT PER QUARTER. The source table carries 142 filings for 86
quarters: standalone and consolidated are filed separately, and a few
quarters carry a much later re-filing (Q3 FY2016 has both 28 Jan 2016 and
17 Jan 2017). The market reacts to the FIRST time a quarter's numbers
land, so the event is MIN(effective_trade_date) and the rest are counted,
not repeated — otherwise one quarter would vote twice in every study.

`effective_trade_date` already carries the honest reaction session: it
rolls forward for after-market filings (verified: 107/107 post-close
filings roll, 27/27 intraday stay same-day, Friday night → Monday), so
nothing here needs to re-derive it and risk introducing look-ahead.

Usage:  python3 sync_results.py [--db charto_bars.db]
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

TABLE = "charto_result_filings_trial"

DDL = """
CREATE TABLE IF NOT EXISTS results (
  symbol       TEXT NOT NULL,
  quarter      TEXT NOT NULL,
  period_start TEXT,
  period_end   TEXT,
  trade_date   TEXT NOT NULL,   -- ISO date of the first reactable session
  broadcast_at INTEGER,         -- epoch seconds of the first announcement
  after_market INTEGER,         -- 1 when it landed after the close
  filings      INTEGER,         -- source rows collapsed into this event
  PRIMARY KEY (symbol, quarter)
);
CREATE INDEX IF NOT EXISTS idx_results_date ON results(symbol, trade_date);
"""

QUERY = f"""
SELECT symbol,
       quarter_label,
       MIN(period_start)::text,
       MAX(period_end)::text,
       MIN(effective_trade_date)::text          AS trade_date,
       EXTRACT(EPOCH FROM MIN(broadcast_at))::bigint,
       BOOL_OR(after_market_close)::int,
       COUNT(*)                                  AS filings
FROM {TABLE}
WHERE effective_trade_date IS NOT NULL
GROUP BY symbol, quarter_label
ORDER BY 5
"""


def db_url() -> str:
    env = Path(__file__).resolve().parents[2] / "pivot" / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("DATABASE_URL"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DATABASE_URL not found in pivot/.env")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).with_name("charto_bars.db")))
    args = ap.parse_args()

    import psycopg2                      # sync-time only, never on a request

    pg = psycopg2.connect(db_url())
    cur = pg.cursor()
    cur.execute(QUERY)
    rows = cur.fetchall()

    # Say what was DROPPED, not just what was kept — a filing with no
    # effective_trade_date is a quarter we cannot place on a chart, and a
    # silent count of survivors would read as full coverage.
    cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT quarter_label) FROM {TABLE}")
    src_rows, src_quarters = cur.fetchone()
    cur.execute(f"""SELECT COUNT(DISTINCT quarter_label) FROM {TABLE}
                    WHERE quarter_label NOT IN (
                      SELECT quarter_label FROM {TABLE}
                      WHERE effective_trade_date IS NOT NULL)""")
    undated = cur.fetchone()[0]

    con = sqlite3.connect(args.db)
    con.executescript(DDL)
    con.execute("DELETE FROM results")
    con.executemany(
        "INSERT INTO results (symbol, quarter, period_start, period_end, "
        "trade_date, broadcast_at, after_market, filings) "
        "VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()

    bars = con.execute("SELECT MIN(ts), MAX(ts) FROM bars").fetchone()
    import datetime as dt
    b0 = dt.datetime.utcfromtimestamp(bars[0]).date().isoformat() if bars[0] else None
    covered = con.execute(
        "SELECT COUNT(*) FROM results WHERE trade_date >= ?", (b0,)).fetchone()[0] if b0 else 0

    print(f"source        : {src_rows} filings over {src_quarters} quarters")
    print(f"undated       : {undated} quarters have no effective_trade_date "
          f"(cannot be placed on a chart)")
    print(f"events written: {len(rows)} (one per quarter, first filing wins)")
    print(f"span          : {rows[0][4]} → {rows[-1][4]}")
    print(f"with price data: {covered} (local bars start {b0})")
    con.close()


if __name__ == "__main__":
    main()
