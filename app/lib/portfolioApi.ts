/**
 * Portfolio performance API client.
 */

import type { ApiResult } from "@/lib/types";
import { getPortfolioPerformance as readPerformance } from "@/lib/api";

// ---------------------------------------------------------------------------
// Portfolio performance series — GET /api/portfolio/performance
//
// The performance router is declared with `prefix="/api/portfolio"` in the
// backend (`routers/portfolio_perf.py`), so its path uses the `/api` base.
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

/**
 * `GET /api/portfolio/performance?period=…` — real historical portfolio value
 * series for the chart. Defaults to `1Y` (matching the backend Query default).
 */
export function getPortfolioPerformance(
  period: PerformancePeriod = "1Y",
): Promise<ApiResult<PortfolioPerformance>> {
  return readPerformance(period);
}
