"""NSE F&O history as tables the model can query: every day's options and
futures from NSE's bhavcopy, and, while a Kite session is up, the live chain
snapshotted through the day.

Nothing here computes PCR, max pain, buildup or IV percentile. Those are a
few lines of SQL or Python over these rows, and the model writes them for the
question it was asked, through `query` or a play's SQL measure input. The job
of this module is only to keep the rows complete, honest about when each one
became known, and cheap to read.

  fo_eod   one row per contract per trading day (options within 15% of the
           underlying that had open interest or trades, and every future).
           `known` is when the row became public: 18:00 IST on its day, when
           NSE has posted the file. A play's measure reads it by `known`, so
           an intraday rule never sees the evening's numbers in the morning.
  fo_snap  the live chain every few minutes during market hours for the
           underlyings being watched; `ts` is the moment it was read.

Units follow options.py: OI, OI change and volume in CONTRACTS (lots).
The file is its own SQLite database beside the bars store, ATTACHed read-only
to the connections `query` and the play measures open, so its tables read
like any other.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import options

log = logging.getLogger("fo_store")

PATH = Path(os.environ.get("CHARTO_FO_DB") or Path(__file__).resolve().parent / "fo_hist.db")
DAYS = int(os.environ.get("CHARTO_FO_DAYS") or 60)            # trading days kept and backfilled
SNAP_EVERY = int(os.environ.get("CHARTO_FO_SNAP_MIN") or 3) * 60
SNAP_DEFAULT = tuple(x.strip().upper() for x in
                     (os.environ.get("CHARTO_FO_SNAP") or "NIFTY,BANKNIFTY").split(",") if x.strip())
BAND = 0.15                     # options kept within this fraction of the underlying
KNOWN_HOUR = 18                 # IST hour a day's file is treated as public

TABLES = {
    "fo_eod": "NSE F&O, one row per contract per trading day (bhavcopy). day YYYY-MM-DD; known = epoch "
              "seconds when it became public (18:00 IST that day: use it as the time column in a play's "
              "SQL measure); und = underlying (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, stocks like "
              "RELIANCE); inst OPT or FUT; expiry; strike and opt (CE/PE, empty for futures); open, high, "
              "low, close, settle (premium or future price); und_close; oi, oi_chg, volume in contracts "
              "(lots); value_cr traded value in crore; lot; symbol (the NFO tradingsymbol). Options are "
              "kept within 15% of the underlying when they had OI or trades. In expiry week OI rolls from "
              "the near month to the next, so sum oi and oi_chg across expiries for an underlying's buildup.",
    "fo_snap": "The live option chain read every few minutes in market hours (only while a Kite session "
               "is up) for watched underlyings: ts epoch seconds; und; expiry; strike; opt; ltp, bid, ask, "
               "iv (percent); oi, oi_chg (since the previous day's close), volume in contracts; und_px.",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fo_eod (
  day TEXT NOT NULL, known INTEGER NOT NULL, und TEXT NOT NULL, inst TEXT NOT NULL,
  expiry TEXT NOT NULL, strike REAL, opt TEXT NOT NULL DEFAULT '',
  open REAL, high REAL, low REAL, close REAL, settle REAL, und_close REAL,
  oi INTEGER, oi_chg INTEGER, volume INTEGER, value_cr REAL, lot INTEGER, symbol TEXT);
CREATE INDEX IF NOT EXISTS fo_eod_und ON fo_eod(und, day, expiry);
CREATE TABLE IF NOT EXISTS fo_snap (
  ts INTEGER NOT NULL, und TEXT NOT NULL, expiry TEXT NOT NULL, strike REAL, opt TEXT,
  ltp REAL, bid REAL, ask REAL, iv REAL, oi INTEGER, oi_chg INTEGER, volume INTEGER, und_px REAL);
CREATE INDEX IF NOT EXISTS fo_snap_und ON fo_snap(und, ts);
CREATE TABLE IF NOT EXISTS fo_days (day TEXT PRIMARY KEY, rows INTEGER NOT NULL, loaded INTEGER NOT NULL);
"""

