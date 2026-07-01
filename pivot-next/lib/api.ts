/**
 * Typed REST client for the Pivot Agent System backend.
 *
 * Every public function returns `Promise<ApiResult<T>>` per docs/API_CONTRACT.md §2:
 * - `{ data }` on 2xx
 * - `{ error: ErrorBody }` on a backend error envelope (non-2xx)
 * - throws on network / parse failure (callers `try/catch` if relevant)
 *
 * Design notes:
 * - Single source of base URL: `NEXT_PUBLIC_PIVOT_API_BASE` (defaults to `/api`).
 * - Auth token, when present, is read from a swappable provider (set via
 *   `setAuthTokenProvider`). The legacy Vite app reads from a cookie/storage;
 *   this layer stays storage-agnostic so we can plug into next-auth or a
 *   server action later.
 * - The step-type catalog falls back to `MOCK_CATALOG` until backend ships
 *   `GET /api/step-types`. Toggle via `setStepTypesSource("real" | "mock")`.
 */

import { MOCK_CATALOG } from "@/lib/mock-catalog";
import type {
  ApiResult,
  Approval,
  ApprovalDecisionRequest,
  BrokerAutomationRequest,
  BrokerCredentialsRequest,
  BrokerDisconnectResponse,
  BrokerHoldingsResponse,
  BrokerLoginUrl,
  BrokersResponse,
  BrokerStatus,
  CompareResult,
  CreateWorkflowRequest,
  Diagnostic,
  ErrorBody,
  ExpressionScores,
  IpoApplicationsListResponse,
  IpoCalendarResponse,
  IpoRegisterRequest,
  IpoRegisterResponse,
  IpoSubscriptionResponse,
  IpoWithdrawResponse,
  OptionStrategyComputeRequest,
  OptionStrategyComputeResponse,
  OptionChainSliceResponse,
  OptionStrategyRegisterRequest,
  OptionStrategyRegisterResponse,
  Paginated,
  PaperIpoAllocation,
  Run,
  RunSummary,
  StepTypeCatalog,
  UpdateWorkflowRequest,
  ViewDetail,
  ViewSummary,
  Workflow,
  WorkflowStatus,
  WorkflowSummary,
} from "@/lib/types";
import { isError } from "@/lib/types";
import type { DslNode, DslSchema, DslDescribeResult } from "@/lib/types";
import { getTradingMode } from "@/lib/trading-mode";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const DEFAULT_BASE = "/api";

