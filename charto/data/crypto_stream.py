#!/usr/bin/env python3
"""Crypto trade websockets -> charto's live tick engine.

The sibling of kite_stream.py, and deliberately the same shape: a pure
`translate()`, a per-symbol cursor, a `freshness()` refusal, a `--self-test`,
a `--dry-run`. dataserver.py still owns the whole live path — `_live_on_tick(
sym, ts, price, vol)` keeps one forming 1-min bar per symbol, writes the
minute to SQLite the instant it closes, pops the daily cache and pushes SSE;
`get_bars` merges the forming bar so every chart tool goes live without
knowing a tick loop exists. This file adds NO write path of its own into
`bars`. Everything here is translation and refusal.

WHY THIS FILE EXISTS AT ALL
---------------------------
kite_stream.py cannot be verified on a Sunday: NSE is shut, the socket has
nothing to say, and a stream that has never carried a real print is a stream
nobody has tested. Crypto trades 24/7 on two venues that need no key and no
auth, so this adapter is the one that can prove the seam end-to-end with real
market data on any day of the week. The venues are genuinely useful in their
own right — the store already holds 10 USDT pairs and 4 -USD pairs — but the
first job is to make the live path falsifiable.

THE CRITICAL DIFFERENCE FROM kite_stream, AND IT IS AN INVERSION
----------------------------------------------------------------
Kite's MODE_QUOTE `volume_traded` is CUMULATIVE for the trading day, so
kite_stream keeps a per-symbol cursor and passes the DELTA (its gotcha #1).

A crypto TRADE feed is the exact opposite. `publicTrade` (Bybit) and `matches`
(Coinbase) each carry the size of ONE trade, which is ALREADY INCREMENTAL.
Both failure directions are real and both are silent:

  * applying kite_stream's delta to per-trade sizes zeroes the book. Sizes do
    not ascend, so `cum < last_cum` on most trades and the cursor returns 0;
    the occasional larger trade contributes only its excess over the previous
    one. The self-test's CONTROL run measures it: 3.0 / 4.0 / 3.5 units of
    real volume collapse to 1.0 / 0.75 / 0.0.
  * conversely, feeding a CUMULATIVE field straight through — which is what a
    ticker/snapshot channel gives you — adds the whole rolling window to every
    bar, every message. That is kite_stream's gotcha #1 in its original form.

So: per-trade sizes are passed straight through and SUMMED, and this file
subscribes to the per-TRADE channel, never the ticker/snapshot one. Bybit's
`tickers` and Coinbase's `ticker` both report a 24h ROLLING volume, which is
cumulative-with-a-moving-floor — worse than either case above, because it can
go both up and down within a session and no cursor can recover a trade size
from it. If anyone later "fixes" one of these two adapters to match the other,
they will break it; that is why this paragraph is this long.

A SECOND UNIT TRAP, ONE LAYER DOWN
-----------------------------------
Crypto sizes are FRACTIONAL — 0.0031 BTC is an ordinary print. `_live_on_tick`
accumulates with `f[5] += vol` and writes `int(f[5])` once, at close, and
backfill_crypto.py stores `int(float(candle_volume))` — the same construction.
So the sizes must be summed as FLOATS and truncated exactly once, by the
engine. Rounding or int()-ing per trade instead would floor every sub-unit
print to zero and every BTC bar in the store would read 0. `vol: int` in
`_live_on_tick`'s signature is a Kite-era annotation, not a constraint;
Python does not enforce it and the accumulator is numerically correct.

WHAT THIS FEED IS
-----------------
Unlike Kite MODE_QUOTE, this IS the trade tape: every print, with its size,
its exchange timestamp and its trade id. Both venues also publish the
aggressor side (Coinbase `side`, Bybit `S`), so order flow is constructible
HERE in a way it is not for any Indian retail feed. This file does not build
it — `bars` has six columns and none of them is delta — but the field is
dropped knowingly, not because it is absent.

LATE TRADES AND WHY THEY ARE DROPPED
------------------------------------
`_live_on_tick` closes the forming bar whenever a tick's bucket differs from
it — in EITHER direction. A trade that arrives out of order and belongs to an
already-closed minute would therefore (a) write the current, partial minute to
`bars` early and (b) start forming a stale minute that the next in-order trade
immediately overwrites the real one with. Two corrupted rows from one late
print. `TradeCursor` drops anything whose bucket is behind the one being
formed, and counts it. Out-of-order WITHIN the forming bucket is harmless and
is accepted (it only moves `close`).

THE PARTIAL FIRST MINUTE
------------------------
Connecting at 13:20:41 means the first forming bar holds 19 seconds of the
13:20 minute. `_live_on_tick` writes it with INSERT OR REPLACE, i.e. it would
overwrite a COMPLETE 13:20 row that backfill_crypto.py had already fetched
with a partial one. So the connect minute is skipped by default, and the same
skip is re-armed on every reconnect, because a socket gap has exactly the same
shape. Cost: up to 60s before the first bar. `--partial-first-minute` opts out.

FRESHNESS
---------
`kite_stream.freshness` is imported, not re-implemented — one definition of
"too stale to stream onto", measured in sessions against the symbol's own
daily series. It already knows the 24/7 case (its reference-age limit is 2
calendar days for UTC_SESSION vs 5 for NSE). `--ignore-stale` overrides it for
testing and says so loudly.

On top of it, and only for 24/7 symbols, `minute_gap()` measures something
freshness() structurally cannot: for a symbol that trades every minute of
every day, "the newest stored minute is 90 minutes old" is a hole, and no
session calendar is needed to say so. That measurement is meaningless for NSE
(15:29 to 09:15 is not a hole, it is a night) which is why it lives here and
not in kite_stream.

    python3 charto/data/crypto_stream.py --dry-run --symbols BTC-USD,ETH-USD
    python3 charto/data/crypto_stream.py --self-test
    python3 charto/data/crypto_stream.py --symbols BTC-USD,ETH-USD --seconds 300
    python3 charto/data/crypto_stream.py --venue bybit --symbols BTCUSDT --seconds 300
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import socket
import sqlite3
import ssl
from concurrent.futures import ThreadPoolExecutor
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

import dataserver as ds
import kite_stream as ks                # freshness(), and the sync_state guards

log = logging.getLogger("charto.crypto_stream")

UTC = timezone.utc
IST = timezone(timedelta(seconds=ds.IST_OFF))

DEFAULT_MAX_STALE_SESSIONS = ks.DEFAULT_MAX_STALE_SESSIONS
DEFAULT_MAX_GAP_MIN = 120   # 24/7 wall-clock hole tolerated before refusing
# Past this the hole is a backfill job, not a handoff: filling it inside a
# driver start is what hung the stream on a symbol 3.7 years behind.
MAX_FILL_MIN = 1440
# how close to `now` a successful gap fill must land
_FILL_TOLERANCE_MIN = 5
_BACKOFF_START = 1.0
_BACKOFF_CAP = 30.0
_STATUS_EVERY = 30.0
_DEDUPE_KEEP = 8192             # trade ids remembered per symbol
_RECORD_CAP = 60_000            # observed trades kept per symbol for --seconds

# The store is written concurrently by backfill_crypto.py. Every connection
# opened here waits rather than raising "database is locked", and the engine's
# own writer — which this file drives but does not own — is given the same
# grace. This is a runtime PRAGMA on a connection in OUR process, not an edit
# to dataserver.py; without it a single lock collision drops a closed minute
# on the floor with a traceback and no retry.
_BUSY_MS = 30_000
try:
    ds._live_writer.execute(f"PRAGMA busy_timeout={_BUSY_MS}")
except Exception:                       # noqa: BLE001
    log.warning("could not set busy_timeout on the engine writer", exc_info=True)


def _connect_store() -> sqlite3.Connection:
    con = sqlite3.connect(ds.DB_PATH, timeout=_BUSY_MS / 1000.0)
    con.execute(f"PRAGMA busy_timeout={_BUSY_MS}")
    return con


# ── venues ────────────────────────────────────────────────────────
# A venue is three pure things: how to subscribe, how to pull trades out of a
# message, and whether it wants an application-level ping. Nothing here
# touches the store or the socket, so `parse()` is testable from a literal.


@dataclass(frozen=True)
class Venue:
    name: str
    url: str
    subscribe: Callable[[Sequence[str]], list[dict]]
    parse: Callable[[dict], list[dict]]
    ping_every: float | None            # app-level ping; None = frames only
    ping_payload: dict | None = None
    owns: Callable[[str], bool] = lambda s: False


def _cb_subscribe(symbols: Sequence[str]) -> list[dict]:
    # `matches` is the trade tape. `heartbeat` is subscribed alongside it for
    # one reason: a quiet pair sends no matches, and a watchdog that cannot
    # tell "no trades" from "socket wedged" is not a watchdog. Heartbeats
    # carry last_trade_id but no price and no size, so they can never become a
    # tick — see _cb_parse, which matches on type explicitly rather than on
    # "has a product_id".
    return [{"type": "subscribe", "product_ids": list(symbols),
             "channels": ["matches", "heartbeat"]}]


def _cb_parse(msg: dict) -> list[dict]:
    t = msg.get("type")
    if t == "error":
        raise _VenueError(f"coinbase: {msg.get('message')} "
                          f"{msg.get('reason', '')}".strip())
    # `last_match` is the most recent trade at subscribe time — a real print,
    # but one that happened BEFORE we connected. It is accepted because the
    # warm-up skip discards the connect minute anyway; if the skip is disabled
    # it lands in the partial first bar, which is already flagged as partial.
    if t not in ("match", "last_match"):
        return []                       # subscriptions, heartbeat, ticker, …
    return [{"venue": "coinbase",
             "symbol": msg.get("product_id"),
             "id": f"cb:{msg.get('product_id')}:{msg.get('trade_id')}",
             "ts": msg.get("time"),     # ISO8601 UTC, sub-second
             "price": msg.get("price"),
             "size": msg.get("size"),
             "side": msg.get("side")}]


def _by_subscribe(symbols: Sequence[str]) -> list[dict]:
    return [{"op": "subscribe",
             "args": [f"publicTrade.{s}" for s in symbols]}]


def _by_parse(msg: dict) -> list[dict]:
    if msg.get("op") in ("ping", "pong", "subscribe"):
        if msg.get("success") is False:
            raise _VenueError(f"bybit: {msg.get('ret_msg')}")
        return []
    topic = msg.get("topic") or ""
    if not topic.startswith("publicTrade."):
        return []                       # tickers/orderbook/anything else
    sym = topic.split(".", 1)[1]
    out = []
    for d in msg.get("data") or ():
        out.append({"venue": "bybit",
                    "symbol": d.get("s") or sym,
                    "id": f"by:{sym}:{d.get('i')}",
                    "ts": d.get("T"),   # epoch MILLISECONDS
                    "price": d.get("p"),
                    "size": d.get("v"), # base-asset size of THIS trade
                    "side": d.get("S")})
    return out


COINBASE = Venue(
    name="coinbase",
    url="wss://ws-feed.exchange.coinbase.com",
    subscribe=_cb_subscribe, parse=_cb_parse,
    ping_every=None,                    # protocol ping frames are enough
    owns=lambda s: s.endswith("-USD"),
)

BYBIT = Venue(
    name="bybit",
    url="wss://stream.bybit.com/v5/public/spot",
    subscribe=_by_subscribe, parse=_by_parse,
    ping_every=20.0,                    # bybit drops idle sockets at ~20s
    ping_payload={"op": "ping"},
    owns=lambda s: s.endswith("USDT"),
)

VENUES = {v.name: v for v in (COINBASE, BYBIT)}


def venue_for(symbol: str) -> Venue | None:
    """The venue that owns a symbol, by the naming already in the store.

    Deliberately the same discriminator dataserver.session_for uses ("-USD"
    vs "USDT"), so a symbol can never be routed to a venue while being
    bucketed on a session the other venue implies.
    """
    for v in VENUES.values():
        if v.owns(symbol):
            return v
    return None


class _VenueError(RuntimeError):
    """The venue said no — bad subscription, unknown product, rate limit."""


class _Reconnect(RuntimeError):
    """Something about this socket is no longer trustworthy; drop and redial."""


# ── the per-symbol trade cursor ───────────────────────────────────
# The mirror image of kite_stream.TickCursor. That one exists to turn a
# cumulative counter into a delta; this one exists to make sure the already-
# incremental size is NOT transformed at all, and to police the things a real
# trade tape actually does wrong: repeat a trade id, and deliver one late.


@dataclass
class TradeCursor:
    trades: int = 0                     # accepted, fed to the engine
    dups: int = 0                       # same trade id seen twice
    late_drops: int = 0                 # trade for an already-closed minute
    bad: int = 0                        # unparseable / non-positive price/size
    no_ts: int = 0                      # no exchange clock; wall clock used
    closes: int = 0                     # minutes this symbol closed
    skipped_warmup: int = 0             # trades discarded in the connect minute
    volume: float = 0.0                 # float sum of accepted sizes
    last_ts: int = 0
    last_price: float = 0.0
    last_bucket: int | None = None
    warmup_armed: bool = False          # skip the (partial) connect minute
    warm_bucket: int | None = None      # that minute's bucket, once first seen
    _ids: set = field(default_factory=set)
    _order: deque = field(default_factory=lambda: deque(maxlen=_DEDUPE_KEEP))

    def seen(self, tid: str | None) -> bool:
        """Trade-id dedupe, bounded.

        A websocket reconnect replays nothing on either venue, but Coinbase's
        `last_match` repeats the newest print on every (re)subscribe and both
        venues can duplicate under load. An unbounded set on a 24/7 stream is
        a slow leak, so only the last _DEDUPE_KEEP ids are remembered — far
        more than any plausible duplication window, and a duplicate older than
        that would be a different bug entirely.
        """
        if not tid:
            return False                # no id to dedupe on; accept and count
        if tid in self._ids:
            self.dups += 1
            return True
        if len(self._order) == self._order.maxlen and self._order:
            self._ids.discard(self._order[0])
        self._order.append(tid)
        self._ids.add(tid)
        return False

    def arm_warmup(self) -> None:
        """Arm the partial-minute skip. Called on connect AND reconnect: a gap
        means the bar being formed either side of it is partial, and a partial
        minute written into `bars` is indistinguishable from a real one to
        every reader downstream.

        Off by default so nothing has to fake its way past it — a cursor
        driven from a stored or synthetic tape has no connect minute, and the
        skip is a property of having just dialled a socket, not of the cursor.
        """
        self.warmup_armed = True
        self.warm_bucket = None
        self.last_bucket = None


_CURSORS: dict[str, TradeCursor] = {}
_CURSOR_GUARD = threading.Lock()


def cursor(symbol: str) -> TradeCursor:
    with _CURSOR_GUARD:
        c = _CURSORS.get(symbol)
        if c is None:
            c = _CURSORS[symbol] = TradeCursor()
        return c


# ── raw trade -> (ts, price, size) ────────────────────────────────

def _epoch(value: Any) -> int | None:
    """Both venues' clocks. Everything on a crypto tape is UTC — Coinbase
    sends ISO8601 with a Z, Bybit sends epoch milliseconds — so unlike
    kite_stream's _epoch there is no naive-datetime-in-local-time hazard to
    defend against. Kept separate anyway: importing that one would tie this
    file to an IST offset it must never apply."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v <= 0:
            return None
        return int(v / 1000.0) if v > 1e11 else int(v)   # ms vs s
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)  # both venues quote UTC
        return int(dt.timestamp())
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return int(dt.timestamp())
    return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def translate(symbol: str, trade: dict, cur: TradeCursor,
              now: float | None = None) -> tuple[int, float, float] | None:
    """One normalised trade -> exactly the three numbers `_live_on_tick` wants.

    Pure but for `cur`, which it advances — same contract as
    kite_stream.translate, and the same reason: it makes the whole adapter
    testable without a socket. Returns None for anything that must not become
    a candle, having counted WHY on the cursor.

    THE SIZE IS PASSED THROUGH UNCHANGED. It is one trade's quantity, already
    incremental. No delta, no cursor arithmetic, no per-trade rounding — see
    the two-failure-direction note in the module docstring.
    """
    px = _as_float(trade.get("price"))
    sz = _as_float(trade.get("size"))
    if px is None or px <= 0 or sz is None or sz < 0:
        cur.bad += 1
        return None                     # a zero print is not a trade

    if cur.seen(trade.get("id")):
        return None

    ts = _epoch(trade.get("ts"))
    if ts is None:
        # Never the default: wall clock files a print into the minute we
        # RECEIVED it, which on a lagging socket is the wrong minute and the
        # candle still looks plausible. Counted so a feed that never sends an
        # exchange clock shows up in status() instead of quietly skewing.
        cur.no_ts += 1
        ts = int(time.time() if now is None else now)

    sess = ds.session_for(symbol)
    _, bts = ds._bucket_stamp(ts, 1, sess)

    if cur.warmup_armed:
        if cur.warm_bucket is None:
            # First trade of this connection; its minute is partial by
            # definition, because we were not listening when it opened.
            cur.warm_bucket = bts
        if bts <= cur.warm_bucket:
            cur.skipped_warmup += 1
            return None
        cur.warmup_armed = False        # first fully-observed minute begins
        cur.warm_bucket = None

    if cur.last_bucket is not None and bts < cur.last_bucket:
        # See "LATE TRADES" in the module docstring: feeding this would close
        # the forming minute early AND start re-forming a finished one.
        cur.late_drops += 1
        return None

    cur.trades += 1
    cur.volume += sz
    cur.last_ts = ts
    cur.last_price = px
    return ts, px, sz


