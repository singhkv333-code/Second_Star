/**
 * agentsApi — feature-scoped REST calls for the Agents tab cluster.
 *
 * Wraps the workflow summary/performance/delete endpoints (mounted under
 * `/api/workflows/...`) and the user's registered option-strategies
 * (mounted bare at the root). Reuses the shared `request` / `requestLegacy`
 * wrappers from `lib/api.ts` so auth, base-url resolution, and the error
 * envelope behave identically to every other call in the app.
 *
 * No fabricated numbers here: these are pure transport functions. The
 * components decide how to render empty/zeroed payloads honestly.
 */

import { request, requestLegacy } from "@/lib/api";
import type { ApiResult } from "@/lib/types";

// ---------------------------------------------------------------------------
// GET /api/workflows/summary
// ---------------------------------------------------------------------------

export type WorkflowTrades6mo = {
  total: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
};

export type DailyPnlPoint = {
  /** YYYY-MM-DD */
  date: string;
  pnl: number;
};

export type StrategyReturn = {
  workflow_id: string;
  name: string;
  return_pct: number | null;
  /** Same sparkline/headline fields GET /workflows/{id}/performance serves,
   *  batched here for EVERY workflow so a card can render off this one
   *  summary response instead of firing its own per-card request. */
  series: WorkflowNavPoint[];
  run_count: number;
  success_rate: number | null;
  last_run_at: string | null;
  has_data: boolean;
};

export type WorkflowsSummary = {
  active_count: number;
  paused_count: number;
  draft_count: number;
  trades_6mo: WorkflowTrades6mo;
  daily_pnl: DailyPnlPoint[];
  strategy_returns: StrategyReturn[];
  total_pnl: number;
  has_data: boolean;
};

/** `GET /api/workflows/summary` — roster counts + 6-month scorecard + daily
 *  P&L series + per-active-agent returns, all computed on-read. */
export function getWorkflowsSummary(): Promise<ApiResult<WorkflowsSummary>> {
  return request<WorkflowsSummary>("/workflows/summary");
}

// ---------------------------------------------------------------------------
// GET /api/workflows/{id}/performance
// ---------------------------------------------------------------------------

export type WorkflowNavPoint = {
  /** YYYY-MM-DD */
  date: string;
  nav: number;
};

export type WorkflowPerformance = {
  series: WorkflowNavPoint[];
  return_pct: number | null;
  last_run_at: string | null;
  run_count: number;
  success_rate: number | null;
  has_data: boolean;
};

/** `GET /api/workflows/{id}/performance` — NAV sparkline + run stats for a
 *  single agent card. `has_data` false → render "No runs yet". */
export function getWorkflowPerformance(
  id: string,
): Promise<ApiResult<WorkflowPerformance>> {
  return request<WorkflowPerformance>(
    `/workflows/${encodeURIComponent(id)}/performance`,
  );
}

// ---------------------------------------------------------------------------
// DELETE /api/workflows/{id}
// ---------------------------------------------------------------------------

export type DeleteWorkflowResponse = {
  deleted: boolean;
  id: string;
};

/** `DELETE /api/workflows/{id}` — hard-delete the agent + children. 404 when
 *  not owned by the caller. */
