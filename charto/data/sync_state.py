#!/usr/bin/env python3
"""Per-symbol freshness watermarks: how far the store is COMPLETE, not how far it goes.

Everything downstream currently infers freshness from `MAX(ts)`, and MAX(ts)
cannot tell a market that closed from a fetcher that died. The case that
motivated this file is RELIANCE: its minute bars end 2026-07-23 14:29. By
MAX(ts) that is the FRESHEST equity in the store — a day newer than the 41
others, which end 2026-07-22 15:29. It is in fact the only broken one. 14:29 is
not a close; it is a backfill that stopped 60 minutes into the session. The
same shape is everywhere once you look: MCX ends 16:17 on a book that runs to
23:30, USDINR ends 12:30, crypto ends ~22:11 on a 24/7 tape.

A pattern, a volume profile or a screen computed over one of those days is
computed over a partial session, and nothing in the row says so. This table
says so.

The rule: session-day D is COMPLETE when the store holds a bar for D at or
after `session_close_for(symbol)` minus one minute of grace — some symbols'
last bar is the close minute itself, some feeds stop a minute early. Day
boundaries come from `dataserver._ist_day` on the symbol's own session anchor
(`day_start = _ist_day(ts, tz_off) * 86400 - tz_off`), which is the exact
arithmetic `_fold_daily` stamps bars_1d with. Re-deriving it here would let the
watermark describe days `get_bars` never draws.

Half-days are judged INCOMPLETE. Muhurat trading, an exchange-declared early
close, a session cut short by a halt — all of them look identical to a dead
fetcher from inside the data, and the only way to tell them apart is a holiday
calendar this deliberately does not carry. The cost of the false positive is a
harmless re-fetch: every writer uses INSERT OR REPLACE, so re-pulling a day
that was already whole changes nothing. The cost of the false NEGATIVE is
silently publishing evidence built on half a session, which is why the rule
errs this way.

    python3 charto/data/sync_state.py            # scan all, print the table
    python3 charto/data/sync_state.py --check    # print only problems, exit 1
    python3 charto/data/sync_state.py --count    # also refresh exact n_bars
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dataserver  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS sync_state (
  symbol                TEXT PRIMARY KEY,
  last_bar_ts           INTEGER,
  last_complete_session INTEGER,
  partial_session       INTEGER,
  n_bars                INTEGER,
  source                TEXT,
  status                TEXT,
  synced_at             INTEGER
)
"""

GRACE_MIN = 1          # see module docstring — one minute, not a fudge factor
_LOOKBACK_DAYS = 400   # how far back to hunt for the last complete session

SOURCES = ("kite_rest", "kite_ws", "bybit", "coinbase", "unknown")


# ── session close ─────────────────────────────────────────────────
# dataserver owns this; the local copy exists only so this module keeps working
# against a dataserver that predates it. Resolved per call rather than at import
# so the real one wins as soon as it lands.
_FX_SYMBOLS = {"USDINR", "EURINR", "GBPINR", "JPYINR"}


def _fallback_session_close(symbol: str) -> int:
    # The INR pairs are checked BEFORE the session anchor, not after it:
    # session_for() puts them on NSE because that is when their day starts, but
    # the currency segment trades to 17:00. Inheriting the 15:29 equity close
    # would mark their day complete 90 minutes early and every top-up after it
    # would skip 15:30-16:59 forever — a hole no MAX(ts) would ever show.
    if symbol in _FX_SYMBOLS:
        return 16 * 60 + 59        # CDS 09:00->16:59 = 480 bars
    session = dataserver.session_for(symbol)
    if session == dataserver.UTC_SESSION:
        return 23 * 60 + 59        # 24/7 tape, last minute of the UTC day
    if session == dataserver.MCX_SESSION:
        return 23 * 60 + 29        # MCX 09:00->23:29 = 870 bars
    return 15 * 60 + 29            # NSE 09:15->15:29 = 375 bars


def session_close_for(symbol: str) -> int:
    """Minute-of-day, in the symbol's own tz, of the last bar of a full session."""
    return getattr(dataserver, "session_close_for", _fallback_session_close)(symbol)


def _day_start(ts: int, tz_off: int) -> int:
    """Epoch of the start of the session-day `ts` falls in — _fold_daily's stamp."""
    return dataserver._ist_day(ts, tz_off) * 86400 - tz_off