def on_trade(symbol: str, trade: dict,
             record: list | None = None) -> tuple[int, float, float] | None:
    """Translate one trade and hand it to the one seam.

    Deliberately thin, for the same reason kite_stream.on_tick is: the session
    gate, the forming bar, the SQLite write on close and the SSE push all
    already live in `_live_on_tick`, and re-implementing any of them here is
    how a second, divergent write path into `bars` gets born.
    """
    cur = cursor(symbol)
    out = translate(symbol, trade, cur)
    if out is None:
        return None
    ts, px, sz = out
    _, bts = ds._bucket_stamp(ts, 1, ds.session_for(symbol))
    ds._live_on_tick(symbol, ts, px, sz)
    if cur.last_bucket is not None and bts != cur.last_bucket:
        # The engine just wrote cur.last_bucket to `bars`.
        cur.closes += 1
        _mark_closed(symbol, cur.last_bucket, trade.get("venue"))
    cur.last_bucket = bts
    if record is not None and len(record) < _RECORD_CAP:
        record.append((ts, px, sz, bts))
    return out


def _mark_closed(symbol: str, bar_ts: int, venue: str | None) -> None:
    """Record the minute the engine just wrote, if sync_state exists.

    The connection, the DDL guard and the one-failure-disables-it flag are
    kite_stream's — reused rather than re-declared. The only thing that
    differs is the `source` label, and that is the whole reason this is not a
    straight call to ks._mark_closed: a Coinbase bar tagged "kite_ws" is a lie
    written into a shipped bookkeeping surface.
    """
    if ks.sync_state is None or ks._state_dead:
        return
    if symbol.startswith("__TEST_"):
        return                          # never leave test residue in the table
    mark = getattr(ks.sync_state, "mark", None)
    if mark is None:
        return
    try:
        with ks._STATE_GUARD:
            con = ks._state_connection()
            if con is None:
                return
            mark(con, symbol, last_bar_ts=bar_ts,
                 source=f"{venue or 'crypto'}_ws")
    except Exception as exc:            # noqa: BLE001
        # Bookkeeping must never take the tick loop down with it, and a
        # per-minute warning per symbol would drown the log.
        ks._state_dead = True
        log.warning("sync_state.mark disabled for this run: %s", exc)


# ── the 24/7-only wall-clock gap ──────────────────────────────────

