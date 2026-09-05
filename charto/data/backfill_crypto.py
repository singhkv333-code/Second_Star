"""Backfill full 1-minute crypto spot history into charto/data/charto_bars.db.

Same storage contract as backfill_1min.py — one row per (symbol, minute) in
`bars`, epoch-seconds UTC — so every other interval stays a read-time fold in
the dataserver. No auth anywhere: both venues serve public market data.

Measured 2026-07-29 (8/16/24/32 parallel workers, scales linearly):
  bybit    1000 bars/req, 64k bars/s @32w  -> 25.7M bars in ~7 min
  coinbase  300 bars/req, 7.8k bars/s @24w -> 37.5M bars in ~80 min
Coinbase is the depth source (BTC-USD from 2015-07-20); every Bybit spot pair
starts 2021 because Bybit launched spot late. Naming keeps them distinct with
no collision: Coinbase "BTC-USD", Bybit "BTCUSDT".

Resume-safe: re-running only fetches windows after each symbol's stored MAX(ts),
so a killed run costs nothing. Kill and restart freely.

Run:  python charto/data/backfill_crypto.py                 # everything
      python charto/data/backfill_crypto.py bybit           # one venue
      python charto/data/backfill_crypto.py coinbase BTC-USD ETH-USD
      python charto/data/backfill_crypto.py --gaps coinbase # repair holes only

--gaps exists because the two repair paths above cannot reach a hole. Resume
starts at MAX(ts), which the live websocket keeps pushing forward, and
--refetch re-downloads from inception. Measured 2026-09-06, before the fix:
Bybit 0.00-0.01% of minutes missing, Coinbase 1.1-7.6% on the mature pairs and
worse on AVAX/DOT/XRP, with holes landing on exact multiples of the 300-minute
Coinbase window — the signature of a dropped fetch, not of a quiet market.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "charto_bars.db"

# (symbol, inception) — inception measured 2026-07-29, not guessed.
COINBASE = [
    ("BTC-USD",  "2015-07-20"), ("ETH-USD",  "2016-05-18"),
    ("LTC-USD",  "2016-08-17"), ("XRP-USD",  "2019-02-26"),
    ("LINK-USD", "2019-06-27"), ("ADA-USD",  "2021-03-18"),
    ("DOGE-USD", "2021-06-03"), ("DOT-USD",  "2021-06-16"),
    ("SOL-USD",  "2021-06-17"), ("AVAX-USD", "2021-09-30"),
]
BYBIT = [
    ("BTCUSDT",  "2021-07-01"), ("ETHUSDT",  "2021-07-01"),
    ("XRPUSDT",  "2021-07-01"), ("DOGEUSDT", "2021-08-01"),
    ("LTCUSDT",  "2021-08-01"), ("LINKUSDT", "2021-09-01"),
    ("SOLUSDT",  "2021-10-01"), ("ADAUSDT",  "2021-10-01"),
    ("AVAXUSDT", "2021-11-01"), ("BNBUSDT",  "2022-03-01"),
]

CB_SPAN = 300 * 60      # Coinbase caps a response at 300 candles
BY_SPAN = 1000 * 60     # Bybit caps at 1000
CB_WORKERS, BY_WORKERS = 16, 32   # sustained; burst tested clean at 24/32

_lock = threading.Lock()
_written = {"n": 0}


def _get(url: str, tries: int = 5):
    """GET with backoff. 429 is the only expected failure at this rate."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "charto-backfill/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            if attempt == tries - 1:
                return None
            time.sleep(0.8 * (attempt + 1))
        except Exception:
            if attempt == tries - 1:
                return None
            time.sleep(0.8 * (attempt + 1))
    return None


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")    # readers keep working mid-insert
    con.execute("PRAGMA synchronous=OFF")     # derived data — re-fetchable
    # 30s was not enough: a concurrent daily tail-fold plus a queued DELETE
    # held the write lock past it on 2026-08-02 and this run died mid-symbol
    # with "database is locked", after 8 minutes of fetching. The job is
    # resume-safe so nothing was lost but the time — and a long backfill is
    # exactly the process that should out-wait a short one, not the reverse.
    con.execute("PRAGMA busy_timeout=180000")
    con.execute(
        "CREATE TABLE IF NOT EXISTS bars ("
        " symbol TEXT NOT NULL, ts INTEGER NOT NULL,"
        " o REAL, h REAL, l REAL, c REAL, v INTEGER,"
        " PRIMARY KEY (symbol, ts))"
    )
    con.commit()
    return con


