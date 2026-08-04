#!/usr/bin/env python3
"""Kite websocket -> charto's live tick engine.

dataserver.py already owns the whole live path. `_live_on_tick(sym, ts,
price, vol)` is the single seam: it keeps one forming 1-min bar per symbol,
writes the minute to SQLite the instant it closes, pops the daily cache, and
pushes SSE; `get_bars` merges the forming bar so every chart tool goes live
without knowing a tick loop exists. Its only driver so far is the replay
thread re-feeding stored rows. This file is the second driver — the real one
— and it adds NO write path of its own into `bars`. Everything here is
translation and refusal.

WHY A DIRECT SOCKET AND NOT pivot's Redis `ticks` CHANNEL
---------------------------------------------------------
pivot/backend/kite/ticker.py already runs a hardened KiteTicker
(reconnect=True, 50 tries, 60s max delay, refcounted subscribe). Riding its
Redis pub/sub would be less code and zero new connections. It is still the
wrong seam, for one concrete reason: `_tick_to_payload` stamps every payload
with `"ts": ts_now` — `int(time.time())` on the machine that received it —
and drops `exchange_timestamp` entirely. That is precisely the field a bar
builder must have (gotcha #2 below). Subscribing to that channel would bake
wall-clock bucketing in at the source, unfixable downstream, and no amount of
care in this file could recover it. The payload also carries the raw
cumulative volume with no cursor, and the channel is a single fan-in for
pivot's universe (holdings + indices), not charto's 91-symbol minute store.

So we open our own KiteTicker with the same construction the manager uses
(mirrored below, not imported, so this file never perturbs pivot's singleton).
The costs, stated plainly:
  - a second websocket on the same API key (Kite allows 3; this makes 2),
  - the construction params are duplicated and can drift from ticker.py,
  - reconnect/backoff behaviour is whatever kiteconnect does, same as there.
The failure modes are independent, which is the point: pivot's ticker dying
does not stop charto's candles, and this file cannot wedge pivot's WS clients.

WHAT THIS FEED IS, AND WHAT IT IS NOT
-------------------------------------
MODE_FULL delivers roughly ONE SNAPSHOT PER SECOND, not every trade. That is
adequate for 1-minute OHLC — the OHLC of per-second snapshots approximates
the OHLC of the underlying trades at minute resolution, and volume is exact
because it is read off a cumulative counter rather than summed per trade. It
is NOT tick-by-tick. There is no aggressor side, no per-trade size, no bid/ask
attribution of any print. Order flow, footprint, delta and cumulative-delta
are therefore IMPOSSIBLE to construct from this feed — not merely unbuilt.
Nothing downstream of this file may present them, and this file must not
create the impression that a richer mode would fix it: FULL is the richest
mode Kite offers and it adds market depth, which is a book snapshot and still
not the trade tape.

THE THREE MISMATCHES
--------------------
1. `volume_traded` is CUMULATIVE for the trading day. `_live_on_tick` does
   `f[5] += vol`, so feeding the raw field would add the entire day's volume
   to every bar, every second — within a minute the store, the chart and the
   volume profile (which reads these same rows) are nonsense. We keep a
   per-symbol cursor and pass the DELTA. See `TickCursor.delta`.
2. Bucket on the tick's EXCHANGE timestamp, not `time.time()`. On a lagging
   connection wall-clock bucketing files trades into the wrong minute, and
   the error is invisible because the candle still looks plausible. We fall
   back to wall clock only when the field is absent, and count it.
3. See above: one snapshot per second, not per trade.

AND A FOURTH, ON SYMBOL SELECTION
---------------------------------
The store has 91 symbols in `bars` but 549 in `bars_1d`. Streaming a symbol
whose 1-minute history is not local writes today's minutes on top of a
multi-day hole — a chart that is live at the right edge and silently broken
behind it. `plan()` refuses any symbol whose 1-min history is more than
`max_stale_sessions` sessions behind its own daily series, and names what is
missing.

AND A FIFTH, THE ONE SESSIONS CANNOT SEE
----------------------------------------
`freshness()` measures in whole SESSIONS, so a symbol whose minutes stop at
11:00 on the CURRENT session is "0 sessions behind" and streams happily on top
of a 331-minute hole. Measured 2026-08-03: ALUMINIUM/ZINC/SILVERM/USDINR all
stored to 13:10 IST with the MCX/CDS sessions still running, and RELIANCE and
25 other equities stopped at 15:14 with the session's last bar at 15:29. Every
one of those passes the session gate. `minute_gap()` is the intraday half of
the answer and `fill_gap()` closes what it finds, bounded by MAX_FILL_MIN so a
handoff never turns into a backfill.

The gap is counted in MINUTES THE MARKET WAS ACTUALLY OPEN, never wall clock:
a stock that closed cleanly at 15:29 is not 1,000 minutes stale at 08:00 the
next morning, it is zero. See `expected_minutes`.

    python3 charto/data/kite_stream.py --dry-run --symbols RELIANCE,INFY
    python3 charto/data/kite_stream.py --self-test
    python3 charto/data/kite_stream.py --fill-only --symbols GOLD   # no socket
    python3 charto/data/kite_stream.py --symbols RELIANCE,INFY   # needs a session
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import dataserver as ds

# sync_state.py is being written concurrently by another worker. Import it
# defensively: a missing module must degrade the freshness check to the raw
# store and silence the mark() calls, never break the stream.
try:                                   # noqa: SIM105
    import sync_state                  # type: ignore
except Exception:                      # noqa: BLE001
    sync_state = None                  # type: ignore[assignment]

log = logging.getLogger("charto.kite_stream")

IST = timezone(timedelta(seconds=ds.IST_OFF))
DEFAULT_MAX_STALE_SESSIONS = 2         # yesterday's close is fine; a week is not
_STATUS_EVERY = 30.0                   # seconds between live status lines

# ── the intraday gap fill ─────────────────────────────────────────
# A handoff, not a backfill. 1,440 open-market minutes is ~3.8 NSE sessions or
# ~1.7 MCX ones — comfortably more than any hole a stream start should be
# repairing, and far less than the multi-year holes backfill_1min.py /
# backfill_macro.py exist for. Past this bound the honest move is to name the
# hole and the script that fixes it; grinding through 6,500 sequential Kite
# requests inside a stream start is how a driver hangs with syms=0 and no error
# (crypto_stream.py hit exactly that with DOT-USD 1,949,380 minutes behind).
MAX_FILL_MIN = 1440
# Kite publishes the current session's minute bars with a lag of a minute or
# two, so a fill that lands one minute short of `now` succeeded. Judged against
# what fill_gap is FOR — closing the hole to roughly now — not against the
# streaming gate, because a hole under the gate is still a hole the chart draws
# over.
FILL_TOLERANCE_MIN = 3
FILL_WORKERS = 3                       # Kite historical is ~3 req/s
FILL_PACE = 0.35                       # per-thread pacing -> ~3 req/s over 3
_FILL_WINDOW_DAYS = 55                 # measured per-request cap is 60
# The day walk in expected_minutes() is O(days). A symbol years behind is
# backfill's problem anyway, so the walk stops here and reports a LOWER BOUND
# rather than counting 900 sessions to reach the same refusal.
_MAX_GAP_DAYS = 400


# ── the cumulative-volume cursor ──────────────────────────────────
# One per symbol. Every branch below exists because a specific wrong number
# reached a candle without it.

@dataclass
class TickCursor:
    last_cum: int | None = None        # last ACCEPTED cumulative reading
    last_day: int | None = None        # its session day, for rollover detection
    last_bucket: int | None = None     # last 1-min bucket stamp we fed
    ticks: int = 0
    reseeds: int = 0                   # deltas deliberately dropped
    stale_drops: int = 0               # out-of-order cumulative readings ignored
    dups: int = 0                      # unchanged last_trade_time (snapshot repeat)
    no_ts: int = 0                     # ticks with no exchange clock
    closes: int = 0                    # minutes this symbol closed
    last_ts: int = 0                   # epoch of the last accepted tick

    def delta(self, cum: int | None, day: int) -> int:
        """Per-tick volume from a cumulative day counter."""
        if cum is None:
            # MODE_LTP, or a quote payload without the field. No volume
            # information at all is not the same as zero volume, but a bar
            # can only carry a number, and 0 is the one that does not lie
            # upward.
            return 0
        if self.last_cum is None:
            # First tick after connect or reconnect. There is no prior
            # reading, so the delta is genuinely unknowable — the counter's
            # value is the whole day so far, and feeding it would dump every
            # trade since 09:15 into the minute we happen to connect in.
            # Passing 0 loses at most the volume traded inside the current
            # minute before we arrived, because the forming bar starts empty.
            self.last_cum, self.last_day = cum, day
            self.reseeds += 1
            return 0
        if cum == self.last_cum:
            self.dups += 1
            return 0                   # same snapshot re-sent; price may differ
        if cum > self.last_cum:
            d = cum - self.last_cum
            self.last_cum, self.last_day = cum, day
            return d
        # cum < last_cum. Two different events wear the same shape.
        if day != self.last_day:
            # Session rollover: the counter reset and this reading IS the new
            # day's volume so far. On a connection held across the boundary
            # that is a few seconds' worth, so it belongs in this bar.
            self.last_cum, self.last_day = cum, day
            self.reseeds += 1
            return cum
        # Same day, counter went backwards: a stale or out-of-order snapshot.
        # Re-seeding here would make the NEXT reading's delta count the
        # rewound volume twice, so drop the reading and leave the cursor
        # where it was. The price on this tick is still usable for OHLC.
        self.stale_drops += 1
        return 0


_CURSORS: dict[str, TickCursor] = {}
_CURSOR_GUARD = threading.Lock()


def cursor(symbol: str) -> TickCursor:
    with _CURSOR_GUARD:
        c = _CURSORS.get(symbol)
        if c is None:
            c = _CURSORS[symbol] = TickCursor()
        return c


def reset_cursors(symbols: Iterable[str] | None = None) -> int:
    """Forget every cumulative reading. Called on connect AND reconnect: a
    gap in the socket means we missed ticks, so the surviving cursor would
    turn the whole gap's volume into one enormous minute."""
    with _CURSOR_GUARD:
        targets = list(_CURSORS) if symbols is None else list(symbols)
        n = 0
        for s in targets:
            c = _CURSORS.get(s)
            if c is not None and c.last_cum is not None:
                c.last_cum = None
                c.last_day = None
                n += 1
        return n


