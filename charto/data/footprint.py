"""Footprint bars: how much traded at each price inside each bar, split by
WHO crossed the spread — the aggressive buyer lifting the offer or the
aggressive seller hitting the bid.

What a footprint needs, and why it is crypto-only
-------------------------------------------------
A footprint is not built from the order book. Depth is resting intent; a
footprint is executed trades, and every trade must carry its AGGRESSOR side.
Both crypto venues publish that on every print:

- Bybit: `S` on the websocket, `side` in the REST tape and the daily archive,
  is the TAKER's side. "Buy" = an aggressive buyer.
- Coinbase: `side` is the MAKER's side ("side indicates the maker order side;
  sell = an up-tick"). The aggressor is the OPPOSITE. Taking it as-is mirrors
  every bar — buyers become sellers — and the chart still looks plausible,
  which is why it is normalised in exactly one place, `aggressor()`.

Kite (every Indian instrument) publishes last price, cumulative volume and a
five-level book, but no aggressor. Inferring it (tick rule / quote rule) is an
estimate dressed as a measurement, so Indian symbols are answered with a
stated boundary instead of a guessed split.

Storage
-------
`fp(symbol, ts, px, buy, sell, n)` in its own SQLite file beside the bar
store: one row per symbol, 1-minute bucket and fine price step. Every bar
interval and every row size is a read-time fold of these, the same contract
`bars` has with the chart's intervals. The fine step is fixed per symbol the
first time it is seen (`fp_meta`) so stored rows never change meaning.

The forming minute lives in memory and is written when the minute turns; the
read path merges it, so the newest bar is live.
"""
from __future__ import annotations