function getBaseUrl(): string {
  // Read at call time so tests / SSR can override.
  // NEXT_PUBLIC_PIVOT_API_BASE (e.g. http://127.0.0.1:8000/api) is set in
  // .env.local for the dev environment; falls back to the relative path so
  // the app still works behind a reverse proxy in production.
  return (
    (typeof process !== "undefined" &&
      process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
    DEFAULT_BASE
  );
}

type AuthTokenProvider = () => string | null | Promise<string | null>;
let authTokenProvider: AuthTokenProvider = () => null;

export function setAuthTokenProvider(provider: AuthTokenProvider): void {
  authTokenProvider = provider;
}

type StepTypesSource = "mock" | "real";
// Default to "real" — the backend now ships GET /api/step-types with compat + group.
// Tests and offline dev can call setStepTypesSource("mock") to pin to the inline catalog.
let stepTypesSource: StepTypesSource = "real";

/**
 * Toggle between the inline mock catalog and the real `GET /api/step-types`
 * endpoint. Defaults to "real" since the backend ships compat + group now.
 * Call setStepTypesSource("mock") in tests or offline dev.
 */
export function setStepTypesSource(source: StepTypesSource): void {
  stepTypesSource = source;
}

/**
 * Single global toggle that switches every Day 2 mock surface (catalog,
 * run stream, …) between in-memory simulators and live backend wires.
 */
export type BackendSource = "mock" | "real";
let backendSource: BackendSource = "real";

export function setBackendSource(source: BackendSource): void {
  backendSource = source;
  setStepTypesSource(source);
}

export function getBackendSource(): BackendSource {
  return backendSource;
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  query?: Record<string, string | number | undefined>;
  /** Optional idempotency key for mutating endpoints (API_CONTRACT.md §1). */
  idempotencyKey?: string;
};

function buildUrl(
  base: string,
  path: string,
  query: RequestOptions["query"],
): string {
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

/**
 * Legacy backend base — the existing Pivot routers (auth, portfolio,
 * orders, etc.) live at the root, not under `/api`. Stripping the
 * trailing `/api` from `getBaseUrl()` lands at the right host.
 */
function getLegacyBase(): string {
  return getBaseUrl().replace(/\/api\/?$/, "");
}

// Exported so feature-scoped API modules (e.g. lib/agentsApi.ts) can reuse
// the exact auth/base/error-envelope handling without re-implementing the
// fetch wrapper or duplicating the token plumbing. `request` targets the
// /api Agent-System mount; `requestLegacy` targets the bare root mount.
export type ApiRequestOptions = RequestOptions;

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  return _doRequest<T>(getBaseUrl(), path, options);
}

export async function requestLegacy<T>(
  path: string,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  return _doRequest<T>(getLegacyBase(), path, options);
}

async function _doRequest<T>(
  base: string,
  path: string,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  const url = buildUrl(base, path, options.query);
  const token = await authTokenProvider();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...(options.headers ?? {}),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  const res = await fetch(url, {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
  });

  // 204 No Content
  if (res.status === 204) {
    return { data: undefined as unknown as T };
  }

  const text = await res.text();
  let parsed: unknown = null;
  if (text.length > 0) {
    try {
      parsed = JSON.parse(text);
    } catch {
      // Non-JSON body is unexpected from this backend; surface as a synthetic error.
      return {
        error: {
          code: "internal_error",
          message: `Unexpected non-JSON response (status ${res.status})`,
        },
      };
    }
  }

  if (!res.ok) {
    // Token expired / invalid → attempt refresh once, then redirect to /login.
    if (res.status === 401 && typeof window !== "undefined") {
      // Guard against infinite loop: if this 401 came from the refresh
      // endpoint itself, just clear and redirect without retrying.
      const isRefreshPath =
        path === "/auth/refresh" || path.endsWith("/auth/refresh");
      if (!isRefreshPath) {
        try {
          const refreshed = await _tryRefresh();
          if (refreshed) {
            // Retry the original request once with the new token.
            return _doRequest<T>(base, path, options);
          }
        } catch {
          // fall through to clearToken + redirect
        }
      }
      clearToken();
      window.location.href = "/login";
      // Return a placeholder — the redirect will navigate away before
      // the caller can act on this, but TypeScript needs a return value.
      return {
        error: {
          code: "http_401",
          message: "Session expired. Redirecting to login.",
        },
      };
    }
    // Two body shapes from this backend:
    //   - Canonical envelope (Agent System routes under /api/*):
    //       { error: { code, message, details } }
    //   - FastAPI default (legacy routes like /auth, /kite, /orders, /portfolio):
    //       { detail: "string" }  or  { detail: [pydantic-style errors] }
    // We accept either so legacy errors surface their real message.
    const envelope = (parsed ?? {}) as {
      error?: Partial<ErrorBody>;
      detail?: unknown;
    };
    const err = envelope.error ?? {};
    let legacyMessage: string | undefined;
    if (typeof envelope.detail === "string") {
      legacyMessage = envelope.detail;
    } else if (Array.isArray(envelope.detail) && envelope.detail.length > 0) {
      const first = envelope.detail[0] as { msg?: string } | undefined;
      legacyMessage = first?.msg;
    }
    return {
      error: {
        code: err.code ?? `http_${res.status}`,
        message:
          err.message ??
          legacyMessage ??
          `Request failed with status ${res.status}`,
        details:
          err.details ??
          (envelope.detail !== undefined && typeof envelope.detail !== "string"
            ? (envelope.detail as Record<string, unknown>)
            : undefined),
      },
    };
  }

  return { data: parsed as T };
}

// ---------------------------------------------------------------------------
// Workflows
// ---------------------------------------------------------------------------

export function listWorkflows(params?: {
  status?: WorkflowStatus | WorkflowStatus[];
  limit?: number;
  cursor?: string;
}): Promise<ApiResult<Paginated<WorkflowSummary>>> {
  const status = Array.isArray(params?.status)
    ? params!.status.join(",")
    : params?.status;
  return request<Paginated<WorkflowSummary>>("/workflows", {
    query: { status, limit: params?.limit, cursor: params?.cursor },
  });
}

export function getWorkflow(id: string): Promise<ApiResult<Workflow>> {
  return request<Workflow>(`/workflows/${encodeURIComponent(id)}`);
}

export function createWorkflow(
  body: CreateWorkflowRequest,
  opts?: { idempotencyKey?: string },
): Promise<ApiResult<Workflow>> {
  return request<Workflow>("/workflows", {
    method: "POST",
    body,
    idempotencyKey: opts?.idempotencyKey,
  });
}

export function updateWorkflow(
  id: string,
  body: UpdateWorkflowRequest,
): Promise<ApiResult<Workflow>> {
  return request<Workflow>(`/workflows/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
  });
}

export function activateWorkflow(id: string): Promise<ApiResult<Workflow>> {
  return request<Workflow>(
    `/workflows/${encodeURIComponent(id)}/activate`,
    { method: "POST" },
  );
}

export function pauseWorkflow(id: string): Promise<ApiResult<Workflow>> {
  return request<Workflow>(`/workflows/${encodeURIComponent(id)}/pause`, {
    method: "POST",
  });
}

export function archiveWorkflow(id: string): Promise<ApiResult<Workflow>> {
  return request<Workflow>(
    `/workflows/${encodeURIComponent(id)}/archive`,
    { method: "POST" },
  );
}

export function runWorkflow(
  id: string,
  opts?: { idempotencyKey?: string },
): Promise<ApiResult<{ run_id: string }>> {
  return request<{ run_id: string }>(
    `/workflows/${encodeURIComponent(id)}/run`,
    { method: "POST", idempotencyKey: opts?.idempotencyKey },
  );
}

/**
 * Eligible response — workflow can be replayed historically. The shape
 * mirrors the indicator backtest payload so the existing chart card
 * renders without changes.
 */
export type BacktestDraftEligible = {
  eligible: true;
  warnings: string[];
  _render_hint: "indicator_backtest_chart";
  symbol: string;
  indicator: string;
  indicator_period: number;
  operator: string;
  threshold: number;
  period_label: string;
  price_curve: { t: string; v: number }[];
  equity_curve: { t: string; v: number }[];
  indicator_curve: { t: string; v: number }[];
  signals: Array<{
    t: string;
    side: string;
    price: number;
    qty?: number;
  }>;
  metrics: {
    total_return_pct: number;
    cagr_pct: number;
    max_drawdown_pct: number;
    n_trades: number;
  };
  bench_buy_hold_return_pct: number;
  summary: string;
};

export type BacktestDraftIneligible = {
  eligible: false;
  reason: string;
  warnings: string[];
};

export type BacktestDraftResponse =
  | BacktestDraftEligible
  | BacktestDraftIneligible;

/**
 * Backtest a workflow draft against historical bars. Used by the
 * "Backtest this agent" button on the WorkflowDraftCard. Returns the
 * same chart payload shape the indicator backtester does.
 */
export function backtestDraftWorkflow(body: {
  name: string;
  description?: string | null;
  steps: Array<{ step_type: string; label: string | null; config: Record<string, unknown> }>;
  period?: string;
}): Promise<ApiResult<BacktestDraftResponse>> {
  return request<BacktestDraftResponse>("/workflows/backtest-draft", {
    method: "POST",
    body,
  });
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

export function listRuns(
  workflowId: string,
  params?: { status?: string; limit?: number; cursor?: string },
): Promise<ApiResult<Paginated<RunSummary>>> {
  return request<Paginated<RunSummary>>(
    `/workflows/${encodeURIComponent(workflowId)}/runs`,
    { query: params },
  );
}

export function getRun(id: string): Promise<ApiResult<Run>> {
  return request<Run>(`/runs/${encodeURIComponent(id)}`);
}

export function cancelRun(
  id: string,
): Promise<
  ApiResult<{ id: string; status: "cancelled"; finished_at: string }>
> {
  return request(`/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

export function listPendingApprovals(
  runId: string,
): Promise<ApiResult<{ items: Approval[] }>> {
  return request<{ items: Approval[] }>(
    `/runs/${encodeURIComponent(runId)}/approvals/pending`,
  );
}

export function decideApproval(
  approvalId: string,
  body: ApprovalDecisionRequest,
): Promise<
  ApiResult<{ id: string; decision: string; decided_at: string }>
> {
  return request(`/approvals/${encodeURIComponent(approvalId)}/decision`, {
    method: "POST",
    body,
  });
}

// ---------------------------------------------------------------------------
// Step type catalog (mock or real, configured via setStepTypesSource)
// ---------------------------------------------------------------------------

let cachedCatalog: { fetchedAt: number; data: StepTypeCatalog } | null = null;
const CATALOG_TTL_MS = 5 * 60 * 1000; // 5 min, per API_CONTRACT.md §8.1

export function getStepTypes(opts?: {
  forceRefresh?: boolean;
}): Promise<ApiResult<StepTypeCatalog>> {
  const now = Date.now();
  if (
    !opts?.forceRefresh &&
    cachedCatalog &&
    now - cachedCatalog.fetchedAt < CATALOG_TTL_MS
  ) {
    return Promise.resolve({ data: cachedCatalog.data });
  }

  if (stepTypesSource === "mock") {
    cachedCatalog = { fetchedAt: now, data: MOCK_CATALOG };
    return Promise.resolve({ data: MOCK_CATALOG });
  }

  // Real endpoint — gracefully fall back to the mock when the backend is
  // unreachable or returns an error (offline dev / unit tests).
  return request<StepTypeCatalog>("/step-types").then((result) => {
    if (!("error" in result)) {
      cachedCatalog = { fetchedAt: now, data: result.data };
      return result;
    }
    // Backend returned an error — fall back to the mock catalog so the editor
    // stays usable. The error is swallowed (the mock is a faithful mirror of the
    // real shape) but we warn so a developer notices the catalog may be stale
    // relative to a freshly-deployed backend.
    console.warn(
      "getStepTypes: backend /step-types returned an error; falling back to the " +
        `mock catalog (version ${MOCK_CATALOG.catalog_version}). Error:`,
      result.error,
    );
    cachedCatalog = { fetchedAt: now, data: MOCK_CATALOG };
    return { data: MOCK_CATALOG };
  });
}

/** Test helper — clears the in-memory catalog cache. */
export function _clearCatalogCache(): void {
  cachedCatalog = null;
}

// ---------------------------------------------------------------------------
// Workflow lint — POST /api/workflows/lint
// ---------------------------------------------------------------------------

/**
 * Request body for `POST /api/workflows/lint`.
 * Steps are the minimal shape the linter needs — no `id` or `step_index`
 * required; the engine assigns indices from the array order.
 */
export type LintWorkflowRequest = {
  steps: Array<{
    step_type: string;
    label?: string | null;
    config: Record<string, unknown>;
  }>;
  /**
   * Ambient state — tells the linter what the engine knows about the user's
   * live book so position/order requirements don't false-positive.
   */
  ambient?: {
    /** Symbols the user currently holds (satisfied "position" requirements). */
    held_symbols?: string[];
    /** True when the user has resting orders (satisfies "pending_orders"). */
    has_pending_orders?: boolean;
  };
};

/**
 * `POST /api/workflows/lint`
 *
 * Runs three passes — structural → ref type-check → capability — and returns
 * a list of `Diagnostic` objects sorted by (step_index, severity). Errors
 * block activation; warnings and info never do.
 *
 * The frontend calls this on a ~250 ms debounce after every edit so the
 * authoritative backend rules (ref-type mismatches, unknown step types) are
 * always reflected. The picker's client-side capability mirror is only for
 * instant bucket classification before the debounce fires.
 */
export function lintWorkflow(
  steps: LintWorkflowRequest["steps"],
  ambient?: LintWorkflowRequest["ambient"],
): Promise<ApiResult<{ diagnostics: Diagnostic[] }>> {
  const body: LintWorkflowRequest = ambient ? { steps, ambient } : { steps };
  return request<{ diagnostics: Diagnostic[] }>("/workflows/lint", {
    method: "POST",
    body,
  });
}

// ---------------------------------------------------------------------------
// DSL condition-tree builder helpers (ConditionBuilder)
//   GET  /api/workflows/dsl/schema    — operand-picker metadata (cached)
//   POST /api/workflows/dsl/describe  — english readback of one tree
// ---------------------------------------------------------------------------

// The schema is static per backend deploy, so cache the first success for the
// session (the use-dsl-schema hook calls this on every editor mount). Errors
// are never cached.
let _dslSchemaCache: DslSchema | null = null;

// Builder helpers degrade gracefully: `request` throws on network/parse
// failure, but the ConditionBuilder must never crash the editor over a
// transient backend hiccup — it just loses the live readback / falls back to
// the JSON hatch. So both helpers swallow throws into the error envelope.
function _dslNetworkError(e: unknown): ErrorBody {
  return {
    code: "internal_error",
    message: e instanceof Error ? e.message : "DSL request failed",
  };
}

export async function getDslSchema(): Promise<ApiResult<DslSchema>> {
  if (_dslSchemaCache) return { data: _dslSchemaCache };
  try {
    const result = await request<DslSchema>("/workflows/dsl/schema");
    if (!isError(result)) _dslSchemaCache = result.data;
    return result;
  } catch (e) {
    return { error: _dslNetworkError(e) };
  }
}

export async function describeDsl(
  tree: DslNode,
  mode: "entry" | "exit",
): Promise<ApiResult<DslDescribeResult>> {
  try {
    return await request<DslDescribeResult>("/workflows/dsl/describe", {
      method: "POST",
      body: { tree, mode },
    });
  } catch (e) {
    return { error: _dslNetworkError(e) };
  }
}

// ---------------------------------------------------------------------------
// Scheduled runs (Calendar tab — API_CONTRACT.md §6.5)
// ---------------------------------------------------------------------------

export type ScheduledRun = {
  workflow_id: string;
  workflow_name: string;
  trigger_type: "trigger.schedule" | "trigger.event";
  /** ISO 8601 UTC */
  fire_time: string;
  /** Pre-formatted in trigger's tz, e.g. "3:55 PM IST" */
  fire_time_local: string;
};

export type ScheduledRunsResponse = { items: ScheduledRun[] };

// ---------------------------------------------------------------------------
// Portfolio (legacy /portfolio/* endpoints — NOT under /api)
// ---------------------------------------------------------------------------

export type Holding = {
  tradingsymbol: string;
  exchange: string;
  quantity: number;
  average_price: number;
  last_price: number;
  pnl: number;
  day_change: number;
  day_change_percentage: number;
};

export type PortfolioSummary = {
  total_value: number;
  invested_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  day_pnl: number;
  num_holdings: number;
};

/** `GET /portfolio/summary` — backed by Kite (mock when KITE_API_KEY is empty).
 *  In paper mode this reads the paper book and adapts it to the SAME shape so
 *  every consumer (metric strip, Portfolio tab) renders identically. */
export function getPortfolioSummary(): Promise<ApiResult<PortfolioSummary>> {
  if (getTradingMode() === "paper") {
    return getPaperSummary().then((r) =>
      isError(r) ? r : { data: adaptPaperSummary(r.data) },
    );
  }
  return requestLegacy<PortfolioSummary>("/portfolio/summary");
}

/** `GET /portfolio/holdings` — list of Holdings (mock data in test mode).
 *  In paper mode this reads the paper positions, adapted to Holding[]. */
export function getPortfolioHoldings(): Promise<ApiResult<Holding[]>> {
  if (getTradingMode() === "paper") {
    return getPaperHoldings().then((r) =>
      isError(r) ? r : { data: r.data.map(adaptPaperHolding) },
    );
  }
  return requestLegacy<Holding[]>("/portfolio/holdings");
}

// Paper → real shape adapters. The goal is visual parity: the paper book's
// fields map onto the real Portfolio/Holding contracts. Fields with no paper
// equivalent are derived or defaulted (never faked into a wrong number).
function adaptPaperSummary(p: PaperSummary): PortfolioSummary {
  if (!p.exists) {
    // Fresh paper book reads as an empty portfolio, not an error.
    return {
      total_value: 0,
      invested_value: 0,
      total_pnl: 0,
      total_pnl_pct: 0,
      day_pnl: 0,
      num_holdings: 0,
    };
  }
  return {
    total_value: p.nav, // NAV = cash + reserved + positions market value
    invested_value: p.invested,
    total_pnl: p.total_pnl,
    total_pnl_pct: p.total_pnl_pct,
    day_pnl: p.day_pnl,
    num_holdings: p.num_positions,
  };
}

function adaptPaperHolding(h: PaperHolding): Holding {
  return {
    tradingsymbol: h.symbol,
    exchange: "NSE", // paper book has no exchange field; it is NSE-only
    quantity: h.quantity,
    average_price: h.avg_cost,
    last_price: h.last_price ?? h.avg_cost, // unmarked lot → book cost
    pnl: h.unrealized_pnl,
    day_change: h.day_pnl,
    day_change_percentage:
      h.invested !== 0 ? (h.day_pnl / h.invested) * 100 : 0,
  };
}

// ---------------------------------------------------------------------------
// Financials — Moneycontrol-derived `financials` Postgres DB.
// GET /api/financials/{symbol} returns company metadata + latest snapshot
// of every named field + multi-year history for headline P&L lines. The
// stock detail page falls back to its placeholder rendering when
// `available === false`.
// ---------------------------------------------------------------------------

export type FinancialsCompany = {
  sc_id: string;
  name: string;
  nse_symbol: string | null;
  bse_code: string | null;
  ticker: string | null;
  sector: string | null;
  industry_slug: string | null;
  market_cap: number | null;
  is_active: boolean;
};

export type FinancialsLatestValue = {
  value: number;
  period_end: string | null;
  period_label: string;
  line_item: string;
  unit: string | null;
  basis: string;
  /** Which data source produced this field. Present when the yfinance fallback is used. */
  source?: "moneycontrol" | "yfinance";
};

export type FinancialsHistoryPoint = {
  period_end: string | null;
  period_label: string;
  value: number | null;
  unit: string | null;
};

export type FinancialsResponse = {
  available: boolean;
  company: FinancialsCompany | null;
  latest: Record<string, FinancialsLatestValue | null>;
  history: Record<string, FinancialsHistoryPoint[]>;
  source: string;
};

/** `GET /api/financials/{symbol}` — fundamentals from the Moneycontrol DB. */
export function getFinancials(symbol: string): Promise<ApiResult<FinancialsResponse>> {
  return request<FinancialsResponse>(`/financials/${encodeURIComponent(symbol)}`);
}

// ---------------------------------------------------------------------------
// Orders — chat-confirm register flow (POST /orders/register)
//
// v1 design: chat builds a LogicCard; user clicks "Confirm & register";
// payload is POSTed here; backend writes a TradeLog row with
// status="registered" and source="chat-confirm". No broker call. The
// resulting row(s) appear in /orders/history immediately.
// ---------------------------------------------------------------------------

export type OrderRegisterLeg = {
  symbol: string;
  exchange?: string;
  transaction_type: "BUY" | "SELL";
  order_type: "MARKET" | "LIMIT" | "GTT" | "SL" | "OCO";
  quantity: number;
  price?: number | null;
  trigger_price?: number | null;
  product?: string;
};

export type OrderRegisterRequest = (
  | OrderRegisterLeg
  | { basket: true; legs: OrderRegisterLeg[] }
) & {
  // Chat session id — when the account is in paper mode the order fills into
  // the paper book and attributes to this conversation's forward-test idea.
  conversation_id?: string;
};

export type RegisteredOrder = {
  id: number;
  symbol: string;
  exchange: string;
  transaction_type: string;
  order_type: string;
  quantity: number;
  price: number | null;
  trigger_price: number | null;
  status: string;
  placed_at: string;
};

export type RegisterOrderResponse =
  | RegisteredOrder
  | { registered: RegisteredOrder[]; count: number };

/** `POST /orders/register` — persist a chat LogicCard intent. In paper mode
 *  the backend also routes it through the paper broker (fills the paper book). */
export function registerOrder(
  body: OrderRegisterRequest,
): Promise<ApiResult<RegisterOrderResponse>> {
  return requestLegacy<RegisterOrderResponse>("/orders/register", {
    method: "POST",
    body,
  });
}

export type OrderHistoryRow = {
  id: number;
  symbol: string;
  action: string;
  quantity: number;
  status: string;
  placed_at: string;
};

/** `GET /orders/history` — most recent registered/executed orders. In paper
 *  mode this returns the paper fills journal adapted to the same row shape. */
export function getOrderHistory(
  limit = 20,
): Promise<ApiResult<OrderHistoryRow[]>> {
  if (getTradingMode() === "paper") {
    return getPaperFills(limit).then((r) =>
      isError(r) ? r : { data: r.data.map(adaptPaperFill) },
    );
  }
  return requestLegacy<OrderHistoryRow[]>("/orders/history", {
    query: { limit },
  });
}

function adaptPaperFill(f: PaperFillRow): OrderHistoryRow {
  return {
    id: Number(f.id) || 0, // paper id is a string; OrderHistoryRow.id is number
    symbol: f.symbol,
    action: f.side, // "BUY" | "SELL"
    quantity: f.quantity,
    status: "filled", // the fills journal contains executed fills only
    placed_at: f.filled_at ?? "",
  };
}

// ── Account trading mode (real/live vs paper) ─────────────────────────────
// Drives the backend's per-account mode, which `should_use_paper` reads to
// route buys/sells. The UI term 'real' maps to the backend's 'live'.
export type BackendMode = "live" | "paper";
export type AccountMode = { mode: BackendMode };

/** `GET /paper/account/mode` — the backend's current trading mode. */
export function getAccountMode(): Promise<ApiResult<AccountMode>> {
  return requestLegacy<AccountMode>("/paper/account/mode");
}

/** `POST /paper/account/mode` — set the backend trading mode. Isolates
 *  buys/sells to the paper book when 'paper'; leaves the real path in 'live'. */
export function setAccountMode(
  mode: BackendMode,
): Promise<ApiResult<AccountMode>> {
  return requestLegacy<AccountMode>("/paper/account/mode", {
    method: "POST",
    body: { mode },
  });
}

/**
 * `GET /api/workflows/scheduled-runs?from=...&to=...`
 *
 * Returns upcoming fire times for all active trigger.schedule workflows in
 * [from, to]. Backend cap: 500 items, window ≤ 90 days.
 */
export function getScheduledRuns(params: {
  from: string;
  to: string;
}): Promise<ApiResult<ScheduledRunsResponse>> {
  return request<ScheduledRunsResponse>("/workflows/scheduled-runs", {
    query: { from: params.from, to: params.to },
  });
}

// ---------------------------------------------------------------------------
// Markets endpoints (NEW for redesign — /api/markets/*)
// ---------------------------------------------------------------------------

export type IndexQuote = {
  name: string;
  symbol: string;
  value: number;
  change: number;
  change_pct: number;
  last_updated: string;
};

export type IndicesResponse = { items: IndexQuote[] };

export type StockQuote = {
  symbol: string;
  exchange: string;
  name: string;
  ltp: number;
  change: number;
  change_pct: number;
  open: number;
  high: number;
  low: number;
  /** Most-recent daily close (previous session). Use for "Prev Close" display. */
  prev_close: number;
  /** @deprecated Backend returns `prev_close`; `close` is no longer present. */
  close?: number;
  /** 52-week high price. */
  w52_high: number;
  /** 52-week low price. */
  w52_low: number;
  volume: number;
  market_cap: number | null;
  pe_ratio: number | null;
  sector: string | null;
  /** Company logo URL (img.logo.dev), or null → render a monogram fallback. */
  logo_url?: string | null;
  /** Phase 2: true when the quote came from Kite (WS or REST). */
  live?: boolean;
  /** Phase 2: which data source produced this quote. */
  source?: "kite_ws" | "kite_rest" | "yfinance";
};

export type SparklinePoint = { t: string; v: number };

export type SparklineResponse = {
  symbol: string;
  range: string;
  interval: string;
  points: SparklinePoint[];
};

export type SparklineRange = "1D" | "1W" | "1M" | "6M" | "1Y" | "5Y";

/** `GET /api/markets/indices` — NIFTY 50, SENSEX, BANK NIFTY, NIFTY MIDCAP 100. 503 if yfinance down. */
export function getMarketIndices(): Promise<ApiResult<IndicesResponse>> {
  return request<IndicesResponse>("/markets/indices");
}

/** `GET /api/markets/quote/{symbol}?exchange=NSE|BSE` — full StockQuote. 404 if unknown. */
export function getStockQuote(
  symbol: string,
  exchange?: "NSE" | "BSE",
): Promise<ApiResult<StockQuote>> {
  return request<StockQuote>(`/markets/quote/${encodeURIComponent(symbol)}`, {
    query: exchange ? { exchange } : undefined,
  });
}

/** `GET /api/markets/sparkline/{symbol}?range=1D|1W|1M|6M|1Y|5Y` — historical close series. */
export function getSparkline(
  symbol: string,
  range: SparklineRange = "1M",
): Promise<ApiResult<SparklineResponse>> {
  return request<SparklineResponse>(
    `/markets/sparkline/${encodeURIComponent(symbol)}`,
    { query: { range } },
  );
}

// OHLCV bars for the TradingView candlestick chart. Kite-primary, yfinance
// fallback — `source` tells the UI which fed the bars (honest tagging).
export type OhlcBar = {
  t: string; // ISO timestamp
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
};

export type OhlcResponse = {
  symbol: string;
  range: string;
  interval: string;
  source: "kite" | "yfinance";
  bars: OhlcBar[];
};

/** `GET /api/markets/ohlc/{symbol}?range=...&exchange=NSE|BSE` — OHLCV bars for candlesticks. */
export function getOhlc(
  symbol: string,
  range: SparklineRange = "6M",
  exchange?: "NSE" | "BSE",
): Promise<ApiResult<OhlcResponse>> {
  return request<OhlcResponse>(
    `/markets/ohlc/${encodeURIComponent(symbol)}`,
    { query: exchange ? { range, exchange } : { range } },
  );
}

// ---------------------------------------------------------------------------
// Company search — `GET /api/companies/search?q=<str>&limit=10`
// Returns a ranked list of symbols matching the query for use in search
// autosuggest dropdowns. Sector and has_fundamentals let the UI surface
// richer context inline.
// ---------------------------------------------------------------------------

export type CompanySearchResult = {
  symbol: string;
  name: string;
  sector: string | null;
  has_fundamentals: boolean;
  /** Company logo URL (img.logo.dev), or null → render a monogram fallback. */
  logo_url?: string | null;
};

export type CompanySearchResponse = {
  results: CompanySearchResult[];
};

/** `GET /api/companies/search?q=<str>&limit=10` — ranked symbol search. */
export function searchCompanies(
  q: string,
  limit = 10,
): Promise<ApiResult<CompanySearchResponse>> {
  return request<CompanySearchResponse>("/companies/search", {
    query: { q, limit },
  });
}

/** symbol (UPPER) → img.logo.dev URL, or null when none is known. */
export type CompanyLogoMap = Record<string, string | null>;

/**
 * `GET /api/companies/logos?symbols=A,B,C` — batch logo lookup for list/table
 * surfaces (screener, portfolio holdings). Returns a symbol→URL map (URL null
 * when no logo is known). Resolves to `{}` on any error so callers can fall
 * back to monograms without special-casing failures.
 */
export async function getCompanyLogos(symbols: string[]): Promise<CompanyLogoMap> {
  const list = Array.from(
    new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean)),
  );
  if (list.length === 0) return {};
  try {
    const res = await request<{ logos: CompanyLogoMap }>("/companies/logos", {
      query: { symbols: list.join(",") },
    });
    return isError(res) ? {} : res.data.logos;
  } catch {
    // Network failure / fetch throw — logos are purely decorative, so a miss
    // must never break the table. Callers fall back to monograms.
    return {};
  }
}

// ---------------------------------------------------------------------------
// Metric series — `GET /api/markets/metric-series/{symbol}?metric=pe|ev_ebitda&range=…`
// Returns a time-series of the requested fundamental metric for the chart.
// `available:false` + empty `points` when the data cannot be computed.
// ---------------------------------------------------------------------------

export type MetricSeriesPoint = { t: string; v: number };

export type MetricSeriesResponse = {
  symbol: string;
  metric: "pe" | "ev_ebitda";
  range: string;
  available: boolean;
  points: MetricSeriesPoint[];
  source: string;
};

/** `GET /api/markets/metric-series/{symbol}?metric=pe|ev_ebitda&range=…` — fundamental metric series. */
export function getMetricSeries(
  symbol: string,
  metric: "pe" | "ev_ebitda",
  range: SparklineRange,
): Promise<ApiResult<MetricSeriesResponse>> {
  return request<MetricSeriesResponse>(
    `/markets/metric-series/${encodeURIComponent(symbol)}`,
    { query: { metric, range } },
  );
}

// ---------------------------------------------------------------------------
// Multi-broker onboarding — /brokers router (bare-mounted, NO /api prefix,
// same host as the legacy /kite router). Use requestLegacy so the trailing
// `/api` is stripped from the base; the JWT Bearer header is sent exactly as
// every other legacy call does (via the shared authTokenProvider). Supersedes
// the old Kite-only getKiteStatus()/getKiteLoginUrl()/credentials helpers.
// ---------------------------------------------------------------------------

/** `GET /brokers` — the broker catalog + per-broker live status. */
export function listBrokers(): Promise<ApiResult<BrokersResponse>> {
  return requestLegacy<BrokersResponse>("/brokers");
}

/**
 * `GET /brokers/{broker}/login_url` — hosted-OAuth login URL for brokers that
 * support it (e.g. kite). `login_url` is null in mock mode. The caller then
 * does `window.location.href = login_url`; after the broker redirects back,
 * the backend bounces the browser to the FE with `?broker=connected` (or
 * `?broker=error&reason=…`), handled in AppShell.
 */
export function getBrokerLoginUrl(
  broker: string,
): Promise<ApiResult<BrokerLoginUrl>> {
  return requestLegacy<BrokerLoginUrl>(
    `/brokers/${encodeURIComponent(broker)}/login_url`,
  );
}

/** `POST /brokers/{broker}/connect-mock` — dev-only stub connect (mock mode). */
export function connectBrokerMock(
  broker: string,
): Promise<ApiResult<BrokerStatus>> {
  return requestLegacy<BrokerStatus>(
    `/brokers/${encodeURIComponent(broker)}/connect-mock`,
    { method: "POST" },
  );
}

/**
 * `POST /brokers/{broker}/credentials` — api-key connect (Dhan) or Kite's
 * advanced auto-login opt-in. Pass only the fields the broker needs; set
 * `auto_login_opt_in: true` to store the (encrypted) credentials for
 * server-side token refresh.
 */
export function setBrokerCredentials(
  broker: string,
  body: BrokerCredentialsRequest,
): Promise<ApiResult<BrokerStatus>> {
  return requestLegacy<BrokerStatus>(
    `/brokers/${encodeURIComponent(broker)}/credentials`,
    { method: "POST", body },
  );
}

/** `POST /brokers/{broker}/automation` — toggle unattended/auto-login. */
export function setBrokerAutomation(
  broker: string,
  auto_login_opt_in: boolean,
): Promise<ApiResult<BrokerStatus>> {
  const body: BrokerAutomationRequest = { auto_login_opt_in };
  return requestLegacy<BrokerStatus>(
    `/brokers/${encodeURIComponent(broker)}/automation`,
    { method: "POST", body },
  );
}

/** `GET /brokers/{broker}/holdings` — connected-state holdings preview. */
export function getBrokerHoldings(
  broker: string,
): Promise<ApiResult<BrokerHoldingsResponse>> {
  return requestLegacy<BrokerHoldingsResponse>(
    `/brokers/${encodeURIComponent(broker)}/holdings`,
  );
}

/** `DELETE /brokers/{broker}/session` — disconnect this broker session. */
export function disconnectBroker(
  broker: string,
): Promise<ApiResult<BrokerDisconnectResponse>> {
  return requestLegacy<BrokerDisconnectResponse>(
    `/brokers/${encodeURIComponent(broker)}/session`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// Auth — user profile (NEW for redesign — /auth/me)
// ---------------------------------------------------------------------------

export type UserProfile = {
  id: string;
  email: string;
  full_name: string | null;
};

/** `GET /auth/me` — returns user profile for dashboard greeting. */
export function getMe(): Promise<ApiResult<UserProfile>> {
  return requestLegacy<UserProfile>("/auth/me");
}

// ---------------------------------------------------------------------------
// Feedback — bug reports (/feedback router, bare-mounted, NO /api prefix)
// ---------------------------------------------------------------------------

export type BugReportCategory = "bug" | "data" | "ui" | "performance" | "other";
export type BugReportSeverity = "low" | "normal" | "high" | "critical";

export type BugReportContext = {
  page?: string;
  tab?: string;
  user_agent?: string;
  app_version?: string;
  viewport?: string;
};

export type BugReportInput = {
  category: BugReportCategory;
  severity: BugReportSeverity;
  title: string;
  description: string;
  email?: string;
  context?: BugReportContext;
};

export type BugReportAck = { ok: boolean; id: string };

/** `POST /feedback` — submit a bug report from the Report-a-bug widget. */
export function submitBugReport(
  report: BugReportInput,
): Promise<ApiResult<BugReportAck>> {
  return requestLegacy<BugReportAck>("/feedback", {
    method: "POST",
    body: report,
  });
}

// ---------------------------------------------------------------------------
// Auth — token storage + login/register/logout helpers
// ---------------------------------------------------------------------------

const TOKEN_KEY = "pivot_jwt";
const REFRESH_KEY = "pivot_refresh";

/** Store both access + refresh tokens. */
export function storeToken(
  accessToken: string,
  refreshToken?: string,
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TOKEN_KEY, accessToken);
    if (refreshToken) {
      window.localStorage.setItem(REFRESH_KEY, refreshToken);
    }
  } catch {
    /* localStorage may be denied in some embeds */
  }
}

/** Clear both access + refresh tokens (called on logout / 401 failure). */
export function clearToken(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* ignore */
  }
}