def _minute_of_day(ts: int, tz_off: int) -> int:
    return ((ts + tz_off) % 86400) // 60


def is_complete(symbol: str, ts: int) -> bool:
    """Does a bar at `ts` prove its session-day ran to the close?"""
    _, tz_off = dataserver.session_for(symbol)
    return _minute_of_day(ts, tz_off) >= session_close_for(symbol) - GRACE_MIN


def _fmt(ts: int | None, tz_off: int) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts + tz_off, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M")


# ── table ─────────────────────────────────────────────────────────
def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(DDL)


_COLS = ("symbol", "last_bar_ts", "last_complete_session", "partial_session",
         "n_bars", "source", "status", "synced_at")

# COALESCE on the columns a caller may not know: a websocket adapter marking a
# fresh tick knows the timestamp and its own source but not the exact row count,
# and a scan run without --count knows the count is expensive, not that it is
# zero. Neither should erase what the other last established.
_UPSERT = """
INSERT INTO sync_state (symbol, last_bar_ts, last_complete_session,
                        partial_session, n_bars, source, status, synced_at)
VALUES (?,?,?,?,?,?,?,?)
ON CONFLICT(symbol) DO UPDATE SET
  last_bar_ts           = excluded.last_bar_ts,
  last_complete_session = COALESCE(excluded.last_complete_session,
                                   sync_state.last_complete_session),
  partial_session       = excluded.partial_session,
  n_bars                = COALESCE(excluded.n_bars, sync_state.n_bars),
  source                = COALESCE(excluded.source, sync_state.source,
                                   'unknown'),
  status                = excluded.status,
  synced_at             = excluded.synced_at
"""


def _row_to_dict(row: tuple) -> dict:
    return dict(zip(_COLS, row))


def read(con: sqlite3.Connection, symbol: str) -> dict | None:
    row = con.execute(
        f"SELECT {','.join(_COLS)} FROM sync_state WHERE symbol=?",
        (symbol,)).fetchone()
    return _row_to_dict(row) if row else None


def read_all(con: sqlite3.Connection) -> dict[str, dict]:
    return {r[0]: _row_to_dict(r) for r in con.execute(
        f"SELECT {','.join(_COLS)} FROM sync_state")}


# ── scan ──────────────────────────────────────────────────────────
def _walk_back(con: sqlite3.Connection, symbol: str,
               last_ts: int) -> tuple[int | None, int | None]:
    """(last_complete_session, partial_session) for a symbol, from its last bar.

    The trailing day needs no range read: MAX(ts) for a symbol IS the last bar
    of its last day, so "the day holds a bar at or after the close" and "the
    last bar is at or after the close" are the same statement. When the trailing
    day is short, step back a day at a time with `MAX(ts) WHERE ts < day_start`
    — each hop is one index seek on (symbol, ts), so finding a complete session
    behind a week of broken ones costs seven seeks, not seven days of rows.
    """
    _, tz_off = dataserver.session_for(symbol)
    close = session_close_for(symbol) - GRACE_MIN

    if _minute_of_day(last_ts, tz_off) >= close:
        return _day_start(last_ts, tz_off), None

    partial = _day_start(last_ts, tz_off)
    cursor = partial
    for _ in range(_LOOKBACK_DAYS):
        prev = con.execute(
            "SELECT MAX(ts) FROM bars WHERE symbol=? AND ts<?",
            (symbol, cursor)).fetchone()[0]
        if prev is None:
            return None, partial          # nothing behind it ever closed
        if _minute_of_day(prev, tz_off) >= close:
            return _day_start(prev, tz_off), partial
        cursor = _day_start(prev, tz_off)
    return None, partial


