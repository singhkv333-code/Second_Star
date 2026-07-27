#!/usr/bin/env python3
"""Sync a benchmark index's daily bars into the local Charto store.

The attribution split in explain_move needs one reference series: without it
a market-wide selloff is indistinguishable from a stock story. This syncs
NIFTY 50 daily OHLC into a `benchmark` table, same pattern as
sync_results.py — synced once, read locally at request time.

Source is yfinance (^NSEI). Indices are the sanctioned yfinance lane (the
Kite-primary contract lists indices as its fallback case), and the `source`
column carries the tag so every reader can relay it. When a Kite session is
live, re-running with --kite upgrades the rows in place.

Run:  cd pivot && .venv/bin/python ../charto/data/sync_benchmark.py
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS benchmark (
  symbol     TEXT NOT NULL,          -- 'NIFTY 50'
  trade_date TEXT NOT NULL,          -- ISO session date (IST)
  o REAL, h REAL, l REAL, c REAL,
  source     TEXT NOT NULL,          -- 'yfinance EOD' | 'kite'
  PRIMARY KEY (symbol, trade_date)
);
"""


def from_yfinance() -> list[tuple]:
    import yfinance as yf
    d = yf.download("^NSEI", start="2015-01-01", progress=False,
                    auto_adjust=False)
    if d is None or d.empty:
        raise SystemExit("yfinance returned nothing for ^NSEI")
    if hasattr(d.columns, "levels"):          # flatten the ticker level
        d.columns = d.columns.get_level_values(0)
    rows = []
    for idx, r in d.iterrows():
        if not float(r["Close"]) > 0:
            continue
        rows.append(("NIFTY 50", idx.date().isoformat(),
                     float(r["Open"]), float(r["High"]),
                     float(r["Low"]), float(r["Close"]), "yfinance EOD"))
    return rows


def from_kite() -> list[tuple]:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pivot"))
    from datetime import datetime, timedelta, timezone
    from backend.database import SessionLocal
    from backend.brokers.sessions import get_active_kite_session
    from backend.kite.auth import get_authenticated_kite, read_kite_access_token

    db = SessionLocal()
    try:
        session = get_active_kite_session(db)
    finally:
        db.close()
    tok = read_kite_access_token(session) if session else None
    if not tok or len(tok) < 20 or tok.startswith("mock_"):
        raise SystemExit("Kite token is mock/expired — re-login, or run without --kite")
    kite = get_authenticated_kite(tok)
    NIFTY_TOKEN = 256265                     # NSE:NIFTY 50 instrument token
    rows, start = [], datetime(2015, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    while start < now:                        # daily cap is 2000d per request
        end = min(start + timedelta(days=1900), now)
        for c in kite.historical_data(NIFTY_TOKEN, start, end, "day") or []:
            rows.append(("NIFTY 50", c["date"].date().isoformat(),
                         float(c["open"]), float(c["high"]),
                         float(c["low"]), float(c["close"]), "kite"))
        start = end
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).with_name("charto_bars.db")))
    ap.add_argument("--kite", action="store_true",
                    help="pull from Kite instead of yfinance (needs live session)")
    args = ap.parse_args()

    rows = from_kite() if args.kite else from_yfinance()
    con = sqlite3.connect(args.db)
    con.executescript(DDL)
    con.executemany(
        "INSERT OR REPLACE INTO benchmark VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    n, lo, hi, src = con.execute(
        "SELECT COUNT(*), MIN(trade_date), MAX(trade_date), "
        "GROUP_CONCAT(DISTINCT source) FROM benchmark WHERE symbol='NIFTY 50'"
    ).fetchone()
    print(f"benchmark: {n} sessions {lo} → {hi} · source(s): {src}")
    con.close()


if __name__ == "__main__":
    main()
