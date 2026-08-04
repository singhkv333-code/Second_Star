#!/usr/bin/env python3
"""Volume-profile features for the whole universe, one row per instrument.

Why a swept table rather than a live computation: the screen matrix is built
from bars_1d and takes ~1.3s cold. A volume profile needs 1-MINUTE bars —
about 15k of them per symbol for the two 20-session windows this stores — and
folding those for 500 instruments inside a screen call would put ~12s on the
first screen of every session. The pattern ledger already solved this shape;
this follows it.

What it stores, and why these four:

  vp20_pos            where the last close sits INSIDE the 20-session value
                      area, as a percentage of the area's own width. 0 = at
                      the value-area low, 100 = at the high, >100 = trading
                      above accepted value, <0 = below it. This is the one
                      that answers "who is out of balance".
  vp20_va_width_pct   the value area as a % of the POC — how tightly volume
                      agreed on price. Narrow = coiled/balanced.
  vp20_poc_dist_pct   how far the close is from the most-traded price.
  vp20_poc_shift_pct  this window's POC against the PRIOR 20 sessions' POC.
                      Value migration: the profile equivalent of a trend.

All four are ratios, which is the point — a POC in rupees ranks TCS above
everything and means nothing. Instruments that print no volume (indices,
India VIX) get no row at all; the screener reads that as null, the same way
it already treats turnover.

    python3 sweep_vp.py            # all symbols
    python3 sweep_vp.py RELIANCE   # one, for checking
"""
import sqlite3
import sys
import time

import dataserver as ds

SESSIONS = 20        # the screening window, fixed and named in every feature
_DDL = """
CREATE TABLE IF NOT EXISTS vp_screen (
  symbol        TEXT PRIMARY KEY,
  sessions      INTEGER,
  close         REAL,
  poc           REAL,
  val           REAL,
  vah           REAL,
  pos           REAL,
  va_width_pct  REAL,
  poc_dist_pct  REAL,
  poc_shift_pct REAL,
  n_rows        INTEGER,
  row_h         REAL,
  minute_bars   INTEGER,
  as_of         INTEGER,
  built_at      INTEGER
)
"""


def session_bounds(con, sym, sessions):
    """(start_ts, end_ts) of the last N sessions, and of the N before them.

    Read off bars_1d so a session is whatever the daily fold already called
    one — deriving it again from minute timestamps would let the screener and
    the chart disagree about what "20 sessions" means.
    """
    days = [r[0] for r in con.execute(
        "SELECT ts FROM bars_1d WHERE symbol=? ORDER BY ts", (sym,))]
    if len(days) < sessions + 2:
        return None
    recent = days[-sessions:]
    prior = days[-2 * sessions:-sessions] if len(days) >= 2 * sessions else []
    return (recent[0], recent[-1] + 86400,
            (prior[0], prior[-1] + 86400) if prior else None)


def features(con, sym, sessions=SESSIONS):
    b = session_bounds(con, sym, sessions)
    if not b:
        return None
    lo_ts, hi_ts, prior = b
    mins = con.execute(
        "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts>=? AND ts<? "
        "ORDER BY ts", (sym, lo_ts, hi_ts)).fetchall()
    if not mins:
        return None
    prof = ds._profile(mins)
    if prof is None:          # no volume, or no range — no row, hence null
        return None

    close = mins[-1][4]
    poc, val, vah = prof["poc"], prof["val"], prof["vah"]
    width = vah - val

    shift = None
    if prior:
        pm = con.execute(
            "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts>=? AND ts<? "
            "ORDER BY ts", (sym, prior[0], prior[1])).fetchall()
        pp = ds._profile(pm) if pm else None
        if pp and pp["poc"]:
            shift = round((poc - pp["poc"]) / pp["poc"] * 100, 2)

    return (sym, sessions, ds._px(close, prof["hi"]), poc, val, vah,
            round((close - val) / width * 100, 1) if width else None,
            round(width / poc * 100, 2) if poc else None,
            round((close - poc) / poc * 100, 2) if poc else None,
            shift, prof["n"], ds._px(prof["row_h"], prof["hi"]), len(mins),
            mins[-1][0], int(time.time()))


def main(argv):
    con = sqlite3.connect(ds.DB_PATH)
    con.execute(_DDL)
    only = [a.upper() for a in argv[1:]]
    syms = only or [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM bars_1d ORDER BY symbol")]
    t0 = time.time()
    rows, skipped = [], []
    for i, s in enumerate(syms, 1):
        try:
            f = features(con, s)
        except Exception as exc:            # noqa: BLE001
            skipped.append((s, str(exc)[:60]))
            continue
        if f:
            rows.append(f)
        else:
            skipped.append((s, "no volume / too little history"))
        if i % 100 == 0:
            print(f"  {i}/{len(syms)}  {time.time() - t0:.1f}s", flush=True)

    con.executemany(
        "INSERT OR REPLACE INTO vp_screen VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    con.commit()
    print(f"vp_screen: {len(rows)} instruments in {time.time() - t0:.1f}s "
          f"({len(skipped)} without a profile)")
    for s, why in skipped[:12]:
        print(f"  skip {s}: {why}")
    if len(skipped) > 12:
        print(f"  … and {len(skipped) - 12} more")
    con.close()


if __name__ == "__main__":
    main(sys.argv)