type AuthResponse = {
  access_token: string;
  refresh_token: string;
  user_id: string;
  email: string;
};

/** `POST /auth/login` — exchange email+password for tokens. */
export async function loginUser(credentials: {
  email: string;
  password: string;
}): Promise<ApiResult<AuthResponse>> {
  const result = await requestLegacy<AuthResponse>("/auth/login", {
    method: "POST",
    body: credentials,
  });
  if (!("error" in result)) {
    storeToken(result.data.access_token, result.data.refresh_token);
  }
  return result;
}

/** `POST /auth/register` — create account + receive tokens. */
export async function registerUser(body: {
  email: string;
  password: string;
  full_name: string;
}): Promise<ApiResult<AuthResponse>> {
  const result = await requestLegacy<AuthResponse>("/auth/register", {
    method: "POST",
    body,
  });
  if (!("error" in result)) {
    storeToken(result.data.access_token, result.data.refresh_token);
  }
  return result;
}

/** `POST /auth/refresh` — silently exchange refresh token for new access token. */
export async function refreshAccess(): Promise<ApiResult<AuthResponse>> {
  let refreshToken: string | null = null;
  try {
    refreshToken =
      typeof window !== "undefined"
        ? window.localStorage.getItem(REFRESH_KEY)
        : null;
  } catch {
    refreshToken = null;
  }
  if (!refreshToken) {
    return { error: { code: "no_refresh_token", message: "No refresh token." } };
  }
  const result = await requestLegacy<AuthResponse>("/auth/refresh", {
    method: "POST",
    body: { refresh_token: refreshToken },
  });
  if (!("error" in result)) {
    storeToken(result.data.access_token, result.data.refresh_token);
  }
  return result;
}