def scan(con: sqlite3.Connection, symbols: list[str] | None = None,
         *, count_bars: bool = False) -> list[dict]:
    """Recompute every watermark, write the rows, return them.

    `count_bars` is off by default and that is a measured decision, not
    laziness: `SELECT symbol, MAX(ts), COUNT(*) FROM bars GROUP BY symbol` was
    measured at 59-72s on this store (11.6 GB, 100M rows), because an exact
    count forces a walk of every entry in ix_bars_sym_ts and no cache holds a
    3 GB index. The freshness answer needs only the per-symbol MAX, which the
    same index answers by seek — 0.03s for all 91 symbols. n_bars is a sanity
    number; freshness is the product, and it should not cost a minute to ask.
    Pass count_bars=True (or --count) to refresh the counts; the previous values
    survive a scan that skips them.
    """
    ensure_table(con)
    known = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM bars")]
    wanted = [s.upper() for s in symbols] if symbols else known

    counts: dict[str, int] = {}
    if count_bars:
        counts = {r[0]: r[1] for r in con.execute(
            "SELECT symbol, COUNT(*) FROM bars GROUP BY symbol")}

    now = int(time.time())
    have = set(known)
    rows: list[dict] = []
    for sym in wanted:
        if sym not in have:
            rows.append(dict(zip(_COLS, (sym, None, None, None, 0, None,
                                         "empty", now))))
            continue
        last_ts = con.execute(
            "SELECT MAX(ts) FROM bars WHERE symbol=?", (sym,)).fetchone()[0]
        complete, partial = _walk_back(con, sym, last_ts)
        rows.append(dict(zip(_COLS, (
            sym, last_ts, complete, partial, counts.get(sym), None,
            "partial" if partial else "ok", now))))

    con.executemany(_UPSERT, [tuple(r[c] for c in _COLS) for r in rows])
    # A scan knows nothing about who fetched the bars, so it passes source=NULL
    # to leave a writer's own label alone. Rows this scan CREATED therefore land
    # with no source at all; name that state rather than leaving a NULL that
    # reads as "we lost it".
    con.execute("UPDATE sync_state SET source='unknown' WHERE source IS NULL")
    con.commit()
    for r in rows:
        r["source"] = r["source"] or "unknown"
    return rows


def mark(con: sqlite3.Connection, symbol: str, *, last_bar_ts: int,
         source: str, status: str | None = None) -> None:
    """Cheap single-symbol update for whoever just wrote bars.

    `last_bar_ts` must be the symbol's MAX(ts), not merely the newest row in the
    batch — a backfill filling a hole in 2019 has not made the symbol fresher.
    Callers that cannot promise that should run scan() on the symbol instead.

    Deliberately query-free: when the trailing day is short this KEEPS whatever
    last_complete_session was already recorded rather than hunting for an older
    one, because a watermark that only ever advances cannot be walked backwards
    by a mid-session tick. A fresh symbol whose very first day is partial gets a
    NULL there until the next scan fills it in.
    """
    complete = _day_start(last_bar_ts, dataserver.session_for(symbol)[1]) \
        if is_complete(symbol, last_bar_ts) else None
    partial = None if complete else _day_start(
        last_bar_ts, dataserver.session_for(symbol)[1])
    con.execute(_UPSERT, (symbol, last_bar_ts, complete, partial, None,
                          source, status or ("ok" if complete else "partial"),
                          int(time.time())))
    con.commit()


# ── staleness, for derived tables ─────────────────────────────────
def _sessions_between(con: sqlite3.Connection, symbol: str,
                      lo_day: int, hi_day: int) -> int:
    """Sessions in (lo_day, hi_day], counted off bars_1d.

    bars_1d is the same list of days the chart and the screener call sessions,
    so "3 sessions behind" means three candles on screen, not three calendar
    days across a weekend. Falls back to distinct minute-days for a symbol that
    has no daily rows yet; that scan is bounded by the gap itself.
    """
    if hi_day <= lo_day:
        return 0
    n = con.execute("SELECT COUNT(*) FROM bars_1d WHERE symbol=? "
                    "AND ts>? AND ts<=?", (symbol, lo_day, hi_day)).fetchone()[0]
    if n:
        return int(n)
    if con.execute("SELECT 1 FROM bars_1d WHERE symbol=? LIMIT 1",
                   (symbol,)).fetchone():
        return 0
    _, tz_off = dataserver.session_for(symbol)
    days = {dataserver._ist_day(r[0], tz_off) for r in con.execute(
        "SELECT ts FROM bars WHERE symbol=? AND ts>? AND ts<=?",
        (symbol, lo_day, hi_day + 86400))}
    return max(0, len(days) - 1)


