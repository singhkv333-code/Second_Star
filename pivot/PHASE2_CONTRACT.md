# Phase 2 — Kite Live Data Integration: Shared Contract

This file is the single source of truth for the backend ↔ frontend split
of Phase 2 work. The backend agent and the frontend agent both read it.
DO NOT edit shapes here without coordinating both sides.

## Goals

1. Replace 15-minute-delayed yfinance quotes with live Kite ticks for any
   user who has authenticated with Zerodha.
2. Stream live ticks to the frontend via a single multiplexed WebSocket
   so the UI never polls.
3. Keep yfinance as a graceful fallback when (a) the user is in mock
   mode, (b) the KiteTicker is down, or (c) the requested symbol is
   outside the active subscription set.

Out of scope for Phase 2: order book / depth, options chains, full
F&O coverage. Phase 2 is **equities + indices only**.

## Layer 1 — Redis tick cache  (Kite ticker manager → readers)

The KiteTicker manager owns this cache. Every reader treats it as
read-only.

- **Key**: `price:{SYMBOL}` where SYMBOL is the upper-case tradingsymbol
  with spaces replaced by underscores. Examples: `price:RELIANCE`,
  `price:NIFTY_50`, `price:NIFTY_BANK`.
- **Value** (JSON-encoded UTF-8 bytes):
  ```json
  {
    "symbol": "RELIANCE",
    "ltp": 2845.55,
    "change_pct": 0.42,
    "open": 2832.00,
    "high": 2850.10,
    "low": 2828.40,
    "prev_close": 2833.65,
    "volume": 4128390,
    "ts": 1747140330,
    "src": "kite_ws"
  }
  ```
  Fields `open`, `high`, `low`, `prev_close`, `volume` may be `null` for
  the first few ticks after a (re)connect — readers must defend against
  this. `src` is one of `kite_ws`, `kite_rest`, `yfinance`.
- **TTL**: 90 seconds. The ticker re-publishes on every tick (≤1s
  cadence per symbol on a busy session), so 90s is a generous safety
  net while markets are open.

Why this shape: the existing `_cached_price` reader in
`backend/agents/context_injector.py:18` already expects `ltp` and
`change_pct`. Phase 2 extends the same shape — old readers keep
working.

## Layer 2 — Redis pub/sub channel  (Kite ticker manager → WS fan-out)

- **Channel**: `ticks`  (single channel — symbol included in payload)
- **Message** (JSON, identical to the cache value above): published on
  every tick that survives the manager's debouncer (typically one msg
  per symbol per ≥200ms).

Why one channel: per-symbol channels (`ticks:RELIANCE`) sound elegant
but force every WS fan-out worker to subscribe to N channels whenever
a client mutates its subscription. One channel + an in-memory filter
in the WS handler is simpler and the bandwidth is trivial.

## Layer 3 — Backend WebSocket endpoint  (FE ↔ backend fan-out)

**URL**: `GET /api/ws/quotes?symbols=RELIANCE,TCS,NIFTY_50`

**Auth**: Same pattern as `backend/routers/run_stream.py` — bearer
token in `Sec-WebSocket-Protocol: bearer.<jwt>`, or `?token=<jwt>` for
tools that can't set the header.

**Protocol** (all messages are JSON):

Server → client:
```json
{ "type": "hello", "subscribed": ["RELIANCE", "TCS", "NIFTY_50"] }
{ "type": "tick", "symbol": "RELIANCE", "ltp": 2845.55, "change_pct": 0.42, "ts": 1747140330 }
{ "type": "ping", "ts": 1747140360 }                 // every 30s
{ "type": "error", "code": "unauthorized" | "rate_limited" | "internal" }
```

After `hello`, the server immediately replays the latest cached tick
for each subscribed symbol so the UI doesn't sit blank while waiting
for the next live tick.

Client → server:
```json
{ "type": "subscribe", "symbols": ["INFY"] }         // additive
{ "type": "unsubscribe", "symbols": ["RELIANCE"] }
{ "type": "pong", "ts": 1747140361 }                 // optional reply to ping
```

**Backpressure**: the server enforces a per-connection max of 100
symbols. Requests over the cap return `{"type":"error","code":"too_many_symbols"}`
and silently drop the overflow.

## Layer 4 — REST quote endpoint upgrade

`GET /api/markets/quote/{symbol}` (existing endpoint in
`backend/routers/markets.py:215`) gains a new flow:

1. Read `price:{SYMBOL}` from Redis. If `ts` is within 5s of now,
   return it tagged `src: "kite_ws"` (skip the yfinance call entirely).
2. Otherwise fall through to the existing yfinance path; tag the
   response with `src: "yfinance"` and a `live: false` flag so the UI
   can grey-out the price or show a "delayed" badge.

`StockQuote` Pydantic model gains two optional fields:
- `live: bool = False` — true when the quote came from Kite (REST or WS)
- `source: Literal["kite_ws", "kite_rest", "yfinance"] = "yfinance"`