def minute_gap(con: sqlite3.Connection, symbol: str,
               now: int | None = None) -> dict:
    """How many minutes of wall clock since this symbol's newest stored bar.

    NOT a second freshness(). freshness() answers "how many SESSIONS of
    minutes are missing", measured against the symbol's own daily series, and
    is the gate. This answers a question that is only meaningful for an
    instrument that trades every minute of every day: for a 24/7 symbol there
    is no night, so a 90-minute-old MAX(ts) is a 90-minute hole. Asking it of
    an NSE name would refuse every symbol at 09:16 because 15:29 was 18 hours
    ago, which is why this lives here and freshness() does not know about it.
    """
    ts_now = int(time.time()) if now is None else now
    row = con.execute("SELECT MAX(ts) FROM bars WHERE symbol=?",
                      (symbol,)).fetchone()
    last = row[0] if row else None
    if last is None:
        return {"symbol": symbol, "applies": True, "last_1m": None,
                "gap_min": None,
                "note": "no 1-minute history at all"}
    if ds.session_for(symbol) != ds.UTC_SESSION:
        return {"symbol": symbol, "applies": False, "last_1m": last,
                "gap_min": None, "note": "not a 24/7 symbol; gap is meaningless"}
    # +60 because a bar stamped 13:19 covers 13:19:00-13:19:59, so it is not
    # "one minute old" the instant the clock says 13:20.
    gap = max(0, (ts_now - (last + 60)) // 60)
    return {"symbol": symbol, "applies": True, "last_1m": last,
            "gap_min": int(gap),
            "note": f"newest stored minute is {int(gap)} minute(s) behind now"}


def _utc_str(ts: int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


# ── a websocket client, from the standard library ─────────────────
# Neither `websockets` nor `websocket-client` is importable in this
# environment (checked), and RFC 6455 client framing is ~120 lines, so it is
# implemented here rather than adding a dependency to a repo that has none for
# this. Scope is exactly what a public market-data feed needs: one text
# subprotocol, no extensions, no permessage-deflate (both venues send
# uncompressed when we do not offer it), no server-side masking expected.


_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OP_CONT, _OP_TEXT, _OP_BIN, _OP_CLOSE, _OP_PING, _OP_PONG = 0, 1, 2, 8, 9, 10


_SSL_CTX: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext:
    """A TLS context with a CA bundle that exists under BOTH interpreters.

    `ssl.create_default_context()` finds a trust store under the system
    python3 and finds NOTHING under pivot's venv, so every connect there died
    with CERTIFICATE_VERIFY_FAILED. That mattered the moment the dataserver
    moved onto pivot's venv (it needs kiteconnect + sqlalchemy for the Kite
    driver): the standalone CLI streamed fine while the in-process driver
    retried forever, connected=false, messages=0 — which reads exactly like a
    dead venue rather than a local trust-store gap. certifi ships in that venv
    already; use it when it is importable and fall back otherwise, so neither
    interpreter is the special case.
    """
    global _SSL_CTX
    if _SSL_CTX is None:
        try:
            import certifi
            _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
        except Exception:                                 # noqa: BLE001
            _SSL_CTX = ssl.create_default_context()
    return _SSL_CTX


class WSClient:
    """Minimal RFC 6455 client over socket+ssl. Text frames in, text out."""

    def __init__(self, url: str, *, connect_timeout: float = 15.0,
                 poll_timeout: float = 0.5) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "wss"):
            raise ValueError(f"not a websocket url: {url}")
        self.host = parts.hostname or ""
        self.port = parts.port or (443 if parts.scheme == "wss" else 80)
        self.path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        self.secure = parts.scheme == "wss"
        self._poll_timeout = poll_timeout
        self._connect_timeout = connect_timeout
        self._sock: socket.socket | None = None
        self._buf = bytearray()
        self._frag_op: int | None = None
        self._frag = bytearray()
        self.closed = False
        self.close_code: int | None = None
        self.bytes_in = 0

    # -- lifecycle ------------------------------------------------
    def connect(self) -> None:
        raw = socket.create_connection((self.host, self.port),
                                       timeout=self._connect_timeout)
        try:
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        if self.secure:
            raw = _ssl_context().wrap_socket(raw, server_hostname=self.host)
        self._sock = raw
        self._handshake()
        self._sock.settimeout(self._poll_timeout)
        self.closed = False

    def _handshake(self) -> None:
        assert self._sock is not None
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {self.path} HTTP/1.1\r\n"
               f"Host: {self.host}\r\n"
               f"Upgrade: websocket\r\n"
               f"Connection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n"
               f"User-Agent: charto-crypto-stream/1.0\r\n\r\n")
        self._sock.sendall(req.encode())
        head = bytearray()
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise _Reconnect("server closed during handshake")
            head += chunk
            if len(head) > 65536:
                raise _Reconnect("handshake response too large")
        header_blob, _, rest = bytes(head).partition(b"\r\n\r\n")
        self._buf += rest               # data may already follow the headers
        lines = header_blob.decode("latin-1").split("\r\n")
        if "101" not in lines[0]:
            raise _Reconnect(f"upgrade refused: {lines[0]}")
        got = ""
        for line in lines[1:]:
            k, _, v = line.partition(":")
            if k.strip().lower() == "sec-websocket-accept":
                got = v.strip()
        want = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        if got != want:
            # Not pedantry: a proxy that terminates the upgrade and speaks
            # something else would otherwise be parsed as frames and produce
            # garbage prices.
            raise _Reconnect("Sec-WebSocket-Accept mismatch — not a WS peer")

    def close(self, code: int = 1000) -> None:
        if self._sock is None:
            return
        try:
            self._send_frame(_OP_CLOSE, struct.pack(">H", code))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None
        self.closed = True

    # -- writing --------------------------------------------------
    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._sock is None:
            raise _Reconnect("send on a closed socket")
        n = len(payload)
        head = bytearray([0x80 | opcode])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)            # clients MUST mask (RFC 6455 §5.3)
        head += mask
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self._sock.sendall(bytes(head) + masked)

    def send_json(self, obj: dict) -> None:
        self._send_frame(_OP_TEXT, json.dumps(obj).encode())

    def ping(self, payload: bytes = b"charto") -> None:
        self._send_frame(_OP_PING, payload)

    # -- reading --------------------------------------------------
    def poll(self) -> list[str]:
        """Every complete text message available right now, possibly none.

        Control frames are handled inline: a ping is answered with a pong
        (a venue that pings and gets nothing back disconnects us and the
        cause looks like a network fault), a close is a reconnect.
        """
        assert self._sock is not None
        try:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise _Reconnect("peer closed the connection")
            self._buf += chunk
            self.bytes_in += len(chunk)
        except socket.timeout:
            pass                        # no data this tick; parse what we have
        except ssl.SSLWantReadError:
            pass
        except OSError as exc:
            raise _Reconnect(f"socket error: {exc}") from exc

        out: list[str] = []
        while True:
            frame = self._take_frame()
            if frame is None:
                return out
            fin, opcode, payload = frame
            if opcode == _OP_PING:
                self._send_frame(_OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if opcode == _OP_CLOSE:
                self.close_code = (struct.unpack(">H", payload[:2])[0]
                                   if len(payload) >= 2 else None)
                raise _Reconnect(f"peer sent close (code={self.close_code})")
            if opcode == _OP_CONT:
                if self._frag_op is None:
                    raise _Reconnect("continuation with no start frame")
                self._frag += payload
                if fin:
                    op, data = self._frag_op, bytes(self._frag)
                    self._frag_op, self._frag = None, bytearray()
                    if op == _OP_TEXT:
                        out.append(data.decode("utf-8", "replace"))
                continue
            # a new data frame
            if not fin:
                self._frag_op, self._frag = opcode, bytearray(payload)
                continue
            if opcode == _OP_TEXT:
                out.append(payload.decode("utf-8", "replace"))
            # binary frames are not used by either venue; ignored, not fatal

    def _take_frame(self) -> tuple[bool, int, bytes] | None:
        """One frame off the buffer, or None if it is not all here yet.
        Nothing is consumed until the whole frame is present, so a partial
        read can never be mistaken for a short message."""
        b = self._buf
        if len(b) < 2:
            return None
        b0, b1 = b[0], b[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        ln = b1 & 0x7F
        off = 2
        if ln == 126:
            if len(b) < off + 2:
                return None
            ln = struct.unpack_from(">H", b, off)[0]
            off += 2
        elif ln == 127:
            if len(b) < off + 8:
                return None
            ln = struct.unpack_from(">Q", b, off)[0]
            off += 8
        mask = b""
        if masked:                      # servers must not mask, but be exact
            if len(b) < off + 4:
                return None
            mask = bytes(b[off:off + 4])
            off += 4
        if len(b) < off + ln:
            return None
        payload = bytes(b[off:off + ln])
        if masked:
            payload = bytes(c ^ mask[i & 3] for i, c in enumerate(payload))
        del b[:off + ln]
        return fin, opcode, payload


# ── the stream ────────────────────────────────────────────────────

class CryptoStream:
    """One venue socket, routed into `_live_on_tick`, with a watchdog."""

    def __init__(self, symbols: Sequence[str], *,
                 venue: Venue | None = None,
                 max_stale_sessions: int = DEFAULT_MAX_STALE_SESSIONS,
                 max_gap_min: int = DEFAULT_MAX_GAP_MIN,
                 dry_run: bool = False,
                 ignore_stale: bool = False,
                 partial_first_minute: bool = False,
                 stale_after: float = 45.0,
                 fill_first: bool = True,
                 record: bool = False) -> None:
        self.requested = [s.strip().upper() for s in symbols if s.strip()]
        self.venue = venue
        self.max_stale_sessions = max_stale_sessions
        self.max_gap_min = max_gap_min
        self.dry_run = dry_run
        self.ignore_stale = ignore_stale
        self.partial_first_minute = partial_first_minute
        self.stale_after = stale_after
        self.fill_first = fill_first
        self.symbols: list[str] = []
        self.refused: dict[str, str] = {}
        self.baseline: dict[str, tuple[int, float]] = {}   # last stored bar
        self.records: dict[str, list] = {}
        # get_bars as it looked WHILE LIVE. Taken before the forming bar is
        # dropped, because dropping it is the last thing a socket session
        # does and afterwards there is nothing left to prove was merged.
        self.snapshot: dict[str, list] = {}
        self._record = record
        self._ws: WSClient | None = None
        self._connected = False
        self._reconnects = 0
        self._connect_fails = 0
        self._msgs = 0
        self._last_msg = 0.0
        self._started_at = 0.0
        self._stop = False
        self._lock = threading.RLock()

    # -- planning ------------------------------------------------
    def plan(self) -> list[dict]:
        """Per-symbol verdict. No socket, no writes.

        Three gates, in order, because they answer different questions:
        venue ownership (can this symbol be streamed at all), freshness (is
        the 1-min history too many SESSIONS behind — kite_stream's, reused),
        and the 24/7 wall-clock gap (is there a hole right now).
        """
        con = _connect_store()
        try:
            out = []
            for sym in self.requested:
                v = venue_for(sym)
                if self.venue is not None:
                    v = self.venue
                fr = ks.freshness(con, sym, self.max_stale_sessions)
                gap = minute_gap(con, sym)
                row = {"symbol": sym, "venue": v.name if v else None,
                       "ok": False, "reason": "", "fresh": fr, "gap": gap}
                last = con.execute(
                    "SELECT ts,c FROM bars WHERE symbol=? ORDER BY ts DESC "
                    "LIMIT 1", (sym,)).fetchone()
                if last:
                    self.baseline[sym] = (int(last[0]), float(last[1]))
                if v is None:
                    row["reason"] = ("no venue owns this symbol — Coinbase "
                                     "trades '*-USD', Bybit spot '*USDT'")
                elif not fr["ok"] and not self.ignore_stale:
                    row["reason"] = fr["reason"]
                elif (gap["applies"] and gap["gap_min"] is not None
                      and gap["gap_min"] > self.max_gap_min
                      and not self.ignore_stale):
                    row["reason"] = (
                        f"24/7 symbol but the newest stored minute is "
                        f"{_utc_str(gap['last_1m'])}, {gap['gap_min']} minute(s) "
                        f"behind now (limit {self.max_gap_min}) — streaming "
                        f"would put live minutes at the right edge of a hole")
                else:
                    row["ok"] = True
                    note = fr["reason"]
                    if not fr["ok"]:
                        note = f"OVERRIDDEN (--ignore-stale): {fr['reason']}"
                    if gap["applies"] and gap["gap_min"]:
                        note += f"; {gap['gap_min']} min behind wall clock"
                    row["reason"] = note
                out.append(row)
            verdicts = out
        finally:
            con.close()
        with self._lock:
            self.symbols = [v["symbol"] for v in verdicts if v["ok"]]
            self.refused = {v["symbol"]: v["reason"]
                            for v in verdicts if not v["ok"]}
            for s in self.symbols:
                self.records.setdefault(s, [])
        return verdicts

    def _venue(self) -> Venue:
        if self.venue is not None:
            return self.venue
        vs = {venue_for(s).name for s in self.symbols if venue_for(s)}
        if len(vs) != 1:
            raise SystemExit(
                f"symbols span {len(vs)} venue(s) {sorted(vs)}; one socket "
                f"serves one venue — run one process per venue or pass --venue")
        return VENUES[vs.pop()]

    # -- the REST -> socket handoff ------------------------------
    def fill_gap(self) -> dict:
        """Fetch the minutes between each symbol's newest stored bar and now,
        over REST, BEFORE opening the socket.

        Without this the stream can only ever be started in the minutes right
        after a backfill. minute_gap() correctly refuses a symbol whose newest
        stored minute is hours old — live ticks written at the right edge of a
        hole draw a chart that looks continuous and is not — but refusing is
        only half an answer when the hole is exactly what this process is able
        to close. Measured 2026-08-02: a three-hour gap opened between a test
        stream and the next start, and every symbol was refused with no way
        forward but a full backfill run.

        The fetchers are backfill_crypto's own, imported rather than
        reimplemented: a second copy of the Coinbase row order (which is
        [time, LOW, HIGH, OPEN, CLOSE, volume], not OHLC) is exactly the kind
        of duplicate that silently transposes a chart months later.
        """
        try:
            import backfill_crypto as bc
        except Exception as exc:                          # noqa: BLE001
            return {"filled": 0, "error": f"backfill_crypto unavailable: {exc}"}
        now = int(time.time())
        out: dict[str, Any] = {"filled": 0, "symbols": {}}
        con = _connect_store()
        try:
            for sym in self.requested:
                v = self.venue or venue_for(sym)
                if v is None:
                    continue
                last = con.execute("SELECT MAX(ts) FROM bars WHERE symbol=?",
                                   (sym,)).fetchone()[0]
                if not last or now - last < 120:
                    continue                      # nothing meaningful to close
                gap_min = (now - int(last)) // 60
                if gap_min > MAX_FILL_MIN:
                    # A handoff is not a backfill. DOT-USD sat 1,949,380
                    # minutes behind after an interrupted backfill, and the
                    # sequential loop below would have issued ~6,500 requests
                    # inside a driver start, hanging the stream with syms=0 and
                    # no error. Past this bound the right tool is
                    # backfill_crypto, and saying so beats silently grinding.
                    out["symbols"][sym] = 0
                    out.setdefault("too_wide", {})[sym] = (
                        f"{gap_min:,} min behind (limit {MAX_FILL_MIN:,}) — "
                        f"run backfill_crypto for this symbol first")
                    continue
                fetch, span, workers = (
                    (bc._cb_fetch, bc.CB_SPAN, bc.CB_WORKERS)
                    if v.name == "coinbase"
                    else (bc._by_fetch, bc.BY_SPAN, bc.BY_WORKERS))
                # re-request the newest stored minute too: it may have been
                # written from a partial minute when the last socket died
                windows = [(sym, s, min(s + span, now))
                           for s in range(int(last), now, span)]
                rows: list[tuple] = []
                if windows:
                    # batched and parallel, same worker counts backfill_crypto
                    # measured — a serial loop here is what made a 4-hour gap
                    # feel like a hang
                    with ThreadPoolExecutor(min(workers, len(windows))) as ex:
                        for got in ex.map(fetch, windows):
                            rows.extend(got or [])
                if rows:
                    con.executemany(
                        "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                        rows)
                    con.commit()
                out["symbols"][sym] = len(rows)
                out["filled"] += len(rows)
                # Verify the hole actually closed. `_get` swallows a 429 after
                # its retries and returns None, so a rate-limited fill writes
                # zero rows and says nothing — the symbol is then refused for
                # being stale, which blames the store for a fetch that failed.
                # Measured: LINK-USD and ADA-USD were refused at ~250 min
                # behind right after a "successful" fill of 0 rows.
                still = con.execute("SELECT MAX(ts) FROM bars WHERE symbol=?",
                                    (sym,)).fetchone()[0] or 0
                left = (now - still) // 60
                # Judge against what fill_gap is FOR — closing the hole to
                # roughly now — not against the streaming limit. Checking
                # against DEFAULT_MAX_GAP_MIN meant a fill that fetched nothing
                # and left a 32-minute hole passed silently, because 32 < 120.
                # A hole under the limit is still a hole the chart draws over.
                if left > _FILL_TOLERANCE_MIN:
                    out.setdefault("unfilled", {})[sym] = (
                        f"still {left} min behind after fetching {len(rows)} "
                        f"row(s) — the venue REST call returned nothing for "
                        f"that window (throttled or not yet published), so the "
                        f"socket will open at the right edge of a {left}-min hole")
        finally:
            con.close()
        return out

    # -- lifecycle -----------------------------------------------
    def start(self) -> bool:
        if self.fill_first and not self.dry_run:
            got = self.fill_gap()
            if got.get("error"):
                print(f"  gap fill skipped: {got['error']}")
            elif got["filled"]:
                print("  gap filled over REST before opening the socket: "
                      + ", ".join(f"{s} +{n}" for s, n in got["symbols"].items()
                                  if n))
            for s, why in (got.get("too_wide") or {}).items():
                print(f"  gap too wide to hand off: {s} — {why}")
            for s, why in (got.get("unfilled") or {}).items():
                print(f"  gap fill INCOMPLETE: {s} — {why}")
        verdicts = self.plan()
        for v in verdicts:
            mark = "stream" if v["ok"] else "REFUSE"
            print(f"  {mark:>6}  {v['symbol']:<10} "
                  f"[{v['venue'] or '-':<8}] {v['reason']}")
        if not self.symbols:
            print("nothing to stream — every requested symbol was refused")
            return False
        if self.dry_run:
            ven = self._venue()
            print(f"\ndry run: would open {ven.url} and subscribe "
                  f"{len(self.symbols)} symbol(s) ({', '.join(self.symbols)}) "
                  f"on the per-TRADE channel; no socket opened, no rows written")
            return False
        return True

    def run(self, seconds: float | None = None) -> None:
        """Blocking. Reconnects with capped exponential backoff; a socket that
        goes quiet is redialled rather than sat on."""
        ven = self._venue()
        deadline = time.time() + seconds if seconds else None
        self._started_at = time.time()
        backoff = _BACKOFF_START
        while not self._stop and (deadline is None or time.time() < deadline):
            try:
                self._open(ven)
                backoff = _BACKOFF_START
            except Exception as exc:                     # noqa: BLE001
                self._connect_fails += 1
                log.warning("connect failed (%s); retrying in %.0fs", exc, backoff)
                self._nap(backoff, deadline)
                backoff = min(backoff * 2, _BACKOFF_CAP)
                continue
            try:
                self._pump(ven, deadline)
            except _Reconnect as exc:
                log.warning("dropping socket: %s", exc)
            except _VenueError as exc:
                log.error("venue refused: %s", exc)
                self._stop = True
            except Exception:                            # noqa: BLE001
                log.exception("unexpected error in the read loop")
            finally:
                self._connected = False
                # Order matters: capture, THEN close, THEN drop. get_bars only
                # merges a forming bar while one exists.
                self._snapshot_get_bars()
                self._close_socket()
                # A forming bar that straddles a socket gap is partial on both
                # sides. Writing it would be indistinguishable from a real
                # minute downstream, so it is dropped and the warm-up re-armed.
                self._drop_forming()
            if self._stop or (deadline is not None and time.time() >= deadline):
                break
            self._reconnects += 1
            self._nap(backoff, deadline)
            backoff = min(backoff * 2, _BACKOFF_CAP)

    def _snapshot_get_bars(self) -> None:
        """get_bars through the ordinary public path, while the forming bar is
        still merged. This is the only moment the merge is observable."""
        for sym in self.symbols:
            st = ds._LIVE.get(sym)
            if st is None or st["form"] is None:
                # Nothing is forming, so a snapshot taken now would show only
                # stored rows and prove nothing about the merge. Keep the one
                # from when there WAS a forming bar rather than overwrite it
                # with a weaker observation.
                continue
            try:
                self.snapshot[sym] = ds.get_bars(sym, "1m", None, 6)["bars"]
            except Exception:                            # noqa: BLE001
                log.exception("get_bars snapshot failed for %s", sym)

    def stop(self) -> None:
        self._stop = True
        self._snapshot_get_bars()
        self._close_socket()
        n = self._drop_forming()
        if n:
            print(f"dropped {n} partial forming minute(s) on stop")

    def _nap(self, secs: float, deadline: float | None) -> None:
        end = time.time() + secs
        if deadline is not None:
            end = min(end, deadline)
        while time.time() < end and not self._stop:
            time.sleep(0.2)

    def _open(self, ven: Venue) -> None:
        ws = WSClient(ven.url)
        ws.connect()
        for frame in ven.subscribe(self.symbols):
            ws.send_json(frame)
        self._ws = ws
        self._connected = True
        self._last_msg = time.time()
        for s in self.symbols:
            if not self.partial_first_minute:
                cursor(s).arm_warmup()
        log.info("connected %s; subscribed %d symbol(s)", ven.url,
                 len(self.symbols))

    def _close_socket(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            ws.close()

    def _drop_forming(self) -> int:
        dropped = 0
        for sym in self.symbols:
            st = ds._LIVE.get(sym)
            if st is None:
                continue
            with st["lock"]:
                if st["form"] is not None:
                    st["form"] = None
                    dropped += 1
            if not self.partial_first_minute:
                cursor(sym).arm_warmup()
        return dropped

    def _pump(self, ven: Venue, deadline: float | None) -> None:
        assert self._ws is not None
        last_ping = time.time()
        last_status = time.time()
        while not self._stop:
            if deadline is not None and time.time() >= deadline:
                return
            for raw in self._ws.poll():
                self._msgs += 1
                self._last_msg = time.time()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                for trade in ven.parse(msg):
                    sym = (trade.get("symbol") or "").upper()
                    if sym not in self.records:
                        continue        # a symbol we did not ask for
                    try:
                        on_trade(sym, trade, self.records[sym])
                    except Exception:                    # noqa: BLE001
                        # One malformed print must not kill the loop and take
                        # every other symbol's candles down with it.
                        log.exception("trade dropped for %s", sym)
            now = time.time()
            if ven.ping_every and now - last_ping >= ven.ping_every:
                if ven.ping_payload:
                    self._ws.send_json(ven.ping_payload)
                else:
                    self._ws.ping()
                last_ping = now
            if now - self._last_msg > self.stale_after:
                # The watchdog. Both venues emit at least once a second while
                # healthy (Coinbase via the heartbeat channel, Bybit via the
                # pong to our own ping), so silence this long is a wedged
                # socket wearing the costume of a quiet market.
                raise _Reconnect(
                    f"no message for {now - self._last_msg:.0f}s "
                    f"(watchdog {self.stale_after:.0f}s)")
            if now - last_status >= _STATUS_EVERY:
                last_status = now
                print(f"  .. {self.status()}")

    # -- observability -------------------------------------------
    def status(self) -> dict:
        with _CURSOR_GUARD:
            cs = {s: _CURSORS[s] for s in self.symbols if s in _CURSORS}
        return {
            "connected": self._connected,
            "venue": self.venue.name if self.venue else (
                self._venue().name if self.symbols else None),
            "symbols": list(self.symbols),
            "refused": dict(self.refused),
            "messages": self._msgs,
            "trades_in": sum(c.trades for c in cs.values()),
            "bars_closed": sum(c.closes for c in cs.values()),
            "volume_units": round(sum(c.volume for c in cs.values()), 6),
            "duplicate_trade_ids": sum(c.dups for c in cs.values()),
            "late_drops": sum(c.late_drops for c in cs.values()),
            "rejected": sum(c.bad for c in cs.values()),
            "warmup_skipped": sum(c.skipped_warmup for c in cs.values()),
            "missing_exchange_ts": sum(c.no_ts for c in cs.values()),
            "last_message_age_s": (round(time.time() - self._last_msg, 1)
                                   if self._last_msg else None),
            "reconnects": self._reconnects,
            "connect_failures": self._connect_fails,
            "uptime_s": (round(time.time() - self._started_at, 1)
                         if self._started_at else None),
        }


# ── live verification ─────────────────────────────────────────────
# Run after a --seconds session. Everything asserted here is read back out of
# the real store or out of get_bars, never out of the stream's own counters.

def verify_live(stream: CryptoStream) -> int:
    fails: list[str] = []
    print("\n" + "=" * 72)
    print("LIVE VERIFICATION")
    print("=" * 72)
    st = stream.status()
    print(f"status: {st}\n")
    if st["trades_in"] == 0:
        print("no trades arrived at all — nothing to verify")
        return 1

    con = _connect_store()
    try:
        for sym in stream.symbols:
            recs = stream.records.get(sym) or []
            cur = cursor(sym)
            print(f"── {sym} ─────────────────────────────────────────")
            if not recs:
                fails.append(f"{sym}: no trades recorded")
                print("  no trades\n")
                continue
            print(f"  trades accepted   {len(recs)}  "
                  f"(dups {cur.dups}, late {cur.late_drops}, "
                  f"rejected {cur.bad}, warmup-skipped {cur.skipped_warmup})")
            head = ", ".join(f"{p:g}@{_utc_str(t)[-9:]}" for t, p, _s, _b in recs[:3])
            tail = ", ".join(f"{p:g}@{_utc_str(t)[-9:]}" for t, p, _s, _b in recs[-3:])
            print(f"  first prints      {head}")
            print(f"  last prints       {tail}")

            # ---- what the trades say each closed minute should be ----
            buckets: dict[int, list] = {}
            for ts, px, sz, bts in recs:
                b = buckets.setdefault(bts, [px, px, px, px, 0.0, 0])
                b[1] = max(b[1], px)
                b[2] = min(b[2], px)
                b[3] = px
                b[4] += sz
                b[5] += 1
            forming = max(buckets)          # the last bucket is still open
            closed_expect = {k: v for k, v in buckets.items() if k != forming}

            rows = con.execute(
                "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts>=? "
                "ORDER BY ts", (sym, min(buckets))).fetchall()
            stored = {r[0]: r for r in rows}
            print(f"  minutes CLOSED and read back out of SQLite: "
                  f"{len(closed_expect)}")
            for bts in sorted(closed_expect):
                exp = closed_expect[bts]
                got = stored.get(bts)
                if got is None:
                    fails.append(f"{sym}: minute {_utc_str(bts)} never landed "
                                 f"in `bars`")
                    print(f"    {_utc_str(bts)}  MISSING from the store")
                    continue
                _ts, o, h, l, c, v = got
                ok_ohlc = (abs(o - exp[0]) < 1e-9 and abs(h - exp[1]) < 1e-9
                           and abs(l - exp[2]) < 1e-9 and abs(c - exp[3]) < 1e-9)
                # int(sum-of-floats) can differ by one ulp from int(sum in a
                # different order); one unit of tolerance, not more.
                ok_vol = abs(v - int(exp[4])) <= 1
                print(f"    {_utc_str(bts)}  o={o:<10.4f} h={h:<10.4f} "
                      f"l={l:<10.4f} c={c:<10.4f} v={v:<6} "
                      f"| {exp[5]} trades, sum={exp[4]:.6f} "
                      f"{'OK' if ok_ohlc and ok_vol else 'MISMATCH'}")
                if not ok_ohlc:
                    fails.append(f"{sym} {_utc_str(bts)}: stored OHLC "
                                 f"{(o, h, l, c)} != trades "
                                 f"{(exp[0], exp[1], exp[2], exp[3])}")
                if not ok_vol:
                    fails.append(f"{sym} {_utc_str(bts)}: stored v={v} != "
                                 f"int(sum of trade sizes)={int(exp[4])}")
                # the explicit invariants the brief asks for, stated separately
                px_all = [p for t, p, s, b in recs if b == bts]
                if h < max(px_all) - 1e-9 or l > min(px_all) + 1e-9:
                    fails.append(f"{sym} {_utc_str(bts)}: high/low do not "
                                 f"bound every trade price")
                if abs(c - px_all[-1]) > 1e-9:
                    fails.append(f"{sym} {_utc_str(bts)}: close {c} is not the "
                                 f"last trade {px_all[-1]}")

            if len(closed_expect) < 2:
                fails.append(f"{sym}: only {len(closed_expect)} minute(s) "
                             f"closed; the brief asks for at least 2")

            # ---- get_bars: the path every chart tool takes ----
            # Captured while the stream was live (see _snapshot_get_bars); by
            # now the partial forming minute has been deliberately dropped, so
            # calling get_bars here would show stored rows only.
            gb = stream.snapshot.get(sym) or []
            print(f"  get_bars('{sym}','1m') tail, sampled while live:")
            for b in gb[-4:]:
                print(f"    t={b['t']} {_utc_str(b['t'])} o={b['o']} h={b['h']} "
                      f"l={b['l']} c={b['c']} v={b['v']}")
            if not gb:
                fails.append(f"{sym}: get_bars returned nothing")
            else:
                if gb[-1]["t"] != forming:
                    fails.append(f"{sym}: get_bars last bar t={gb[-1]['t']} "
                                 f"({_utc_str(gb[-1]['t'])}) is not the forming "
                                 f"minute {forming} ({_utc_str(forming)})")
                else:
                    print(f"    ^ last bar IS the forming minute "
                          f"{_utc_str(forming)}, merged by get_bars")
                have = {b["t"] for b in gb}
                missing = [b for b in sorted(closed_expect) if b not in have][-3:]
                # only the tail of the closed set can be checked against a
                # 6-bar window; older ones legitimately fall outside it
                for b in missing:
                    if b >= sorted(have)[0]:
                        fails.append(f"{sym}: closed minute {_utc_str(b)} "
                                     f"absent from get_bars")

            # ---- is this even the right instrument? ----
            base = stream.baseline.get(sym)
            if base is None:
                fails.append(f"{sym}: no stored history to sanity-check "
                             f"the price against")
            else:
                b_ts, b_px = base
                live_px = recs[-1][1]
                drift = abs(live_px - b_px) / b_px * 100 if b_px else 999
                print(f"  sanity: last stored bar {_utc_str(b_ts)} c={b_px} "
                      f"vs live {live_px} -> {drift:.2f}% apart")
                if drift > 20:
                    fails.append(f"{sym}: live price {live_px} is {drift:.1f}% "
                                 f"from the last stored close {b_px} — this "
                                 f"may not be the same instrument")
            # ---- do the new rows extend the history, or overlap it? ----
            if base is not None:
                newest_new = max(closed_expect) if closed_expect else None
                if newest_new is not None:
                    olap = [b for b in closed_expect if b <= b_ts]
                    print(f"  history: stored ended {_utc_str(b_ts)}; wrote "
                          f"{_utc_str(min(closed_expect))}..{_utc_str(newest_new)}"
                          f"  -> {len(olap)} overlap, "
                          f"{len(closed_expect) - len(olap)} extend"
                          + (f", GAP of {(min(closed_expect) - b_ts) // 60 - 1} "
                             f"minute(s) left for the backfill"
                             if min(closed_expect) > b_ts + 60 else ""))
            print()
    finally:
        con.close()

    if fails:
        print(f"LIVE VERIFICATION FAILED ({len(fails)})")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("LIVE VERIFICATION PASSED")
    return 0


# ── self-test ─────────────────────────────────────────────────────
# No socket. Synthetic trade tapes with a KNOWN per-minute volume, fed through
# on_trade into the REAL engine and read back out of the REAL store. Scratch
# symbols only — and they must end in "-USD", because dataserver.session_for
# keys the 24/7 clock off the suffix and a name it does not recognise would be
# bucketed on the NSE session, where _live_on_tick's pre-open gate silently
# discards everything before 09:15 IST.

_SCRATCH = "__TEST_CRYPTO-USD"
_SCRATCH_CTRL = "__TEST_CRYPTO_CTRL-USD"     # the deliberately-broken control
_SCRATCH_DAY = "__TEST_CRYPTO_DAY-USD"       # the UTC-midnight boundary
_ALL_SCRATCH = (_SCRATCH, _SCRATCH_CTRL, _SCRATCH_DAY)

# (offset_s, price, size, trade_id). Minute boundaries at 0/60/120/180.
# Sizes are FRACTIONAL on purpose: int()-ing any of them individually gives 0.
_FEED: list[tuple[int, float, float, str]] = [
    # minute 0 — four ordinary prints
    (0,   100.0, 0.50, "t1"),
    (15,  103.0, 0.25, "t2"),
    (30,   98.0, 1.50, "t3"),
    (45,  101.0, 0.75, "t4"),
    # minute 1 — a duplicate trade id in the middle
    (60,  105.0, 1.50, "t5"),
    (75,  107.0, 2.25, "t6"),
    (75,  107.0, 2.25, "t6"),          # SAME id: must contribute nothing
    (90,  104.0, 0.25, "t7"),
    # minute 2 — out of order within the bucket, plus a genuinely late print
    (120, 104.5, 1.00, "t8"),
    (150, 110.0, 2.00, "t10"),
    (135, 109.0, 0.50, "t9"),          # earlier ts, SAME bucket: accepted
    (95,  999.0, 5.00, "t11"),         # minute 1's bucket, already closed: DROP
    # minute 3 — one print to close minute 2; stays forming
    (180, 108.0, 1.00, "t12"),
]

# Intended, by construction. `v` is int(float sum) because that is exactly
# what _live_on_tick writes and what backfill_crypto.py stored.
#   m0 0.50+0.25+1.50+0.75 = 3.00 -> 3
#   m1 1.50+2.25+0.25      = 4.00 -> 4   (the repeated t6 adds nothing)
#   m2 1.00+2.00+0.50      = 3.50 -> 3   (t11 dropped, so 999.0 is not the high
#                                         and 5.00 is not in the volume)
# m2's close is 109.0, not 110.0: t9 arrived last even though it is stamped
# earlier, and within a bucket the engine takes the last price it is handed.
_WANT = {
    0: {"v": 3, "sum": 3.00, "o": 100.0, "h": 103.0, "l": 98.0,  "c": 101.0},
    1: {"v": 4, "sum": 4.00, "o": 105.0, "h": 107.0, "l": 104.0, "c": 104.0},
    2: {"v": 3, "sum": 3.50, "o": 104.5, "h": 110.0, "l": 104.5, "c": 109.0},
}

# The UTC-midnight tape. Base is 23:58:00 UTC, so the third closed minute is
# the first minute of the NEXT day.
_DAY_FEED: list[tuple[int, float, float, str]] = [
    (0,   50.0, 1.0, "d1"),            # 23:58
    (30,  51.0, 2.0, "d2"),
    (60,  52.0, 3.0, "d3"),            # 23:59
    (110, 53.0, 1.0, "d4"),
    (120, 54.0, 4.0, "d5"),            # 00:00 — new UTC day
    (180, 55.0, 1.0, "d6"),            # 00:01, closes 00:00
]


def _base_ts() -> int:
    return int(datetime(2026, 6, 15, 10, 0, 0, tzinfo=UTC).timestamp())


def _day_base_ts() -> int:
    return int(datetime(2026, 6, 15, 23, 58, 0, tzinfo=UTC).timestamp())


def _cb_msg(base: int, off: int, price: float, size: float, tid: str,
            product: str) -> dict:
    """A payload shaped exactly like Coinbase's `match`, so the venue parser
    is exercised too rather than being bypassed by a hand-built trade dict."""
    ts = datetime.fromtimestamp(base + off, tz=UTC)
    return {"type": "match", "trade_id": tid, "sequence": 1,
            "product_id": product, "side": "buy",
            "time": ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
            "size": f"{size:.8f}", "price": f"{price:.2f}"}


def _read_minutes(sym: str, n: int) -> list[tuple]:
    con = _connect_store()
    try:
        return con.execute(
            "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? ORDER BY ts LIMIT ?",
            (sym, n)).fetchall()
    finally:
        con.close()


def _cleanup(*syms: str) -> None:
    con = _connect_store()
    try:
        for s in syms:
            # Exact match only. LIKE would read the leading underscores in the
            # scratch names as single-character wildcards and reach real rows.
            con.execute("DELETE FROM bars WHERE symbol=?", (s,))
        con.commit()
    finally:
        con.close()
    for s in syms:
        with _CURSOR_GUARD:
            _CURSORS.pop(s, None)
        ds._LIVE.pop(s, None)
        ds._daily_cache.pop(s, None)


def _kite_style_delta(state: dict, size: float) -> float:
    """The WRONG logic, on purpose: kite_stream.TickCursor.delta's arithmetic
    applied to a per-trade size as if it were a cumulative day counter.
    Written out locally rather than flag-switched into the shipped path, so
    the assertions above cannot be satisfied by a code path that only exists
    for tests."""
    last = state.get("last")
    if last is None:
        state["last"] = size
        return 0.0                      # "first reading after connect"
    if size == last:
        return 0.0                      # "duplicate snapshot"
    if size > last:
        state["last"] = size
        return size - last
    return 0.0                          # "stale/out-of-order counter rewind"


def self_test() -> int:
    fails: list[str] = []
    _cleanup(*_ALL_SCRATCH)
    base, dbase = _base_ts(), _day_base_ts()
    print(f"scratch symbols: {', '.join(_ALL_SCRATCH)}")
    print(f"  session_for('{_SCRATCH}') = {ds.session_for(_SCRATCH)} "
          f"(UTC_SESSION = {ds.UTC_SESSION})")
    if ds.session_for(_SCRATCH) != ds.UTC_SESSION:
        fails.append("the scratch symbol is not on the 24/7 clock; every "
                     "assertion below would be measuring the NSE session")
    print(f"synthetic tape base {_utc_str(base)}  "
          f"({len(_FEED)} Coinbase `match` messages)\n")

    # ---- 1. the adapter, as shipped -------------------------------
    # Fed through the real venue parser. The warm-up skip is NOT armed: a
    # synthetic tape has no connect minute, every minute here is fully
    # observed, and the skip is exercised on its own in section 3.
    cur = cursor(_SCRATCH)
    for off, px, sz, tid in _FEED:
        for tr in _cb_parse(_cb_msg(base, off, px, sz, tid, _SCRATCH)):
            on_trade(_SCRATCH, tr)
    rows = _read_minutes(_SCRATCH, 8)
    print("adapter (per-trade sizes SUMMED, as they must be):")
    for i, (ts, o, h, l, c, v) in enumerate(rows):
        w = _WANT.get(i)
        ok = w and (v == w["v"] and o == w["o"] and h == w["h"]
                    and l == w["l"] and c == w["c"])
        print(f"  m{i} {_utc_str(ts)[-9:]}  o={o:<7} h={h:<7} l={l:<7} "
              f"c={c:<7} v={v:<4} want v={w['v'] if w else '?':<4} "
              f"(sum {w['sum'] if w else '?'})  {'OK' if ok else 'MISMATCH'}")

    if len(rows) != 3:
        fails.append(f"expected 3 closed minutes, got {len(rows)}")
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
                         f"(per-trade sizes are not being summed correctly)")
        if (o, h, l, c) != (w["o"], w["h"], w["l"], w["c"]):
            fails.append(f"m{i} OHLC {(o, h, l, c)}, want "
                         f"{(w['o'], w['h'], w['l'], w['c'])}")

    print(f"\n  cursor: trades={cur.trades} dups={cur.dups} "
          f"late_drops={cur.late_drops} rejected={cur.bad} "
          f"no_exchange_ts={cur.no_ts} closes={cur.closes} "
          f"volume={cur.volume:.4f}")
    if cur.dups != 1:
        fails.append(f"expected 1 duplicate trade id, got {cur.dups}")
    if cur.late_drops != 1:
        fails.append(f"expected 1 late (already-closed-minute) drop, got "
                     f"{cur.late_drops}")
    if cur.no_ts != 0:
        fails.append(f"{cur.no_ts} trade(s) fell back to wall clock; every "
                     f"message in the feed carries an exchange clock")
    if cur.closes != 3:
        fails.append(f"cursor counted {cur.closes} minute closes, want 3")
    want_vol = sum(w["sum"] for w in _WANT.values()) + 1.00   # + forming m3
    if abs(cur.volume - want_vol) > 1e-9:
        fails.append(f"cursor volume {cur.volume} != {want_vol} — a size was "
                     f"rounded or dropped somewhere")

    # The second unit trap, stated in the open rather than left to be inferred
    # from the totals: what each minute would read if the size were int()'d
    # PER TRADE instead of summed as a float and truncated once. Computed over
    # the ACCEPTED trades only (the duplicate and the late print are already
    # gone by then), so it is a like-for-like comparison.
    _seen_ids: set = set()
    per_trade_int: dict[int, int] = {}
    for off, px, sz, tid in _FEED:
        if tid in _seen_ids or tid == "t11":     # dup id / late print
            continue
        _seen_ids.add(tid)
        per_trade_int[off // 60] = per_trade_int.get(off // 60, 0) + int(sz)
    print(f"  had each size been int()'d per trade: "
          f"{[per_trade_int.get(i, 0) for i in range(3)]} vs correct "
          f"{[_WANT[i]['v'] for i in range(3)]}  "
          f"(m2 agrees by coincidence — 1.0+2.0+0.5 truncates to the same 3)")

    # get_bars is the path every chart tool takes.
    live = ds.get_bars(_SCRATCH, "1m", None, 10)["bars"]
    print(f"  get_bars 1m -> {len(live)} bars, last t={live[-1]['t']} "
          f"v={live[-1]['v']} (minute 3, forming)")
    if len(live) != 4:
        fails.append(f"get_bars returned {len(live)} bars, want 4 "
                     f"(3 closed + 1 forming)")
    elif [b["v"] for b in live[:3]] != [_WANT[i]["v"] for i in range(3)]:
        fails.append("get_bars volumes disagree with the store")

    # ---- 2. the same tape with kite_stream's delta logic applied ---
    print("\ncontrol (kite_stream's CUMULATIVE-DELTA logic applied to "
          "per-trade sizes):")
    ctrl_state: dict = {}
    ctrl_cur = cursor(_SCRATCH_CTRL)
    last_bucket = None
    for off, px, sz, tid in _FEED:
        for tr in _cb_parse(_cb_msg(base, off, px, sz, tid, _SCRATCH_CTRL)):
            # identical accept/drop path; the ONLY difference is the volume
            out = translate(_SCRATCH_CTRL, tr, ctrl_cur)
            if out is None:
                continue
            ts, p, s = out
            _, bts = ds._bucket_stamp(ts, 1, ds.UTC_SESSION)
            if last_bucket is not None and bts < last_bucket:
                continue
            last_bucket = bts
            ds._live_on_tick(_SCRATCH_CTRL, ts, p, _kite_style_delta(ctrl_state, s))
    ctrl_rows = _read_minutes(_SCRATCH_CTRL, 8)
    for i, (_ts, _o, _h, _l, _c, v) in enumerate(ctrl_rows):
        w = _WANT.get(i)
        print(f"  m{i} v={v:<4} want v={w['v'] if w else '?':<4} "
              f"-> {'WRONG (as it must be)' if w and v != w['v'] else 'agrees'}")
    broke = [i for i, r in enumerate(ctrl_rows)
             if i in _WANT and r[5] != _WANT[i]["v"]]
    if len(broke) != len(ctrl_rows) or not ctrl_rows:
        fails.append("the control did NOT break — the volume assertions above "
                     "are not actually testing that sizes are summed")
    else:
        print(f"  -> all {len(broke)} minutes wrong: the assertions do test "
              f"the summing, and the delta logic really would zero the book")

    # ---- 3. the pure branches, no store needed --------------------
    print("\npure translate() branches:")

    c = TradeCursor()
    got = translate(_SCRATCH, {"id": "x", "ts": "2026-06-15T10:00:00.5Z",
                               "price": "100.5", "size": "0.001"}, c)
    print(f"  ISO8601 with Z          -> {got}")
    if got != (base, 100.5, 0.001):
        fails.append(f"ISO8601 parse -> {got}, want ({base}, 100.5, 0.001)")

    c = TradeCursor()
    got = translate(_SCRATCH, {"id": "y", "ts": base * 1000 + 500,
                               "price": 100.5, "size": 0.001}, c)
    print(f"  epoch milliseconds      -> {got}")
    if got != (base, 100.5, 0.001):
        fails.append(f"epoch-ms parse -> {got}, want ({base}, 100.5, 0.001)")

    c = TradeCursor()
    got = translate(_SCRATCH, {"id": "z", "price": 100.0, "size": 1.0}, c,
                    now=base + 7)
    print(f"  no exchange clock       -> ts={got[0] if got else None} "
          f"no_ts={c.no_ts} (counted, not silent)")
    if got is None or got[0] != base + 7 or c.no_ts != 1:
        fails.append(f"missing clock -> {got}, no_ts={c.no_ts}; want wall-clock "
                     f"ts={base + 7} and no_ts=1")

    c = TradeCursor()
    first = translate(_SCRATCH, {"id": "dup", "ts": base, "price": 1.0,
                                 "size": 2.0}, c)
    again = translate(_SCRATCH, {"id": "dup", "ts": base, "price": 1.0,
                                 "size": 2.0}, c)
    print(f"  duplicate trade id      -> {first} then {again} (dups={c.dups})")
    if again is not None or c.dups != 1 or abs(c.volume - 2.0) > 1e-9:
        fails.append(f"duplicate id -> {again}, dups={c.dups}, "
                     f"volume={c.volume}; want None, 1, 2.0")

    c = TradeCursor()
    for bad in ({"id": "b1", "ts": base, "price": 0.0, "size": 1.0},
                {"id": "b2", "ts": base, "price": None, "size": 1.0},
                {"id": "b3", "ts": base, "price": 5.0, "size": None},
                {"id": "b4", "ts": base, "price": "n/a", "size": "1"}):
        if translate(_SCRATCH, bad, c) is not None:
            fails.append(f"a bad print was accepted: {bad}")
    print(f"  zero/priceless/sizeless -> rejected ({c.bad} of 4)")
    if c.bad != 4:
        fails.append(f"expected 4 rejections, got {c.bad}")

    c = TradeCursor()
    c.arm_warmup()                      # exactly what _open() does on connect
    r1 = translate(_SCRATCH, {"id": "w1", "ts": base + 30, "price": 1.0,
                              "size": 9.0}, c)
    r2 = translate(_SCRATCH, {"id": "w2", "ts": base + 50, "price": 1.0,
                              "size": 9.0}, c)
    r3 = translate(_SCRATCH, {"id": "w3", "ts": base + 61, "price": 2.0,
                              "size": 1.0}, c)
    print(f"  warm-up (connect minute) -> {r1}, {r2}, then {r3} "
          f"(skipped={c.skipped_warmup})")
    if r1 is not None or r2 is not None or r3 is None or c.skipped_warmup != 2:
        fails.append(f"warm-up skip wrong: {r1}, {r2}, {r3}, "
                     f"skipped={c.skipped_warmup}; want None, None, a trade, 2")

    c = TradeCursor()
    c.last_bucket = base + 120          # a minute has already closed
    late = translate(_SCRATCH, {"id": "L", "ts": base + 30, "price": 1.0,
                                "size": 1.0}, c)
    print(f"  late print (closed min) -> {late} (late_drops={c.late_drops})")
    if late is not None or c.late_drops != 1:
        fails.append(f"late print -> {late}, late_drops={c.late_drops}; "
                     f"want None and 1")

    # ---- 4. the UTC-midnight day boundary -------------------------
    print(f"\nUTC-midnight boundary, tape base {_utc_str(dbase)}:")
    cursor(_SCRATCH_DAY)
    for off, px, sz, tid in _DAY_FEED:
        for tr in _cb_parse(_cb_msg(dbase, off, px, sz, tid, _SCRATCH_DAY)):
            on_trade(_SCRATCH_DAY, tr)
    drows = _read_minutes(_SCRATCH_DAY, 8)
    want_days = [ds._ist_day(dbase, 0), ds._ist_day(dbase, 0),
                 ds._ist_day(dbase, 0) + 1]
    want_v = [3, 4, 4]
    for i, (ts, o, h, l, c_, v) in enumerate(drows):
        day = ds._ist_day(ts, 0)
        print(f"  m{i} {_utc_str(ts)}  v={v:<3} utc_day={day} "
              f"want_day={want_days[i] if i < 3 else '?'}")
        if i < 3:
            if ts != dbase + i * 60:
                fails.append(f"day-boundary m{i} ts {ts} != {dbase + i * 60}")
            if day != want_days[i]:
                fails.append(f"day-boundary m{i} lands on UTC day {day}, "
                             f"want {want_days[i]}")
            if v != want_v[i]:
                fails.append(f"day-boundary m{i} v={v}, want {want_v[i]}")
    if len(drows) != 3:
        fails.append(f"day boundary: expected 3 closed minutes, got {len(drows)}")
    else:
        midnight = dbase + 120
        if drows[2][0] != midnight:
            fails.append(f"the 00:00 bar is stamped {drows[2][0]}, want "
                         f"{midnight} — a 24/7 day must open at UTC midnight")
        else:
            print(f"  -> the third minute opens a NEW UTC day at "
                  f"{_utc_str(midnight)}, exactly on midnight")

    # ---- 5. venue parsers, on literal payloads --------------------
    print("\nvenue parsers:")
    cb_hb = _cb_parse({"type": "heartbeat", "product_id": "BTC-USD",
                       "last_trade_id": 123, "time": "2026-08-02T12:00:00Z"})
    print(f"  coinbase heartbeat      -> {cb_hb} (must be empty: no size)")
    if cb_hb:
        fails.append("a Coinbase heartbeat was parsed as a trade — it has no "
                     "size and would enter a candle as a zero-volume print")
    cb_m = _cb_parse({"type": "match", "trade_id": 7, "product_id": "BTC-USD",
                      "time": "2026-08-02T12:00:00.1Z", "size": "0.004",
                      "price": "63000.12", "side": "sell"})
    print(f"  coinbase match          -> {cb_m}")
    if len(cb_m) != 1 or cb_m[0]["size"] != "0.004":
        fails.append(f"coinbase match parse wrong: {cb_m}")
    by_m = _by_parse({"topic": "publicTrade.BTCUSDT", "type": "snapshot",
                      "ts": 1785673140000,
                      "data": [{"T": 1785673140123, "s": "BTCUSDT", "S": "Buy",
                                "v": "0.003", "p": "63000.5", "i": "abc"},
                               {"T": 1785673140456, "s": "BTCUSDT", "S": "Sell",
                                "v": "0.007", "p": "63000.1", "i": "def"}]})
    print(f"  bybit publicTrade (x2)  -> {len(by_m)} trades, sizes "
          f"{[t['size'] for t in by_m]}")
    if len(by_m) != 2 or by_m[0]["size"] != "0.003":
        fails.append(f"bybit publicTrade parse wrong: {by_m}")
    by_tick = _by_parse({"topic": "tickers.BTCUSDT",
                         "data": {"volume24h": "12345.6"}})
    print(f"  bybit tickers (24h vol) -> {by_tick} (must be empty: that "
          f"volume is CUMULATIVE)")
    if by_tick:
        fails.append("a Bybit ticker snapshot was parsed as a trade — its "
                     "volume24h is a rolling cumulative figure")
    try:
        _cb_parse({"type": "error", "message": "no", "reason": "bad product"})
        fails.append("a Coinbase error payload did not raise")
    except _VenueError as exc:
        print(f"  coinbase error payload  -> raises _VenueError: {exc}")

    print(f"  venue_for('BTC-USD')={venue_for('BTC-USD').name}  "
          f"venue_for('BTCUSDT')={venue_for('BTCUSDT').name}  "
          f"venue_for('RELIANCE')={venue_for('RELIANCE')}")
    if venue_for("RELIANCE") is not None:
        fails.append("an NSE symbol was routed to a crypto venue")

    # ---- 6. minute_gap -------------------------------------------
    print("\nminute_gap (24/7 only):")
    mem = sqlite3.connect(":memory:")
    mem.execute("CREATE TABLE bars (symbol TEXT, ts INTEGER, o REAL, h REAL,"
                " l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts))")
    mem.execute("INSERT INTO bars VALUES ('FRESH-USD',?,1,1,1,1,1)", (base - 120,))
    mem.execute("INSERT INTO bars VALUES ('HOLEY-USD',?,1,1,1,1,1)", (base - 7200,))
    mem.execute("INSERT INTO bars VALUES ('RELIANCE',?,1,1,1,1,1)", (base - 7200,))
    for name, want_gap, want_applies in (("FRESH-USD", 1, True),
                                         ("HOLEY-USD", 119, True),
                                         ("RELIANCE", None, False),
                                         ("NOTHERE-USD", None, True)):
        g = minute_gap(mem, name, now=base)
        print(f"  {name:<12} applies={g['applies']!s:<5} "
              f"gap_min={g['gap_min']}  {g['note']}")
        if g["gap_min"] != want_gap or g["applies"] is not want_applies:
            fails.append(f"minute_gap({name}) -> {g['gap_min']}/"
                         f"{g['applies']}, want {want_gap}/{want_applies}")
    mem.close()

    # ---- 7. websocket framing, without a socket -------------------
    print("\nRFC6455 framing (loopback through the parser):")
    ws = WSClient("wss://example.invalid/x")
    for label, payload in (("short (<126)", "a" * 10),
                           ("medium (126-65535)", "b" * 5000),
                           ("long (>65535)", "c" * 70000)):
        blob = _server_frame(_OP_TEXT, payload.encode())
        ws._buf = bytearray(blob)
        f = ws._take_frame()
        ok = f is not None and f[2].decode() == payload
        print(f"  {label:<20} {len(blob)} bytes -> "
              f"{'OK' if ok else 'MISMATCH'}")
        if not ok:
            fails.append(f"frame parse failed for {label}")
    # fragmented text: two continuation frames
    ws2 = WSClient("wss://example.invalid/x")
    ws2._sock = _NullSock()
    ws2._buf = bytearray(_server_frame(_OP_TEXT, b'{"a":', fin=False)
                         + _server_frame(_OP_CONT, b'1}', fin=True))
    msgs = _drain(ws2)
    print(f"  fragmented text      -> {msgs}")
    if msgs != ['{"a":1}']:
        fails.append(f"fragmented text reassembly -> {msgs}")
    # a truncated frame must consume nothing
    ws3 = WSClient("wss://example.invalid/x")
    ws3._buf = bytearray(_server_frame(_OP_TEXT, b"hello")[:4])
    before = len(ws3._buf)
    if ws3._take_frame() is not None or len(ws3._buf) != before:
        fails.append("a truncated frame was consumed or mis-parsed")
    print(f"  truncated frame      -> None, buffer intact ({before} bytes)")
    # a ping must be answered with a pong
    ws4 = WSClient("wss://example.invalid/x")
    ws4._sock = _NullSock()
    ws4._buf = bytearray(_server_frame(_OP_PING, b"hi"))
    _drain(ws4)
    sent = ws4._sock.sent                                # type: ignore[attr-defined]
    pong_op = sent[0][0] & 0x0F if sent else None
    print(f"  ping                 -> replied opcode {pong_op} (want 10)")
    if pong_op != _OP_PONG:
        fails.append(f"a ping was not answered with a pong (got {pong_op})")
    # a client frame must be masked
    ws5 = WSClient("wss://example.invalid/x")
    ws5._sock = _NullSock()
    ws5.send_json({"op": "subscribe"})
    frame = ws5._sock.sent[0]                            # type: ignore[attr-defined]
    print(f"  client frame masked  -> {bool(frame[1] & 0x80)} (RFC 6455 §5.3)")
    if not frame[1] & 0x80:
        fails.append("a client frame went out unmasked; every venue would "
                     "close the connection with 1002")

    # ---- 8. cleanup, verified by querying rather than asserted ----
    _cleanup(*_ALL_SCRATCH)
    con = _connect_store()
    try:
        left = con.execute(
            "SELECT symbol, COUNT(*) FROM bars WHERE symbol IN (?,?,?) "
            "GROUP BY symbol", _ALL_SCRATCH).fetchall()
        # substr, not LIKE: '_' is a LIKE wildcard and '__TEST_%' would match
        # real symbols. This catches residue from ANY test symbol, not just
        # the three this run knows about.
        stray = con.execute(
            "SELECT DISTINCT symbol FROM bars WHERE substr(symbol,1,7)='__TEST_'"
        ).fetchall()
        stray_d = con.execute(
            "SELECT DISTINCT symbol FROM bars_1d "
            "WHERE substr(symbol,1,7)='__TEST_'").fetchall()
    finally:
        con.close()
    print(f"\ncleanup: rows left for this run's scratch symbols: {left or 0}")
    print(f"         any '__TEST_' symbol left in bars:    {stray or 'none'}")
    print(f"         any '__TEST_' symbol left in bars_1d: {stray_d or 'none'}")
    if left:
        fails.append(f"scratch rows survived cleanup: {left}")
    if stray or stray_d:
        fails.append(f"test residue in the store: {stray} {stray_d}")

    if fails:
        print(f"\nSELF-TEST FAILED ({len(fails)})")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nSELF-TEST PASSED")
    return 0


def _server_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """A SERVER->client frame (unmasked), for framing tests only."""
    head = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head.append(n)
    elif n < 65536:
        head.append(126)
        head += struct.pack(">H", n)
    else:
        head.append(127)
        head += struct.pack(">Q", n)
    return bytes(head) + payload


class _NullSock:
    """Captures what the client sends; recv always says "nothing right now"."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendall(self, b: bytes) -> None:
        self.sent.append(b)

    def recv(self, _n: int) -> bytes:
        raise socket.timeout()

    def close(self) -> None:
        pass


def _drain(ws: WSClient) -> list[str]:
    try:
        return ws.poll()
    except _Reconnect:
        return []


# ── cli ───────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--symbols", default="",
                   help="comma-separated symbols, e.g. BTC-USD,ETH-USD "
                        "(Coinbase) or BTCUSDT (Bybit spot)")
    p.add_argument("--venue", choices=sorted(VENUES),
                   help="force a venue; inferred from the symbol suffix "
                        "otherwise")
    p.add_argument("--seconds", type=float,
                   help="stream for this long, then verify and exit")
    p.add_argument("--dry-run", action="store_true",
                   help="plan and freshness verdicts only; no socket")
    p.add_argument("--self-test", action="store_true",
                   help="synthetic verification; no socket, no network")
    p.add_argument("--max-stale-sessions", type=int,
                   default=DEFAULT_MAX_STALE_SESSIONS,
                   help="sessions of missing 1-min history tolerated before "
                        "refusing a symbol")
    p.add_argument("--max-gap-minutes", type=int, default=DEFAULT_MAX_GAP_MIN,
                   help="for 24/7 symbols: wall-clock minutes the stored "
                        "minutes may lag before refusing")
    p.add_argument("--ignore-stale", action="store_true",
                   help="stream anyway despite a freshness/gap refusal "
                        "(testing only — it writes live minutes onto a hole)")
    p.add_argument("--partial-first-minute", action="store_true",
                   help="do NOT skip the connect minute; its bar will be "
                        "partial and may overwrite a complete stored row")
    p.add_argument("--watchdog", type=float, default=45.0,
                   help="seconds of silence before forcing a reconnect")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if a.self_test:
        return self_test()
    syms = [s for s in a.symbols.split(",") if s.strip()]
    if not syms:
        p.error("--symbols is required (or use --self-test)")

    stream = CryptoStream(
        syms, venue=VENUES[a.venue] if a.venue else None,
        max_stale_sessions=a.max_stale_sessions,
        max_gap_min=a.max_gap_minutes, dry_run=a.dry_run,
        ignore_stale=a.ignore_stale,
        partial_first_minute=a.partial_first_minute,
        stale_after=a.watchdog, record=True)
    if not stream.start():
        return 0
    if a.ignore_stale:
        print("\n!! --ignore-stale: a freshness refusal was overridden. Live "
              "minutes may be landing at the right edge of a hole.")
    print(f"\nstreaming{f' for {a.seconds:.0f}s' if a.seconds else ''} "
          f"(ctrl-c to stop)\n")
    try:
        stream.run(a.seconds)
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        stream.stop()
    return verify_live(stream)


if __name__ == "__main__":
    raise SystemExit(main())