export function deleteWorkflow(
  id: string,
): Promise<ApiResult<DeleteWorkflowResponse>> {
  return request<DeleteWorkflowResponse>(
    `/workflows/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// GET /users/option-strategies
//
// The backend list route returns `{ strategies: [...] }` (NOT `{ items }`)
// where each row is the full `serialize_option_strategy` shape — including
// legs[] and net_premium, which the trimmed register-response type omits.
// We model the real list shape here rather than reuse the partial type.
// ---------------------------------------------------------------------------

export type OptionStrategyLeg = {
  option_type: "CE" | "PE";
  side: "BUY" | "SELL";
  strike: number;
  tradingsymbol: string | null;
  qty_lots: number | null;
  lot_size: number | null;
  entry_mid: number | null;
  entry_iv: number | null;
};

export type RegisteredOptionStrategy = {
  id: string;
  underlying: string;
  segment: string | null;
  exchange: string | null;
  template: string;
  expiry: string;
  book: "paper" | "live";
  status: string;
  qty_lots: number;
  lot_size: number | null;
  net_premium: number | null;
  max_loss: number | null;
  max_profit: number | null;
  pop: number | null;
  capital_required: number | null;
  margin_estimate: number | null;
  net_greeks: Record<string, number> | null;
  critique_verdict: string | null;
  legs: OptionStrategyLeg[];
  created_at: string | null;
};

/** `GET /users/option-strategies` — this user's registered option strategies,
 *  most-recent first. */
export function listRegisteredOptionStrategies(): Promise<
  ApiResult<{ strategies: RegisteredOptionStrategy[] }>
> {
  return requestLegacy<{ strategies: RegisteredOptionStrategy[] }>(
    "/users/option-strategies",
  );
}

/** `POST /option-strategies/{id}/withdraw` — the delete-equivalent for a
 *  registered (not-yet-filled) option strategy intent. */
export function withdrawRegisteredOptionStrategy(
  id: string,
): Promise<ApiResult<{ success: boolean }>> {
  return requestLegacy<{ success: boolean }>(
    `/option-strategies/${encodeURIComponent(id)}/withdraw`,
    { method: "POST" },
  );
}

/** `POST /option-strategies/{id}/close` — square off (exit) every leg of an
 *  ACTIVE (filled) paper-book strategy at the live chain. */
export function closeOptionStrategy(id: string): Promise<
  ApiResult<{
    success: boolean;
    strategy: RegisteredOptionStrategy;
    execution: { success: boolean; fills: unknown[]; error: string | null } | null;
    error: string | null;
  }>
> {
  return requestLegacy(`/option-strategies/${encodeURIComponent(id)}/close`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Equity baskets — the user's own equity/ETF strategies (Agents → Strategies).
// Persisted on the legacy `strategies` table under strategy_type=equity_basket;
// mounted bare at /strategies (like option-strategies), so requestLegacy.
// ---------------------------------------------------------------------------

export type BasketWeighting = "equal" | "custom";

export type EquityBasketMember = {
  symbol: string;
  /** Percent 0-100. Server-normalised to sum 100. */
  weight: number;
  /** Resolved display name (falls back to the symbol server-side). */
  name?: string;
};

export type EquityBasket = {
  id: number;
  name: string;
  description: string | null;
  weighting: BasketWeighting;
  members: EquityBasketMember[];
  capital_inr: number | null;
  status: string;
  /** True when the basket has been TRADED and still holds an open position
   *  (drives the card's Deploy ⇄ Square-off toggle). Absent on older payloads
   *  → treat as not-deployed. */
  deployed?: boolean;
  created_at: string | null;
  updated_at: string | null;
};

export type EquityBasketInput = {
  name: string;
  description?: string | null;
  members: EquityBasketMember[];
  weighting: BasketWeighting;
  capital_inr?: number | null;
};

/** `GET /strategies/baskets` — this user's saved equity baskets. */
export function listEquityBaskets(): Promise<
  ApiResult<{ baskets: EquityBasket[] }>
> {
  return requestLegacy<{ baskets: EquityBasket[] }>("/strategies/baskets");
}

// Every basket card in a replayed conversation resolves its own saved state on
// mount; without a shared read they'd each fire a request for the same list.
// Short TTL + explicit invalidation on every mutation keeps it honest.
let _basketsCache: { at: number; inflight: Promise<ApiResult<{ baskets: EquityBasket[] }>> } | null =
  null;
const _BASKETS_TTL_MS = 30_000;

/** De-duplicated `GET /strategies/baskets` for read-only lookups. Surfaces
 *  that render the basket grid should keep using `listEquityBaskets`. */
export function listEquityBasketsCached(): Promise<ApiResult<{ baskets: EquityBasket[] }>> {
  const now = Date.now();
  if (!_basketsCache || now - _basketsCache.at > _BASKETS_TTL_MS) {
    _basketsCache = { at: now, inflight: listEquityBaskets() };
  }
  return _basketsCache.inflight;
}

/** Drop the cached list — called after any basket mutation. */
export function invalidateEquityBasketsCache(): void {
  _basketsCache = null;
}

/** `POST /strategies/baskets` — create an equity basket. */
export async function createEquityBasket(
  body: EquityBasketInput,
): Promise<ApiResult<EquityBasket>> {
  const res = await requestLegacy<EquityBasket>("/strategies/baskets", {
    method: "POST",
    body,
  });
  invalidateEquityBasketsCache();
  return res;
}

/** `PATCH /strategies/baskets/{id}` — edit a basket (partial). */
export async function updateEquityBasket(
  id: number,
  body: Partial<EquityBasketInput>,
): Promise<ApiResult<EquityBasket>> {
  const res = await requestLegacy<EquityBasket>(`/strategies/baskets/${id}`, {
    method: "PATCH",
    body,
  });
  invalidateEquityBasketsCache();
  return res;
}

/** `DELETE /strategies/baskets/{id}` — square off every held member, then
 *  delete the basket for good. */
export async function deleteEquityBasket(
  id: number,
): Promise<ApiResult<{ id: number; status: string; squareoff: BasketCloseResult }>> {
  const res = await requestLegacy<{ id: number; status: string; squareoff: BasketCloseResult }>(
    `/strategies/baskets/${id}`,
    { method: "DELETE" },
  );
  invalidateEquityBasketsCache();
  return res;
}

export type BasketCloseResult = {
  count: number;
  registered: BasketTradePlacedLeg[];
  skipped: BasketTradeSkip[];
};

/** `POST /strategies/baskets/{id}/close` — square off (sell) every member
 *  currently held in the paper/broker book. The basket itself is untouched. */
export function closeEquityBasket(
  id: number,
): Promise<ApiResult<BasketCloseResult>> {
  return requestLegacy<BasketCloseResult>(`/strategies/baskets/${id}/close`, {
    method: "POST",
  });
}

// ── Trade a basket ──────────────────────────────────────────────────────────

export type BasketTradeLeg = {
  symbol: string;
  quantity: number;
  est_price: number;
  est_cost: number;
};
export type BasketTradeSkip = { symbol: string; reason: string };

export type BasketTradeDryRun = {
  dry_run: true;
  capital_inr: number;
  est_total: number;
  legs: BasketTradeLeg[];
  skipped: BasketTradeSkip[];
};

export type BasketTradePlacedLeg = {
  id: number;
  symbol: string;
  transaction_type: string;
  quantity: number;
  status: string;
  placed_at: string;
};

export type BasketTradePlaced = {
  dry_run: false;
  routed_to: "broker" | "paper";
  count: number;
  est_total: number;
  registered: BasketTradePlacedLeg[];
  skipped: BasketTradeSkip[];
};

/**
 * `POST /strategies/baskets/{id}/trade` — size the basket to whole shares at
 * live prices and place BUY orders through the connected broker (or preview
 * with `dry_run`). Register-not-execute: needs a broker session (409 if none),
 * and while live execution is off the legs are REGISTERED not placed.
 */
export function tradeEquityBasket(
  id: number,
  body: { capital_inr?: number; dry_run: boolean },
): Promise<ApiResult<BasketTradeDryRun | BasketTradePlaced>> {
  return requestLegacy<BasketTradeDryRun | BasketTradePlaced>(
    `/strategies/baskets/${id}/trade`,
    { method: "POST", body },
  );
}

// ── Basket performance ──────────────────────────────────────────────────────

export type BasketPerformance = {
  series: WorkflowNavPoint[];
  return_pct: number | null;
  has_data: boolean;
};

/** `GET /strategies/baskets/{id}/performance` — live NAV sparkline + headline
 *  return for a basket, on-read from its linked forward-test idea. A basket
 *  that's never been traded has no idea yet, so this comes back
 *  `has_data: false` rather than 404. */
export function getBasketPerformance(
  id: number,
): Promise<ApiResult<BasketPerformance>> {
  return requestLegacy<BasketPerformance>(`/strategies/baskets/${id}/performance`);
}
