/**
 * liveQuoteManager — module-level singleton that owns the single
 * `/api/ws/quotes` WebSocket for all live-quote consumers.
 *
 * Design:
 * - One WS connection shared across all `useLiveQuote` hook instances.
 * - Subscribe-counted per symbol: send `subscribe` when first listener
 *   arrives for a symbol, send `unsubscribe` when last listener leaves.
 * - Auto-reconnect with exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s cap.
 * - Heartbeat: replies `pong` to inbound `ping` frames.
 * - 30s idle timer: closes the WS if no symbols remain subscribed.
 * - Symbol normalisation: upper-case + spaces→underscores.
 * - Auth: reads bearer JWT from `localStorage.pivot_jwt` (same key as
 *   AppBootstrap's TOKEN_KEY). Passed via `?token=` query param so the
 *   browser upgrade request carries auth without a custom header.
 */

import { getAccessToken } from "@/lib/authToken";

// ---------------------------------------------------------------------------
// Types (exported for consumers)
// ---------------------------------------------------------------------------

export type LiveTick = {
  symbol: string;
  ltp: number;
  changePct: number;
  ts: number;
};

export type Listener = (tick: LiveTick) => void;

// ---------------------------------------------------------------------------
// Wire-frame shapes from the backend (§Layer 3)
// ---------------------------------------------------------------------------

type HelloFrame = {
  type: "hello";
  subscribed: string[];
};

type TickFrame = {
  type: "tick";
  symbol: string;
  ltp: number;
  change_pct: number;
  ts: number;
};

type PingFrame = {
  type: "ping";
  ts: number;
};

type ErrorFrame = {
  type: "error";
  code: string;
};

type ServerFrame = HelloFrame | TickFrame | PingFrame | ErrorFrame;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normalise(symbol: string): string {
  return symbol.trim().toUpperCase().replace(/ /g, "_");
}

async function buildWsUrl(): Promise<string> {
  if (typeof window === "undefined") return "ws://localhost/api/ws/quotes";
  const envBase =
    typeof process !== "undefined" && process.env.NEXT_PUBLIC_PIVOT_WS_BASE;
  let base: string;
  if (envBase) {
    base = envBase;
  } else {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    base = `${proto}//${window.location.host}/api`;
  }
  // Proactively refresh an expired/near-expiry access token before handing it
  // to the WS handshake — otherwise a token that lapsed overnight makes the
  // socket 401 and live quotes silently stop until a manual re-login.
  const token = await getAccessToken();
  const url = new URL(`${base}/ws/quotes`);
  if (token) url.searchParams.set("token", token);
  return url.toString();
}

// ---------------------------------------------------------------------------
// Singleton state
// ---------------------------------------------------------------------------

let ws: WebSocket | null = null;

/** Listener sets keyed by normalised symbol. */
const listeners = new Map<string, Set<Listener>>();

/** Subscription counts keyed by normalised symbol. */
const counts = new Map<string, number>();

let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let idleTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectAttempts = 0;
const BACKOFF_SEQUENCE = [1000, 2000, 4000, 8000, 16000, 30000];
const IDLE_MS = 30_000;

function nextBackoff(): number {
  const idx = Math.min(reconnectAttempts, BACKOFF_SEQUENCE.length - 1);
  return BACKOFF_SEQUENCE[idx]!;
}

// ---------------------------------------------------------------------------
// Connection lifecycle
// ---------------------------------------------------------------------------

function cancelIdle(): void {
  if (idleTimer) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
}

function scheduleIdle(): void {
  cancelIdle();
  idleTimer = setTimeout(() => {
    if (totalSubscribed() === 0) {
      closeConnection();
    }
  }, IDLE_MS);
}

function totalSubscribed(): number {
  let total = 0;
  for (const c of counts.values()) total += c;
  return total;
}

