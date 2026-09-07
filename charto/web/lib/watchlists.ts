"use client";

// Shared watchlist store — a single source of truth for the user's five
// numbered watchlists, persisted in localStorage and kept in sync across every
// mounted consumer (the screener's WatchlistStrip and the stock page's bookmark
// button) in the same tab AND across tabs (via the `storage` event).
//
// Backed by useSyncExternalStore so server + first-client render agree (no
// hydration mismatch): the server snapshot is the seeded default, then React
// re-renders with the localStorage-backed client snapshot after hydration.

import { useSyncExternalStore } from "react";

export const WL_STORAGE_KEY = "pivot.screener.watchlists.v2";
export const MAX_WATCHLISTS = 5;
// Slot 1 ships with a starter set; slots 2–5 begin empty.
const SEED_TICKERS = ["HDFCBANK", "TCS", "RELIANCE", "INFY", "ITC", "SBIN"];

export type Watchlist = { id: number; tickers: string[] };
export type WatchlistState = { lists: Watchlist[]; activeId: number };

function makeDefault(): WatchlistState {
  return {
    lists: Array.from({ length: MAX_WATCHLISTS }, (_, i) => ({
      id: i + 1,
      tickers: i === 0 ? [...SEED_TICKERS] : [],
    })),
    activeId: 1,
  };
}

// A single stable object for SSR / the initial client render.
const SERVER_STATE: WatchlistState = makeDefault();

// Re-key whatever was persisted onto the five fixed slots so the shape is
// always 1–5 regardless of what was stored. No seeding here — an empty stored
// slot stays empty (the seed only applies to a first-ever, unstored visit).
function normalizeStored(raw: unknown): WatchlistState {
  const byId: Record<number, string[]> = {};
  if (raw && typeof raw === "object" && Array.isArray((raw as { lists?: unknown }).lists)) {
    for (const l of (raw as { lists: unknown[] }).lists) {
      if (l && typeof l === "object") {
        const id = Number((l as Watchlist).id);
        const tickers = (l as Watchlist).tickers;
        if (id >= 1 && id <= MAX_WATCHLISTS && Array.isArray(tickers)) {
          byId[id] = tickers
            .filter((t): t is string => typeof t === "string")
            .map((t) => t.trim().toUpperCase())
            .filter(Boolean);
        }
      }
    }
  }
  const lists = Array.from({ length: MAX_WATCHLISTS }, (_, i) => ({
    id: i + 1,
    tickers: byId[i + 1] ?? [],
  }));
  let activeId = Number((raw as { activeId?: unknown })?.activeId);
  if (!(activeId >= 1 && activeId <= MAX_WATCHLISTS)) activeId = 1;
  return { lists, activeId };
}

function readState(): WatchlistState {
  if (typeof window === "undefined") return SERVER_STATE;
  try {
    const raw = localStorage.getItem(WL_STORAGE_KEY);
    if (!raw) return makeDefault();
    return normalizeStored(JSON.parse(raw));
  } catch {
    return makeDefault();
  }
}

// Cached client snapshot — useSyncExternalStore requires getSnapshot to return
// a stable reference between changes, so we only recompute it on a write or a
// cross-tab storage event.
let _snapshot: WatchlistState | null = null;
const listeners = new Set<() => void>();
let _storageBound = false;

function emit(): void {
  listeners.forEach((l) => l());
}

function getSnapshot(): WatchlistState {
  if (typeof window === "undefined") return SERVER_STATE;
  if (_snapshot === null) _snapshot = readState();
  return _snapshot;
}

function getServerSnapshot(): WatchlistState {
  return SERVER_STATE;
}

function subscribe(cb: () => void): () => void {
  if (!_storageBound && typeof window !== "undefined") {
    _storageBound = true;
    window.addEventListener("storage", (e) => {
      if (e.key === WL_STORAGE_KEY) {
        _snapshot = readState();
        emit();
      }
    });
  }
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function commit(next: WatchlistState): void {
  _snapshot = next;
  if (typeof window !== "undefined") {
    try {
      localStorage.setItem(WL_STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* private mode / quota — non-fatal, lists just won't persist */
    }
  }
  emit();
}

// ── Public mutations ─────────────────────────────────────
export function addToWatchlist(ticker: string, listId?: number): void {
  const t = ticker.trim().toUpperCase();
  if (!t) return;
  const s = getSnapshot();
  const id = listId ?? s.activeId;
  commit({
    ...s,
    lists: s.lists.map((l) =>
      l.id === id && !l.tickers.includes(t)
        ? { ...l, tickers: [...l.tickers, t] }
        : l,
    ),
  });
}

// Remove from a specific list, or — when listId is omitted — from EVERY list
// (used by the stock-page bookmark toggle, which reflects "saved anywhere").
export function removeFromWatchlist(ticker: string, listId?: number): void {
  const t = ticker.trim().toUpperCase();
  if (!t) return;
  const s = getSnapshot();
  commit({
    ...s,
    lists: s.lists.map((l) =>
      listId == null || l.id === listId
        ? { ...l, tickers: l.tickers.filter((x) => x !== t) }
        : l,
    ),
  });
}

export function setActiveWatchlist(id: number): void {
  if (!(id >= 1 && id <= MAX_WATCHLISTS)) return;
  commit({ ...getSnapshot(), activeId: id });
}

// ── Selectors ────────────────────────────────────────────
export function isInAnyWatchlist(state: WatchlistState, ticker: string): boolean {
  const t = ticker.trim().toUpperCase();
  return t !== "" && state.lists.some((l) => l.tickers.includes(t));
}

// ── Hook ─────────────────────────────────────────────────
export function useWatchlists(): WatchlistState {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
