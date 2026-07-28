"""Fill bars_1d — the daily table the universe screener reads.

Two modes, one table:

  seed          fold every LOCALLY hydrated symbol's 1-min bars into daily
                bars, so screening works today against the handful of symbols
                this machine has.
  <path>.db     ATTACH the VM-built universe_daily.db and absorb its bars_1d
                wholesale. INSERT OR REPLACE, so the real 500-symbol rows
                simply overwrite the seeded few and re-running is harmless.

The fold math is dataserver._fold_daily itself, imported rather than copied:
a second implementation would eventually stamp a different daily epoch than
the chart does, and the screener would answer about days get_bars never shows.
Importing dataserver is safe — it only serves under __main__.

  python3 charto/data/import_universe_daily.py seed
  python3 charto/data/import_universe_daily.py /path/to/universe_daily.db
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dataserver  # noqa: E402

DDL = ("CREATE TABLE IF NOT EXISTS bars_1d ("
       "symbol TEXT NOT NULL, ts INTEGER NOT NULL, "
       "o REAL, h REAL, l REAL, c REAL, v INTEGER, "
       "PRIMARY KEY (symbol, ts))")


def _open() -> sqlite3.Connection:
    con = sqlite3.connect(dataserver.DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(DDL)
    return con


def _report(con: sqlite3.Connection) -> None:
    n, syms, newest = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol), MAX(ts) FROM bars_1d"
    ).fetchone()
    print(f"bars_1d: {n:,} daily bars across {syms} symbols"
          + (f", newest {dataserver._ist(newest, False)}" if newest else ""))


def seed() -> None:
    con = _open()
    syms = [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM bars ORDER BY symbol")]
    if not syms:
        print("no 1-min bars stored locally — nothing to seed")
        return
    print(f"seeding bars_1d from {len(syms)} locally hydrated symbols")
    t0 = time.time()
    for sym in syms:
        t1 = time.time()
        daily = dataserver._fold_daily(con.execute(
            "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? ORDER BY ts", (sym,)))
        con.executemany("INSERT OR REPLACE INTO bars_1d VALUES (?,?,?,?,?,?,?)",
                        [(sym, *b) for b in daily])
        con.commit()
        print(f"  {sym:<12} {len(daily):>5} days  {time.time() - t1:5.1f}s")
    print(f"seeded in {time.time() - t0:.1f}s")
    _report(con)


def absorb(path: str) -> None:
    src = Path(path).expanduser().resolve()
    if not src.exists():
        sys.exit(f"no such file: {src}")
    con = _open()
    before = con.execute("SELECT COUNT(*) FROM bars_1d").fetchone()[0]
    con.execute("ATTACH DATABASE ? AS u", (str(src),))
    try:
        n, syms = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM u.bars_1d").fetchone()
    except sqlite3.Error as exc:
        sys.exit(f"{src.name} has no readable bars_1d table: {exc}")
    print(f"{src.name}: {n:,} daily bars across {syms} symbols")
    t0 = time.time()
    con.execute("INSERT OR REPLACE INTO bars_1d (symbol,ts,o,h,l,c,v) "
                "SELECT symbol,ts,o,h,l,c,v FROM u.bars_1d")
    # bump the screen-cache version: count+newest miss an absorb that only
    # revises rows in place, so a running dataserver would keep serving the
    # old values forever
    con.execute("CREATE TABLE IF NOT EXISTS screen_meta (version INTEGER)")
    cur = con.execute("SELECT MAX(version) FROM screen_meta").fetchone()[0] or 0
    con.execute("DELETE FROM screen_meta")
    con.execute("INSERT INTO screen_meta VALUES (?)", (cur + 1,))
    con.commit()
    con.execute("DETACH DATABASE u")
    after = con.execute("SELECT COUNT(*) FROM bars_1d").fetchone()[0]
    print(f"absorbed in {time.time() - t0:.1f}s — "
          f"{before:,} -> {after:,} rows ({after - before:+,} new)")
    _report(con)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "seed":
        seed()
    elif arg:
        absorb(arg)
    else:
        sys.exit(__doc__)
