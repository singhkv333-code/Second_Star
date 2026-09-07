#!/usr/bin/env python3
"""Sync the flows layer — delivery %, futures OI, bulk/block deals — into
the local Charto store.

Two modes, matching how the data actually arrives in real life:

  --from-sweep PATH   one-time import of this symbol's rows from a
                      market-wide bhavcopy sweep DB (the ~5,500-file
                      historical backfill, run once)
  (no args)           nightly top-up straight from NSE's per-symbol
                      historical APIs — 3 requests, a few seconds. Uses
                      the &csv=true export parameter, which bypasses the
                      APIs' undocumented 70-row JSON pagination cap.

Delivery % answers "did real ownership change hands or was it churn";
futures OI answers "was the move fresh positioning or unwinding"; deals
name who traded size. None of that is visible in price alone — which is
exactly why explain_move wants it.
"""
from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

SYMBOL = "RELIANCE"

DDL = """
CREATE TABLE IF NOT EXISTS delivery (
  symbol TEXT, d TEXT, close REAL, qty INTEGER,
  deliv_qty INTEGER, deliv_per REAL, trades INTEGER,
  PRIMARY KEY (symbol, d));
CREATE TABLE IF NOT EXISTS fut_oi (
  symbol TEXT, d TEXT, expiry TEXT, oi INTEGER, oi_chg INTEGER, close REAL,
  PRIMARY KEY (symbol, d, expiry));
CREATE TABLE IF NOT EXISTS deals (
  symbol TEXT, d TEXT, kind TEXT, client TEXT, side TEXT,
  qty INTEGER, price REAL);
"""


def import_sweep(db: sqlite3.Connection, sweep_path: str) -> None:
    db.execute("ATTACH DATABASE ? AS sweep", (sweep_path,))
    for table in ("delivery", "fut_oi"):
        db.execute(f"INSERT OR REPLACE INTO {table} "
                   f"SELECT * FROM sweep.{table} WHERE symbol=?", (SYMBOL,))
    # deals have no natural PK; replace wholesale to stay idempotent
    db.execute("DELETE FROM deals WHERE symbol=?", (SYMBOL,))
    db.execute("INSERT INTO deals SELECT * FROM sweep.deals WHERE symbol=?",
               (SYMBOL,))
    db.commit()
    db.execute("DETACH DATABASE sweep")


def _nse_session():
    import requests
    ua = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
    s = requests.Session()
    s.headers.update(ua)
    s.get("https://www.nseindia.com", timeout=20)
    return s


def _csv_rows(sess, url: str, referer: str) -> list[dict]:
    for attempt in range(3):
        r = sess.get(url, headers={"Referer": referer}, timeout=90)
        # requests guesses latin-1 for these; the BOM then corrupts the
        # first header key and every row silently fails to parse
        r.encoding = "utf-8-sig"
        if r.status_code == 200 and "," in r.text[:400]:
            return [{k.strip(): (v or "").strip() for k, v in row.items() if k}
                    for row in csv.DictReader(io.StringIO(r.text))]
        sess.get("https://www.nseindia.com", timeout=20)   # cookie re-warm
        time.sleep(1 + attempt)
    return []


def _num(s: str) -> float:
    return float((s or "0").replace(",", "") or 0)


def top_up(db: sqlite3.Connection, days: int) -> dict:
    frm = (date.today() - timedelta(days=days)).strftime("%d-%m-%Y")
    to = date.today().strftime("%d-%m-%Y")
    sess = _nse_session()
    got = {"delivery": 0, "fut_oi": 0, "deals": 0}

    rows = _csv_rows(sess,
        "https://www.nseindia.com/api/historicalOR/generateSecurityWiseHistoricalData"
        f"?from={frm}&to={to}&symbol={SYMBOL}&type=priceVolumeDeliverable&series=EQ&csv=true",
        "https://www.nseindia.com/report-detail/eq_security")
    for r in rows:
        try:
            d_iso = time.strftime("%Y-%m-%d", time.strptime(r["Date"], "%d-%b-%Y"))
            db.execute("INSERT OR REPLACE INTO delivery VALUES (?,?,?,?,?,?,?)",
                       (SYMBOL, d_iso, _num(r.get("Close Price")),
                        int(_num(r.get("Total Traded Quantity"))),
                        int(_num(r.get("Deliverable Qty"))),
                        _num(r.get("% Dly Qt to Traded Qty")),
                        int(_num(r.get("No. of Trades")))))
            got["delivery"] += 1
        except (KeyError, ValueError):
            continue

    rows = _csv_rows(sess,
        "https://www.nseindia.com/api/historicalOR/foCPV"
        f"?from={frm}&to={to}&instrumentType=FUTSTK&symbol={SYMBOL}&csv=true",
        "https://www.nseindia.com/report-detail/fo_eq_security")
    for r in rows:
        try:
            d_iso = time.strftime("%Y-%m-%d", time.strptime(r["Date"], "%d-%b-%Y"))
            db.execute("INSERT OR REPLACE INTO fut_oi VALUES (?,?,?,?,?,?)",
                       (SYMBOL, d_iso, r.get("Expiry"),
                        int(_num(r.get("Open Int"))),
                        int(_num(r.get("Change in OI"))),
                        _num(r.get("Close"))))
            got["fut_oi"] += 1
        except (KeyError, ValueError):
            continue

    for kind in ("bulk_deals", "block_deals"):
        rows = _csv_rows(sess,
            "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
            f"?optionType={kind}&from={frm}&to={to}&csv=true",
            "https://www.nseindia.com/report-detail/display-bulk-and-block-deals")
        for r in rows:
            if (r.get("Symbol") or "").strip() != SYMBOL:
                continue
            try:
                d_iso = time.strftime("%Y-%m-%d", time.strptime(r["Date"], "%d-%b-%Y"))
            except (KeyError, ValueError):
                continue
            db.execute("DELETE FROM deals WHERE symbol=? AND d=? AND kind=? "
                       "AND client=? AND qty=?",
                       (SYMBOL, d_iso, kind.split("_")[0],
                        r.get("Client Name"), int(_num(r.get("Quantity Traded")))))
            db.execute("INSERT INTO deals VALUES (?,?,?,?,?,?,?)",
                       (SYMBOL, d_iso, kind.split("_")[0], r.get("Client Name"),
                        r.get("Buy / Sell"), int(_num(r.get("Quantity Traded"))),
                        _num(r.get("Trade Price / Wght. Avg. Price"))))
            got["deals"] += 1
    db.commit()
    return got


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).with_name("charto_bars.db")))
    ap.add_argument("--from-sweep", help="path to a market-wide sweep DB to import")
    ap.add_argument("--days", type=int, default=30,
                    help="top-up window in days (default 30)")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.executescript(DDL)
    if args.from_sweep:
        import_sweep(db, args.from_sweep)
    else:
        print("top-up:", top_up(db, args.days))

    for t in ("delivery", "fut_oi", "deals"):
        print(t, ":", db.execute(
            f"SELECT MIN(d), MAX(d), COUNT(*) FROM {t} WHERE symbol=?",
            (SYMBOL,)).fetchone())
    db.close()


if __name__ == "__main__":
    main()
