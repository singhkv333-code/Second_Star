#!/usr/bin/env python3
"""The universe screen's features on a synthetic store.

Daily bars for two stocks and the opening minutes of one of them are written
to a scratch database, so every number asserted here is arithmetic on bars
this file made.

Run: python3 test_screen_features.py   (exit 0 = all pass)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

_DB = os.path.join(tempfile.mkdtemp(), "screen.db")
os.environ["CHARTO_DB"] = _DB

DAY = 86400
T0 = 1_700_000_000 // DAY * DAY - 5 * 3600 - 1800      # a midnight, IST


def _daily(base: float, drift: float, n: int = 260) -> list[tuple]:
    out, px = [], base
    for i in range(n):
        o = px
        c = px + drift + (0.6 if i % 3 else -0.4)
        out.append((T0 + i * DAY, o, max(o, c) + 1, min(o, c) - 1, c, 10_000 + i))
        px = c
    return out


def _seed() -> None:
    con = sqlite3.connect(_DB)
    for t in ("bars_1d", "bars"):
        con.execute(f"CREATE TABLE {t} (symbol TEXT, ts INTEGER, o REAL, h REAL, "
                    "l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts))")
    con.execute("CREATE TABLE classification (symbol TEXT PRIMARY KEY, name TEXT, "
                "industry TEXT)")
    up, down = _daily(100, 0.5), _daily(300, -0.5)
    con.executemany("INSERT INTO bars_1d VALUES ('UPCO',?,?,?,?,?,?)", up)
    con.executemany("INSERT INTO bars_1d VALUES ('DOWNCO',?,?,?,?,?,?)", down)
    con.executemany("INSERT INTO classification VALUES (?,?,?)",
                    [("UPCO", "Up Co", "software"), ("DOWNCO", "Down Co", "software")])
    # UPCO's last session: the first 30 minutes trade in 200-210, and the day
    # closes well above that, so it broke out of its opening range.
    last = up[-1][0] + 3 * 3600 + 45 * 60                 # 09:15 IST
    mins = [(last + m * 60, 205, 210 if m < 15 else 208, 200, 205, 100)
            for m in range(30)]
    con.executemany("INSERT INTO bars VALUES ('UPCO',?,?,?,?,?,?)", mins)
    con.commit()
    con.close()


_seed()
import dataserver as ds  # noqa: E402  (reads CHARTO_DB at import)

FAILS: list[str] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if not cond else ""))
    if not cond:
        FAILS.append(name)


feats = ds._screen_features()
up, down = feats["UPCO"], feats["DOWNCO"]
for k in ("gap_pct", "ret_open", "close_pos", "hi20_break_pct", "vol_ratio20",
          "streak", "adx14", "macd_hist_pct", "bb_pct_b", "bb_width_pct",
          "stoch_k", "supertrend_dir"):
    check(f"daily feature {k} computed", up.get(k) is not None, up.get(k))
check("uptrend reads up on Supertrend and MACD",
      up["supertrend_dir"] == 1 and up["macd_hist_pct"] is not None)
check("downtrend reads down on Supertrend", down["supertrend_dir"] == -1,
      down["supertrend_dir"])
check("opening range: close above the first 15 minutes' high",
      up["orb15_pos"] is not None and up["orb15_pos"] > 100, up.get("orb15_pos"))
check("no minute bars, no opening range", down.get("orb15_pos") is None)

res = ds.tool_screen_universe(filters=[{"feature": "orb15_pos", "op": "gt",
                                        "value": 100}])
check("an opening-range-breakout screen finds the breakout",
      [r["symbol"] for r in res.get("rows", [])] == ["UPCO"], res.get("rows"))
cov = res.get("minute_bar_coverage") or {}
check("the screen reports its minute-bar coverage",
      cov.get("scored") == 1 and cov.get("pool") == 2, cov)

bad = ds.tool_screen_universe(pattern="opening range breakout")
names = bad.get("available", {}).get("chart", [])
check("an unknown pattern lists names as words",
      names and all("_" not in n for n in names), names[:5])
ok = ds.tool_screen_universe(pattern="Bull Flag")
check("a pattern named in words is accepted", "error" not in ok, ok.get("error"))

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
