"""Build a local dev store in charto_bars.db from yfinance, with no Kite login.

Why this exists. The two documented ways to (re)build the store both assume
something a fresh laptop does not have:

  company_tables.py slim   needs an EXISTING full charto_bars.db as its source,
                           so it cannot help when that file is the 0-byte one
                           sqlite invented.
  hydrate_symbol.py        needs charto/data/.blob_sas, and it only ever runs
                           `INSERT INTO bars` — it never creates the table, so
                           against an empty DB it fails the same way.
  backfill_1min.py         creates the schema but needs an ACTIVE Kite session,
                           and the daily token expires ~6 AM IST.

yfinance is already the project's documented automatic fallback for market
data, so this uses it to get a chart-usable store standing again offline.

This is a DEV store, deliberately not the 29 GB production one:
yfinance serves at most 60 days of 1-minute history (and only 8 days per
request), so intraday depth is ~1-2 months rather than back to 2015. Daily
bars go back years and are fetched in full. That is enough for the chart, the
company page and the screener to work on the seeded symbols.

Because the source is not Kite, every row is tagged in `sync_state` so the
relay can say so rather than implying Kite freshness.

Run:  pivot/.venv/Scripts/python charto/data/hydrate_yf.py RELIANCE TCS
      pivot/.venv/Scripts/python charto/data/hydrate_yf.py --default
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "charto_bars.db"

# The canonical schema, copied verbatim from catchup_remote.BARS_DDL and
# import_universe_daily.DDL. It must stay byte-identical to those: the
# dataserver reads (ts,o,h,l,c,v) positionally in several hot paths.
BARS_DDL = ("CREATE TABLE IF NOT EXISTS bars ("
            "symbol TEXT NOT NULL, ts INTEGER NOT NULL, o REAL, h REAL, "
            "l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts))")
BARS_1D_DDL = ("CREATE TABLE IF NOT EXISTS bars_1d ("
               "symbol TEXT NOT NULL, ts INTEGER NOT NULL, "
               "o REAL, h REAL, l REAL, c REAL, v INTEGER, "
               "PRIMARY KEY (symbol, ts))")
IX_DDL = "CREATE INDEX IF NOT EXISTS ix_bars_sym_ts ON bars(symbol, ts)"
SYNC_DDL = ("CREATE TABLE IF NOT EXISTS sync_state ("
            "key TEXT PRIMARY KEY, value TEXT)")

# A small, deliberately boring spread: two index proxies and the large caps the
# demo prompts reach for. Enough to exercise the chart without a long download.
DEFAULT = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
           "SBIN", "NIFTYBEES", "BANKBEES"]

# yfinance wants an exchange suffix; the store keys on the bare NSE symbol,
# which is what the dataserver and the chart's `symbol=` param both use.
SUFFIX = ".NS"


def _open() -> sqlite3.Connection:
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")     # readers keep working mid-insert
    con.execute("PRAGMA busy_timeout=60000")   # a live tick writer may hold it
    con.execute(BARS_DDL)
    con.execute(BARS_1D_DDL)
    con.execute(IX_DDL)
    con.execute(SYNC_DDL)
    con.commit()
    return con


def _rows(frame, symbol: str) -> list[tuple]:
    """DataFrame -> the store's positional tuple, in epoch SECONDS UTC.

    `ts` is UTC throughout the store; IST_OFF is applied by the dataserver at
    read time, never here. Writing an IST-shifted epoch would put every bar
    5:30 out and the error would look like bad data, not a bad timestamp.
    """
    out: list[tuple] = []
    for stamp, row in frame.iterrows():
        o, h, l_, c = row["Open"], row["High"], row["Low"], row["Close"]
        # yfinance yields NaN rows for halted/illiquid minutes. A NaN would
        # become a NULL price and read downstream as a real quote of nothing.
        if any(v != v for v in (o, h, l_, c)):
            continue
        vol = row["Volume"]
        out.append((symbol, int(stamp.timestamp()),
                    float(o), float(h), float(l_), float(c),
                    int(0 if vol != vol else vol)))
    return out


def _write(con: sqlite3.Connection, table: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    con.executemany(
        f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


def hydrate(symbol: str, con: sqlite3.Connection) -> tuple[int, int]:
    import yfinance as yf

    ticker = yf.Ticker(f"{symbol}{SUFFIX}")

    # Daily first: it is the series the company page and screener read, it is
    # cheap, and it is the one yfinance serves deep history for.
    daily = ticker.history(period="max", interval="1d", auto_adjust=False)
    n_1d = _write(con, "bars_1d", _rows(daily, symbol))

    # Intraday: 8 days per request, and — measured 2026-09-06, not the 60 the
    # docs imply — a hard 30-day floor ("The requested range must be within the
    # last 30 days"). Walking past it only buys four failed requests per
    # symbol, so the loop stops at 28.
    minute: list[tuple] = []
    for offset in range(0, 28, 7):
        try:
            chunk = ticker.history(
                interval="1m", start=_ago(offset + 7), end=_ago(offset),
                auto_adjust=False)
        except Exception as exc:                      # noqa: BLE001
            print(f"  {symbol}: 1m window -{offset + 7}d failed: "
                  f"{type(exc).__name__}")
            continue
        minute.extend(_rows(chunk, symbol))
    n_1m = _write(con, "bars", minute)
    return n_1d, n_1m


def _ago(days: int) -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=days)).isoformat()


def main(symbols: list[str]) -> None:
    t0 = time.time()
    con = _open()
    total_1d = total_1m = 0
    for sym in symbols:
        try:
            n_1d, n_1m = hydrate(sym, con)
        except Exception as exc:                      # noqa: BLE001
            print(f"FAILED {sym}: {type(exc).__name__}: {exc}")
            continue
        total_1d += n_1d
        total_1m += n_1m
        print(f"  {sym}: {n_1d:,} daily, {n_1m:,} minute")

    # Tag the provenance. CLAUDE.md's rule is that a non-Kite source must be
    # visible rather than implied, and this store is entirely non-Kite.
    con.execute("INSERT OR REPLACE INTO sync_state VALUES (?,?)",
                ("bars_source", "yfinance"))
    con.execute("INSERT OR REPLACE INTO sync_state VALUES (?,?)",
                ("bars_hydrated_at", str(int(time.time()))))
    con.commit()
    con.close()
    print(f"\nstore {DB}")
    print(f"{total_1d:,} daily + {total_1m:,} minute rows over "
          f"{len(symbols)} symbols in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        raise SystemExit("usage: hydrate_yf.py SYMBOL [SYMBOL...] | --default")
    main(DEFAULT if args[0] == "--default" else [a.upper() for a in args])
