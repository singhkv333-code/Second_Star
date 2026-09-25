"use client";

// Saved screens — the user's kept screens, from the chart's chat or built in
// the Screener itself.
//
// TWO KINDS, deliberately, because they are not the same promise:
//
//   kind "symbols"  a FROZEN membership. This is what a screen handed over
//                   from the chart becomes. Charto screens its own 500-
//                   instrument daily matrix on features this service does not
//                   have (vol_z20, distance from the 20/50/200-day SMA), so
//                   the query cannot be replayed here — only what it matched,
//                   plus the sentence it matched on and the session it ran
//                   against. Re-opening shows those names priced today; it
//                   does NOT re-run the screen, and the UI has to say so.
//   kind "filters"  this service's own query. Re-opening RE-RUNS it, so the
//                   membership is whatever qualifies now.
//
// Conflating them would be the expensive kind of wrong: a user who thinks a
// frozen list is live would read a stale membership as a live signal.

import { getScreenerStocks } from "./screenerApi";
import type {
  ScreenerMcapTier,
  ScreenerSortBy,
  ScreenerStocksResponse,
} from "./screenerApi";
import { isError } from "@/lib/types";
import type { ApiResult } from "@/lib/types";

export type ScreenKind = "symbols" | "filters";
export type ScreenSource = "chat" | "screener";

/** The Screener's own query, as stored on a `filters` screen. Mirrors the
 *  subset of ScreenerStocksParams that defines a screen (pagination is not
 *  part of a screen's identity, so limit/offset are deliberately absent). */
export type ScreenFilters = {
  sector?: string;
  mcap_tier?: ScreenerMcapTier;
  pe_max?: number;
  roe_min?: number;
  filters?: string;
  sort_by?: ScreenerSortBy;
  sort_dir?: "asc" | "desc";
};

export type SavedScreen = {
  id: number;
  name: string;
  kind: ScreenKind;
  source: ScreenSource;
  symbols?: string[] | null;
  filters?: ScreenFilters | null;
  criteria?: string | null;
  as_of?: string | null;
  count: number;
  created_at?: string | null;
  updated_at?: string | null;
};

/** A screen handed over by the chart, before it has been named and saved.
 *  Shaped by charto's postMessage payload (cards.js `wireScreen`). */
export type PendingScreen = {
  symbols: string[];
  criteria: string;
  /** How the source screen ordered its rows. Carried for display only. */
  ranking?: string;
  as_of: string;
  matched: number;
  universe: number;
};

const BASE = process.env.NEXT_PUBLIC_PIVOT_API_BASE || "/api";
const TOKEN_KEY = "pivot_jwt";

function authHeaders(): Record<string, string> {
  let token: string | null = null;
  try {
    token = window.localStorage.getItem(TOKEN_KEY);
  } catch {
    /* localStorage may be denied in some embeds */
  }
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function call<T>(
  path: string,
  init?: RequestInit,
): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        ...(init?.headers || {}),
      },
    });
  } catch {
    return { error: { code: "network_error", message: "Could not reach Pivot." } };
  }
  let parsed: unknown = null;
  try {
    parsed = await res.json();
  } catch {
    parsed = null;
  }
  if (!res.ok) {
    const env = (parsed ?? {}) as {
      error?: { code?: string; message?: string };
      detail?: unknown;
    };
    return {
      error: {
        code: env.error?.code ?? `http_${res.status}`,
        message:
          env.error?.message ??
          (typeof env.detail === "string" ? env.detail : undefined) ??
          `Request failed with status ${res.status}`,
      },
    };
  }
  return { data: parsed as T };
}

export async function listScreens(): Promise<ApiResult<SavedScreen[]>> {
  const r = await call<{ screens: SavedScreen[] }>("/screener/screens");
  if (isError(r)) return r;
  return { data: r.data.screens ?? [] };
}

/** Save (or, on a name collision, update) a screen. The server upserts on
 *  (user, name), so re-saving under an existing name is an edit — which is
 *  what a user pressing Save twice means. */
export function saveScreen(body: {
  name: string;
  kind: ScreenKind;
  source: ScreenSource;
  symbols?: string[];
  filters?: ScreenFilters;
  criteria?: string;
  as_of?: string;
}): Promise<ApiResult<SavedScreen>> {
  return call<SavedScreen>("/screener/screens", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteScreen(id: number): Promise<ApiResult<{ deleted: number }>> {
  return call<{ deleted: number }>(`/screener/screens/${id}`, {
    method: "DELETE",
  });
}

/** Load a screen's rows through the Screener's OWN columns.
 *
 *  For a symbols screen this restricts the universe to the membership and
 *  prices it today; the endpoint reports any names it cannot serve in `note`
 *  (a screen off charto's matrix can name an index or a crypto pair, which is
 *  not in this service's equity universe) rather than returning short. For a
 *  filters screen it simply re-runs the query. */
export function loadScreenRows(
  screen: Pick<SavedScreen, "kind" | "symbols" | "filters">,
  extra: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<ApiResult<ScreenerStocksResponse>> {
  if (screen.kind === "symbols") {
    return getScreenerStocks(
      {
        symbols: (screen.symbols ?? []).join(","),
        limit: extra.limit ?? 300,
        offset: extra.offset ?? 0,
      },
      signal,
    );
  }
  const f = screen.filters ?? {};
  return getScreenerStocks(
    {
      sector: f.sector,
      mcap_tier: f.mcap_tier,
      pe_max: f.pe_max,
      roe_min: f.roe_min,
      filters: f.filters,
      sort_by: f.sort_by,
      sort_dir: f.sort_dir,
      limit: extra.limit ?? 60,
      offset: extra.offset ?? 0,
    },
    signal,
  );
}

// ── Handover from the chart ───────────────────────────────────────────
//
// The chart posts a screen; the Screener may not be mounted yet (the user is
// on the Chart tab when they press it). sessionStorage carries it across the
// tab switch — sessionStorage rather than localStorage because a handover is
// for THIS visit: a pending screen still sitting there in a new tab tomorrow
// would reopen a screen nobody asked for.

export const PENDING_KEY = "pivot.screener.pendingScreen.v1";

/** Fired after a screen is parked. A Screener that is already mounted (it
 *  stays mounted, hidden, after its first visit) reads it on this event; a
 *  first visit reads it on mount. Without the event every handover after the
 *  first opened the tab on the ordinary universe. */
export const PENDING_SCREEN_EVENT = "pivot:pending-screen";

export function putPendingScreen(s: PendingScreen): void {
  try {
    window.sessionStorage.setItem(PENDING_KEY, JSON.stringify(s));
  } catch {
    /* storage may be denied; the tab switch still happens */
  }
  window.dispatchEvent(new Event(PENDING_SCREEN_EVENT));
}

/** Read and CLEAR the pending screen — it is consumed once, by whichever
 *  mount gets there first. */
export function takePendingScreen(): PendingScreen | null {
  try {
    const raw = window.sessionStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    window.sessionStorage.removeItem(PENDING_KEY);
    const p = JSON.parse(raw) as PendingScreen;
    if (!p || !Array.isArray(p.symbols) || !p.symbols.length) return null;
    return p;
  } catch {
    return null;
  }
}