/**
 * Internal helper: attempt one silent refresh. Returns true when the new
 * token was stored successfully. Called by the 401 handler in _doRequest.
 * Declared as a regular function so it is hoisted and available to
 * _doRequest which is defined earlier in this module.
 */
async function _tryRefresh(): Promise<boolean> {
  const result = await refreshAccess();
  return !("error" in result);
}

/** `POST /auth/logout` — best-effort server-side session revocation. */
export async function logoutUser(): Promise<void> {
  try {
    await requestLegacy("/auth/logout", { method: "POST" });
  } catch {
    /* best-effort — we clear local tokens regardless */
  }
  clearToken();
}

// ---------------------------------------------------------------------------
// Chat — propose workflow (Day 6 #38 backend endpoint)
// ---------------------------------------------------------------------------

export type ProposedDraftStep = {
  step_type: string;
  label: string | null;
  config: Record<string, unknown>;
};

export type ProposedWorkflowDraft = {
  name: string;
  description: string | null;
  steps: ProposedDraftStep[];
  rationale: string | null;
  warnings: string[];
};

/**
 * `POST /api/propose-workflow`
 *
 * Translates a natural-language strategy into a validated WorkflowDraft.
 * Does NOT persist — returns a draft for user review. "Open in editor →"
 * is the next step.
 */
