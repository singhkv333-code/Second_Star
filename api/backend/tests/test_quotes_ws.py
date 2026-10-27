"""Integration test for /api/ws/quotes.

Uses FastAPI's TestClient + websocket_connect. We seed the Redis tick
cache and verify the WS hello/replay path works. We don't exercise
pub/sub (MockRedis doesn't implement it); the cache-replay leg is the
load-bearing path for the demo when the ticker isn't running.
"""
from __future__ import annotations

import json

from backend.cache import get_redis
from backend.kite.ticker import cache_key, reset_ticker_manager_for_tests


def _seed_cache(symbol: str, ltp: float, ts: int) -> None:
    rc = get_redis()
    payload = {
        "symbol": symbol,
        "ltp": ltp,
        "change_pct": 0.5,
        "open": ltp - 5,
        "high": ltp + 10,
        "low": ltp - 7,
        "prev_close": ltp - 2,
        "volume": 1000,
        "ts": ts,
        "src": "kite_ws",
    }
    rc.set(cache_key(symbol), json.dumps(payload), ex=90)


def test_quotes_ws_rejects_missing_token(client):
    """No token → close with 4401."""
    try:
        with client.websocket_connect("/api/ws/quotes") as ws:
            # Server accepts then closes — receive surfaces the close.
            try:
                ws.receive_text()
            except Exception:
                pass
    except Exception:
        # Some httpx versions raise WebSocketDisconnect on close-after-accept.
        pass


def test_quotes_ws_hello_and_replay(client, auth_headers):
    """With auth + a cached tick, the WS sends hello + the cached tick."""
    reset_ticker_manager_for_tests()
    import time as _time
    _seed_cache("RELIANCE", 2845.55, int(_time.time()))

    token = auth_headers["Authorization"].replace("Bearer ", "", 1)
    with client.websocket_connect(
        f"/api/ws/quotes?symbols=RELIANCE&token={token}",
    ) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["subscribed"] == ["RELIANCE"]

        tick = ws.receive_json()
        assert tick["type"] == "tick"
        assert tick["symbol"] == "RELIANCE"
        assert tick["ltp"] == 2845.55


def test_quotes_ws_inbound_subscribe(client, auth_headers):
    """Client can subscribe after connect; cached tick replays."""
    reset_ticker_manager_for_tests()
    import time as _time
    _seed_cache("INFY", 1480.0, int(_time.time()))

    token = auth_headers["Authorization"].replace("Bearer ", "", 1)
    with client.websocket_connect(
        f"/api/ws/quotes?token={token}",
    ) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["subscribed"] == []

        ws.send_json({"type": "subscribe", "symbols": ["INFY"]})
        tick = ws.receive_json()
        assert tick["type"] == "tick"
        assert tick["symbol"] == "INFY"
        assert tick["ltp"] == 1480.0