_wlock = threading.Lock()
_stop = threading.Event()
STATS = {"days": 0, "rows": 0, "snaps": 0, "errors": 0, "last": None}
watch_fn = None                 # dataserver sets it: () -> set of underlyings armed plays trade


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(PATH, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def attach(con: sqlite3.Connection) -> bool:
    """ATTACH the store read-only to a reader's connection (opened with
    uri=True), so fo_eod and fo_snap resolve unqualified."""
    if not PATH.exists():
        return False
    try:
        con.execute("ATTACH DATABASE ? AS fo", (f"file:{PATH}?mode=ro",))
        return True
    except sqlite3.OperationalError:
        return False


# ── the daily file ───────────────────────────────────────────────────────────

def _fetch(day: date) -> bytes | None:
    """The day's zip straight from NSE (options.py keeps only a fortnight on
    disk; the history lives in the table, not in zips). None = no file."""
    key = day.strftime("%Y%m%d")
    cached = options.CACHE / f"fo_{key}.csv.zip"
    if cached.exists() and cached.stat().st_size > 1000:
        return cached.read_bytes()
    req = urllib.request.Request(options.URL.format(d=key), headers=options.UA)
    try:
        with urllib.request.urlopen(req, timeout=25, context=options._ctx()) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse(raw: bytes, day: date) -> list[tuple]:
    z = zipfile.ZipFile(io.BytesIO(raw))
    text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    known = int(datetime(day.year, day.month, day.day, KNOWN_HOUR, 0, tzinfo=options.IST).timestamp())
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        tp = r.get("FinInstrmTp")
        if tp not in ("IDO", "STO", "IDF", "STF"):
            continue
        lot = int(_f(r.get("NewBrdLotQty"))) or 1
        und_px, k = _f(r.get("UndrlygPric")), _f(r.get("StrkPric"))
        oi, vol = _f(r.get("OpnIntrst")), _f(r.get("TtlTradgVol"))
        opt = tp in ("IDO", "STO")
        if opt and (not (oi or vol) or not und_px or abs(k / und_px - 1) > BAND):
            continue
        out.append((r.get("TradDt") or day.isoformat(), known, r["TckrSymb"], "OPT" if opt else "FUT",
                    r["XpryDt"], k if opt else None, r.get("OptnTp") if opt else "",
                    _f(r.get("OpnPric")), _f(r.get("HghPric")), _f(r.get("LwPric")), _f(r.get("ClsPric")),
                    _f(r.get("SttlmPric")), und_px, round(oi / lot), round(_f(r.get("ChngInOpnIntrst")) / lot),
                    int(vol), round(_f(r.get("TtlTrfVal")) / 1e7, 4), lot, r.get("FinInstrmNm", "")))
    return out


def ingest(day: date) -> int | None:
    """Load one day. Returns its row count, 0 for a day with no file (a
    holiday), None when NSE could not be reached."""
    key = day.isoformat()
    with _wlock:
        con = _con()
        try:
            if con.execute("SELECT 1 FROM fo_days WHERE day=?", (key,)).fetchone():
                return -1
        finally:
            con.close()
    try:
        raw = _fetch(day)
    except Exception as exc:                                # noqa: BLE001 — an outage, not a holiday
        STATS["errors"] += 1
        log.warning("fo_store: %s fetch failed: %s", key, exc)
        return None
    if raw is None:
        if day < datetime.now(options.IST).date():
            with _wlock:
                con = _con()
                con.execute("INSERT OR REPLACE INTO fo_days VALUES (?,?,?)", (key, 0, int(time.time())))
                con.commit()
                con.close()
        return 0
    rows = parse(raw, day)
    with _wlock:
        con = _con()
        try:
            con.execute("DELETE FROM fo_eod WHERE day=?", (key,))
            con.executemany(f"INSERT INTO fo_eod VALUES ({','.join('?' * 19)})", rows)
            con.execute("INSERT OR REPLACE INTO fo_days VALUES (?,?,?)", (key, len(rows), int(time.time())))
            con.commit()
        finally:
            con.close()
    STATS["days"] += 1
    STATS["rows"] += len(rows)
    STATS["last"] = key
    return len(rows)


def _weekdays_back(n: int):
    d = datetime.now(options.IST).date()
    if datetime.now(options.IST).hour < KNOWN_HOUR:
        d -= timedelta(days=1)           # today's file is not out yet
    seen = 0
    while seen < n:
        if d.weekday() < 5:
            seen += 1
            yield d
        d -= timedelta(days=1)


def backfill(days: int = DAYS, pause: float = 0.4) -> dict:
    """The last `days` weekdays, newest first, skipping what is loaded. Stops
    at the first outage (it is retried on the next pass)."""
    got = 0
    for d in _weekdays_back(days):
        n = ingest(d)
        if n is None:
            break
        if n and n > 0:
            got += 1
            time.sleep(pause)
    _trim(days)
    return {"loaded": got, **STATS}


def _trim(days: int) -> None:
    with _wlock:
        con = _con()
        try:
            keep = [r[0] for r in con.execute("SELECT day FROM fo_days WHERE rows > 0 ORDER BY day DESC "
                                              "LIMIT ?", (days,))]
            if len(keep) >= days:
                con.execute("DELETE FROM fo_eod WHERE day < ?", (keep[-1],))
                con.execute("DELETE FROM fo_days WHERE day < ?", (keep[-1],))
            cut = int(time.time()) - 20 * 86400                  # snapshots: four weeks
            con.execute("DELETE FROM fo_snap WHERE ts < ?", (cut,))
            con.commit()
        finally:
            con.close()


# ── the live chain, through the day ─────────────────────────────────────────

def _market_open(now: datetime) -> bool:
    hm = now.hour * 100 + now.minute
    return now.weekday() < 5 and 915 <= hm <= 1530


def snapshot(und: str) -> int:
    """One read of the live chain (nearest expiry, 15 strikes a side). Stored
    only when the quotes are live: an end-of-day chain is already in fo_eod."""
    ch = options.chain(und, None, strikes=15, live=True)
    if not ch.get("available") or ch.get("source") != "kite":
        return 0
    ts = int(time.time())
    rows = []
    for r in ch.get("rows") or []:
        for side in ("ce", "pe"):
            q = r.get(side)
            if not q:
                continue
            rows.append((ts, ch["underlying"], ch["expiry"], r["strike"], side.upper(), q.get("ltp"),
                         q.get("bid"), q.get("ask"), q.get("iv"), q.get("oi"), q.get("oi_chg"),
                         q.get("volume"), ch.get("spot")))
    if rows:
        with _wlock:
            con = _con()
            con.executemany(f"INSERT INTO fo_snap VALUES ({','.join('?' * 13)})", rows)
            con.commit()
            con.close()
        STATS["snaps"] += 1
    return len(rows)


def watched() -> set[str]:
    out = set(SNAP_DEFAULT)
    if watch_fn:
        try:
            out |= {str(x).upper() for x in watch_fn() or ()}
        except Exception:                                   # noqa: BLE001
            pass
    return out


def _loop() -> None:
    last_fill = 0.0
    while not _stop.is_set():
        try:
            now = datetime.now(options.IST)
            if time.time() - last_fill > 1800:
                backfill()
                last_fill = time.time()
            if _market_open(now):
                for und in sorted(watched()):
                    snapshot(und)
        except Exception:                                   # noqa: BLE001
            STATS["errors"] += 1
            log.warning("fo_store: pass failed", exc_info=True)
        _stop.wait(SNAP_EVERY)


def start() -> threading.Thread:
    _con().close()
    th = threading.Thread(target=_loop, name="fo-store", daemon=True)
    th.start()
    return th


def stop() -> None:
    _stop.set()
