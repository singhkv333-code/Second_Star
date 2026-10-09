"""Backfill footprint history from each venue's public trade tape.

    python backfill_footprint.py                 # the streamed majors, last 2 days
    python backfill_footprint.py --days=5 BTCUSDT ETH-USD

Sources, both public and both carrying the aggressor (footprint.aggressor
normalises them):
  Bybit    the daily spot archive, public.bybit.com/spot/<PAIR>/<PAIR>_<day>.csv.gz
           (id,timestamp ms,price,volume,side,…; one file per finished UTC day,
           so today's bars before the stream started have no Bybit source)
  Coinbase /products/<id>/trades, 1,000 per page, walked backward by the
           cb-after cursor until the window is covered

Whole minutes are REPLACED, never added, so a re-run cannot double count; the
newest minute and anything the walk only partly reached are left to the live
stream. Finished Bybit days are recorded in fp_loaded and skipped next time.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import footprint as fp
from backfill_crypto import BYBIT, COINBASE

DB = Path(os.environ.get("CHARTO_DB") or Path(__file__).parent / "charto_bars.db")
UA = {"User-Agent": "charto-footprint/1"}


def _get(url: str, timeout: float = 60) -> tuple[bytes, dict]:
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=timeout, context=fp_ssl()) as r:
                return r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if e.code != 429 and e.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"gave up on {url}")


def fp_ssl():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _add(acc: dict, symbol: str, venue: str, ts: float, px: float, sz: float, side: str) -> None:
    who = fp.aggressor(venue, side)
    if who is None or px <= 0 or sz < 0:
        return
    step = fp.step_for(symbol, px)
    cell = acc[int(ts) // 60 * 60].setdefault(fp._key(px, step), [0.0, 0.0, 0])
    cell[0 if who == "buy" else 1] += sz
    cell[2] += 1


def bybit_day(symbol: str, day: int) -> int:
    """One finished UTC day from the archive. Returns trades read."""
    name = time.strftime("%Y-%m-%d", time.gmtime(day * 86400))
    body, _ = _get(f"https://public.bybit.com/spot/{symbol}/{symbol}_{name}.csv.gz", timeout=180)
    acc: dict = defaultdict(dict)
    n = 0
    for rec in csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(body)), "utf-8")):
        try:
            _add(acc, symbol, "bybit", int(rec["timestamp"]) / 1000, float(rec["price"]),
                 float(rec["volume"]), rec["side"])
            n += 1
        except (KeyError, ValueError):
            continue
    fp.store_minutes(symbol, acc)
    fp.mark_loaded(symbol, day, "bybit-archive", n)
    return n


def coinbase_window(symbol: str, since: int) -> int:
    """Walk the tape back to `since`. Only whole minutes the walk passed
    completely are written, and never the minute still forming."""
    acc: dict = defaultdict(dict)
    cursor, n, oldest = None, 0, None
    newest_whole = int(time.time()) // 60 * 60 - 60
    while True:
        url = f"https://api.exchange.coinbase.com/products/{symbol}/trades?limit=1000"
        if cursor:
            url += f"&after={cursor}"
        body, hdr = _get(url)
        page = json.loads(body)
        if not page:
            break
        for t in page:
            ts = datetime.fromisoformat(t["time"].replace("Z", "+00:00")).timestamp()
            oldest = ts if oldest is None else min(oldest, ts)
            if ts < newest_whole + 60:
                _add(acc, symbol, "coinbase", ts, float(t["price"]), float(t["size"]), t["side"])
                n += 1
        cursor = hdr.get("cb-after") or hdr.get("Cb-After")
        if oldest is not None and oldest < since or not cursor:
            break
        time.sleep(0.15)            # ~6 requests a second, under the public limit
    first_whole = (int(oldest) // 60 + 1) * 60 if oldest is not None else None
    keep = {m: c for m, c in acc.items()
            if first_whole is not None and first_whole <= m <= newest_whole and m >= since}
    fp.store_minutes(symbol, keep)
    return n


def main(argv: list[str]) -> None:
    days = 2
    for a in argv:
        if a.startswith("--days="):
            days = max(1, int(a.split("=", 1)[1]))
    want = [a.upper() for a in argv if not a.startswith("--")]
    syms = want or [s for s, _ in BYBIT] + [s for s, _ in COINBASE]
    fp.bind(DB.with_name("footprint.db"))
    today = int(time.time()) // 86400
    since = (today - days + 1) * 86400
    for sym in syms:
        t0 = time.time()
        try:
            if sym.endswith("-USD"):
                n = coinbase_window(sym, since)
                print(f"DONE {sym:10s} coinbase tape  {n:>9,} trades  {time.time() - t0:5.0f}s", flush=True)
            elif sym.endswith("USDT"):
                n = 0
                for day in range(today - days + 1, today):   # finished days only
                    if fp.loaded(sym, day, "bybit-archive"):
                        continue
                    try:
                        n += bybit_day(sym, day)
                    except urllib.error.HTTPError as e:
                        print(f"  {sym} {day}: archive {e.code}", flush=True)
                print(f"DONE {sym:10s} bybit archive  {n:>9,} trades  {time.time() - t0:5.0f}s", flush=True)
        except Exception as exc:                     # noqa: BLE001 — next symbol
            print(f"FAIL {sym}: {exc}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
