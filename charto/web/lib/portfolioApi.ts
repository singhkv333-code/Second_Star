/**
 * Portfolio performance API client.
 */

import type { ApiResult, ErrorBody } from "@/lib/types";
import { getAccessToken } from "@/lib/authToken";

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

async function getLegacy<T>(path: string): Promise<ApiResult<T>> {
  const base = getLegacyBase();
  const sep = base.endsWith("/") || path.startsWith("/") ? "" : "/";
  const url = `${base}${sep}${path}`;
  const token = await getAccessToken();

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
  const token = await getAccessToken();

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
