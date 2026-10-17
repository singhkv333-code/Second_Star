"""Order-book depth for the chart's Market Depth widget.

One function, `book(symbol, levels, scope)`, answers with a snapshot of the
book or with a stated reason it has none. It never synthesises a level:
a book is either read off a venue or the answer says it is unavailable.

Where each family's book comes from
-----------------------------------
- Bybit spot pairs (`*USDT`): the public REST order book
  (/v5/market/orderbook, no key needed), up to 200 levels.
- Coinbase pairs (`*-USD`): the public level-2 book, truncated here.
- Indian instruments: Kite's MODE_FULL tick already carries a five-level book
  for every streamed symbol, so `kite_stream.on_tick` hands it to
  `record_kite` and the freshest one is served from memory. A symbol that is
  not being streamed falls back to Kite's quote API for NSE equities, which
  needs the day's session; without one the answer says so.
- Indices print no orders, so they have no book, and the answer says that
  rather than showing an empty ladder that looks like a dead feed.

Every answer is cached for one second per (symbol, levels), and concurrent
requests for the same key share a single upstream call: a dozen open widgets
on one symbol cost the venue one request a second, not twelve.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

_TTL = 1.0                 # seconds a book is served from cache
_KITE_FRESH = 5.0          # a streamed book older than this is not "live"
_KITE_BACKOFF = 60.0       # after a failed Kite login, wait before retrying

_cache: dict[tuple, tuple[float, dict]] = {}
_inflight: dict[tuple, threading.Event] = {}
_guard = threading.Lock()

_kite_books: dict[str, tuple[float, dict]] = {}
_kite_client = None
_kite_client_at = 0.0
_kite_dead_until = 0.0
_kite_lock = threading.Lock()


def _ssl_ctx():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get_json(url: str, timeout: float = 4.0) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "charto-depth/1"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
        return json.loads(r.read())


def _levels(rows, n: int) -> list[list[float]]:
    out = []
    for row in rows[:n]:
        try:
            p, q = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if p > 0 and q > 0:
            out.append([p, q])
    return out


# ── venues ─────────────────────────────────────────────────────────────

def _bybit(symbol: str, n: int) -> dict:
    q = urllib.parse.urlencode({"category": "spot", "symbol": symbol,
                                "limit": max(1, min(n, 200))})
    d = _get_json(f"https://api.bybit.com/v5/market/orderbook?{q}")
    if d.get("retCode") != 0:
        return {"available": False,
                "reason": f"Bybit refused the book request ({d.get('retMsg')})"}
    res = d.get("result") or {}
    return {"available": True, "source": "bybit", "bids": _levels(res.get("b") or [], n),
            "asks": _levels(res.get("a") or [], n),
            "ts": int(res.get("ts") or time.time() * 1000) / 1000}


def _coinbase(symbol: str, n: int) -> dict:
    d = _get_json(f"https://api.exchange.coinbase.com/products/"
                  f"{urllib.parse.quote(symbol)}/book?level=2")
    return {"available": True, "source": "coinbase",
            "bids": _levels(d.get("bids") or [], n),
            "asks": _levels(d.get("asks") or [], n), "ts": time.time()}


def record_kite(symbol: str, depth: dict | None) -> None:
    """Called from kite_stream.on_tick with the tick's `depth` field."""
    if not depth:
        return
    _kite_books[symbol] = (time.time(), depth)


def _kite_rows(side) -> list[list[float]]:
    out = []
    for lv in side or []:
        try:
            p, q, o = float(lv.get("price") or 0), float(lv.get("quantity") or 0), \
                int(lv.get("orders") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if p > 0 and q > 0:
            out.append([p, q, o])
    return out


def _kite_answer(depth: dict, at: float, how: str) -> dict:
    return {"available": True, "source": "kite", "via": how,
            "bids": _kite_rows(depth.get("buy")), "asks": _kite_rows(depth.get("sell")),
            "ts": at}


def _kite(symbol: str) -> dict:
    global _kite_client, _kite_client_at, _kite_dead_until
    hit = _kite_books.get(symbol)
    if hit and time.time() - hit[0] <= _KITE_FRESH:
        return _kite_answer(hit[1], hit[0], "stream")
    if time.time() < _kite_dead_until:
        return {"available": False,
                "reason": "Market depth needs the live Kite session, which is not connected."}
    with _kite_lock:
        try:
            if _kite_client is None or time.time() - _kite_client_at > 600:
                import kite_stream
                _kite_client = kite_stream._kite_client(kite_stream._kite_access_token())
                _kite_client_at = time.time()
            key = f"NSE:{symbol}"
            q = (_kite_client.quote([key]) or {}).get(key) or {}
        except BaseException as exc:            # noqa: BLE001 — SystemExit from a mock token too
            _kite_client = None
            _kite_dead_until = time.time() + _KITE_BACKOFF
            return {"available": False,
                    "reason": "Market depth needs the live Kite session, which is not connected.",
                    "detail": str(exc)[:160]}
    depth = q.get("depth")
    if not depth:
        return {"available": False,
                "reason": f"Kite returned no order book for {symbol}."}
    return _kite_answer(depth, time.time(), "quote")


# ── the one entry point ────────────────────────────────────────────────

def _fetch(symbol: str, n: int, scope: str) -> dict:
    if scope in ("index_in", "volatility_in"):
        return {"available": False,
                "reason": "Indices are calculated, not traded, so they have no order book."}
    if symbol.endswith("USDT"):
        return _bybit(symbol, n)
    if symbol.endswith("-USD"):
        return _coinbase(symbol, n)
    return _kite(symbol)


def book(symbol: str, levels: int = 20, scope: str = "") -> dict:
    symbol = symbol.strip().upper()
    n = max(1, min(int(levels or 20), 100))
    key = (symbol, n)
    while True:
        with _guard:
            hit = _cache.get(key)
            if hit and time.time() - hit[0] < _TTL:
                return hit[1]
            ev = _inflight.get(key)
            if ev is None:
                ev = threading.Event()
                _inflight[key] = ev
                owner = True
            else:
                owner = False
        if not owner:
            ev.wait(5)
            continue
        try:
            try:
                out = _fetch(symbol, n, scope)
            except Exception as exc:              # noqa: BLE001
                out = {"available": False,
                       "reason": "The venue did not answer the book request.",
                       "detail": str(exc)[:160]}
            out = {"symbol": symbol, "levels": n, **out}
            with _guard:
                _cache[key] = (time.time(), out)
            return out
        finally:
            with _guard:
                _inflight.pop(key, None)
            ev.set()


if __name__ == "__main__":                       # quick manual check
    import sys
    print(json.dumps(book(sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT", 5), indent=1))
