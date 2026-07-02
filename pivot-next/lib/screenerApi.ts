/**
 * screenerApi — typed client for the NEW screener endpoints.
 *
 * Lives in its own file (per task ownership) so it never collides with the
 * shared `lib/api.ts` client. It mirrors that client's conventions:
 *   - base URL from `NEXT_PUBLIC_PIVOT_API_BASE` (defaults to `/api`). The
 *     screener routes are mounted UNDER `/api` (prefix="/api/screener"), so we
 *     use the base as-is and DO NOT strip a trailing `/api` (unlike the legacy
 *     /portfolio router).
 *   - Bearer JWT read from `localStorage.pivot_jwt` (the same TOKEN_KEY
 *     AppBootstrap writes; matching liveQuoteManager.getToken() and
 *     portfolioApi.getToken()).
 *   - returns `Promise<ApiResult<T>>` so callers use the shared `isError`
 *     guard exactly like every other Pivot fetch.
 *
 * Backend contract (backend/routers/screener.py):
 *   GET /api/screener/stocks   → filterable/sortable grid (server-side)
 *   GET /api/screener/search   → symbol/name autosuggest over the universe
 *   GET /api/screener/sectors  → the sector filter rail
 *
 * Honest-data contract: `div_yield` and `one_year_pct` are ALWAYS null on the
 * stocks path (no source on this route — see `null_metrics`), and `pe`/`roe`
 * are null per-row when the financials DB can't serve them. The UI must render
 * an em-dash, never a fabricated value.
 */

import type { ApiResult, ErrorBody } from "@/lib/types";

// ---------------------------------------------------------------------------
// Response shapes (from the backend contract)
// ---------------------------------------------------------------------------

export type ScreenerStock = {
  symbol: string;
  name: string;
  /** canonical sector key, e.g. "private_bank" */
  sector: string;
  market_cap_cr: number | null;
  /** Last price (₹). Kite-primary when a live session exists, else delayed
   *  yfinance; null while the market-metrics cache is warming. */
  price: number | null;
  /** Day change (%), signed. Same source/nullability as `price`. */
  change_pct: number | null;
  pe: number | null;
  roe: number | null;
  /** 1-year price return (%), signed. Same source/nullability as `price`. */
  one_year_pct: number | null;
  /** No source on this path — always null (kept for contract stability). */
  div_yield: number | null;
  logo_url: string | null;
};

export type ScreenerStocksResponse = {
  count: number;
  results: ScreenerStock[];
  /** Row metrics this endpoint does not serve — render "—", not "screened out". */
  null_metrics: string[];
  /** Human-readable disclosure of ignored/unserved filters & sort fallbacks. */
  note: string;
};

export type ScreenerSearchResult = {
  symbol: string;
  name: string;
  sector: string | null;
  logo_url: string | null;
  has_fundamentals: boolean;
};

export type ScreenerSearchResponse = {
  results: ScreenerSearchResult[];
};

export type ScreenerSector = {
  /** canonical key, e.g. "private_bank" */
  sector: string;
  /** display label, e.g. "Private Bank" */
  label: string;
  count: number;
};

export type ScreenerSectorsResponse = {
  sectors: ScreenerSector[];
};

/** Server-side sort fields accepted by GET /api/screener/stocks. */
export type ScreenerSortBy = "market_cap_cr" | "pe" | "roe" | "symbol" | "name";

/** Market-cap tier accepted by GET /api/screener/stocks. */
export type ScreenerMcapTier = "large" | "mid" | "small";

export type ScreenerStocksParams = {
  sector?: string;
  mcap_tier?: ScreenerMcapTier;
  pe_max?: number;
  roe_min?: number;
  dy_min?: number;
  ret_min?: number;
  sort_by?: ScreenerSortBy;
  limit?: number;
};

// ---------------------------------------------------------------------------
// Minimal fetch (/api base + bearer token), additive — no shared client edits
// ---------------------------------------------------------------------------

const DEFAULT_BASE = "/api";

/** `/api` base — screener routes are mounted under it; do NOT strip. */
function getApiBase(): string {
  const base =
    (typeof process !== "undefined" &&
      process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
    DEFAULT_BASE;
  return base;
}

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem("pivot_jwt");
  } catch {
    return null;
  }
}

function buildUrl(
  path: string,
  query?: Record<string, string | number | undefined>,
): string {
  const base = getApiBase();
  const sep = base.endsWith("/") || path.startsWith("/") ? "" : "/";
  let url = `${base}${sep}${path}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") {
        params.append(k, String(v));
      }
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  return url;
}

async function getJson<T>(
  path: string,
  query?: Record<string, string | number | undefined>,
  signal?: AbortSignal,
): Promise<ApiResult<T>> {
  const url = buildUrl(path, query);
  const token = getToken();

  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(url, {
      method: "GET",
      headers,
      cache: "no-store",
      signal,
    });
  } catch (e) {
    // Surface aborts so callers can ignore them; other failures are network.
    if (e instanceof DOMException && e.name === "AbortError") {
      return { error: { code: "aborted", message: "Request aborted" } };
    }
    return {
      error: {
        code: "network_error",
        message: e instanceof Error ? e.message : "Network request failed",
      },
    };
  }

  const text = await res.text();
  let parsed: unknown = null;
  if (text.length > 0) {
    try {
      parsed = JSON.parse(text);
    } catch {
      return {
        error: {
          code: "internal_error",
          message: `Unexpected non-JSON response (status ${res.status})`,
        },
      };
    }
  }

  if (!res.ok) {
    // Accept both the canonical envelope and FastAPI's { detail } shape.
    const envelope = (parsed ?? {}) as {
      error?: Partial<ErrorBody>;
      detail?: unknown;
    };
    const err = envelope.error ?? {};
    const legacyMessage =
      typeof envelope.detail === "string" ? envelope.detail : undefined;
    return {
      error: {
        code: err.code ?? `http_${res.status}`,
        message:
          err.message ??
          legacyMessage ??
          `Request failed with status ${res.status}`,
      },
    };
  }

  return { data: parsed as T };
}

/** `GET /api/screener/stocks` — server-filtered/sorted stock grid. */
export function getScreenerStocks(
  params: ScreenerStocksParams = {},
  signal?: AbortSignal,
): Promise<ApiResult<ScreenerStocksResponse>> {
  return getJson<ScreenerStocksResponse>(
    "/screener/stocks",
    {
      sector: params.sector,
      mcap_tier: params.mcap_tier,
      pe_max: params.pe_max,
      roe_min: params.roe_min,
      dy_min: params.dy_min,
      ret_min: params.ret_min,
      sort_by: params.sort_by,
      limit: params.limit,
    },
    signal,
  );
}

/** `GET /api/screener/search` — symbol/name autosuggest across the universe. */
export function searchScreener(
  q: string,
  limit = 15,
  signal?: AbortSignal,
): Promise<ApiResult<ScreenerSearchResponse>> {
  return getJson<ScreenerSearchResponse>(
    "/screener/search",
    { q, limit },
    signal,
  );
}

/** `GET /api/screener/sectors` — sectors (with counts) for the filter rail. */
export function getScreenerSectors(
  signal?: AbortSignal,
): Promise<ApiResult<ScreenerSectorsResponse>> {
  return getJson<ScreenerSectorsResponse>("/screener/sectors", undefined, signal);
}