export function proposeWorkflow(
  user_intent: string,
): Promise<ApiResult<ProposedWorkflowDraft>> {
  return request<ProposedWorkflowDraft>("/propose-workflow", {
    method: "POST",
    body: { user_intent },
  });
}

// ---------------------------------------------------------------------------
// Backtester (GET /api/backtest/expr/fields, POST /api/backtest/expr/run etc.)
// Top-level aliases /api/backtest/{fields,validate,run} also work (Day 8 BE).
// ---------------------------------------------------------------------------

export type BacktestField = {
  name: string;
  kind: "base" | "computed";
  description: string | null;
  unit: string | null;
  statement?: string;
  ttm_eligible?: boolean;
  expr?: string;
};

export type BacktestFieldsResponse = {
  base_fields: BacktestField[];
  computed_fields: BacktestField[];
  specials: string[];
  ttm_suffix_note: string;
};

export type BacktestForwardStats = {
  observed_sharpe: number | null;
  skew: number | null;
  kurtosis: number | null;
  n_obs: number;
  num_trials: number;
  psr: number | null;
  min_trl: number | null;
  deflated_sharpe: number | null;
};

export type BacktestMonteCarlo = {
  n_sims: number;
  block_size: number;
  dd_median_pct: number | null;
  dd_p95_severity_pct: number | null;
  dd_worst_pct: number | null;
  terminal_median_pct: number | null;
  terminal_p05_pct: number | null;
  prob_loss: number | null;
  prob_dd_worse_than_tol: number | null;
  drawdown_tolerance_pct: number | null;
};

