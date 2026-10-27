"""Hyperliquid perpetuals as symbols: BTC-PERP, ETH-PERP, SOL-PERP.

Read from Hyperliquid's public info API (no key): the perp universe with
each market's mark, mid, funding, open interest and day volume; candles;
and the hourly funding history. Nothing here trades: a perp position is a
paper leg in the day book (daybook.py), marked from these mids.

Prices are in USD, as Hyperliquid quotes them; the day book converts a
position's money to rupees with the USD/INR rate the paper book uses.

  bars(symbol, interval, to, limit)  OHLCV, the forming candle included
  ctx(coin)                          mark, mid, funding (hourly), OI, volume
  mids()                             every market's mid, in one call
  funding_history(coin, start)       [(ts, rate)] hourly
  start(on_bar)                      a poller that reports closed minutes for
                                     the perps something is watching
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger("hyperliquid")

URL = "https://api.hyperliquid.xyz/info"
ICON = "https://app.hyperliquid.xyz/coins/{coin}.svg"
SUFFIX = "-PERP"
IV = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h",
      "1d": "1d", "1w": "1w", "1mo": "1M"}
IV_SECS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
           "1d": 86400, "1w": 604800, "1mo": 2592000}

_lock = threading.Lock()
_meta: dict = {"t": 0.0, "by": {}}
_mids: dict = {"t": 0.0, "px": {}}
_candles: dict[tuple, tuple[float, list]] = {}
_watch: set[str] = set()
_stop = threading.Event()
STATS = {"calls": 0, "errors": 0}


def _post(body: dict, timeout: float = 12.0):
    STATS["calls"] += 1
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        STATS["errors"] += 1
        raise


def is_perp(symbol: str) -> bool:
    return str(symbol or "").upper().endswith(SUFFIX)


def coin(symbol: str) -> str:
    return str(symbol or "").upper()[: -len(SUFFIX)] if is_perp(symbol) else str(symbol or "").upper()


def universe(max_age: float = 30.0) -> dict[str, dict]:
    """{coin: {max_leverage, sz_decimals, mark, mid, oracle, funding, oi, day_volume_usd,
    prev_day}}, refreshed at most every `max_age` seconds."""
    with _lock:
        if time.time() - _meta["t"] < max_age and _meta["by"]:
            return _meta["by"]
    meta, ctxs = _post({"type": "metaAndAssetCtxs"})
    by = {}
    for a, c in zip(meta.get("universe") or [], ctxs or []):
        if a.get("isDelisted"):
            continue
        f = lambda k: float(c[k]) if c.get(k) not in (None, "") else None  # noqa: E731
        by[a["name"].upper()] = {
            "coin": a["name"], "max_leverage": int(a.get("maxLeverage") or 1),
            "sz_decimals": int(a.get("szDecimals") or 0), "mark": f("markPx"), "mid": f("midPx"),
            "oracle": f("oraclePx"), "funding": f("funding"), "premium": f("premium"),
            "oi": f("openInterest"), "day_volume_usd": f("dayNtlVlm"), "prev_day": f("prevDayPx")}
    with _lock:
        _meta.update(t=time.time(), by=by)
    return by


def ctx(c: str) -> dict | None:
    try:
        return universe().get(coin(c))
    except Exception:                                       # noqa: BLE001
        return None


def known(symbol: str) -> bool:
    return is_perp(symbol) and ctx(symbol) is not None


def mids(max_age: float = 3.0) -> dict[str, float]:
    with _lock:
        if time.time() - _mids["t"] < max_age and _mids["px"]:
            return _mids["px"]
    got = _post({"type": "allMids"})
    px = {k.upper(): float(v) for k, v in (got or {}).items() if not k.startswith("@")}
    with _lock:
        _mids.update(t=time.time(), px=px)
    return px


def mark(symbol: str) -> float | None:
    try:
        return mids().get(coin(symbol))
    except Exception:                                       # noqa: BLE001
        c = ctx(symbol)
        return c and (c.get("mid") or c.get("mark"))


def bars(symbol: str, interval: str, to: int | None, limit: int) -> dict:
    """The perp's candles as the chart's bars (t is the bar's open, seconds).
    Recent windows are cached for 20 seconds; the forming candle is included,
    as the chart's own bars include it."""
    iv = IV.get(interval)
    if not iv:
        return {"bars": [], "error": f"Hyperliquid has no {interval} candles"}
    step = IV_SECS[interval]
    end = int(to) if to else int(time.time()) + step
    limit = max(1, min(int(limit or 500), 5000))
    key = (coin(symbol), iv, end // 20 if not to else end, limit)
    with _lock:
        hit = _candles.get(key)
        if hit and time.time() - hit[0] < 20:
            rows = hit[1]
        else:
            rows = None
    if rows is None:
        start = end - step * (limit + 2)
        got = _post({"type": "candleSnapshot",
                     "req": {"coin": coin(symbol), "interval": iv,
                             "startTime": start * 1000, "endTime": end * 1000}}) or []
        rows = [{"t": int(c["t"]) // 1000, "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]),
                 "c": float(c["c"]), "v": float(c["v"])} for c in got]
        rows = [r for r in rows if not to or r["t"] < to]
        with _lock:
            if len(_candles) > 400:
                _candles.clear()
            _candles[key] = (time.time(), rows)
    rows = rows[-limit:]
    return {"bars": rows, "has_more": bool(rows) and len(rows) >= limit, "venue": "Hyperliquid",
            "note": "Hyperliquid perpetual, priced in USD"}


def funding_history(symbol: str, start: int) -> list[tuple[int, float]]:
    """[(ts seconds, hourly rate)], oldest first, from `start`. Paged: the API
    returns at most 500 rows a call."""
    out: list[tuple[int, float]] = []
    t = int(start) * 1000
    for _ in range(12):
        got = _post({"type": "fundingHistory", "coin": coin(symbol), "startTime": t}) or []
        if not got:
            break
        out += [(int(r["time"]) // 1000, float(r["fundingRate"])) for r in got]
        if len(got) < 500:
            break
        t = int(got[-1]["time"]) + 1
    return out


def icon(symbol: str) -> str:
    return ICON.format(coin=coin(symbol))


# ── the live clock ───────────────────────────────────────────────────────────

def watch(symbols) -> None:
    with _lock:
        _watch.clear()
        _watch.update(s for s in symbols if is_perp(s))


def start(on_bar, watched=lambda: ()) -> threading.Thread:
    """Every 10 seconds, for each perp something watches: when a minute has
    closed, report it to `on_bar(symbol, [minute_open_ts, o, h, l, c, v], True)`,
    the same shape the chart's live feed reports in."""
    last: dict[str, int] = {}

    def loop():
        while not _stop.is_set():
            try:
                syms = {s for s in watched() if is_perp(s)} | set(_watch)
                if syms:
                    px = mids(max_age=2.0)
                    now_min = int(time.time()) // 60 * 60
                    for s in syms:
                        closed = now_min - 60
                        if last.get(s) == closed:
                            continue
                        last[s] = closed
                        p = px.get(coin(s))
                        if p:
                            on_bar(s, [closed, p, p, p, p, 0.0], True)
            except Exception:                               # noqa: BLE001
                log.warning("hyperliquid: poll failed", exc_info=True)
            _stop.wait(10)
    th = threading.Thread(target=loop, name="hyperliquid-clock", daemon=True)
    th.start()
    return th


def stop() -> None:
    _stop.set()