## Layer 5 — Ticker manager lifecycle

Owned by a single process-wide singleton (similar to how
`backend/kite/auth.py` mirrors module flags). Started on FastAPI
startup if a Kite access token is available; stopped cleanly on
shutdown.

**Universe selection on start**:
1. The auth'd user's holdings (top 50 by market value).
2. NIFTY 50 + NIFTY BANK + SENSEX index instrument tokens.
3. Any symbol present in the active WS clients' subscriptions.

When a WS client subscribes to a symbol that isn't yet in the
universe, the manager hot-adds it via `ticker.subscribe([token])`
within ~500ms.

**Admin surface**:
- `GET /api/admin/kite-ticker/status` →
  `{"running": bool, "symbol_count": int, "last_tick_ts": int | null, "reconnects": int, "user_id": int | null}`
- `POST /api/admin/kite-ticker/start` → starts under the calling user's
  access token; idempotent (no-op if already running).
- `POST /api/admin/kite-ticker/stop` → stops; idempotent.

These endpoints require the same auth as other admin routes (existing
`backend/routers/admin.py` pattern).

## Layer 6 — Frontend hook

```ts
// pivot-next/hooks/useLiveQuote.ts
export function useLiveQuote(symbol: string): {
  ltp: number | null;
  changePct: number | null;
  ts: number | null;
  isLive: boolean;
};
```

- Internally talks to a **module-level singleton WS manager** so all
  consumers share one connection (multiplex). Subscribe-counted —
  unsubscribe only when the last consumer of a symbol unmounts.
- Auto-reconnects on close with exponential backoff (1s, 2s, 4s, max
  30s). On reconnect, re-sends `subscribe` for the union of active
  subscriptions.
- Falls back to the existing REST `/api/markets/quote/{symbol}` (one
  fetch per minute) when WS is unreachable for >5s; `isLive` reflects
  which source is feeding the value.
- Conversion: `symbol` is uppercased and `' '` → `'_'` before sending
  to the server, matching the Redis key shape.

Initial integration points (do not refactor more than this):
1. `pivot-next/components/agent-panel/PortfolioTab.tsx` — replace
   static holding LTPs with `useLiveQuote(symbol)` per row.
2. The Stock Detail page (existing `[symbol]/page.tsx` route) — top
   price ticker.
3. Chat snapshot card
   (`pivot-next/components/chat/StockSnapshotCard.tsx`) — overlay the
   `live` flag from the new REST response when no WS is connected.

## Layer 7 — Failure & fallback matrix

| Scenario | Backend behavior | FE behavior |
|---|---|---|
| Kite mock mode (no creds) | Ticker never starts. REST quotes serve yfinance, `live: false` | `isLive=false`, polled REST every 60s |
| Kite authenticated, ticker stopped | Cache empty → REST falls through to yfinance | Same as mock mode |
| Ticker running, symbol not in universe | Hot-add on first WS subscribe. REST checks cache (miss) → yfinance for that single call | Brief delay (~500ms) then live ticks |
| Token expired mid-session | Ticker emits log + auto-reconnect attempt; on hard fail, manager stops and clears cache | WS connections close with `unauthorized`; FE shows toast + falls back to REST |
| WS bandwidth saturation | (Not expected in v1.) Manager logs warning; no other action | n/a |

## Test plan

Backend agent owns:
- Unit test for the manager's universe-selection logic.
- Integration test that publishes a synthetic tick to the `ticks`
  channel and verifies the WS handler relays it to a connected client.
- Manual smoke: start the backend with mock Kite, call `/api/ws/quotes`
  with a couple of symbols, observe `hello` → `tick (cached
  yfinance)` → wait 60s → see another `tick`.

Frontend agent owns:
- Unit test for the WS manager's subscribe-counting (mount two
  components on RELIANCE; unmount one; subscription should remain).
- Manual smoke: open PortfolioTab, watch LTPs update.

## Boundaries — do NOT touch

- `backend/routers/chat.py`, `backend/services/chat_service.py` — chat
  is not part of Phase 2.
- `backend/routers/run_stream.py` — run-stream WS already works; only
  borrow its auth pattern.
- `backend/agents/tool_executor.py` — `_get_live_price` already reads
  `price:{SYMBOL}` correctly. Don't change its signature.
- Legacy `frontend/` directory (Vite) — Phase 2 is pivot-next only.
- The Kite credentials panel, OAuth flow, place_order paths — all
  shipped in Phase 1, leave intact.

## Open questions (decide during implementation)

1. Should the manager auto-start on FastAPI boot if a Kite token exists
   in `KiteSession`, or only when the first WS client connects? **Default:
   auto-start on boot if a valid token is present** — saves the cold-start
   latency for the first chat query.
2. KiteTicker reconnection policy when the upstream WS drops. Use
   kiteconnect's built-in `reconnect=True, reconnect_max_tries=50,
   reconnect_max_delay=60`. Re-subscribe on every reconnect.