export type BacktestSubPeriods = {
  n_periods: number;
  period_returns_pct: number[];
  positive_period_frac: number | null;
  best_period_return_pct: number | null;
  worst_period_return_pct: number | null;
  concentration: number | null;
};

export type BacktestTrustVerdict = {
  verdict: "insufficient_data" | "no_edge" | "unproven" | "promising";
  label: string;
  confidence: number;
  rationale: string;
  flags: string[];
};

export type BacktestMetrics = {
  cagr_pct: number;
  sharpe: number | null;
  max_drawdown_pct: number;
  calmar: number | null;
  turnover_pct: number | null;
  hit_rate_pct: number | null;
  n_unique_companies: number | null;
  total_return_pct: number;
  forward_stats?: BacktestForwardStats | null;
  monte_carlo?: BacktestMonteCarlo | null;
  sub_periods?: BacktestSubPeriods | null;
  trust_verdict?: BacktestTrustVerdict | null;
};

export type BacktestEquityPoint = { date: string; value: number };

export type BacktestRebalance = {
  date: string;
  entered: Array<{ symbol: string; weight: number }>;
  exited: Array<{ symbol: string }>;
};

export type BacktestResult = {
  expression: string;
  start: string;
  end: string;
  rebalance: string;
  metrics: BacktestMetrics;
  equity_curve: BacktestEquityPoint[];
  benchmark_curve: BacktestEquityPoint[];
  rebalances: BacktestRebalance[];
  n_trades: number;
  universe_audit: Record<string, unknown>[];
  leaf_fields: string[];
  referenced_fields: string[];
  warnings: string[];
};

export type BacktestRunRequest = {
  expression: string;
  start: string;
  end: string;
  rebalance?: string;
  starting_capital?: number;
  benchmark_sc_id?: string | null;
  basis?: string;
  auto_map_symbols?: boolean;
};

/** `GET /api/backtest/expr/fields` — list available screener fields. */
export function getBacktestFields(): Promise<ApiResult<BacktestFieldsResponse>> {
  return request<BacktestFieldsResponse>("/backtest/expr/fields");
}

/** `POST /api/backtest/expr/validate` — validate expression without DB hit. */
export function validateBacktestExpr(
  expression: string,
): Promise<ApiResult<{ ok: boolean; error?: string; suggestions?: string[]; referenced_fields?: string[]; warnings?: string[] }>> {
  return request("/backtest/expr/validate", {
    method: "POST",
    body: { expression },
  });
}

/** `POST /api/backtest/expr/run` — run full backtest with equity curve + metrics. */
export function runBacktest(
  body: BacktestRunRequest,
): Promise<ApiResult<BacktestResult>> {
  return request<BacktestResult>("/backtest/expr/run", {
    method: "POST",
    body,
  });
}

// ---------------------------------------------------------------------------
// Stock automations — GET /api/stocks/{symbol}/automations
// ---------------------------------------------------------------------------

export type StockAutomation = {
  workflow_id: string;
  workflow_name: string;
  /** "trigger_price" | "strike_price" | "past_fire" | "scheduled_run" */
  overlay_type: string;
  price_level?: number;
  expiry?: string | null;
  label?: string;
  fired_at?: string | null;
  scheduled_at?: string | null;
};

export type StockAutomationsResponse = { items: StockAutomation[] };

/**
 * `GET /api/stocks/{symbol}/automations`
 * Returns trigger price levels, past fires, and scheduled run dates
 * for all user workflows that touch this symbol.
 */
export function getStockAutomations(
  symbol: string,
): Promise<ApiResult<StockAutomationsResponse>> {
  return request<StockAutomationsResponse>(
    `/stocks/${encodeURIComponent(symbol)}/automations`,
  );
}

// ---------------------------------------------------------------------------
// News — GET /api/news?symbol=
// ---------------------------------------------------------------------------

export type NewsItem = {
  id: string;
  title: string;
  source: string;
  url: string;
  published_at: string;
  summary: string | null;
};

export type NewsResponse = { items: NewsItem[] };

/**
 * `GET /api/news?symbol=X`
 * Returns top 10 news items for a symbol.
 */
export function getNews(symbol: string): Promise<ApiResult<NewsResponse>> {
  return request<NewsResponse>("/news", { query: { symbol } });
}

// ---------------------------------------------------------------------------
// Conversations (GET/POST /api/conversations — shipped Day 8 backend)
// ---------------------------------------------------------------------------

export type Conversation = {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
};

export type ConversationMessage = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

/**
 * `GET /api/conversations` — list all conversations for the authenticated user.
 * Auto-created when the user sends their first message.
 */
export function listConversations(params?: {
  limit?: number;
  cursor?: string;
}): Promise<ApiResult<Paginated<Conversation>>> {
  return request<Paginated<Conversation>>("/conversations", {
    query: { limit: params?.limit, cursor: params?.cursor },
  });
}

/**
 * `POST /api/conversations` — create a new conversation (optional title).
 */
export function createConversation(body?: {
  title?: string;
}): Promise<ApiResult<Conversation>> {
  return request<Conversation>("/conversations", {
    method: "POST",
    body: body ?? {},
  });
}

/**
 * `GET /api/conversations/{id}/messages` — list messages in a conversation.
 */
// ---------------------------------------------------------------------------
// Portfolio performance — GET /api/portfolio/performance?period=
// ---------------------------------------------------------------------------

export type PortfolioPerformancePeriod = "1M" | "3M" | "6M" | "1Y" | "5Y";

/** A single point on the portfolio value curve. `t` is an ISO timestamp,
 *  `v` is the portfolio value (₹) at that time. Matches the backend
 *  `PerfPoint` model in routers/portfolio_perf.py. */
export type PortfolioPerformancePoint = { t: string; v: number };

export type PortfolioPerformanceResponse = {
  period: string;
  points: PortfolioPerformancePoint[];
  starting_value: number;
  ending_value: number;
  total_return: number;
  total_return_pct: number;
};

/** `GET /api/portfolio/performance?period=1M|3M|6M|1Y|5Y` — portfolio value curve. */
export function getPortfolioPerformance(
  period: PortfolioPerformancePeriod = "1Y",
): Promise<ApiResult<PortfolioPerformanceResponse>> {
  return request<PortfolioPerformanceResponse>("/portfolio/performance", {
    query: { period },
  });
}

// ---------------------------------------------------------------------------
// Index history — GET /api/quotes/index/{symbol}/history?period=
// ---------------------------------------------------------------------------

export type IndexHistorySymbol =
  | "NIFTY50"
  | "SENSEX"
  | "BANKNIFTY"
  | "NIFTYMIDCAP100";

/** A single benchmark-index close point — `t` ISO timestamp, `v` close
 *  level. Mirrors the backend `SparklinePoint` (routers/markets.py). */
export type IndexHistoryPoint = { t: string; v: number };

export type IndexHistoryResponse = {
  symbol: string;
  range: string;
  interval: string;
  points: IndexHistoryPoint[];
};