function sendJson(msg: object): void {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

function resubscribeAll(): void {
  const syms = [...counts.keys()].filter((s) => (counts.get(s) ?? 0) > 0);
  if (syms.length > 0) {
    sendJson({ type: "subscribe", symbols: syms });
  }
}

// Guards re-entrancy: buildWsUrl() is now async (it may await a token
// refresh), so a second connect() could slip in during that await. This flag
// blocks the window between "started connecting" and "socket assigned".
let connecting = false;

async function connect(): Promise<void> {
  if (
    connecting ||
    (ws &&
      (ws.readyState === WebSocket.OPEN ||
        ws.readyState === WebSocket.CONNECTING))
  ) {
    return;
  }
  connecting = true;

  let url: string;
  try {
    url = await buildWsUrl();
  } catch {
    connecting = false;
    scheduleReconnect();
    return;
  }

  try {
    ws = new WebSocket(url);
  } catch {
    connecting = false;
    scheduleReconnect();
    return;
  }
  connecting = false;

  ws.onopen = () => {
    reconnectAttempts = 0;
    resubscribeAll();
  };

  ws.onmessage = (event: MessageEvent<string>) => {
    let frame: ServerFrame;
    try {
      frame = JSON.parse(event.data) as ServerFrame;
    } catch {
      // Malformed frame — ignore silently.
      return;
    }
    handleFrame(frame);
  };

  ws.onerror = () => {
    // Browser doesn't expose error detail; onclose will drive reconnect.
  };

  ws.onclose = (event: CloseEvent) => {
    ws = null;
    // 4001 = unauthorized — don't bother reconnecting.
    if (event.code === 4001) return;
    // If there are still subscribers, try to reconnect.
    if (totalSubscribed() > 0) {
      scheduleReconnect();
    }
  };
}

function scheduleReconnect(): void {
  if (reconnectTimer) return;
  const delay = nextBackoff();
  reconnectAttempts += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    void connect();
  }, delay);
}

function closeConnection(): void {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    try {
      ws.close(1000, "idle");
    } catch {
      // Ignore.
    }
    ws = null;
  }
  reconnectAttempts = 0;
}

// ---------------------------------------------------------------------------
// Frame handler
// ---------------------------------------------------------------------------

function handleFrame(frame: ServerFrame): void {
  switch (frame.type) {
    case "hello":
      // Server confirmed subscriptions — nothing extra needed.
      return;

    case "tick": {
      const tick: LiveTick = {
        symbol: frame.symbol,
        ltp: frame.ltp,
        changePct: frame.change_pct,
        ts: frame.ts,
      };
      const set = listeners.get(normalise(frame.symbol));
      if (set) {
        for (const fn of set) {
          fn(tick);
        }
      }
      return;
    }

    case "ping":
      sendJson({ type: "pong", ts: frame.ts });
      return;

    case "error":
      // Individual useLiveQuote hooks handle fallback independently via REST.
      // Swallow silently in production — no console.warn to avoid lint error.
      return;

    default: {
      // Exhaustiveness guard — safe to ignore unknown frames in prod.
      const _: never = frame;
      void _;
    }
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Subscribe `listener` to live ticks for `symbol`.
 * Normalises the symbol and sends `{"type":"subscribe"}` to the server
 * when this is the first listener for that symbol.
 */
export function subscribe(symbol: string, listener: Listener): void {
  const sym = normalise(symbol);

  // Add listener.
  let set = listeners.get(sym);
  if (!set) {
    set = new Set();
    listeners.set(sym, set);
  }
  set.add(listener);

  // Increment count; send subscribe on 0→1 transition.
  const prev = counts.get(sym) ?? 0;
  counts.set(sym, prev + 1);

  cancelIdle();

  // Ensure connection is up.
  void connect();

  if (prev === 0) {
    // First subscriber for this symbol.
    sendJson({ type: "subscribe", symbols: [sym] });
  }
}

/**
 * Unsubscribe `listener` from ticks for `symbol`.
 * Sends `{"type":"unsubscribe"}` when the last listener for a symbol leaves.
 * If no symbols remain subscribed, starts the 30s idle-close timer.
 */
export function unsubscribe(symbol: string, listener: Listener): void {
  const sym = normalise(symbol);

  const set = listeners.get(sym);
  if (set) {
    set.delete(listener);
  }

  const prev = counts.get(sym) ?? 0;
  const next = Math.max(0, prev - 1);
  counts.set(sym, next);

  if (next === 0) {
    // Last listener for this symbol.
    sendJson({ type: "unsubscribe", symbols: [sym] });
    counts.delete(sym);
    listeners.delete(sym);
  }

  if (totalSubscribed() === 0) {
    scheduleIdle();
  }
}

/** Exposed for tests only — resets all singleton state. */
export function _resetForTest(): void {
  closeConnection();
  cancelIdle();
  listeners.clear();
  counts.clear();
  reconnectAttempts = 0;
}