def _resume_from(con: sqlite3.Connection, symbol: str, inception: str,
                 refetch: bool = False, since_days: int = 0) -> int:
    """Start one span before the newest stored bar so the tail bar is re-filled.

    `refetch` ignores what is stored and starts at inception. It exists because
    a stored bar can be WRONG rather than missing, and resume-from-MAX(ts) can
    never repair that: every crypto minute written before the _vol fix had its
    volume truncated toward zero, which zeroed 17.3% of BTC-USD's minutes
    outright. Rewriting is INSERT OR REPLACE on (symbol, ts), so a refetch is
    idempotent — it costs bandwidth, never correctness.
    """
    floor = int(datetime.strptime(inception, "%Y-%m-%d")
                .replace(tzinfo=timezone.utc).timestamp())
    if since_days:
        # Coinbase is a TOKEN BUCKET, not a concurrency problem: measured
        # 2026-08-02, the same 8-worker fetch ran at 17,947 bars/s on a fresh
        # bucket and 3,310 bars/s moments later, and 8/16/24/32/48/64 workers
        # all land in the same 2-4k sustained band while 64 starts DROPPING
        # windows. Throwing more requests at it cannot help, so the only real
        # lever is fetching less. Recent history is what the volume profile,
        # the screener and the default chart window actually read, so repair
        # that first and let the deep history run behind it.
        return max(floor, int(time.time()) - since_days * 86400)
    if refetch:
        return floor
    row = con.execute("SELECT MAX(ts) FROM bars WHERE symbol=?", (symbol,)).fetchone()
    if row and row[0]:
        return max(floor, int(row[0]) - BY_SPAN)
    return floor