import math
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fp (
  symbol TEXT NOT NULL, ts INTEGER NOT NULL, px REAL NOT NULL,
  buy REAL NOT NULL DEFAULT 0, sell REAL NOT NULL DEFAULT 0,
  n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (symbol, ts, px)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS fp_meta (symbol TEXT PRIMARY KEY, step REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fp_loaded (
  symbol TEXT NOT NULL, day INTEGER NOT NULL, source TEXT NOT NULL,
  trades INTEGER NOT NULL, at INTEGER NOT NULL,
  PRIMARY KEY (symbol, day, source));
"""

BOUNDARY = ("A footprint needs the aggressor side of every trade. Indian feeds "
            "(Kite) publish price, volume and a five-level book but not who "
            "crossed the spread, so a buy/sell split here would be a guess. "
            "Volume profile shows how much traded at each price without it.")

_con: sqlite3.Connection | None = None
_lock = threading.Lock()
_steps: dict[str, float] = {}
# symbol -> (minute ts, {px: [buy, sell, n]}) — the forming minute only
_open: dict[str, tuple[int, dict[float, list]]] = {}


def bind(path: str | Path) -> None:
    """Open (and create) the footprint store. Safe to call more than once."""
    global _con
    with _lock:
        if _con is not None:
            return
        con = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript(_SCHEMA)
        con.commit()
        _steps.update(dict(con.execute("SELECT symbol, step FROM fp_meta")))
        _con = con


def is_crypto(symbol: str) -> bool:
    return symbol.endswith("USDT") or symbol.endswith("-USD")


def aggressor(venue: str | None, side: str | None) -> str | None:
    """The one place venue side conventions become buyer/seller aggression."""
    s = str(side or "").strip().lower()
    if s not in ("buy", "sell"):
        return None
    if venue == "coinbase":          # maker side → the taker is the other one
        return "sell" if s == "buy" else "buy"
    return s                         # bybit: already the taker's side


def nice_step(x: float) -> float:
    """The 1-2-5 number at or just below `x`, never zero."""
    if not x or x <= 0 or not math.isfinite(x):
        return 1e-8
    e = math.floor(math.log10(x))
    base = 10 ** e
    for m in (5, 2, 1):
        if m * base <= x * (1 + 1e-9):
            return round(m * base, 12)
    return round(base, 12)


def step_for(symbol: str, price: float) -> float:
    """The storage grain: ~1/50,000 of the first price seen, fixed forever."""
    s = _steps.get(symbol)
    if s is None:
        s = nice_step(price * 2e-5)
        _steps[symbol] = s
        if _con is not None:
            with _lock:
                _con.execute("INSERT OR IGNORE INTO fp_meta VALUES (?,?)", (symbol, s))
                _con.commit()
            s = _steps[symbol] = dict(_con.execute(
                "SELECT symbol, step FROM fp_meta WHERE symbol=?", (symbol,))).get(symbol, s)
    return s


def _key(px: float, step: float) -> float:
    return round(math.floor(px / step + 1e-9) * step, 10)


def _write(symbol: str, minute: int, cells: dict[float, list], replace: bool = False) -> None:
    if _con is None or not cells:
        return
    rows = [(symbol, minute, px, b, s, n) for px, (b, s, n) in cells.items()]
    sql = ("INSERT OR REPLACE INTO fp VALUES (?,?,?,?,?,?)" if replace else
           "INSERT INTO fp VALUES (?,?,?,?,?,?) ON CONFLICT(symbol, ts, px) DO UPDATE SET "
           "buy=buy+excluded.buy, sell=sell+excluded.sell, n=n+excluded.n")
    with _lock:
        _con.executemany(sql, rows)
        _con.commit()


def record(symbol: str, ts: int, px: float, size: float, venue: str | None,
           side: str | None) -> None:
    """One live trade. Never raises: it runs on the tick path, where an
    exception would cost the minute's candle, which matters more than this."""
    try:
        who = aggressor(venue, side)
        if who is None or not (px > 0) or not (size >= 0):
            return
        minute = int(ts) // 60 * 60
        step = step_for(symbol, px)
        cur = _open.get(symbol)
        if cur is None or cur[0] != minute:
            if cur is not None and minute < cur[0]:
                # a late print for a closed minute: add it straight to the store
                _write(symbol, minute, {_key(px, step): [size if who == "buy" else 0.0,
                                                         size if who == "sell" else 0.0, 1]})
                return
            if cur is not None:
                _write(symbol, cur[0], cur[1])
            cur = (minute, {})
            _open[symbol] = cur
        cell = cur[1].setdefault(_key(px, step), [0.0, 0.0, 0])
        cell[0 if who == "buy" else 1] += size
        cell[2] += 1
    except Exception:                 # noqa: BLE001 — see docstring
        pass


def store_minutes(symbol: str, minutes: dict[int, dict[float, list]]) -> int:
    """Backfill: REPLACE whole minutes from a complete tape. Returns rows."""
    n = 0
    for minute, cells in minutes.items():
        _write(symbol, minute, cells, replace=True)
        n += len(cells)
    return n


def mark_loaded(symbol: str, day: int, source: str, trades: int) -> None:
    if _con is None:
        return
    with _lock:
        _con.execute("INSERT OR REPLACE INTO fp_loaded VALUES (?,?,?,?,?)",
                     (symbol, day, source, trades, int(time.time())))
        _con.commit()


def loaded(symbol: str, day: int, source: str) -> bool:
    if _con is None:
        return False
    with _lock:
        return _con.execute("SELECT 1 FROM fp_loaded WHERE symbol=? AND day=? AND source=?",
                            (symbol, day, source)).fetchone() is not None


def minutes(symbol: str, start: int, end: int) -> list[tuple[int, float, float, float, int]]:
    """(minute, px, buy, sell, n) for start <= minute < end, forming minute included."""
    out: list[tuple] = []
    if _con is not None:
        with _lock:
            out = _con.execute(
                "SELECT ts, px, buy, sell, n FROM fp WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
                (symbol, start, end)).fetchall()
    cur = _open.get(symbol)
    if cur is not None and start <= cur[0] < end:
        out = out + [(cur[0], px, b, s, n) for px, (b, s, n) in cur[1].items()]
    return out


# ══ the read-time fold ═════════════════════════════════════════════════════

def auto_row(bars: list[dict], step: float) -> float:
    """~8 rows across a typical bar: median high-low range / 8, on a 1-2-5
    number and never finer than the stored grain. Eight is what leaves each
    row tall enough for its figures once the price axis fits the bars."""
    # the newest bars, the ones on screen: a quiet morning in a 200-bar window
    # would otherwise shrink the rows of the active afternoon the reader sees
    rngs = sorted(b["h"] - b["l"] for b in bars[-24:] if b.get("h") and b.get("l"))
    if not rngs:
        return step
    med = rngs[len(rngs) // 2] or rngs[-1]
    return max(step, nice_step(med / 8))


def fold(bars: list[dict], cells: list[tuple], stamp, row: float,
         ratio: float = 3.0, stack: int = 3, va: float = 0.70) -> list[dict]:
    """Group minute cells into the chart's bars and `row`-sized price rows.

    `stamp(minute_ts) -> bar_ts` is the chart's own bucket arithmetic, so a
    footprint column lands on exactly the candle it describes. Every figure
    the UI shows is computed here: rows, totals, delta, POC, value area,
    diagonal imbalances and stacked-imbalance zones.
    """
    by_bar: dict[int, dict[float, list]] = {}
    for minute, px, b, s, n in cells:
        t = stamp(minute)
        r = round(math.floor(px / row + 1e-9) * row, 10)
        cell = by_bar.setdefault(t, {}).setdefault(r, [0.0, 0.0, 0])
        cell[0] += b
        cell[1] += s
        cell[2] += n
    out, cvd = [], 0.0
    for bar in bars:
        rows = by_bar.get(bar["t"])
        item = {"t": bar["t"], "o": bar["o"], "h": bar["h"], "l": bar["l"], "c": bar["c"],
                "v": bar.get("v")}
        if not rows:
            item.update({"rows": [], "covered": 0.0})
            out.append(item)
            continue
        prices = sorted(rows)
        buy = sum(rows[p][0] for p in prices)
        sell = sum(rows[p][1] for p in prices)
        tot = buy + sell
        delta = buy - sell
        cvd += delta
        vols = {p: rows[p][0] + rows[p][1] for p in prices}
        poc = max(prices, key=lambda p: (vols[p], -abs(p - bar["c"])))
        # value area: grow from the POC toward the larger neighbour until va%
        lo = hi = prices.index(poc)
        acc = vols[poc]
        while acc < tot * va and (lo > 0 or hi < len(prices) - 1):
            down = vols[prices[lo - 1]] if lo > 0 else -1
            up = vols[prices[hi + 1]] if hi < len(prices) - 1 else -1
            if up >= down:
                hi += 1
                acc += vols[prices[hi]]
            else:
                lo -= 1
                acc += vols[prices[lo]]
        # Diagonal imbalance, as every footprint platform defines it: a row's
        # BUYS against the SELLS one row BELOW (the offer that was lifted sat
        # one tick above the bid that was hit), and a row's SELLS against the
        # BUYS one row ABOVE. A zero opposite side counts only when this side
        # is itself meaningful (at least the bar's median row volume), so one
        # stray lot against nothing is not flagged.
        med = sorted(vols.values())[len(vols) // 2]
        imb = {}
        for i, p in enumerate(prices):
            b, s = rows[p][0], rows[p][1]
            below = rows[prices[i - 1]][1] if i > 0 and abs(prices[i - 1] - (p - row)) < row / 2 else 0.0
            above = rows[prices[i + 1]][0] if i + 1 < len(prices) and abs(prices[i + 1] - (p + row)) < row / 2 else 0.0
            if b > 0 and ((below > 0 and b >= ratio * below) or (below == 0 and b >= med)):
                imb[p] = "buy"
            elif s > 0 and ((above > 0 and s >= ratio * above) or (above == 0 and s >= med)):
                imb[p] = "sell"
        zones, run = [], []
        for p in prices + [None]:
            side = imb.get(p) if p is not None else None
            if run and (side != imb.get(run[-1]) or p is None or abs(p - run[-1] - row) > row / 2):
                if len(run) >= stack:
                    zones.append({"side": imb[run[0]], "lo": run[0], "hi": run[-1] + row})
                run = []
            if side:
                run.append(p)
        bar_v = bar.get("v") or 0
        item.update({
            "rows": [[p, round(rows[p][1], 8), round(rows[p][0], 8), imb.get(p, "")]
                     for p in reversed(prices)],          # [price, sell, buy, imbalance]
            "buy": round(buy, 8), "sell": round(sell, 8), "delta": round(delta, 8),
            "cvd": round(cvd, 8), "trades": sum(rows[p][2] for p in prices),
            "poc": poc, "vah": prices[hi] + row, "val": prices[lo],
            "zones": zones,
            # Coverage against the candle's own volume: a bar whose tape was
            # only partly observed says so instead of passing as complete.
            "covered": round(min(1.0, tot / bar_v), 3) if bar_v else 1.0,
        })
        out.append(item)
    return out
