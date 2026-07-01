/**
 * portfolioApi — typed client for the NEW portfolio-scores endpoint.
 *
 * Lives in its own file (per task ownership) so it never collides with the
 * shared `lib/api.ts` client. It mirrors that client's conventions:
 *   - base URL from `NEXT_PUBLIC_PIVOT_API_BASE` (defaults to `/api`),
 *     with the trailing `/api` stripped because `/portfolio/*` is a LEGACY
 *     router mounted at the host root (same as getPortfolioSummary).
 *   - Bearer JWT read from `localStorage.pivot_jwt` (the same TOKEN_KEY
 *     AppBootstrap writes; matching liveQuoteManager.getToken()).
 *   - returns `Promise<ApiResult<T>>` so callers use the shared `isError`
 *     guard exactly like every other Pivot fetch.
 *
 * GET /portfolio/scores — three transparent, real-data-derived scores for the
 * current user's holdings. All three are `null` (with `reason: "no_holdings"`)
 * when the user has no holdings, so the UI must render an honest empty state.
 */

import type { ApiResult, ErrorBody } from "@/lib/types";

// ---------------------------------------------------------------------------
// Response shapes (from the backend contract for GET /portfolio/scores)
// ---------------------------------------------------------------------------

export type DiversificationScoreComponents = {
  n_holdings: number;
  n_sectors: number;
  top_holding_pct: number;
  top_sector_pct: number;
  hhi: number;
};

export type DiversificationScore = {
  score: number;
  components: DiversificationScoreComponents;
  explainer: string;
};

export type PortfolioScoreSubscores = {
  diversification: number;
  concentration_penalty: number;
  /** Present only when a real NAV series exists. */
  performance?: number;
};

export type PortfolioScoreWeights = {
  diversification: number;
  concentration_penalty: number;
  /** Present only when a real NAV series exists. */
  performance?: number;
};

export type PortfolioScoreComponents = {
  subscores: PortfolioScoreSubscores;
  weights: PortfolioScoreWeights;
  performance_available: boolean;
  total_return_pct: number | null;
};

export type PortfolioScore = {
  score: number;
  components: PortfolioScoreComponents;
  explainer: string;
};

export type CommunityScore = {
  score: number;
  percentile: number;
  /** Honest description of the comparison basis (a benchmark, not live peers). */
  basis: string;
  explainer: string;
};

export type PortfolioScoresResponse = {
  diversification_score: DiversificationScore | null;
  portfolio_score: PortfolioScore | null;
  community_score: CommunityScore | null;
  /** `null` on success; `"no_holdings"` when the user has no holdings. */
  reason: string | null;
};

// ---------------------------------------------------------------------------
// Minimal fetch (legacy base + bearer token), additive — no shared client edits
// ---------------------------------------------------------------------------

const DEFAULT_BASE = "/api";

/** Host root base (legacy routers like /portfolio live here, NOT under /api). */
function getLegacyBase(): string {
  const base =
    (typeof process !== "undefined" &&
      process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
    DEFAULT_BASE;
  return base.replace(/\/api\/?$/, "");
}

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem("pivot_jwt");
  } catch {
    return null;
  }
}

async function getLegacy<T>(path: string): Promise<ApiResult<T>> {
  const base = getLegacyBase();
  const sep = base.endsWith("/") || path.startsWith("/") ? "" : "/";
  const url = `${base}${sep}${path}`;
  const token = getToken();

  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(url, { method: "GET", headers, cache: "no-store" });
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

/** `GET /portfolio/scores` — diversification + portfolio + community scores. */
export function getPortfolioScores(): Promise<
  ApiResult<PortfolioScoresResponse>
> {
  return getLegacy<PortfolioScoresResponse>("/portfolio/scores");
}

// ---------------------------------------------------------------------------
// Portfolio performance series — GET /api/portfolio/performance
//
// NOTE the base: unlike `/portfolio/*` (the legacy root router above), the
// performance router is declared with `prefix="/api/portfolio"` in the backend
// (`routers/portfolio_perf.py`) and mounted with no extra prefix — so its real
// path lives UNDER `/api`. We therefore fetch it with the `/api` base (mirroring
// lib/api.ts's getBaseUrl), NOT the legacy-root helper.
// ---------------------------------------------------------------------------

/** Backend supported periods (yfinance-backed). UI ranges map onto these. */
export type PerformancePeriod = "1M" | "3M" | "6M" | "1Y" | "5Y";

/** One historical portfolio-value point: ISO timestamp + total value (₹). */
export type PerformancePoint = {
  t: string;
  v: number;
};

/** `GET /api/portfolio/performance` response (see PerformanceResponse model). */
export type PortfolioPerformance = {
  period: string;
  points: PerformancePoint[];
  starting_value: number;
  ending_value: number;
  total_return: number;
  total_return_pct: number;
};

/** `/api` base (workflows/agents live here, AND so does portfolio/performance). */
function getApiBase(): string {
  const base =
    (typeof process !== "undefined" &&
      process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
    DEFAULT_BASE;
  return base;
}

async function getApi<T>(path: string): Promise<ApiResult<T>> {
  const base = getApiBase();
  const sep = base.endsWith("/") || path.startsWith("/") ? "" : "/";
  const url = `${base}${sep}${path}`;
  const token = getToken();

  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(url, { method: "GET", headers, cache: "no-store" });
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

/**
 * `GET /api/portfolio/performance?period=…` — real historical portfolio value
 * series for the chart. Defaults to `1Y` (matching the backend Query default).
 */
export function getPortfolioPerformance(
  period: PerformancePeriod = "1Y",
): Promise<ApiResult<PortfolioPerformance>> {
  const qs = encodeURIComponent(period);
  return getApi<PortfolioPerformance>(`/portfolio/performance?period=${qs}`);
}