def staleness(con: sqlite3.Connection, symbol: str,
              as_of_epoch: int) -> dict | None:
    """Does a derived table's as_of disagree with this symbol's watermark?

    Returns None when they agree — the caller says nothing and the answer reads
    clean. Returns {"stale", "sessions_behind", "note"} when they do not, and
    the note is written to be pasted straight into a reply: the whole point is
    that a user reading pattern evidence that stops on 22 Jul, on a chart drawn
    to 2 Aug, is told which one they are looking at.

    Two distinct disagreements, both worth disclosing:
      behind  — the derived row was built before newer complete sessions landed.
      partial — the derived row was built ON a session that never closed. That
                is the RELIANCE 14:29 case, and it is the more dangerous of the
                two because the as_of looks NEWER than everyone else's.
    """
    st = read(con, symbol)
    if not st or not st["last_bar_ts"]:
        return None
    _, tz_off = dataserver.session_for(symbol)
    as_of_day = _day_start(as_of_epoch, tz_off)
    watermark = st["last_complete_session"]

    if st["partial_session"] and as_of_day == st["partial_session"]:
        return {
            "stale": True,
            "sessions_behind": 0,
            "note": (f"{symbol}: this was computed over "
                     f"{_fmt(as_of_day, tz_off)[:10]}, a session that never "
                     f"closed in the store (last bar "
                     f"{_fmt(st['last_bar_ts'], tz_off)[11:]}, close "
                     f"{session_close_for(symbol) // 60:02d}:"
                     f"{session_close_for(symbol) % 60:02d}). Treat it as a "
                     f"partial day."),
        }

    if watermark is None or as_of_day >= watermark:
        return None

    behind = _sessions_between(con, symbol, as_of_day, watermark)
    if not behind:
        return None
    return {
        "stale": True,
        "sessions_behind": behind,
        "note": (f"{symbol}: this stops at {_fmt(as_of_day, tz_off)[:10]}, "
                 f"{behind} session{'s' if behind != 1 else ''} behind the "
                 f"stored data ({_fmt(watermark, tz_off)[:10]})."),
    }


# ── CLI ───────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    # Writable, because scan() writes: a read-only handle would only cover the
    # MAX/COUNT half and the second connection would then race its own writes.
    con = sqlite3.connect(dataserver.DB_PATH)
    con.execute("PRAGMA cache_size=-262144")     # match dataserver — 256 MB
    con.execute("PRAGMA mmap_size=4294967296")   # 4 GB window
    return con


def _print(rows: list[dict], only_problems: bool) -> None:
    order = {"empty": 0, "partial": 1, "ok": 2}
    shown = [r for r in rows if not only_problems or r["status"] != "ok"]
    if not shown:
        print("all symbols end on a complete session")
        return
    shown.sort(key=lambda r: (order.get(r["status"], 9),
                             -(r["last_bar_ts"] or 0), r["symbol"]))
    w = max(len(r["symbol"]) for r in shown)
    print(f"{'symbol':<{w}}  {'last bar (own clock)':<20}  {'status':<8}  "
          f"{'complete through':<16}  refetch")
    for r in shown:
        _, tz = dataserver.session_for(r["symbol"])
        print(f"{r['symbol']:<{w}}  {_fmt(r['last_bar_ts'], tz):<20}  "
              f"{r['status']:<8}  "
              f"{_fmt(r['last_complete_session'], tz)[:10]:<16}  "
              f"{_fmt(r['partial_session'], tz)[:10] if r['partial_session'] else ''}")
    print("\ntimes are on each symbol's OWN clock (NSE/MCX/INR = IST, "
          "crypto = UTC) so they can be read against its close directly")


def main(argv: list[str]) -> int:
    only_problems = "--check" in argv
    count_bars = "--count" in argv
    syms = [a.upper() for a in argv[1:] if not a.startswith("--")]

    con = _connect()
    t0 = time.time()
    rows = scan(con, syms or None, count_bars=count_bars)
    wall = time.time() - t0

    bad = [r for r in rows if r["status"] != "ok"]
    _print(rows, only_problems)
    print(f"\nsync_state: {len(rows)} symbols, {len(bad)} not on a complete "
          f"session, scanned in {wall:.2f}s"
          + ("" if count_bars else " (n_bars not recounted — pass --count)"))
    con.close()
    return 1 if (only_problems and bad) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
