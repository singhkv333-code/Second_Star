"use client";

/**
 * useLiveQuote — React hook that delivers a live price tick for one symbol.
 *
 * Sources (in priority order):
 * 1. WebSocket ticks from the module-level liveQuoteManager singleton.
 * 2. REST fallback: `GET /api/markets/quote/{symbol}` on mount (after 5s if
 *    no WS tick has arrived) and every 60s while the WS is disconnected.
 *
 * `isLive` is true only when the latest update came via the WS.
 *
 * Pass `null` to opt out — all returned values will be null and no
 * subscription is registered.
 */

import { useEffect, useRef, useSyncExternalStore } from "react";
import { subscribe, unsubscribe, type LiveTick } from "@/lib/liveQuoteManager";
import { getStockQuote } from "@/lib/api";
import { isError } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type LiveQuoteResult = {
  ltp: number | null;
  changePct: number | null;
  ts: number | null;
  isLive: boolean;
};

// ---------------------------------------------------------------------------
// Per-symbol external store (useSyncExternalStore)
//
// We keep one store per symbol string (normalised). Each store is a lightweight
// object with a snapshot and a set of React "notify" callbacks registered via
// `subscribe`. When a WS tick arrives the snapshot is replaced and all
// subscribed components re-render — no unnecessary re-renders for components
// watching a *different* symbol.
// ---------------------------------------------------------------------------

type Snapshot = {
  ltp: number | null;
  changePct: number | null;
  ts: number | null;
  isLive: boolean;
};

const NULL_SNAPSHOT: Snapshot = {
  ltp: null,
  changePct: null,
  ts: null,
  isLive: false,
};

type Store = {
  snapshot: Snapshot;
  listeners: Set<() => void>;
  notify: () => void;
};

const stores = new Map<string, Store>();

function getOrCreateStore(sym: string): Store {
  let store = stores.get(sym);
  if (!store) {
    store = {
      snapshot: NULL_SNAPSHOT,
      listeners: new Set(),
      notify() {
        for (const fn of this.listeners) fn();
      },
    };
    stores.set(sym, store);
  }
  return store;
}

function subscribeStore(sym: string, notify: () => void): () => void {
  const store = getOrCreateStore(sym);
  store.listeners.add(notify);
  return () => {
    store.listeners.delete(notify);
  };
}

function getSnapshot(sym: string | null): Snapshot {
  if (!sym) return NULL_SNAPSHOT;
  return stores.get(sym)?.snapshot ?? NULL_SNAPSHOT;
}

function getServerSnapshot(): Snapshot {
  return NULL_SNAPSHOT;
}

// ---------------------------------------------------------------------------
// Normalise helper (mirrors liveQuoteManager)
// ---------------------------------------------------------------------------

function normalise(symbol: string): string {
  return symbol.trim().toUpperCase().replace(/ /g, "_");
}

// ---------------------------------------------------------------------------
// useLiveQuote
// ---------------------------------------------------------------------------

const REST_FALLBACK_DELAY_MS = 5_000;
const REST_POLL_INTERVAL_MS = 60_000;

export function useLiveQuote(symbol: string | null): LiveQuoteResult {
  const sym = symbol ? normalise(symbol) : null;

  // ── External store subscription ──────────────────────────────────────────
  const snapshot = useSyncExternalStore(
    (notify) => {
      if (!sym) return () => {};
      return subscribeStore(sym, notify);
    },
    () => getSnapshot(sym),
    getServerSnapshot,
  );

  // ── Refs for REST fallback management ───────────────────────────────────
  const wsTickArrivedRef = useRef(false);
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearFallbackTimer = (): void => {
    if (fallbackTimerRef.current) {
      clearTimeout(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }
  };

  const clearPollTimer = (): void => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  };

  // ── REST fetch helper ────────────────────────────────────────────────────
  const fetchRest = (s: string): void => {
    getStockQuote(s)
      .then((result) => {
        if (isError(result)) return;
        // Only apply if no WS tick has arrived yet (don't overwrite live data).
        if (wsTickArrivedRef.current) return;
        const store = getOrCreateStore(s);
        store.snapshot = {
          ltp: result.data.ltp,
          changePct: result.data.change_pct,
          ts: Date.now() / 1000,
          isLive: result.data.live === true,
        };
        store.notify();
      })
      .catch(() => {
        // Network error — silently ignore; user sees null values.
      });
  };

  // ── WS listener (updates the store, stops REST poll) ────────────────────
  useEffect(() => {
    if (!sym) return;

    wsTickArrivedRef.current = false;

    const listener = (tick: LiveTick): void => {
      if (!wsTickArrivedRef.current) {
        wsTickArrivedRef.current = true;
        clearFallbackTimer();
        clearPollTimer();
      }
      const store = getOrCreateStore(sym);
      store.snapshot = {
        ltp: tick.ltp,
        changePct: tick.changePct,
        ts: tick.ts,
        isLive: true,
      };
      store.notify();
    };

    subscribe(sym, listener);

    // 5s grace: if no WS tick arrives, fall back to REST.
    fallbackTimerRef.current = setTimeout(() => {
      if (!wsTickArrivedRef.current) {
        fetchRest(sym);
        // Then poll every 60s while WS is down.
        pollTimerRef.current = setInterval(() => {
          if (!wsTickArrivedRef.current) {
            fetchRest(sym);
          } else {
            clearPollTimer();
          }
        }, REST_POLL_INTERVAL_MS);
      }
    }, REST_FALLBACK_DELAY_MS);

    return () => {
      unsubscribe(sym, listener);
      clearFallbackTimer();
      clearPollTimer();
      wsTickArrivedRef.current = false;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- sym is derived from symbol; exhaustive-deps would misfire on inline functions
  }, [sym]);

  return snapshot;
}