/** `GET /api/quotes/index/{symbol}/history?period=1Y` — benchmark overlay series. */
export function getIndexHistory(
  symbol: IndexHistorySymbol,
  period: string = "1Y",
): Promise<ApiResult<IndexHistoryResponse>> {
  return request<IndexHistoryResponse>(
    `/quotes/index/${encodeURIComponent(symbol)}/history`,
    { query: { period } },
  );
}

// ---------------------------------------------------------------------------
// Calendar events — GET /api/events/calendar?from=&to=
// ---------------------------------------------------------------------------

export type CalendarEvent = {
  id: string;
  title: string;
  event_type: "earnings" | "dividend" | "ipo" | "macro" | "scheduled_run";
  fire_time: string;
  symbol: string | null;
  description: string | null;
  workflow_id: string | null;
};

export type CalendarEventsResponse = { items: CalendarEvent[] };

/** `GET /api/events/calendar?from=&to=` — event-trigger entries for calendar overlay. */
export function getCalendarEvents(params: {
  from: string;
  to: string;
}): Promise<ApiResult<CalendarEventsResponse>> {
  return request<CalendarEventsResponse>("/events/calendar", {
    query: { from: params.from, to: params.to },
  });
}

export function listConversationMessages(
  conversationId: string,
  params?: { limit?: number; cursor?: string },
): Promise<ApiResult<Paginated<ConversationMessage>>> {
  return request<Paginated<ConversationMessage>>(
    `/conversations/${encodeURIComponent(conversationId)}/messages`,
    { query: { limit: params?.limit, cursor: params?.cursor } },
  );
}

// ---------------------------------------------------------------------------
// Paper trading endpoints (P5 — /api/paper/*) — backs the Paper Trading tab.
// The read service is read-only; all money fields are numbers (₹).
// ---------------------------------------------------------------------------

/** Account summary. `exists:false` means the user has no paper book yet. */
export type PaperSummaryData = {
  exists: true;
  mode: string;
  starting_capital: number;
  cash_available: number;
  cash_settled: number;
  cash_reserved: number;
  buying_power: number;
  positions_mv: number;
  invested: number;
  nav: number;
  unrealized_pnl: number;
  realized_pnl_cum: number;
  day_pnl: number;
  total_pnl: number;
  total_pnl_pct: number;
  unrealized_pct: number;
  num_positions: number;
  num_open_orders: number;
  is_stale: boolean;
};
export type PaperSummary = { exists: false } | PaperSummaryData;

export type PaperHolding = {
  symbol: string;
  quantity: number;
  avg_cost: number;
  last_price: number | null;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pct: number;
  day_pnl: number;
  invested: number;
  realized_pnl: number;
  sector: string;
  stale: boolean;
  last_mark_at: string | null;
};

export type PaperOpenOrder = {
  id: string;
  symbol: string;
  side: string; // "BUY" | "SELL"
  order_type: string; // MARKET | LIMIT | SL | SL-M | GTT
  quantity: number;
  limit_price: number | null;
  trigger_price: number | null;
  reserved_cash: number;
  status: string;
  source: string | null;
  origin_kind: string | null;
  created_at: string | null;
};

export type PaperFillRow = {
  id: string;
  symbol: string;
  side: string;
  quantity: number;
  fill_price: number;
  gross_value: number;
  charges: number;
  net_cashflow: number;
  realized_pnl: number | null;
  filled_at: string | null;
  order_id: string;
};

export type PaperNavPoint = {
  as_of_date: string | null;
  nav: number;
  cash_available: number;
  positions_mv: number;
  realized_pnl_cum: number;
  unrealized_pnl: number;
  nifty_close: number | null;
};

// The paper router mounts at `/paper` (no `/api` alias), like the other
// legacy surfaces (/portfolio, /orders). Use requestLegacy so the trailing
// `/api` is stripped from the base — request() would 404 on `/api/paper/*`.

/** `GET /paper/summary` */
export function getPaperSummary(): Promise<ApiResult<PaperSummary>> {
  return requestLegacy<PaperSummary>("/paper/summary");
}

/** `GET /paper/holdings` — open positions, sorted by market value. */
export function getPaperHoldings(): Promise<ApiResult<PaperHolding[]>> {
  return requestLegacy<PaperHolding[]>("/paper/holdings");
}

/** `GET /paper/orders` — the resting-order blotter. */
export function getPaperOpenOrders(): Promise<ApiResult<PaperOpenOrder[]>> {
  return requestLegacy<PaperOpenOrder[]>("/paper/orders");
}

/** `GET /paper/fills` — the trade journal (newest first). */
export function getPaperFills(limit = 50): Promise<ApiResult<PaperFillRow[]>> {
  return requestLegacy<PaperFillRow[]>("/paper/fills", { query: { limit } });
}

/** `GET /paper/nav` — the equity curve (oldest first). */
export function getPaperNavCurve(
  start?: string,
  end?: string,
): Promise<ApiResult<PaperNavPoint[]>> {
  return requestLegacy<PaperNavPoint[]>("/paper/nav", { query: { start, end } });
}

// ── Forward-test scorecards (P6) ──────────────────────────────────────────
// Per-idea forward-test track records. Scorecard headline metrics arrive as
// number|null (null = insufficient data → DASH on the FE). verdict ∈
// {"on_track","decayed","execution_problem","insufficient_data"}.

/** One idea's list-view scorecard headline. */
export type PaperIdea = {
  id: string;
  label: string;
  origin_kind: string; // "workflow" | "chat" | "strategy" | "manual"
  status: string; // "paper" | "candidate" | "promoted" | "retired"
  inception_date: string | null;
  maturity_days: number | null;
  n_obs: number | null;
  cum_return_pct: number | null;
  sharpe: number | null;
  alpha: number | null;
  psr: number | null;
  max_drawdown_pct: number | null;
  verdict: string | null;
  has_backtest: boolean;
};

/** A point on an idea's forward (live) NAV curve. */
export type IdeaNavPoint = {
  as_of_date: string | null;
  idea_nav: number;
  committed_capital: number;
  positions_mv: number;
  realized_pnl: number;
  unrealized_pnl: number;
  nifty_close: number | null;
};

/** The stored backtest baseline this idea is compared against (if any). */
export type IdeaBacktest = {
  sharpe_ratio: number | null;
  total_return_pct: number | null;
  cagr_pct: number | null;
  max_drawdown_pct: number | null;
  benchmark_return_pct: number | null;
  total_trades: number | null;
  start_date: string | null;
  end_date: string | null;
  primary_symbol: string | null;
  equity_curve: { date: string | null; equity: number }[];
};

/** One backtest-vs-forward stat-gate row. */
export type IdeaGate = {
  label: string;
  forward: number | null;
  backtest: number | null;
  pass: boolean | null;
};

/** Full per-idea scorecard: headline + forward curve + backtest baseline + gates. */
export type PaperIdeaDetail = PaperIdea & {
  cohort_trial_count: number;
  backtest_run_id: string | null;
  status_changed_at: string | null;
  mintrl: number | null;
  dsr: number | null;
  promotion_ready: boolean;
  forward_curve: IdeaNavPoint[];
  backtest: IdeaBacktest | null;
  gates: IdeaGate[];
};

/** `GET /paper/ideas` — the forward-test idea list (newest first). */
export function getPaperIdeas(): Promise<ApiResult<PaperIdea[]>> {
  return requestLegacy<PaperIdea[]>("/paper/ideas");
}

/** `GET /paper/ideas/{id}` — one idea's full scorecard. */
export function getPaperIdeaDetail(
  id: string,
): Promise<ApiResult<PaperIdeaDetail>> {
  return requestLegacy<PaperIdeaDetail>(`/paper/ideas/${encodeURIComponent(id)}`);
}

/** `GET /paper/ipo-allocations` — simulated IPO allotment records (newest first). */
export function getPaperIpoAllocations(): Promise<ApiResult<PaperIpoAllocation[]>> {
  return requestLegacy<PaperIpoAllocation[]>("/paper/ipo-allocations");
}

/** `GET /paper/greeks` — portfolio-level net Greeks + breakdown by underlying + expiry. */
export function fetchPaperGreeks(): Promise<ApiResult<import("@/lib/types").PortfolioGreeksPayload>> {
  return requestLegacy<import("@/lib/types").PortfolioGreeksPayload>("/paper/greeks");
}