def _missing_minutes(con: sqlite3.Connection, symbol: str) -> int:
    """Minutes between the first and last stored bar that have no bar."""
    n, lo, hi = con.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM bars WHERE symbol=?",
        (symbol,)).fetchone()
    if not n or lo is None:
        return 0
    return ((hi - lo) // 60 + 1) - n


def _gap_windows(con: sqlite3.Connection, symbol: str, span: int,
                 min_gap: int = 2) -> list[tuple]:
    """Fetch windows covering the HOLES in what is already stored.

    _resume_from() can only ever move forward: it starts one span behind
    MAX(ts), so the moment the live websocket writes a current bar, every
    hole behind it is out of reach of a normal re-run, and --refetch (the
    only other repair path) re-downloads the entire history from inception to
    fix a ten-minute gap. Neither is a gap repair, so gaps accumulated.

    A hole is not automatically our fault, and this deliberately does not
    guess: Coinbase itself serves nothing for 2026-05-08 01:16-07:48 or
    2025-10-25 15:12-21:03 — the same two windows are missing from ADA, AVAX,
    BTC, DOGE, DOT, LINK and SOL at once, which is a venue outage, not a
    fetcher that failed seven times in parallel. Re-requesting those costs one
    empty response each and writes nothing, and the closing report says how
    many minutes are still missing so the two classes stay distinguishable.

    `min_gap` is in MISSING MINUTES. The default of 2 skips single-minute
    holes: on a thin pair a minute with no trade is a real minute with no
    bar, and chasing those would spend most of the run on windows that are
    already correct. Pass --min-gap=1 to sweep them too.
    """
    out: list[tuple] = []
    prev: int | None = None
    for (ts,) in con.execute(
            "SELECT ts FROM bars WHERE symbol=? ORDER BY ts", (symbol,)):
        if prev is not None and (ts - prev) // 60 - 1 >= min_gap:
            s = prev + 60
            while s < ts:
                out.append((symbol, s, min(s + span, ts)))
                s += span
        prev = ts
    return out


# --------------------------------------------------------------------------- venues

def _cb_fetch(args) -> list[tuple]:
    symbol, start, end = args
    s = datetime.fromtimestamp(start, timezone.utc).isoformat()
    e = datetime.fromtimestamp(end, timezone.utc).isoformat()
    d = _get(f"https://api.exchange.coinbase.com/products/{symbol}/candles"
             f"?granularity=60&start={s}&end={e}")
    if not isinstance(d, list):
        # None, NOT []. A window that FAILED and a window that is genuinely
        # empty were the same value here, and that is how 65,549 BTC-USD
        # minutes went missing: _get gives up after 5 tries, the caller
        # extended rows by nothing, and the run reported success. Because
        # _resume_from() then starts at MAX(ts), no later re-run ever comes
        # back for it — the hole is permanent and silent. Measured 2026-09-06:
        # every Coinbase pair carries holes that are exact multiples of
        # CB_SPAN (300 min), which is the fingerprint of dropped windows
        # rather than of a venue that stopped trading.
        return None
    # Coinbase row order is [time, LOW, HIGH, OPEN, CLOSE, volume] — not OHLC.
    return [(symbol, int(r[0]), float(r[3]), float(r[2]),
             float(r[1]), float(r[4]), _vol(r[5])) for r in d]


def _vol(x) -> int | float:
    """Volume, exactly — integral when it is, fractional when it is not.

    `int(float(v))` truncated every crypto minute toward zero, and a fraction
    of a coin is a normal minute's turnover: measured 2026-08-02, 990,989 of
    BTC-USD's 5,731,677 stored minutes (17.3%) carried v=0 for minutes that
    really traded, and the volume profile reads these bars. The `v INTEGER`
    column is not a constraint — SQLite column types are AFFINITY, so a REAL
    that cannot be losslessly narrowed is stored as a REAL — so this needs no
    migration. NSE share counts stay ints and the file stays compact.
    """
    f = float(x or 0)
    return int(f) if f.is_integer() else round(f, 8)


def _by_fetch(args) -> list[tuple]:
    symbol, start, end = args
    d = _get(f"https://api.bybit.com/v5/market/kline?category=spot&symbol={symbol}"
             f"&interval=1&limit=1000&start={start * 1000}&end={end * 1000}")
    try:
        rows = d["result"]["list"]
    except (TypeError, KeyError):
        return None          # see _cb_fetch: unknown is not empty
    return [(symbol, int(r[0]) // 1000, float(r[1]), float(r[2]),
             float(r[3]), float(r[4]), _vol(r[5])) for r in rows]


def run_symbol(con, symbol: str, inception: str, venue: str,
               refetch: bool = False, since_days: int = 0,
               gaps: bool = False, min_gap: int = 2) -> None:
    fetch, span, workers = ((_cb_fetch, CB_SPAN, CB_WORKERS) if venue == "coinbase"
                            else (_by_fetch, BY_SPAN, BY_WORKERS))
    t0 = time.time()
    now = int(time.time())
    if gaps:
        windows = _gap_windows(con, symbol, span, min_gap)
        if not windows:
            print(f"  {symbol:10s} no gaps >= {min_gap} min", flush=True)
            return
        miss = _missing_minutes(con, symbol)
        print(f"  {symbol:10s} {len(windows)} gap windows, "
              f"{miss:,} minutes missing", flush=True)
    else:
        start = _resume_from(con, symbol, inception, refetch, since_days)
        if start >= now - 60:
            print(f"  {symbol:10s} already current", flush=True)
            return
        windows = [(symbol, s, min(s + span, now)) for s in range(start, now, span)]
    total = 0
    lost: list[tuple] = []
    # Chunked so a long symbol commits progress instead of buffering it all.
    CHUNK = workers * 12
    with ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(windows), CHUNK):
            batch = windows[i:i + CHUNK]
            rows: list[tuple] = []
            for win, got in zip(batch, ex.map(fetch, batch)):
                if got is None:
                    lost.append(win)      # unknown, not empty — see _cb_fetch
                else:
                    rows.extend(got)
            if rows:
                with _lock:
                    con.executemany(
                        "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)", rows)
                    con.commit()
                    _written["n"] += len(rows)
                total += len(rows)
            done = min(i + CHUNK, len(windows))
            el = time.time() - t0
            print(f"  {symbol:10s} {done:6d}/{len(windows)} win  {total:9,d} bars  "
                  f"{el:6.1f}s  {total / max(el, .1):8,.0f} b/s", flush=True)

    # One retry, in the same run. A window lost here is a window nothing else
    # will ever come back for, so the retry happens now rather than being left
    # as an exercise for whoever reads the log.
    if lost:
        print(f"  {symbol:10s} {len(lost)} windows failed — retrying", flush=True)
        rows, still = [], []
        with ThreadPoolExecutor(workers) as ex:
            for win, got in zip(lost, ex.map(fetch, lost)):
                if got is None:
                    still.append(win)
                else:
                    rows.extend(got)
        if rows:
            with _lock:
                con.executemany(
                    "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)", rows)
                con.commit()
                _written["n"] += len(rows)
            total += len(rows)
        if still:
            # Named, not swallowed. This is the exact line whose absence let
            # the holes accumulate unnoticed for over a year.
            print(f"  {symbol:10s} WINDOWS LOST: {len(still)} "
                  f"(first {datetime.fromtimestamp(still[0][1], timezone.utc):%Y-%m-%d %H:%M}) "
                  f"— re-run with --gaps to repair", flush=True)

    n, lo, hi = con.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM bars WHERE symbol=?", (symbol,)).fetchone()
    print(f"DONE {symbol:10s} {n:>10,} bars  "
          f"{datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(hi, timezone.utc):%Y-%m-%d %H:%M}  "
          f"{_missing_minutes(con, symbol):,} min still missing  "
          f"{time.time() - t0:.0f}s", flush=True)


