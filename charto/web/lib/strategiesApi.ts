/**
 * strategiesApi — the saved rules behind the paper book.
 *
 * Charto's own surface, not Pivot's: a strategy here is a draft the chart's
 * execution mode built, stored in Charto's account database and evaluated by
 * Charto's own runtime against Charto's bars. It reuses `requestLegacy` for
 * the base URL, the bearer token and the error envelope, so it cannot drift
 * from the paper endpoints it sits beside.
 */

import { requestLegacy, type ApiRequestOptions } from "@/lib/api";
import type { ApiResult } from "@/lib/types";

export type StrategyState = "draft" | "armed" | "paused" | "retired";

export type StrategyReadback = {
  /** The entry condition in English, as the card printed it. */
  entry: string;
  /** The exit condition in English; empty when the strategy has no exit. */
  exit: string;
  description: string;
};

export type Strategy = {
  id: number;
  name: string;
  symbol: string;
  interval: string;
  side: string;
  quantity: number;
  state: StrategyState;
  note: string;
  /** True while the strategy is holding the position it opened. */
  in_position: boolean;
  entry_price: number | null;
  fire_count: number;
  has_exit: boolean;
  /** The last refusal or fault, verbatim. Empty when there is none. */
  last_error: string;
  created: string | null;
  readback: StrategyReadback;
};

export type StrategyEvent = {
  ts: string | null;
  bar_ts: string | null;
  kind: "entry" | "exit" | "reject" | "error";
  price: number | null;
  quantity: number | null;
  detail: string;
  order_id: string | null;
};

export type StrategyDetail = Strategy & { log: StrategyEvent[] };

/** `GET /strategies` — everything not retired, newest first. */
export function getStrategies(
  state?: StrategyState,
): Promise<ApiResult<{ strategies: Strategy[] }>> {
  const options: ApiRequestOptions = state ? { query: { state } } : {};
  return requestLegacy<{ strategies: Strategy[] }>("/strategies", options);
}

/** `GET /strategies/{id}` — one strategy plus its firing history. */
export function getStrategy(id: number): Promise<ApiResult<StrategyDetail>> {
  return requestLegacy<StrategyDetail>(`/strategies/${id}`);
}

/** `POST /strategies/{id}` — arm, pause, or rename. */
export function patchStrategy(
  id: number,
  body: { state?: StrategyState; name?: string; note?: string },
): Promise<ApiResult<StrategyDetail>> {
  return requestLegacy<StrategyDetail>(`/strategies/${id}`, {
    method: "POST",
    body,
  });
}

/**
 * `POST /strategies/{id}/delete` — retire it.
 *
 * Retire, never erase: the strategy is the provenance of every fill it made,
 * and those fills are what actually happened.
 */
export function retireStrategy(
  id: number,
): Promise<ApiResult<StrategyDetail>> {
  return requestLegacy<StrategyDetail>(`/strategies/${id}/delete`, {
    method: "POST",
    body: {},
  });
}
