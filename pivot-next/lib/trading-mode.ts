"use client";

/**
 * Global trading-mode store — 'real' (live) vs 'paper' (simulated).
 *
 * A framework-agnostic external store so the plain data functions in
 * `lib/api.ts` can read the mode SYNCHRONOUSLY (they are async helpers, not
 * React hooks). Components subscribe via `useTradingMode()`.
 *
 * Source of truth for WRITES (buys/sells) is the backend account mode; this
 * store mirrors it for the UI + read routing and persists the user's choice
 * to localStorage so it survives reloads. The frontend term 'real' maps to
 * the backend's 'live'.
 */

import { useSyncExternalStore } from "react";

export type TradingMode = "real" | "paper";

const LS_KEY = "pivot-trading-mode";
const DEFAULT_MODE: TradingMode = "real";

let current: TradingMode = DEFAULT_MODE;
const listeners = new Set<() => void>();

function readStored(): TradingMode {
  if (typeof window === "undefined") return DEFAULT_MODE; // SSR-safe
  try {
    const v = window.localStorage.getItem(LS_KEY);
    return v === "paper" || v === "real" ? v : DEFAULT_MODE;
  } catch {
    return DEFAULT_MODE;
  }
}

// Hydrate from storage at module load (client only). On the server `current`
// stays DEFAULT_MODE; `useTradingMode`'s server snapshot returns the same,
// so the first client paint matches the server HTML (no hydration drift).
current = readStored();

/** Synchronous getter — imported by `lib/api.ts` to route reads. */
export function getTradingMode(): TradingMode {
  return current;
}

export function isPaperMode(): boolean {
  return current === "paper";
}

/** Update the mode: persist to localStorage and notify subscribers. */
export function setTradingMode(mode: TradingMode): void {
  if (mode === current) return;
  current = mode;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(LS_KEY, mode);
    } catch {
      /* ignore — non-persistent fallback still works in-memory */
    }
  }
  listeners.forEach((l) => l());
}

export function subscribeTradingMode(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/**
 * React hook — re-renders the caller whenever the mode changes. Built on
 * `useSyncExternalStore` so it is correct under concurrent rendering and SSR
 * (the server snapshot is the default mode).
 */
export function useTradingMode(): TradingMode {
  return useSyncExternalStore(
    subscribeTradingMode,
    getTradingMode,
    () => DEFAULT_MODE,
  );
}
