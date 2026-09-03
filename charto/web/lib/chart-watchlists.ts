"use client";

import { useSyncExternalStore } from "react";

/** The exact localStorage record used by charto/preview/js/panels.js. */
export const CHART_WATCHLIST_STORAGE_KEY = "charto:watchlists";

const SEED_SYMBOLS = ["NIFTY 50", "NIFTY BANK", "RELIANCE", "TCS", "HDFCBANK", "INFY"];

export type ChartWatchlist = {
  id: string;
  name: string;
  syms: string[];
};

export type ChartWatchlistState = {
  lists: ChartWatchlist[];
  active: string;
  cols: { last: boolean; chg: boolean; pct: boolean };
  sort: string;
  folded: string[];
  [key: string]: unknown;
};

function makeDefault(): ChartWatchlistState {
  return {
    lists: [{ id: "1", name: "My list", syms: [...SEED_SYMBOLS] }],
    active: "1",
    cols: { last: true, chg: true, pct: true },
    sort: "manual",
    folded: [],
  };
}

const SERVER_STATE = makeDefault();

function cleanSymbols(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(
    value
      .filter((item): item is string => typeof item === "string")
      .map((item) => item.trim().toUpperCase())
      .filter(Boolean),
  )];
}

function normalizeStored(value: unknown): ChartWatchlistState {
  const raw = value && typeof value === "object"
    ? value as Record<string, unknown>
    : {};
  const sourceLists = Array.isArray(raw.lists) ? raw.lists.slice(0, 12) : [];
  const lists: ChartWatchlist[] = [];

  for (const item of sourceLists) {
    if (!item || typeof item !== "object") continue;
    const list = item as Record<string, unknown>;
    const id = String(list.id ?? lists.length + 1);
    const name = String(list.name || "My list").trim().slice(0, 32) || "My list";
    lists.push({ id, name, syms: cleanSymbols(list.syms) });
  }

  if (lists.length === 0) return makeDefault();

  const requestedActive = String(raw.active ?? "");
  const active = lists.some((list) => list.id === requestedActive)
    ? requestedActive
    : lists[0]!.id;
  const rawCols = raw.cols && typeof raw.cols === "object"
    ? raw.cols as Record<string, unknown>
    : {};

  // Spread the original record so chart-only preferences added later survive
  // a bookmark edit made from the stock page.
  const cols = {
    last: rawCols.last !== false,
    chg: rawCols.chg !== false,
    pct: rawCols.pct !== false,
  };
  if (!cols.last && !cols.chg && !cols.pct) cols.pct = true;

  return {
    ...raw,
    lists,
    active,
    cols,
    sort: typeof raw.sort === "string" ? raw.sort : "manual",
    folded: Array.isArray(raw.folded) ? raw.folded.map(String) : [],
  };
}

function readState(): ChartWatchlistState {
  if (typeof window === "undefined") return SERVER_STATE;
  try {
    const stored = localStorage.getItem(CHART_WATCHLIST_STORAGE_KEY);
    return stored ? normalizeStored(JSON.parse(stored)) : makeDefault();
  } catch {
    return makeDefault();
  }
}

let snapshot: ChartWatchlistState | null = null;
let eventsBound = false;
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

function getSnapshot(): ChartWatchlistState {
  if (typeof window === "undefined") return SERVER_STATE;
  if (snapshot === null) snapshot = readState();
  return snapshot;
}

function subscribe(listener: () => void): () => void {
  if (!eventsBound && typeof window !== "undefined") {
    eventsBound = true;
    window.addEventListener("storage", (event) => {
      if (event.key !== CHART_WATCHLIST_STORAGE_KEY) return;
      snapshot = readState();
      emit();
    });
    window.addEventListener("charto:watchlists-change", () => {
      snapshot = readState();
      emit();
    });
  }
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function commit(next: ChartWatchlistState): void {
  snapshot = next;
  if (typeof window !== "undefined") {
    try {
      localStorage.setItem(CHART_WATCHLIST_STORAGE_KEY, JSON.stringify(next));
      window.dispatchEvent(new Event("charto:watchlists-change"));
    } catch {
      emit();
    }
  } else {
    emit();
  }
}

export function addToChartWatchlist(symbol: string, listId?: string): void {
  const normalized = symbol.trim().toUpperCase();
  if (!normalized) return;
  const state = getSnapshot();
  const targetId = listId ?? state.active;
  commit({
    ...state,
    lists: state.lists.map((list) =>
      list.id === targetId && !list.syms.includes(normalized)
        ? { ...list, syms: [...list.syms, normalized] }
        : list,
    ),
  });
}

export function removeFromChartWatchlist(symbol: string, listId?: string): void {
  const normalized = symbol.trim().toUpperCase();
  if (!normalized) return;
  const state = getSnapshot();
  commit({
    ...state,
    lists: state.lists.map((list) =>
      listId == null || list.id === listId
        ? { ...list, syms: list.syms.filter((item) => item !== normalized) }
        : list,
    ),
  });
}

export function isInAnyChartWatchlist(
  state: ChartWatchlistState,
  symbol: string,
): boolean {
  const normalized = symbol.trim().toUpperCase();
  return normalized !== "" && state.lists.some((list) => list.syms.includes(normalized));
}

export function useChartWatchlists(): ChartWatchlistState {
  return useSyncExternalStore(subscribe, getSnapshot, () => SERVER_STATE);
}
