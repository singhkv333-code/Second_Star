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


def _download(url: str) -> bytes:
    """4 ranged streams — the single-stream transfer was half the wall time."""
    head = urllib.request.Request(url, method="HEAD")
    size = int(urllib.request.urlopen(head, timeout=30, context=CTX)
               .headers["Content-Length"])
    if size < 2_000_000:
        return urllib.request.urlopen(url, timeout=120, context=CTX).read()
    from concurrent.futures import ThreadPoolExecutor
    step = -(-size // 4)

    def part(lo: int) -> bytes:
        req = urllib.request.Request(
            url, headers={"Range": f"bytes={lo}-{min(lo + step, size) - 1}"})
        return urllib.request.urlopen(req, timeout=120, context=CTX).read()

    with ThreadPoolExecutor(4) as ex:
        return b"".join(ex.map(part, range(0, size, step)))


def main(symbol: str) -> None:
    t0 = time.time()
    sas = (HERE / ".blob_sas").read_text().strip()
    blob = urllib.parse.quote(f"nse/1min/{symbol}_1min.parquet")
    data = _download(f"{ACCOUNT}/{blob}?{sas}")
    t_dl = time.time()

    import pyarrow.parquet as pq   # pandas costs 0.6 s of import for nothing
    t_ = pq.read_table(io.BytesIO(data))
    epoch = t_.column("epoch").to_numpy()
    rows = zip([symbol] * t_.num_rows, epoch.tolist(),
               (t_.column("o").to_numpy() / 100.0).tolist(),
               (t_.column("h").to_numpy() / 100.0).tolist(),
               (t_.column("l").to_numpy() / 100.0).tolist(),
               (t_.column("c").to_numpy() / 100.0).tolist(),
               t_.column("v").to_numpy().tolist())

    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")     # readers keep working mid-insert
    con.execute("PRAGMA synchronous=OFF")      # derived data — re-fetchable
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
          f"{time.time() - t0:.1f}s (download {t_dl - t0:.1f}s)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: hydrate_symbol.py SYMBOL")
    main(sys.argv[1].upper())