# ── raw tick -> (ts, price, volume) ───────────────────────────────

def _epoch(value: Any, tz_off: int) -> int | None:
    """Kite timestamps, in every shape the client hands them over in.

    kiteconnect parses `exchange_timestamp` / `last_trade_time` into NAIVE
    datetimes that are already in IST. Calling .timestamp() on those uses the
    HOST's timezone, so on a UTC box every bar lands 5h30m early — the same
    class of bug as bucketing on wall clock, just quieter. Naive values are
    therefore pinned to the symbol's own offset explicitly.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return int(value.replace(tzinfo=timezone(
                timedelta(seconds=tz_off))).timestamp())
        return int(value.timestamp())
    if isinstance(value, (int, float)):
        v = float(value)
        if v <= 0:
            return None
        return int(v / 1000.0) if v > 1e11 else int(v)   # ms vs s
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return _epoch(dt, tz_off)
    return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def translate(symbol: str, raw: dict[str, Any], cur: TickCursor,
              now: float | None = None) -> tuple[int, float, int] | None:
    """Raw Kite quote dict -> exactly the three numbers `_live_on_tick` wants.

    Pure but for `cur`, which it advances: the volume delta is a function of
    the reading BEFORE it, so the cursor is the state and passing it in is
    what makes this testable without a socket. Returns None for a tick that
    cannot become a candle at all.
    """
    price = raw.get("last_price")
    try:
        px = float(price) if price is not None else 0.0
    except (TypeError, ValueError):
        return None
    if px <= 0:
        return None                    # a zero print is not a trade

    tz_off = ds.session_for(symbol)[1]
    ts = _epoch(raw.get("exchange_timestamp"), tz_off)
    if ts is None:
        ts = _epoch(raw.get("last_trade_time"), tz_off)
    if ts is None:
        # Wall clock is the fallback, never the default: on a lagging
        # connection it files trades into the minute we RECEIVED them, not
        # the minute they happened. Counted so a feed that never sends an
        # exchange clock is visible in status() instead of silently skewing.
        cur.no_ts += 1
        ts = int(time.time() if now is None else now)

    cum = _as_int(raw.get("volume_traded"))
    if cum is None:
        cum = _as_int(raw.get("volume"))
    vol = cur.delta(cum, ds._ist_day(ts, tz_off))

    cur.ticks += 1
    cur.last_ts = ts
    return ts, px, vol


def on_tick(symbol: str, raw_tick: dict[str, Any]) -> None:
    """Translate one raw Kite tick and hand it to the one seam.

    Deliberately thin. Everything about candles — the session gate, the
    forming bar, the SQLite write on close, the SSE push — already lives in
    `_live_on_tick`, and re-implementing any of it here is how a second,
    divergent write path into `bars` gets born.
    """
    cur = cursor(symbol)
    out = translate(symbol, raw_tick, cur)
    if out is None:
        return
    ts, px, vol = out
    _, bts = ds._bucket_stamp(ts, 1, ds.session_for(symbol))
    ds._live_on_tick(symbol, ts, px, vol)
    if cur.last_bucket is not None and bts != cur.last_bucket:
        # The engine just wrote cur.last_bucket to `bars`; record it as the
        # newest minute this source is responsible for.
        cur.closes += 1
        _mark_closed(symbol, cur.last_bucket)
    cur.last_bucket = bts


# ── sync_state, when it exists ────────────────────────────────────

_state_con: sqlite3.Connection | None = None
_STATE_GUARD = threading.Lock()
_state_dead = False                    # one failure disables it for the run


def _state_connection() -> sqlite3.Connection | None:
    """Own connection (WAL is on) so a bookkeeping write can never land
    mid-read on dataserver's shared reader."""
    global _state_con
    if sync_state is None or _state_dead:
        return None
    if _state_con is None:
        _state_con = sqlite3.connect(ds.DB_PATH, check_same_thread=False)
        ensure = getattr(sync_state, "ensure_table", None)
        if ensure is not None:
            ensure(_state_con)         # the other worker owns the DDL
    return _state_con


def _mark_closed(symbol: str, bar_ts: int) -> None:
    """Record the minute the engine just wrote.

    sync_state.mark's contract is that last_bar_ts is the symbol's MAX(ts),
    not merely the newest row of a batch. A live stream satisfies that by
    construction — it only ever writes forward — which is exactly why it may
    use the cheap single-symbol update instead of a full scan().
    """
    global _state_dead
    if sync_state is None or _state_dead:
        return
    if symbol.startswith("__TEST_"):
        # The self-test writes scratch symbols. `sync_state` is another
        # worker's table and read_all() fans it out to real callers; a row
        # named __TEST_STREAM__ in there is test residue in a shipped
        # surface. Never write it rather than write-then-delete.
        return
    mark = getattr(sync_state, "mark", None)
    if mark is None:
        return
    try:
        with _STATE_GUARD:
            con = _state_connection()
            if con is None:
                return
            mark(con, symbol, last_bar_ts=bar_ts, source="kite_ws")
    except Exception as exc:            # noqa: BLE001
        # Bookkeeping must never take the tick loop down with it, and a
        # per-minute warning per symbol would drown the log.
        _state_dead = True
        log.warning("sync_state.mark disabled for this run: %s", exc)


def _state_last_bar(con: sqlite3.Connection, symbol: str) -> int | None:
    """sync_state's view of the newest stored minute, if the module is here.
    Row shape is the other worker's to define, so every access is guarded."""
    if sync_state is None:
        return None
    read = getattr(sync_state, "read", None)
    if read is None:
        return None
    try:
        ensure = getattr(sync_state, "ensure_table", None)
        if ensure is not None:
            ensure(con)
        row = read(con, symbol)
    except Exception:                   # noqa: BLE001
        return None
    if row is None:
        return None
    for get in (lambda r: r["last_bar_ts"],
                lambda r: getattr(r, "last_bar_ts"),
                lambda r: r[1]):
        try:
            return _as_int(get(row))
        except Exception:               # noqa: BLE001
            continue
    return None


# ── freshness: the refusal that keeps holes off the chart ─────────

def freshness(con: sqlite3.Connection, symbol: str,
              max_stale_sessions: int = DEFAULT_MAX_STALE_SESSIONS,
              now: int | None = None) -> dict:
    """Is this symbol's 1-min history current enough to stream onto?

    Measured in SESSIONS, not days, and against the symbol's OWN daily series
    rather than a market calendar — sweep_vp.py already established that a
    session is whatever the daily fold called one, and deriving it twice lets
    the screener and the chart disagree. Wall-clock days would also count
    weekends and holidays as staleness and refuse a perfectly current symbol
    every Monday.
    """
    tz_off = ds.session_for(symbol)[1]
    last_min = con.execute("SELECT MAX(ts) FROM bars WHERE symbol=?",
                           (symbol,)).fetchone()[0]
    from_state = _state_last_bar(con, symbol)
    if from_state and (last_min is None or from_state > last_min):
        last_min = from_state
    days = [r[0] for r in con.execute(
        "SELECT ts FROM bars_1d WHERE symbol=? ORDER BY ts DESC LIMIT 400",
        (symbol,))]

    out: dict[str, Any] = {"symbol": symbol, "ok": False,
                           "last_1m": last_min, "last_1d": days[0] if days else None,
                           "stale_sessions": None, "reason": ""}
    if last_min is None:
        out["reason"] = ("no 1-minute history in `bars` at all — streaming "
                         "would write today's minutes onto an empty symbol")
        return out
    if not days:
        out["reason"] = ("no rows in `bars_1d` — there is no session calendar "
                         "to measure 1-minute staleness against")
        return out

    # The daily series is the yardstick, so a STALE yardstick certifies nothing:
    # when both series stop on the same old day, `stale` is 0 and a symbol four
    # days behind reads as current — the exact hole this function exists to
    # catch. Measured 2026-08-02: BTCUSDT's minutes ended 29 Jul and bars_1d
    # ended with them, so it reported "0 sessions behind" and would have been
    # streamed onto a four-day gap.
    #
    # The threshold follows the symbol's own cadence rather than one constant:
    # a 24/7 symbol trades every calendar day, so two days of silence is already
    # definitive, while an NSE name can legitimately sit five days behind across
    # a long weekend without anything being wrong.
    # `now` is injectable so this stays testable: the self-test's fixture is
    # anchored to fixed synthetic dates, and a hardcoded wall clock would make
    # the whole fixture rot into a failure a few days after it was written.
    ref_age_days = ((int(time.time()) if now is None else now) - days[0]) // 86400
    ref_limit = 2 if ds.session_for(symbol) == ds.UTC_SESSION else 5
    if ref_age_days > ref_limit:
        out["reason"] = (
            f"the daily series itself ends {_ist_str(days[0])}, {ref_age_days} "
            f"calendar day(s) ago — there is no current reference to measure "
            f"1-minute staleness against, so freshness cannot be certified. "
            f"Top up before streaming.")
        return out

    last_min_day = ds._ist_day(last_min, tz_off)
    stale = sum(1 for d in days if ds._ist_day(d, tz_off) > last_min_day)
    out["stale_sessions"] = stale
    if stale > max_stale_sessions:
        out["reason"] = (
            f"1-minute history ends {_ist_str(last_min)} but the daily series "
            f"runs to {_ist_str(days[0])} — {stale} session(s) of minutes are "
            f"missing (limit {max_stale_sessions}). Backfill before streaming.")
        return out
    out["ok"] = True
    out["reason"] = (f"1-minute history current to {_ist_str(last_min)} "
                     f"({stale} session(s) behind the daily series)")
    return out


