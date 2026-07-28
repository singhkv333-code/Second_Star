"""Hydrate one symbol into the local Charto store from the blob universe.

Downloads nse/1min/{SYMBOL}_1min.parquet from pivotmarketdata (~7 MB,
2-3 s), converts paise-int prices to rupee REALs and inserts into
charto_bars.db, then copies the symbol's flows (delivery / futures OI /
deals) from the local market-wide flows_market.db. After this the
dataserver serves the symbol fully offline — hydration happens once.

Needs pandas+pyarrow, so it runs under the pivot venv; the dataserver
(stdlib-only by design) shells out to it:

  pivot/.venv/bin/python charto/data/hydrate_symbol.py TCS
"""
from __future__ import annotations

import io
import sqlite3
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import certifi

CTX = ssl.create_default_context(cafile=certifi.where())

HERE = Path(__file__).parent
DB = HERE / "charto_bars.db"
FLOWS = HERE / "flows_market.db"
ACCOUNT = "https://pivotmarketdata.blob.core.windows.net/kite-1min"


def main(symbol: str) -> None:
    t0 = time.time()
    sas = (HERE / ".blob_sas").read_text().strip()
    blob = urllib.parse.quote(f"nse/1min/{symbol}_1min.parquet")
    data = urllib.request.urlopen(f"{ACCOUNT}/{blob}?{sas}", timeout=120,
                                  context=CTX).read()

    import pandas as pd
    df = pd.read_parquet(io.BytesIO(data))
    rows = list(zip([symbol] * len(df), df["epoch"].tolist(),
                    (df["o"] / 100.0).tolist(), (df["h"] / 100.0).tolist(),
                    (df["l"] / 100.0).tolist(), (df["c"] / 100.0).tolist(),
                    df["v"].tolist()))

    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")     # readers keep working mid-insert
    con.execute("PRAGMA busy_timeout=10000")
    con.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)", rows)

    flows = {"delivery": 0, "fut_oi": 0, "deals": 0}
    if FLOWS.exists():
        con.execute("ATTACH DATABASE ? AS mkt", (str(FLOWS),))
        for t in ("delivery", "fut_oi"):
            cur = con.execute(f"INSERT OR REPLACE INTO {t} "
                              f"SELECT * FROM mkt.{t} WHERE symbol=?", (symbol,))
            flows[t] = cur.rowcount
        con.execute("DELETE FROM deals WHERE symbol=?", (symbol,))
        flows["deals"] = con.execute(
            "INSERT INTO deals SELECT * FROM mkt.deals WHERE symbol=?",
            (symbol,)).rowcount
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM bars WHERE symbol=?", (symbol,)).fetchone()[0]
    con.close()
    print(f"HYDRATED {symbol}: {n:,} bars, flows {flows}, "
          f"{time.time() - t0:.1f}s")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: hydrate_symbol.py SYMBOL")
    main(sys.argv[1].upper())