def main(argv: list[str]) -> None:
    venues = {"coinbase": COINBASE, "bybit": BYBIT}
    refetch = "--refetch" in argv
    gaps = "--gaps" in argv
    since_days = 0
    min_gap = 2
    for a in argv:
        if a.startswith("--since="):
            since_days = int(a.split("=", 1)[1])
        if a.startswith("--min-gap="):
            min_gap = max(1, int(a.split("=", 1)[1]))
    # An UNRECOGNISED flag used to be dropped here with every other "--" arg,
    # so `--help` — or a typo like `--gap` — silently meant "backfill every
    # symbol from inception", which is hours of fetching and a surprised
    # operator. Refuse instead: a run this long should never be something you
    # started by accident.
    known = ("--refetch", "--gaps", "--since=", "--min-gap=", "--help", "-h")
    unknown = [a for a in argv
               if a.startswith("-") and not a.startswith(tuple(known))]
    if unknown or "--help" in argv or "-h" in argv:
        if unknown:
            print(f"unknown option(s): {' '.join(unknown)}\n", file=sys.stderr)
        print(__doc__)
        print("Options:\n"
              "  --gaps          fetch only the windows that cover holes\n"
              "  --min-gap=N     with --gaps, the smallest hole to repair "
              "(minutes, default 2)\n"
              "  --refetch       re-download from inception, rewriting bars\n"
              "  --since=N       start N days back instead of at MAX(ts)")
        raise SystemExit(0 if not unknown else 2)
    argv = [a for a in argv if not a.startswith("--")]
    which = [a.lower() for a in argv if a.lower() in venues] or list(venues)
    picked = [a.upper() for a in argv if a.upper() not in ("COINBASE", "BYBIT")]
    if refetch:
        print("REFETCH: starting from inception, rewriting stored bars "
              "(volume was truncated before the _vol fix)", flush=True)
    if gaps:
        print(f"GAPS: fetching only windows that cover holes of >= {min_gap} "
              "missing minutes. Idempotent; a hole the venue itself does not "
              "serve stays a hole and is counted in the closing line.",
              flush=True)

    con = _connect()
    t0 = time.time()
    for venue in which:
        pairs = [(s, i) for s, i in venues[venue] if not picked or s in picked]
        if not pairs:
            continue
        print(f"\n=== {venue}: {len(pairs)} symbols ===", flush=True)
        for symbol, inception in pairs:
            run_symbol(con, symbol, inception, venue, refetch, since_days,
                       gaps, min_gap)
    print(f"\nALL DONE: {_written['n']:,} bars written in {time.time() - t0:.0f}s", flush=True)
    con.close()


if __name__ == "__main__":
    main(sys.argv[1:])