def _ist_str(ts: int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=IST).strftime("%Y-%m-%d %H:%M IST")


# ── the intraday gap, in OPEN-MARKET minutes ──────────────────────
# freshness() answers "how many SESSIONS of minutes are missing". This answers
# "how many minutes inside a session are missing", which is the question a
# symbol that stopped at 11:00 today fails and the session count cannot see.
#
# The session clock is not uniform and dataserver.py OWNS it — session_for()
# gives (bucket-anchor minute, tz offset) and session_close_for() gives the
# minute-of-day the last bar of a complete session OPENS, both measured off
# complete stored sessions rather than an exchange brochure. They are imported,
# not re-tabulated: topup_1min.py duplicates the family sets deliberately (its
# --dry-run must run with no dataserver and no 13 GB mmap), but this module
# already imports dataserver at the top for _live_on_tick, so a second copy of
# NSE 15:29 / MCX 23:29 / CDS 16:59 here would buy nothing and drift.
#
# One correction is unavoidable. session_for() returns a BUCKET ANCHOR, not a
# session open, and it puts the INR pairs on the NSE anchor (09:15) even though
# the currency segment opens at 09:00 — the same trap session_close_for()'s
# docstring describes at the other end of the day. Verified against the store
# on 2026-08-03: USDINR's 2026-07-31 session holds 480 bars, 09:00 to 16:59;
# GOLD holds 870, 09:00 to 23:29; RELIANCE holds 375, 09:15 to 15:29. Using the
# 09:15 anchor for USDINR would under-count every morning gap by 15 minutes and
# quietly leave 09:00-09:14 missing forever.


def session_bounds(symbol: str) -> tuple[int, int, int]:
    """(first bar minute-of-day, last bar minute-of-day, tz offset)."""
    open_min, tz_off = ds.session_for(symbol)
    close_min = ds.session_close_for(symbol)
    if symbol in getattr(ds, "_FX_SYMBOLS", ()):
        open_min = ds.MCX_SESSION[0]        # CDS opens 09:00, like MCX
    return open_min, close_min, tz_off


def _bar_ts(day: int, minute_of_day: int, tz_off: int) -> int:
    """Epoch of the bar that OPENS at `minute_of_day` on session-day `day`."""
    return day * 86400 + minute_of_day * 60 - tz_off


def _is_weekend(day: int) -> bool:
    """Epoch day 0 (1970-01-01) was a Thursday, so Monday==0 is (day+3) % 7."""
    return (day + 3) % 7 >= 5


def session_days(con: sqlite3.Connection, symbol: str, day_from: int,
                 day_to: int, tz_off: int) -> set[int]:
    """Which session-days in [day_from, day_to] the market was actually open.

    A weekday test alone counts every exchange holiday as a missing session,
    which turns one Diwali into a 375-minute phantom gap and a fill that
    fetches nothing. The store already knows the real answer: `bars_1d` holds
    one row per session the daily fold saw, and its dates carry the holidays
    (measured: 25-26 Jul and 1-2 Aug are simply absent). Days PAST the end of
    the daily series have no answer there yet — the fold lags the minute store
    — so those fall back to weekday-not-weekend, which is right for every day
    except a holiday inside the last session or two, where the fill just
    returns nothing and the post-fill verification says so.
    """
    if ds.session_for(symbol) == ds.UTC_SESSION:
        return set(range(day_from, day_to + 1))      # 24/7: every day trades
    rows = con.execute(
        "SELECT ts FROM bars_1d WHERE symbol=? AND ts>=? AND ts<?",
        (symbol, day_from * 86400 - tz_off, (day_to + 1) * 86400 - tz_off)
    ).fetchall()
    days = {ds._ist_day(int(t), tz_off) for (t,) in rows}
    top = con.execute("SELECT MAX(ts) FROM bars_1d WHERE symbol=?",
                      (symbol,)).fetchone()[0]
    top_day = ds._ist_day(int(top), tz_off) if top is not None else None
    for d in range(day_from, day_to + 1):
        if top_day is not None and d <= top_day:
            continue                                 # the fold has an opinion
        if not _is_weekend(d):
            days.add(d)
    return days


def expected_minutes(con: sqlite3.Connection, symbol: str,
                     from_ts: int, to_ts: int) -> int:
    """How many 1-minute bars SHOULD exist with open stamps in [from, to].

    This is the whole correctness property. Wall-clock minutes would call an
    NSE stock that closed cleanly at 15:29 "1,006 minutes stale" at 08:15 the
    next morning and send a fill after minutes that never existed; every night
    and weekend would read as a hole. Only minutes between the symbol's own
    open and close, on days its own market traded, are counted.
    """
    if to_ts < from_ts:
        return 0
    open_min, close_min, tz_off = session_bounds(symbol)
    d0 = ds._ist_day(from_ts, tz_off)
    d1 = ds._ist_day(to_ts, tz_off)
    days = session_days(con, symbol, d0, d1, tz_off)
    n = 0
    for d in range(d0, d1 + 1):
        if d not in days:
            continue
        lo = max(from_ts, _bar_ts(d, open_min, tz_off))
        hi = min(to_ts, _bar_ts(d, close_min, tz_off))
        if hi >= lo:
            n += (hi - lo) // 60 + 1
    return n