// ---------------------------------------------------------------------------
// IPO Applications — router mounted bare at /ipo-applications
// ---------------------------------------------------------------------------

/** `POST /ipo-applications` — register intent for an IPO. */
export function registerIpoApplication(
  body: IpoRegisterRequest,
): Promise<ApiResult<IpoRegisterResponse>> {
  return requestLegacy<IpoRegisterResponse>("/ipo-applications", {
    method: "POST",
    body,
  });
}

/** `POST /ipo-applications/{id}/withdraw` — withdraw a registered intent. */
export function withdrawIpoApplication(
  id: number | string,
): Promise<ApiResult<IpoWithdrawResponse>> {
  return requestLegacy<IpoWithdrawResponse>(
    `/ipo-applications/${encodeURIComponent(id)}/withdraw`,
    { method: "POST" },
  );
}

/** `GET /users/ipo-applications` — list this user's IPO applications. */
export function listMyIpoApplications(): Promise<ApiResult<IpoApplicationsListResponse>> {
  return requestLegacy<IpoApplicationsListResponse>("/users/ipo-applications");
}

/**
 * `GET /ipo-calendar?from=&to=` — upcoming IPO open/close windows.
 *
 * Bare-mounted at /ipo-calendar (no /api prefix), same as the other IPO
 * routes. `from` and `to` are optional ISO date strings; when omitted the
 * backend returns all upcoming IPOs it has in its feed.
 *
 * TODO(P2.1 calendar): wire this response into CalendarTab to show IPO
 * open/close entries alongside workflow runs. Currently the fetcher + types
 * are wired; the CalendarTab integration is behind a lightweight feature
 * flag (see CalendarTab.tsx comment) to avoid risking the existing view.
 */
export function getIpoCalendar(
  from?: string,
  to?: string,
): Promise<ApiResult<IpoCalendarResponse>> {
  return requestLegacy<IpoCalendarResponse>("/ipo-calendar", {
    query: { from, to },
  });
}

/**
 * `GET /ipo-subscription/{symbol}` — live per-category subscription data.
 *
 * Bare-mounted at /ipo-subscription (no /api prefix), same as other IPO
 * routes. Returns per-category subscription multiples (times subscribed)
 * from NSE's ipo-active-category endpoint. Only meaningful when status=="open".
 * Returns subscription:null + note when data is unavailable (honest-null).
 */
export function getIpoSubscription(
  symbol: string,
): Promise<ApiResult<IpoSubscriptionResponse>> {
  return requestLegacy<IpoSubscriptionResponse>(
    `/ipo-subscription/${encodeURIComponent(symbol)}`,
  );
}

// ---------------------------------------------------------------------------
// Option Strategies — bare-mounted at /option-strategies
// ---------------------------------------------------------------------------

/** `POST /option-strategies` — register an option strategy intent. */
export function registerOptionStrategy(
  body: OptionStrategyRegisterRequest,
): Promise<ApiResult<OptionStrategyRegisterResponse>> {
  return requestLegacy<OptionStrategyRegisterResponse>("/option-strategies", {
    method: "POST",
    body,
  });
}

/** `POST /option-strategies/{id}/withdraw` — withdraw a registered strategy. */
export function withdrawOptionStrategy(
  id: string,
): Promise<ApiResult<{ success: boolean }>> {
  return requestLegacy<{ success: boolean }>(
    `/option-strategies/${encodeURIComponent(id)}/withdraw`,
    { method: "POST" },
  );
}

/** `POST /option-strategies/compute` — non-persisting recompute for the
 *  interactive strategy builder (fresh payoff/Greeks/margin/critique). */
export function computeOptionStrategy(
  body: OptionStrategyComputeRequest,
): Promise<ApiResult<OptionStrategyComputeResponse>> {
  return requestLegacy<OptionStrategyComputeResponse>(
    "/option-strategies/compute",
    { method: "POST", body },
  );
}

/** `GET /option-strategies/chain` — trimmed chain for strike/expiry pickers. */
export function getOptionChainSlice(params: {
  underlying: string;
  expiry?: string;
  width?: number;
}): Promise<ApiResult<OptionChainSliceResponse>> {
  return requestLegacy<OptionChainSliceResponse>("/option-strategies/chain", {
    query: {
      underlying: params.underlying,
      ...(params.expiry ? { expiry: params.expiry } : {}),
      ...(params.width ? { width: String(params.width) } : {}),
    },
  });
}

/** `GET /users/option-strategies` — list this user's option strategies. */
export function listOptionStrategies(): Promise<
  ApiResult<{ items: OptionStrategyRegisterResponse["strategy"][] }>
> {
  return requestLegacy<{ items: OptionStrategyRegisterResponse["strategy"][] }>(
    "/users/option-strategies",
  );
}

// ---------------------------------------------------------------------------
// Views — View Markets V2  (GET /api/views, GET /api/views/{id}, …)
//
// All reads are GLOBAL (curated content; no per-user filtering). Follow is
// per-user best-effort. Endpoints are flag-gated on settings.view_markets_enabled
// on the backend — the FE receives a 404 { error } when the flag is off.
// ---------------------------------------------------------------------------

/**
 * `GET /api/views` — list curated views (newest first, non-archived by default).
 *
 * @param params - optional filters mirroring the query params the backend accepts.
 */
export function listViews(params?: {
  status?: string;
  view_type?: string;
  category?: string;
}): Promise<ApiResult<{ items: ViewSummary[] }>> {
  return request<{ items: ViewSummary[] }>("/views", { query: params });
}

/**
 * `GET /api/views/{view_id}` — full view detail including transmission,
 * expectations, confidence evidence, and expression ladder.
 */
export function getView(id: string): Promise<ApiResult<ViewDetail>> {
  return request<ViewDetail>(`/views/${encodeURIComponent(id)}`);
}

/**
 * `POST /api/views/expressions/{expression_id}/deploy`
 *
 * Links (or creates) a workflow draft from the expression. If the expression
 * already has a `workflow_id` and re-arming isn't requested, returns the
 * existing workflow id. The caller then opens the AgentPanel draft editor
 * via `onOpenWorkflowById` — no order fires until the user approves.
 */
export function deployExpression(
  expressionId: string,
  body?: { activate?: boolean; timing_mode?: string },
): Promise<
  ApiResult<{
    workflow_id: string;
    status: string;
    steps_count: number;
    activated: boolean;
  }>
> {
  return request<{
    workflow_id: string;
    status: string;
    steps_count: number;
    activated: boolean;
  }>(`/views/expressions/${encodeURIComponent(expressionId)}/deploy`, {
    method: "POST",
    body: body ?? {},
  });
}

/**
 * `POST /api/views/{view_id}/compare`
 *
 * Ranks the three tiers and returns a `recommended_tier` with rationale.
 * The FE highlights the recommended ExpressionCard.
 */
export function compareViewTiers(
  viewId: string,
): Promise<ApiResult<CompareResult>> {
  return request<CompareResult>(
    `/views/${encodeURIComponent(viewId)}/compare`,
    { method: "POST", body: {} },
  );
}

/**
 * `POST /api/views/expressions/{expression_id}/backtest`
 *
 * Triggers a backtest run for the expression and persists the result.
 * The returned `ExpressionScores` can be merged into local component state
 * so the `RiskReturnPanel` refreshes without a full page reload.
 */
export function backtestExpression(
  expressionId: string,
): Promise<ApiResult<ExpressionScores>> {
  return request<ExpressionScores>(
    `/views/expressions/${encodeURIComponent(expressionId)}/backtest`,
    { method: "POST", body: {} },
  );
}

/**
 * `POST /api/views/{view_id}/follow` — follow a view (per-user, best-effort).
 *
 * Callers should apply the result optimistically and silently revert on error.
 */
export function followView(
  viewId: string,
): Promise<ApiResult<{ is_following: boolean; follower_count: number }>> {
  return request<{ is_following: boolean; follower_count: number }>(
    `/views/${encodeURIComponent(viewId)}/follow`,
    { method: "POST", body: {} },
  );
}

/**
 * `DELETE /api/views/{view_id}/follow` — unfollow a view (per-user, best-effort).
 *
 * Callers should apply the result optimistically and silently revert on error.
 */
export function unfollowView(
  viewId: string,
): Promise<ApiResult<{ is_following: boolean; follower_count: number }>> {
  return request<{ is_following: boolean; follower_count: number }>(
    `/views/${encodeURIComponent(viewId)}/follow`,
    { method: "DELETE" },
  );
}