def minute_gap(con: sqlite3.Connection, symbol: str,
               now: int | None = None) -> dict:
    """The hole between this symbol's newest stored minute and now.

    Reads MAX(ts) from `bars` and nothing else — deliberately NOT sync_state's
    watermark, which freshness() does consult. A watermark is a claim about
    what was written; a gap is a question about what is actually there, and if
    the two disagree the rows are the ones the chart draws.
    """
    ts_now = int(time.time() if now is None else now)
    row = con.execute("SELECT MAX(ts) FROM bars WHERE symbol=?",
                      (symbol,)).fetchone()
    last = row[0] if row else None
    out: dict[str, Any] = {"symbol": symbol, "last_1m": last, "gap_min": None,
                           "wall_min": None, "first_missing": None,
                           "last_expected": None, "capped": False, "note": ""}
    if last is None:
        out["note"] = ("no 1-minute history at all — a fill has no anchor; "
                       "run the backfill for this symbol first")
        return out

    last = int(last)
    first_missing = last + 60
    # A bar stamped 15:29 covers 15:29:00-15:29:59, so it is not late until
    # 15:31 — the minute now forming is not missing, it is forming.
    last_expected = (ts_now // 60) * 60 - 60
    out["first_missing"] = first_missing
    if last_expected < first_missing:
        out.update(gap_min=0, wall_min=0, last_expected=last_expected,
                   note="stored up to the newest completed minute")
        return out

    open_min, close_min, tz_off = session_bounds(symbol)
    d0 = ds._ist_day(first_missing, tz_off)
    d1 = ds._ist_day(last_expected, tz_off)
    if d1 - d0 > _MAX_GAP_DAYS:
        d1 = d0 + _MAX_GAP_DAYS
        last_expected = _bar_ts(d1, close_min, tz_off)
        out["capped"] = True
    out["last_expected"] = last_expected
    out["wall_min"] = (last_expected - first_missing) // 60 + 1
    out["gap_min"] = expected_minutes(con, symbol, first_missing, last_expected)
    bound = ">=" if out["capped"] else ""
    out["note"] = (
        f"newest stored minute {_ist_str(last)}; {bound}{out['gap_min']:,} "
        f"open-market minute(s) missing to {_ist_str(last_expected)} "
        f"({out['wall_min']:,} wall-clock)")
    return out


def too_wide(gap: dict, max_fill_min: int = MAX_FILL_MIN) -> bool:
    """Is this hole beyond what a stream start may close itself?

    One predicate, called by fill_gaps and asserted by the self-test, so the
    bound cannot be tested against a re-statement of itself. `capped` counts as
    too wide by construction: a capped gap is a LOWER bound past _MAX_GAP_DAYS,
    and something at least 400 sessions behind is a backfill either way.
    """
    return bool(gap.get("capped")) or (gap.get("gap_min") or 0) > max_fill_min


def _owner_script(symbol: str) -> str:
    """Which backfill owns a hole too wide for the streamer to close.

    topup_1min.py owns the family -> script map; imported through a guard
    because the lead is editing that file and a stream must not die of it.
    """
    try:
        import topup_1min as tp                       # type: ignore
        fam = tp.family(symbol)
        return {"equity": "backfill_1min.py", **tp.OWNER}.get(
            fam, "backfill_1min.py")
    except Exception:                                 # noqa: BLE001
        return "backfill_1min.py / backfill_macro.py"


# ── the bounded, parallel Kite fill ───────────────────────────────

def _fill_connection() -> sqlite3.Connection:
    """Own writer connection. 60s busy_timeout because dataserver, the live
    writer and a concurrent top-up all hold this file — `database is locked`
    has bitten this store before and a 5s default is not enough for a 13 GB
    WAL checkpoint to get out of the way."""
    con = sqlite3.connect(ds.DB_PATH, check_same_thread=False, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    return con


def fill_windows(last_ts: int, last_expected: int) -> list[tuple[datetime, datetime]]:
    """Kite request windows for one symbol's hole, as IST-AWARE datetimes.

    THE trap, hit twice in this repo (see backfill_macro.IST_TZ): the Kite
    python client formats a datetime as a bare "%Y-%m-%d %H:%M:%S" and DROPS
    tzinfo, so the server reads whatever it is handed as IST. A UTC-aware `now`
    therefore asks for "up to 13:13" and Kite hears 13:13 IST — silently
    truncating 5:30 of the current session. Measured 2026-08-03 on GOLD: UTC
    bound -> 254 bars ending 13:13, IST bound -> 584 ending 18:43. Every bound
    below is built with `datetime.fromtimestamp(ts, IST)` so the wall-clock
    string the client sends IS the IST the server assumes.

    Starts at the newest STORED minute, not at the first missing one: the last
    row may have been written from a truncated minute by a killed process, and
    INSERT OR REPLACE rewrites it in place.
    """
    frm = datetime.fromtimestamp(int(last_ts), IST)
    to = datetime.fromtimestamp(int(last_expected) + 60, IST)
    out: list[tuple[datetime, datetime]] = []
    cur = frm
    while cur < to:
        nxt = min(cur + timedelta(days=_FILL_WINDOW_DAYS), to)
        out.append((cur, nxt))
        cur = nxt
    return out


_THREAD_KITE = threading.local()


def _kite_client(access_token: str) -> Any:
    """One KiteConnect per worker thread, same as topup_1min's worker().

    Keyed on the token as well as the thread: the daily session expires ~6 AM
    IST, and a long-lived process that re-logs-in would otherwise keep handing
    every fetch a client holding the dead token.
    """
    k = getattr(_THREAD_KITE, "kite", None)
    if k is None or getattr(_THREAD_KITE, "token", None) != access_token:
        from backend.kite.auth import get_authenticated_kite
        k = _THREAD_KITE.kite = get_authenticated_kite(access_token)
        _THREAD_KITE.token = access_token
    return k


def _fetch_window(job: tuple) -> tuple[str, list[tuple], str]:
    symbol, token, frm, to, access_token = job
    try:
        candles = _kite_client(access_token).historical_data(
            instrument_token=token, from_date=frm, to_date=to,
            interval="minute", continuous=False, oi=False)
    except Exception as exc:                          # noqa: BLE001
        time.sleep(1.0)
        return symbol, [], f"{frm:%Y-%m-%d %H:%M}: {str(exc)[:80]}"
    rows = [
        # kiteconnect returns `date` as an IST-AWARE datetime, so .timestamp()
        # is correct here without the naive-datetime pinning _epoch() needs for
        # the websocket payloads.
        (symbol, int(c["date"].timestamp()),
         float(c["open"]), float(c["high"]), float(c["low"]),
         float(c["close"]),
         # NOT int(): _exact_vol keeps a fractional volume fractional and only
         # narrows when it is integral. `int()` truncation once zeroed 17.3% of
         # BTC-USD's stored minutes. NSE/MCX volumes are integral, so this is
         # free here and correct if this ever meets a venue that is not.
         ds._exact_vol(c.get("volume", 0) or 0))
        for c in (candles or [])
    ]
    time.sleep(FILL_PACE)
    return symbol, rows, ""


def resolve_tokens(symbols: Sequence[str], access_token: str,
                   ) -> tuple[dict[str, int], dict[str, str]]:
    """symbol -> instrument token, for every family.

    Resolving everything against "NSE" silently dropped four of five MCX
    metals: GOLD is not an NSE tradingsymbol, it is a rolling MCX future
    (GOLD26AUGFUT), and an index lives in the INDICES segment. backfill_macro's
    resolve() already does this properly — including the monthly-vs-weekly
    contract rule a dead weekly would otherwise satisfy — so it is imported
    rather than reimplemented; a second copy is how the streamer and the
    backfill end up on different contracts for one symbol.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pivot"))
    from backend.kite.historical import _resolve_instrument_token
    import backfill_macro as bm

    tokens: dict[str, int] = {}
    failed: dict[str, str] = {}
    wanted = [s for s in symbols]
    macro = {s for s in wanted
             if s in bm.INDICES or s in bm.METALS or s in bm.CURRENCY}
    if macro:
        from backend.kite.auth import get_authenticated_kite
        try:
            for sym, tok, _is_fut in bm.resolve(
                    get_authenticated_kite(access_token), macro):
                tokens[sym] = int(tok)
        except Exception as exc:                      # noqa: BLE001
            log.warning("macro instrument resolve failed: %s", exc)
    for sym in wanted:
        if sym in tokens:
            continue
        try:
            tok = _resolve_instrument_token(sym, "NSE", access_token)
        except Exception as exc:                      # noqa: BLE001
            failed[sym] = f"instrument resolve raised: {str(exc)[:70]}"
            continue
        if tok is None:
            failed[sym] = ("no Kite instrument token for this symbol on NSE, "
                           "MCX-FUT, CDS-FUT or INDICES")
            continue
        tokens[sym] = int(tok)
    return tokens, failed


def fill_gaps(symbols: Sequence[str], access_token: str, *,
              max_fill_min: int = MAX_FILL_MIN,
              now: int | None = None,
              con: sqlite3.Connection | None = None) -> dict:
    """Measure each symbol's intraday hole and close it over Kite REST.

    Runs BEFORE the socket opens. Refusing a stale symbol is only half an
    answer when the hole is exactly what this process can close: measured
    2026-08-03, 26 NSE equities sat at 15:14 against a 15:29 close and four
    MCX/CDS names at 13:10 with their sessions still running, all of which the
    session-level gate waves through and none of which any later run repairs.
    """
    out: dict[str, Any] = {"filled": 0, "symbols": {}, "skipped": {},
                           "too_wide": {}, "unfilled": {}, "errors": []}
    # `con` is injectable so the MAX_FILL_MIN bound can be proved against a
    # synthetic store: the refusal must short-circuit before any Kite path, and
    # the only honest way to show that is to run it with a store the test owns
    # and a token that would fail if it were ever used.
    owned = con is None
    con = _fill_connection() if owned else con
    try:
        jobs: list[tuple[str, dict]] = []
        for sym in symbols:
            if ds.session_for(sym) == ds.UTC_SESSION:
                # Kite does not carry crypto; crypto_stream.py owns those and
                # has its own fill. Silently "filling" 0 rows would read as a
                # closed hole.
                out["skipped"][sym] = "24/7 symbol — crypto_stream.py owns it"
                continue
            g = minute_gap(con, sym, now=now)
            if g["gap_min"] is None:
                out["skipped"][sym] = g["note"]
                continue
            if g["gap_min"] == 0:
                out["symbols"][sym] = 0
                continue
            if too_wide(g, max_fill_min):
                msg = (f"{g['gap_min']:,}{'+' if g['capped'] else ''} "
                       f"open-market minute(s) behind (limit {max_fill_min:,}) "
                       f"— newest stored minute is {_ist_str(g['last_1m'])}; "
                       f"run {_owner_script(sym)} for this symbol first")
                out["too_wide"][sym] = msg
                log.warning("%s: NOT filling and NOT streaming over it — %s",
                            sym, msg)
                continue
            jobs.append((sym, g))
        if not jobs:
            return out

        try:
            tokens, failed = resolve_tokens([s for s, _ in jobs], access_token)
        except Exception as exc:                      # noqa: BLE001
            # A dead instrument master or a missing backend import must not
            # take the stream start down; the session gate still runs and the
            # unclosed hole is named rather than papered over.
            out["errors"].append(f"instrument resolve failed: {str(exc)[:120]}")
            return out
        for sym, why in failed.items():
            out["skipped"][sym] = why
        work: list[tuple] = []
        for sym, g in jobs:
            tok = tokens.get(sym)
            if tok is None:
                continue
            for frm, to in fill_windows(int(g["last_1m"]), int(g["last_expected"])):
                work.append((sym, tok, frm, to, access_token))
        if work:
            got: dict[str, list[tuple]] = {}
            # Parallel, not serial: three symbols each a few hundred minutes
            # behind is a stream start that feels like a hang when the requests
            # queue up one behind the other.
            with ThreadPoolExecutor(min(FILL_WORKERS, len(work))) as ex:
                for sym, rows, err in ex.map(_fetch_window, work):
                    if err:
                        out["errors"].append(f"{sym} {err}")
                    if rows:
                        got.setdefault(sym, []).extend(rows)
            for sym, rows in got.items():
                con.executemany(
                    "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)", rows)
                con.commit()
                out["symbols"][sym] = len(rows)
                out["filled"] += len(rows)
                # The fold the chart reads is memoised per symbol; a fill that
                # does not evict it is invisible until the process restarts.
                try:
                    ds._daily_cache.pop(sym, None)
                except Exception:                     # noqa: BLE001
                    pass

        # VERIFY. A fill that fetched nothing must be reported, never assumed:
        # a throttled or not-yet-published window returns an empty list with no
        # exception, and the symbol then streams onto the same hole it was
        # supposed to have closed.
        for sym, _g in jobs:
            if sym not in tokens:
                continue
            after = minute_gap(con, sym, now=now)
            left = after["gap_min"] or 0
            if left > FILL_TOLERANCE_MIN:
                msg = (f"still {left:,} open-market minute(s) behind after "
                       f"writing {out['symbols'].get(sym, 0)} row(s) — Kite "
                       f"returned nothing for that window (throttled, or the "
                       f"minutes are not published yet), so the socket would "
                       f"open at the right edge of a {left:,}-minute hole")
                out["unfilled"][sym] = msg
                log.warning("%s: %s", sym, msg)
    finally:
        if owned:
            con.close()
    return out


# ── the stream ────────────────────────────────────────────────────

class KiteStream:
    """Owns one KiteTicker and routes its ticks into `_live_on_tick`."""

    def __init__(self, symbols: Sequence[str], *,
                 max_stale_sessions: int = DEFAULT_MAX_STALE_SESSIONS,
                 dry_run: bool = False, fill_first: bool = True,
                 max_fill_min: int = MAX_FILL_MIN) -> None:
        self.requested = [s.strip().upper() for s in symbols if s.strip()]
        self.max_stale_sessions = max_stale_sessions
        self.dry_run = dry_run
        self.fill_first = fill_first
        self.max_fill_min = max_fill_min
        self.symbols: list[str] = []
        self.refused: dict[str, str] = {}
        self._access_token: str | None = None
        self._ticker: Any = None
        self._tok_to_sym: dict[int, str] = {}
        self._sym_to_tok: dict[str, int] = {}
        self._connected = False
        self._reconnects = 0
        self._started_at = 0.0
        self._lock = threading.RLock()

    # -- planning ------------------------------------------------
    def plan(self) -> list[dict]:
        """Freshness verdict per requested symbol, plus the intraday gap.

        Two independent measures, both reported: a symbol can be 0 sessions
        behind and 331 minutes behind at the same time, which is precisely the
        case the session count alone waves through. No socket, no Kite session,
        no writes.
        """
        con = sqlite3.connect(ds.DB_PATH, timeout=60)
        con.execute("PRAGMA busy_timeout=60000")   # a live writer holds this file
        try:
            verdicts = []
            for s in self.requested:
                v = freshness(con, s, self.max_stale_sessions)
                try:
                    v["gap"] = minute_gap(con, s)
                except Exception as exc:              # noqa: BLE001
                    # A measure that raises must not take the plan down; the
                    # session gate above still stands on its own.
                    log.warning("minute_gap(%s) failed: %s", s, exc)
                    v["gap"] = {"gap_min": None, "note": f"gap unmeasured: {exc}"}
                verdicts.append(v)
        finally:
            con.close()
        with self._lock:
            self.symbols = [v["symbol"] for v in verdicts if v["ok"]]
            self.refused = {v["symbol"]: v["reason"]
                            for v in verdicts if not v["ok"]}
        return verdicts

    def token(self) -> str:
        if self._access_token is None:
            self._access_token = _kite_access_token()
        return self._access_token

    def fill_gap(self) -> dict:
        """Close every requested symbol's intraday hole before subscribing."""
        return fill_gaps(self.requested, self.token(),
                         max_fill_min=self.max_fill_min)

    # -- lifecycle -----------------------------------------------
    def start(self) -> None:
        # Fill BEFORE planning, not after: the fill is what makes a symbol
        # fresh, and a symbol one session behind should be repaired and then
        # streamed rather than refused with no way forward.
        got: dict[str, Any] = {"too_wide": {}, "unfilled": {}, "symbols": {},
                               "errors": []}
        if self.fill_first and not self.dry_run:
            got = self.fill_gap()
            for sym, why in got["too_wide"].items():
                print(f"  REFUSE  {sym:<14} {why}")
            for sym, why in got["unfilled"].items():
                print(f"  WARN    {sym:<14} {why}")
            for sym, n in sorted(got["symbols"].items()):
                if n:
                    print(f"  filled  {sym:<14} {n:,} minute(s)")
            for err in got["errors"][:5]:
                print(f"  fetch error: {err}")

        verdicts = self.plan()
        for v in verdicts:
            mark = "stream" if v["ok"] else "REFUSE"
            gap = v.get("gap", {}).get("gap_min")
            tail = f" [gap {gap:,}m]" if gap else ""
            print(f"  {mark:>6}  {v['symbol']:<14} {v['reason']}{tail}")
        # A symbol whose hole is too wide to close must not stream over it. The
        # session gate cannot catch this one — an intraday hole is 0 sessions
        # wide — so the refusal has to be applied here or not at all.
        for sym in list(self.symbols):
            if sym in got["too_wide"]:
                self.symbols.remove(sym)
                self.refused[sym] = got["too_wide"][sym]
        if not self.symbols:
            print("nothing to stream — every requested symbol was refused")
            return
        if self.dry_run:
            print(f"\ndry run: would subscribe {len(self.symbols)} symbol(s) "
                  f"in MODE_FULL ({', '.join(self.symbols)}); "
                  f"no socket opened, no rows written")
            return

        token = self.token()
        self._resolve_tokens(token)
        if not self._tok_to_sym:
            print("no instrument tokens resolved — refusing to open a socket")
            return
        self._ticker = self._build_ticker(token)
        self._wire(self._ticker)
        self._started_at = time.time()
        # connect(threaded=True) returns immediately; kiteconnect owns the
        # reader thread and the reconnect loop.
        self._ticker.connect(threaded=True, disable_ssl_verification=False)
        print(f"connected: {len(self._tok_to_sym)} symbol(s) in MODE_FULL")

    def stop(self) -> None:
        with self._lock:
            t, self._ticker = self._ticker, None
        if t is not None:
            try:
                t.close(code=1000, reason="shutdown")
            except Exception:           # noqa: BLE001
                log.warning("KiteTicker close raised; ignoring", exc_info=True)
        self._connected = False
        # Drop the forming minute rather than flushing it. A partial minute
        # written to `bars` is indistinguishable from a real one to every
        # reader downstream — the volume profile, the pattern ledger, the
        # daily fold — and nothing would ever mark it as truncated. Losing
        # the last <60s is the honest trade.
        dropped = 0
        for sym in self.symbols:
            st = ds._LIVE.get(sym)
            if st is None:
                continue
            with st["lock"]:
                if st["form"] is not None:
                    st["form"] = None
                    dropped += 1
        if dropped:
            print(f"dropped {dropped} partial forming minute(s) on stop")

    # -- observability -------------------------------------------
    def status(self) -> dict:
        with _CURSOR_GUARD:
            cs = {s: _CURSORS[s] for s in self.symbols if s in _CURSORS}
        last_ts = max((c.last_ts for c in cs.values()), default=0)
        return {
            "connected": self._connected,
            "symbols": list(self.symbols),
            "refused": dict(self.refused),
            "ticks_in": sum(c.ticks for c in cs.values()),
            "bars_closed": sum(c.closes for c in cs.values()),
            "volume_reseeds": sum(c.reseeds for c in cs.values()),
            "stale_volume_drops": sum(c.stale_drops for c in cs.values()),
            "duplicate_snapshots": sum(c.dups for c in cs.values()),
            "missing_exchange_ts": sum(c.no_ts for c in cs.values()),
            "last_tick_age_s": (round(time.time() - last_ts, 1)
                                if last_ts else None),
            "reconnects": self._reconnects,
            "uptime_s": (round(time.time() - self._started_at, 1)
                         if self._started_at else None),
        }

    # -- kite plumbing -------------------------------------------
    def _resolve_tokens(self, access_token: str) -> None:
        """Instrument tokens for the accepted symbols, one map for both the
        socket and the fill — see resolve_tokens() for why every family needs
        its own segment."""
        tokens, failed = resolve_tokens(self.symbols, access_token)
        for sym, tok in tokens.items():
            self._tok_to_sym[tok] = sym
            self._sym_to_tok[sym] = tok
        for sym, why in failed.items():
            self.refused[sym] = why
            if sym in self.symbols:
                self.symbols.remove(sym)

    def _build_ticker(self, access_token: str) -> Any:
        """Same construction as KiteTickerManager._build_ticker — mirrored,
        not imported, so starting this stream cannot mutate pivot's
        process-wide ticker singleton or its subscription refcounts."""
        from kiteconnect import KiteTicker           # type: ignore
        from backend.config import settings
        return KiteTicker(
            api_key=settings.kite_api_key,
            access_token=access_token,
            reconnect=True,
            reconnect_max_tries=50,
            reconnect_max_delay=60,
        )

    def _wire(self, ticker: Any) -> None:
        ticker.on_ticks = self._on_ticks
        ticker.on_connect = self._on_connect
        ticker.on_reconnect = self._on_reconnect
        ticker.on_close = self._on_close
        ticker.on_error = self._on_error
        ticker.on_noreconnect = self._on_noreconnect

    def _on_ticks(self, _ws: Any, ticks: list[dict]) -> None:
        for tick in ticks or ():
            tok = _as_int(tick.get("instrument_token"))
            sym = self._tok_to_sym.get(tok) if tok is not None else None
            if sym is None:
                continue
            try:
                on_tick(sym, tick)
            except Exception:           # noqa: BLE001
                # One malformed tick must not kill the reader thread and take
                # every other symbol's candles down with it.
                log.exception("tick dropped for %s", sym)

    def _on_connect(self, ws: Any, _response: Any) -> None:
        toks = list(self._tok_to_sym)
        # MODE_FULL, not MODE_QUOTE. The depth half of FULL is indeed useless
        # to a 1-min candle, but FULL is the ONLY mode that carries
        # exchange_timestamp and last_trade_time, and both are load-bearing
        # here. Measured against live MCX on 2026-08-03, a QUOTE-mode run
        # logged missing_exchange_ts=552 out of 552 ticks: every single bar was
        # bucketed by the receiver's wall clock, so a lagging connection files
        # trades into the wrong minute. last_trade_time also turns dedup from a
        # guess into a fact — the same run counted 454 duplicate snapshots out
        # of 552, which QUOTE mode gives no way to tell from real prints.
        # MODE_LTP remains wrong for a different reason: no volume counter.
        try:
            ws.subscribe(toks)
            ws.set_mode(ws.MODE_FULL, toks)
        except Exception:               # noqa: BLE001
            log.exception("subscribe failed on connect")
            return
        n = reset_cursors(self.symbols)
        self._connected = True
        log.info("connected; %d symbols, %d cursor(s) re-seeded", len(toks), n)

    def _on_reconnect(self, _ws: Any, attempts: int) -> None:
        self._reconnects += 1
        # The gap swallowed an unknown number of ticks, so every cumulative
        # reading we hold is stale. Keeping them would attribute the whole
        # outage's volume to whichever minute the socket comes back in.
        reset_cursors(self.symbols)
        log.warning("reconnecting (attempt %s); cursors re-seeded", attempts)

    def _on_close(self, _ws: Any, code: Any, reason: Any) -> None:
        self._connected = False
        log.info("closed: code=%s reason=%s", code, reason)

    def _on_error(self, _ws: Any, code: Any, reason: Any) -> None:
        log.error("ticker error: code=%s reason=%s", code, reason)

    def _on_noreconnect(self, _ws: Any) -> None:
        self._connected = False
        log.error("ticker gave up reconnecting")


def _kite_access_token() -> str:
    """The live session pivot already holds — same path backfill_1min.py uses.
    Imported lazily so --dry-run and --self-test need neither the pivot venv
    nor a database."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pivot"))
    from backend.database import SessionLocal
    from backend.brokers.sessions import get_active_kite_session
    from backend.kite.auth import read_kite_access_token
    db = SessionLocal()
    try:
        session = get_active_kite_session(db)
    finally:
        db.close()
    if session is None:
        raise SystemExit("no active Kite session — login first")
    tok = read_kite_access_token(session)
    if not tok or tok.startswith("mock_") or len(tok) < 20:
        raise SystemExit("Kite token is mock/expired — re-login")
    return tok


# ── self-test ─────────────────────────────────────────────────────
# No socket. A synthetic MODE_QUOTE stream with a KNOWN per-minute volume
# profile, expressed the way Kite expresses it — as a cumulative day counter
# — fed through on_tick into the real engine and read back out of the real
# store. Scratch symbols only, deleted at the end.

_SCRATCH = "__TEST_STREAM__"
_SCRATCH_RAW = "__TEST_STREAM_RAW__"   # the deliberately-broken control

# (offset_s, price, cumulative_volume). Minute boundaries at 0/60/120/180.
# Base is 10:00 IST so the pre-open gate in _live_on_tick lets it through.
_FEED: list[tuple[int, float, int]] = [
    # minute 0 — the connect minute. The first snapshot reports the running
    # total with nothing new since we arrived, which is the ordinary case.
    (0,   100.0, 4_500_000),
    (15,  103.0, 4_500_400),
    (30,   98.0, 4_500_900),
    (45,  101.0, 4_501_200),
    # minute 1
    (60,  105.0, 4_501_500),
    (75,  107.0, 4_501_750),
    (90,  104.0, 4_502_100),
    # minute 2
    (120, 104.5, 4_503_100),
    (135, 110.0, 4_504_000),
    (150, 109.0, 4_504_600),
    # minute 3 — the anomaly minute: a duplicate snapshot and a backwards one.
    (180, 108.0, 4_504_800),
    (195, 108.5, 4_504_800),           # duplicate counter, different price
    (210, 107.0, 4_504_750),           # out-of-order/stale: counter rewinds
    (225, 109.0, 4_505_300),
    # minute 4 — one tick purely to close minute 3; stays forming. It repeats
    # minute 3's counter, so it is the SECOND duplicate snapshot in the feed.
    (240, 109.5, 4_505_300),
]

# Intended, by construction. Minute 0 is the full 1200 because the connect
# snapshot carried no new volume. Minute 3 is 200 + 0 (dup) + 0 (stale) + 500.
_WANT = {
    0: {"v": 1200, "o": 100.0, "h": 103.0, "l": 98.0,  "c": 101.0},
    1: {"v": 900,  "o": 105.0, "h": 107.0, "l": 104.0, "c": 104.0},
    2: {"v": 2500, "o": 104.5, "h": 110.0, "l": 104.5, "c": 109.0},
    3: {"v": 700,  "o": 108.0, "h": 109.0, "l": 107.0, "c": 109.0},
}


def _base_ts() -> int:
    return int(datetime(2026, 6, 15, 10, 0, 0, tzinfo=IST).timestamp())


def _raw(offset: int, price: float, cum: int) -> dict:
    """A MODE_QUOTE payload shaped like kiteconnect's: exchange_timestamp is
    a NAIVE datetime that is already IST."""
    ts = _base_ts() + offset
    naive_ist = datetime.fromtimestamp(ts, tz=IST).replace(tzinfo=None)
    return {"instrument_token": 999999, "last_price": price,
            "volume_traded": cum, "exchange_timestamp": naive_ist,
            "ohlc": {"open": 100.0, "high": 110.0, "low": 98.0, "close": 99.0}}


def _read_minutes(sym: str, n: int) -> list[tuple]:
    con = sqlite3.connect(ds.DB_PATH)
    try:
        return con.execute(
            "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? ORDER BY ts LIMIT ?",
            (sym, n)).fetchall()
    finally:
        con.close()


def _cleanup(*syms: str) -> None:
    con = sqlite3.connect(ds.DB_PATH)
    try:
        for s in syms:
            # Exact match only. LIKE would treat the leading underscores in
            # the scratch name as wildcards and reach real symbols.
            con.execute("DELETE FROM bars WHERE symbol=?", (s,))
        con.commit()
    finally:
        con.close()
    for s in syms:
        with _CURSOR_GUARD:
            _CURSORS.pop(s, None)
        ds._LIVE.pop(s, None)
        ds._daily_cache.pop(s, None)


def self_test() -> int:
    fails: list[str] = []
    _cleanup(_SCRATCH, _SCRATCH_RAW)
    base = _base_ts()
    print(f"scratch symbols {_SCRATCH} / {_SCRATCH_RAW}")
    print(f"synthetic session base {_ist_str(base)}  "
          f"({len(_FEED)} MODE_QUOTE snapshots)\n")

    # ---- 1. the adapter, as shipped -------------------------------
    for off, px, cum in _FEED:
        on_tick(_SCRATCH, _raw(off, px, cum))
    rows = _read_minutes(_SCRATCH, 8)
    print("adapter (delta logic ON):")
    for i, (ts, o, h, l, c, v) in enumerate(rows):
        w = _WANT.get(i)
        ok = w and (v == w["v"] and o == w["o"] and h == w["h"]
                    and l == w["l"] and c == w["c"])
        print(f"  m{i} {_ist_str(ts)[-9:]}  o={o:<7} h={h:<7} l={l:<7} "
              f"c={c:<7} v={v:<8} want v={w['v'] if w else '?':<8} "
              f"{'OK' if ok else 'MISMATCH'}")

    if len(rows) != 4:
        fails.append(f"expected 4 closed minutes, got {len(rows)}")
    for i, (ts, o, h, l, c, v) in enumerate(rows):
        w = _WANT.get(i)
        if w is None:
            fails.append(f"unexpected extra minute {i}")
            continue
        if ts != base + i * 60:
            fails.append(f"m{i} bucketed at {ts}, want {base + i * 60} "
                         f"(exchange-timestamp bucketing broken)")
        if v != w["v"]:
            fails.append(f"m{i} volume {v}, want {w['v']} "
                         f"(cumulative-delta broken)")
        if (o, h, l, c) != (w["o"], w["h"], w["l"], w["c"]):
            fails.append(f"m{i} OHLC {(o, h, l, c)}, want "
                         f"{(w['o'], w['h'], w['l'], w['c'])}")

    cur = cursor(_SCRATCH)
    print(f"\n  cursor: ticks={cur.ticks} reseeds={cur.reseeds} "
          f"dups={cur.dups} stale_drops={cur.stale_drops} "
          f"no_exchange_ts={cur.no_ts} closes={cur.closes}")
    if cur.reseeds != 1:
        fails.append(f"expected exactly 1 connect re-seed, got {cur.reseeds}")
    if cur.dups != 2:
        # m3's repeated counter, plus the m4 flush tick which repeats it again.
        fails.append(f"expected 2 duplicate snapshots, got {cur.dups}")
    if cur.stale_drops != 1:
        fails.append(f"expected 1 stale/out-of-order drop, got "
                     f"{cur.stale_drops}")
    if cur.no_ts != 0:
        fails.append(f"{cur.no_ts} tick(s) fell back to wall clock; the feed "
                     f"carries an exchange timestamp on every one")

    # get_bars is the path every chart tool takes; prove it agrees, and that
    # the still-forming minute 4 is merged rather than written.
    live = ds.get_bars(_SCRATCH, "1m", None, 10)["bars"]
    print(f"  get_bars 1m -> {len(live)} bars, last "
          f"t={live[-1]['t']} v={live[-1]['v']} (minute 4, forming)")
    if len(live) != 5:
        fails.append(f"get_bars returned {len(live)} bars, want 5 "
                     f"(4 closed + 1 forming)")
    elif [b["v"] for b in live[:4]] != [_WANT[i]["v"] for i in range(4)]:
        fails.append("get_bars volumes disagree with the store")

    # ---- 2. the same feed with the delta logic removed -------------
    # Not a flag on the shipped code: a local naive translator, so the
    # regression above cannot be satisfied by a code path that only exists
    # for tests.
    print("\ncontrol (delta logic REMOVED — raw volume_traded passed through):")
    for off, px, cum in _FEED:
        ts = base + off
        ds._live_on_tick(_SCRATCH_RAW, ts, px, cum)
    raw_rows = _read_minutes(_SCRATCH_RAW, 8)
    for i, (_ts, _o, _h, _l, _c, v) in enumerate(raw_rows):
        w = _WANT.get(i)
        print(f"  m{i} v={v:<12,} want v={w['v'] if w else '?':<8} "
              f"overstated {v // w['v'] if w and w['v'] else 0:,}x")
    broke = [i for i, r in enumerate(raw_rows)
             if i in _WANT and r[5] != _WANT[i]["v"]]
    if len(broke) != len(raw_rows):
        fails.append("the control did NOT break — the volume assertions "
                     "above are not actually testing the delta logic")
    else:
        print(f"  -> all {len(broke)} minutes wrong, as they must be: the "
              f"assertions do test gotcha #1")

    # ---- 3. the branches the synthetic feed cannot reach -----------
    # translate() is pure but for its cursor, so these need no store at all.
    print("\npure translate() branches:")

    c = TickCursor()
    # naive IST datetime must not be read through the host's timezone
    got = translate(_SCRATCH, _raw(0, 100.0, 10), c)
    if got is None or got[0] != base:
        fails.append(f"naive IST exchange_timestamp -> {got}, want ts={base}")
    print(f"  naive-IST datetime      ts={got[0] if got else None} "
          f"want {base}")

    c = TickCursor()
    no_clock = {"last_price": 100.0, "volume_traded": 10}
    got = translate(_SCRATCH, no_clock, c, now=base + 7)
    if got is None or got[0] != base + 7 or c.no_ts != 1:
        fails.append(f"missing exchange clock -> {got}, no_ts={c.no_ts}; "
                     f"want wall-clock ts={base + 7} and no_ts=1")
    print(f"  no exchange clock       ts={got[0] if got else None} "
          f"no_ts={c.no_ts} (counted, not silent)")

    c = TickCursor()
    day = ds._ist_day(base, ds.IST_OFF)
    c.delta(5_000_000, day)            # seed, drops the first delta
    same_day_back = c.delta(4_999_000, day)
    after_back = c.delta(5_000_400, day)
    if (same_day_back, after_back) != (0, 400):
        fails.append(f"same-day rewind then resume -> {same_day_back}, "
                     f"{after_back}; want 0 then 400 (cursor must not move)")
    print(f"  same-day rewind         delta={same_day_back} then "
          f"{after_back} (want 0 then 400 — no double count)")

    c = TickCursor()
    c.delta(9_000_000, day)            # yesterday's closing total
    rollover = c.delta(1_500, day + 1)
    if rollover != 1_500:
        fails.append(f"session rollover -> {rollover}, want 1500 "
                     f"(the new day's volume so far)")
    print(f"  session rollover        delta={rollover} (want 1500) "
          f"reseeds={c.reseeds}")

    c = TickCursor()
    c.delta(5_000_000, day)
    c.last_cum, c.last_day = None, None    # what a reconnect does
    after_reconnect = c.delta(6_000_000, day)
    if after_reconnect != 0:
        fails.append(f"first tick after reconnect -> {after_reconnect}, "
                     f"want 0 (the 1M-share gap must not become one bar)")
    print(f"  first tick post-reconnect delta={after_reconnect} (want 0)")

    if translate(_SCRATCH, {"last_price": 0.0}, TickCursor()) is not None:
        fails.append("a zero print was accepted as a trade")
    if translate(_SCRATCH, {}, TickCursor()) is not None:
        fails.append("a payload with no last_price was accepted")
    print("  zero / priceless ticks  rejected")

    # ---- 4. the freshness refusal (hazard #4) ---------------------
    # Against an in-memory store, so the verdict is deterministic no matter
    # how fresh the real 11.6 GB store happens to be when this runs.
    print("\nfreshness refusal, on a synthetic store:")
    mem = sqlite3.connect(":memory:")
    mem.execute("CREATE TABLE bars (symbol TEXT, ts INTEGER, o REAL, h REAL,"
                " l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts))")
    mem.execute("CREATE TABLE bars_1d (symbol TEXT, ts INTEGER, o REAL,"
                " h REAL, l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts))")
    day0 = ds._ist_day(base, ds.IST_OFF)
    for name, minute_day in (("HOLEY", day0 - 5), ("CURRENT", day0 - 1)):
        mem.execute("INSERT INTO bars VALUES (?,?,?,?,?,?,?)",
                    (name, minute_day * 86400 - ds.IST_OFF + 10 * 3600,
                     1, 1, 1, 1, 1))
        for d in range(day0 - 20, day0 + 1):       # a full daily series
            mem.execute("INSERT INTO bars_1d VALUES (?,?,?,?,?,?,?)",
                        (name, d * 86400 - ds.IST_OFF, 1, 1, 1, 1, 1))
    mem.execute("INSERT INTO bars_1d VALUES ('NOMINUTES',?,1,1,1,1,1)",
                (day0 * 86400 - ds.IST_OFF,))
    # STALEREF: minutes and daily stop on the SAME old day, so stale_sessions
    # is 0 and the symbol reads as current unless the yardstick's own age is
    # checked. This is the real BTCUSDT case from 2026-08-02, where minutes and
    # bars_1d both ended 29 Jul and it reported "0 sessions behind".
    mem.execute("INSERT INTO bars VALUES ('STALEREF',?,1,1,1,1,1)",
                ((day0 - 9) * 86400 - ds.IST_OFF + 10 * 3600,))
    for d in range(day0 - 29, day0 - 8):
        mem.execute("INSERT INTO bars_1d VALUES ('STALEREF',?,1,1,1,1,1)",
                    (d * 86400 - ds.IST_OFF,))
    # the fixture's clock, not the wall clock — see freshness(now=...)
    synthetic_now = day0 * 86400 - ds.IST_OFF + 16 * 3600
    for name, want_ok in (("HOLEY", False), ("CURRENT", True),
                          ("NOMINUTES", False), ("STALEREF", False)):
        v = freshness(mem, name, DEFAULT_MAX_STALE_SESSIONS, now=synthetic_now)
        print(f"  {'stream' if v['ok'] else 'REFUSE':>6}  {name:<10} "
              f"stale={v['stale_sessions']}  {v['reason'][:78]}")
        if v["ok"] is not want_ok:
            fails.append(f"freshness({name}) ok={v['ok']}, want {want_ok}")
    mem.close()

    # ---- 5. the intraday gap, in open-market minutes ---------------
    # The property under test: a night, a weekend and a holiday are NOT gaps,
    # and a hole inside a session IS one — counted per instrument family, on
    # that family's own clock. A synthetic store so the numbers are exact.
    print("\nintraday gap (open-market minutes), on a synthetic store:")
    gm = sqlite3.connect(":memory:")
    gm.execute("CREATE TABLE bars (symbol TEXT, ts INTEGER, o REAL, h REAL,"
               " l REAL, c REAL, v REAL, PRIMARY KEY (symbol, ts))")
    gm.execute("CREATE TABLE bars_1d (symbol TEXT, ts INTEGER, o REAL, h REAL,"
               " l REAL, c REAL, v REAL, PRIMARY KEY (symbol, ts))")

    # 2026-06-15 is a Monday; 06-19 a Friday, 06-20/21 the weekend. 06-17 is
    # declared a holiday by simply not existing in bars_1d, which is exactly
    # how a real holiday appears in this store.
    mon = ds._ist_day(int(datetime(2026, 6, 15, 12, 0, tzinfo=IST).timestamp()),
                      ds.IST_OFF)
    sessions = [mon, mon + 1, mon + 3, mon + 4]          # Mon Tue THU Fri
    holiday = mon + 2                                    # Wednesday, no session

    def _seed(sym: str, last_day: int, last_mod: int) -> None:
        """Minutes up to (last_day, last_mod); a CURRENT daily series.

        bars_1d covers every session including ones the minutes never reached,
        which is the real shape of the store — the daily fold is imported for
        the whole universe while the minute store is what falls behind. It is
        also what makes the holiday assertion meaningful: Wednesday is absent
        from bars_1d, so it must not be counted, while the days past the end of
        bars_1d fall back to weekday-not-weekend.
        """
        open_min, close_min, tz = session_bounds(sym)
        for d in sessions:
            gm.execute("INSERT OR REPLACE INTO bars_1d VALUES (?,?,?,?,?,?,?)",
                       (sym, d * 86400 - tz, 1, 1, 1, 1, 1))
            if d > last_day:
                continue
            # two rows per day is enough: the gap reads MAX(ts) only
            gm.execute("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                       (sym, _bar_ts(d, open_min, tz), 1, 1, 1, 1, 1))
            end = last_mod if d == last_day else close_min
            gm.execute("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                       (sym, _bar_ts(d, end, tz), 1, 1, 1, 1, 1))

    def _at(day: int, mod: int, tz: int = ds.IST_OFF) -> int:
        return _bar_ts(day, mod, tz)

    # (symbol, last stored day/minute, "now", expected gap, what it proves)
    cases = [
        ("RELIANCE", mon, 15 * 60 + 29, _at(mon, 20 * 60), 0,
         "closed cleanly 15:29, asked at 20:00 same day"),
        ("RELIANCE", mon, 15 * 60 + 29, _at(mon + 1, 8 * 60), 0,
         "closed cleanly, asked 08:00 NEXT morning — the night is not a gap"),
        ("RELIANCE", mon, 15 * 60 + 29, _at(mon + 1, 10 * 60), 45,
         "next day 10:00 -> 09:15..09:59 really are missing"),
        ("RELIANCE", mon, 14 * 60 + 29, _at(mon, 20 * 60), 60,
         "truncated at 14:29 -> the 60 minutes to 15:29"),
        ("RELIANCE", mon, 11 * 60, _at(mon, 16 * 60, ), 269,
         "the reported case: stops 11:00, asked 16:00 -> 11:01..15:29"),
        ("RELIANCE", mon + 4, 15 * 60 + 29, _at(mon + 7, 8 * 60), 0,
         "Friday close, Monday 08:00 — the weekend is not a gap"),
        ("RELIANCE", mon + 1, 15 * 60 + 29, _at(mon + 3, 9 * 60), 0,
         "Tue close, Thu pre-open — Wednesday was a holiday, not a hole"),
        ("GOLD", mon, 13 * 60 + 10, _at(mon, 20 * 60), 409,
         "MCX runs to 23:29, so 13:11..19:59 are all open minutes"),
        ("USDINR", mon, 13 * 60 + 10, _at(mon, 20 * 60), 229,
         "CDS closes 16:59, so only 13:11..16:59 count"),
        ("USDINR", mon, 16 * 60 + 59, _at(mon + 1, 9 * 60 + 30), 30,
         "CDS OPENS 09:00, not the 09:15 bucket anchor -> 30, not 15"),
    ]
    for sym, last_day, last_mod, now_ts, want, why in cases:
        gm.execute("DELETE FROM bars WHERE symbol=?", (sym,))
        gm.execute("DELETE FROM bars_1d WHERE symbol=?", (sym,))
        _seed(sym, last_day, last_mod)
        g = minute_gap(gm, sym, now=now_ts)
        ok = g["gap_min"] == want
        print(f"  {'OK ' if ok else 'BAD'} {sym:<9} gap={g['gap_min']:>5} "
              f"want={want:<5} wall={g['wall_min']:>6}  {why}")
        if not ok:
            fails.append(f"minute_gap({sym}, {why}) = {g['gap_min']}, "
                         f"want {want}")
    # The control: the same two cases measured in WALL-CLOCK minutes, which is
    # what this function replaces. If these two agreed with the open-market
    # count there would be nothing to test.
    gm.execute("DELETE FROM bars WHERE symbol=?", ("RELIANCE",))
    gm.execute("DELETE FROM bars_1d WHERE symbol=?", ("RELIANCE",))
    _seed("RELIANCE", mon + 4, 15 * 60 + 29)
    over_weekend = minute_gap(gm, "RELIANCE", now=_at(mon + 7, 8 * 60))
    print(f"  control: Friday close -> Monday 08:00 reads "
          f"{over_weekend['wall_min']:,} wall-clock minutes and "
          f"{over_weekend['gap_min']} open-market minutes")
    if over_weekend["wall_min"] < 3000:
        fails.append("the wall-clock control is not large enough to be a "
                     "control — the assertions above prove nothing")
    if over_weekend["gap_min"] != 0:
        fails.append("a clean Friday close reads as a hole on Monday morning")

    # ---- 5b. the MAX_FILL_MIN bound --------------------------------
    # A hole wider than the bound must be REPORTED, not fetched. The token is
    # deliberately invalid: if the refusal did not short-circuit ahead of every
    # Kite path, the run would raise or record a fetch error, and both are
    # asserted against below.
    print("\nMAX_FILL_MIN bound:")
    gm.execute("DELETE FROM bars WHERE symbol=?", ("RELIANCE",))
    gm.execute("DELETE FROM bars_1d WHERE symbol=?", ("RELIANCE",))
    _seed("RELIANCE", mon, 9 * 60 + 15)
    wide_now = _at(mon + 4, 15 * 60 + 30)
    wide = minute_gap(gm, "RELIANCE", now=wide_now)
    got = fill_gaps(["RELIANCE"], "NOT-A-REAL-TOKEN", max_fill_min=MAX_FILL_MIN,
                    now=wide_now, con=gm)
    tripped = "RELIANCE" in got["too_wide"]
    print(f"  gap={wide['gap_min']:,} min vs limit {MAX_FILL_MIN:,} -> "
          f"{'REFUSED' if tripped else 'FILLED'}; wrote {got['filled']} row(s), "
          f"{len(got['errors'])} fetch error(s)")
    if tripped:
        print(f"    {got['too_wide']['RELIANCE']}")
    if wide["gap_min"] <= MAX_FILL_MIN:
        fails.append(f"the bound fixture is only {wide['gap_min']} min wide; "
                     f"it cannot trip a {MAX_FILL_MIN} min limit")
    if not tripped:
        fails.append("a hole wider than MAX_FILL_MIN was not refused")
    if got["filled"] or got["errors"]:
        fails.append("the too-wide refusal reached a Kite fetch — it must "
                     "short-circuit before resolving an instrument")
    # The other side of the bound, asserted through the same predicate
    # fill_gaps calls — not through fill_gaps itself, because proceeding means
    # resolving an instrument and --self-test must stay offline.
    narrow_now = _at(mon + 1, 15 * 60 + 30)
    narrow = minute_gap(gm, "RELIANCE", now=narrow_now)
    print(f"  same symbol two sessions earlier: gap={narrow['gap_min']:,} min "
          f"-> {'REFUSED' if too_wide(narrow) else 'would fill'}")
    if too_wide(narrow):
        fails.append("a hole INSIDE the bound was refused; the bound is not "
                     "the thing being tested")
    if not too_wide(wide):
        fails.append("too_wide() disagrees with the refusal fill_gaps took")
    gm.close()

    # ---- 6. cleanup ------------------------------------------------
    _cleanup(_SCRATCH, _SCRATCH_RAW)
    left = sum(_read_minutes(s, 1) and 1 or 0
               for s in (_SCRATCH, _SCRATCH_RAW))
    print(f"\ncleanup: {left} scratch row-group(s) remaining (want 0)")
    if left:
        fails.append("scratch rows survived cleanup")

    if fails:
        print(f"\nSELF-TEST FAILED ({len(fails)})")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nSELF-TEST PASSED")
    return 0


# ── cli ───────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--symbols", default="",
                   help="comma-separated tradingsymbols")
    p.add_argument("--dry-run", action="store_true",
                   help="plan and freshness verdicts only; no socket")
    p.add_argument("--self-test", action="store_true",
                   help="synthetic tick verification; no socket, no Kite")
    p.add_argument("--max-stale-sessions", type=int,
                   default=DEFAULT_MAX_STALE_SESSIONS,
                   help="how many sessions of missing 1-min history to "
                        "tolerate before refusing a symbol")
    p.add_argument("--max-fill-min", type=int, default=MAX_FILL_MIN,
                   help="widest intraday hole (in open-market minutes) the "
                        "streamer will close itself before subscribing")
    p.add_argument("--no-fill", action="store_true",
                   help="do not close intraday holes before subscribing")
    p.add_argument("--fill-only", action="store_true",
                   help="measure and close intraday holes, then exit; "
                        "no socket")
    p.add_argument("--gaps", action="store_true",
                   help="measure intraday gaps and exit; no Kite, no writes")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if a.self_test:
        return self_test()
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    if not syms:
        p.error("--symbols is required (or use --self-test)")

    if a.gaps:
        con = sqlite3.connect(f"file:{ds.DB_PATH}?mode=ro", uri=True)
        try:
            for s in syms:
                g = minute_gap(con, s)
                print(f"  {s:<16} gap={str(g['gap_min']):>8}  "
                      f"wall={str(g['wall_min']):>8}  {g['note']}")
        finally:
            con.close()
        return 0

    stream = KiteStream(syms, max_stale_sessions=a.max_stale_sessions,
                        dry_run=a.dry_run, fill_first=not a.no_fill,
                        max_fill_min=a.max_fill_min)
    if a.fill_only:
        got = stream.fill_gap()
        for sym in sorted(set(syms)):
            n = got["symbols"].get(sym)
            why = (got["too_wide"].get(sym) or got["unfilled"].get(sym)
                   or got["skipped"].get(sym) or "")
            print(f"  {sym:<16} wrote={0 if n is None else n:>6}  {why}")
        for err in got["errors"]:
            print(f"  fetch error: {err}")
        print(f"total {got['filled']:,} row(s) written")
        return 0
    stream.start()
    if a.dry_run or not stream.symbols:
        return 0
    try:
        while True:
            time.sleep(_STATUS_EVERY)
            print(stream.status())
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        stream.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
