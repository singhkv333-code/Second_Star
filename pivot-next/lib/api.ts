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
  CreateWorkflowRequest,
  ErrorBody,
  Paginated,
  Run,
  RunSummary,
  StepTypeCatalog,
  UpdateWorkflowRequest,
  Workflow,
  WorkflowStatus,
  WorkflowSummary,
} from "@/lib/types";

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
let stepTypesSource: StepTypesSource = "mock";

/**
 * Toggle between the inline mock catalog (Day 1-4) and the real
 * `GET /api/step-types` endpoint (Day 5+). Frontend switches to "real"
 * once the backend ships its catalog response.
 */
export function setStepTypesSource(source: StepTypesSource): void {
  stepTypesSource = source;
}

/**
 * Single global toggle that switches every Day 2 mock surface (catalog,
 * run stream, …) between in-memory simulators and live backend wires.
 * Day 5 default flips to "real" once the engine + WS land.
 */
export type BackendSource = "mock" | "real";
let backendSource: BackendSource = "mock";

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

async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  return _doRequest<T>(getBaseUrl(), path, options);
}

async function requestLegacy<T>(
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
    // Token expired / invalid → wipe localStorage and bounce the user
    // back through the AppBootstrap auth gate. Without this, every
    // surface keeps retrying with a stale JWT and the UI shows
    // generic "request failed" errors everywhere ("token problem
    // that keeps coming up").
    if (res.status === 401 && typeof window !== "undefined") {
      try {
        window.localStorage.removeItem("pivot_jwt");
      } catch {
        /* localStorage may be denied in some embeds; safe to ignore */
      }
      // Reload — AppBootstrap will detect the missing token and
      // render SignInPrompt. One reload, not a polling loop.
      window.location.reload();
    }
    const envelope = (parsed ?? {}) as { error?: Partial<ErrorBody> };
    const err = envelope.error ?? {};
    return {
      error: {
        code: err.code ?? `http_${res.status}`,
        message:
          err.message ?? `Request failed with status ${res.status}`,
        details: err.details,
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

  return request<StepTypeCatalog>("/step-types").then((result) => {
    if (!("error" in result)) {
      cachedCatalog = { fetchedAt: now, data: result.data };
    }
    return result;
  });
}

/** Test helper — clears the in-memory catalog cache. */
export function _clearCatalogCache(): void {
  cachedCatalog = null;
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

/** `GET /portfolio/summary` — backed by Kite (mock when KITE_API_KEY is empty). */
export function getPortfolioSummary(): Promise<ApiResult<PortfolioSummary>> {
  return requestLegacy<PortfolioSummary>("/portfolio/summary");
}

/** `GET /portfolio/holdings` — list of Holdings (mock data in test mode). */
export function getPortfolioHoldings(): Promise<ApiResult<Holding[]>> {
  return requestLegacy<Holding[]>("/portfolio/holdings");
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
  close: number;
  week_52_high: number;
  week_52_low: number;
  volume: number;
  market_cap: number | null;
  pe_ratio: number | null;
  sector: string | null;
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

export type BacktestMetrics = {
  cagr_pct: number;
  sharpe: number | null;
  max_drawdown_pct: number;
  calmar: number | null;
  turnover_pct: number | null;
  hit_rate_pct: number | null;
  n_unique_companies: number | null;
  total_return_pct: number;
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

export type PortfolioPerformancePoint = { date: string; value: number };

export type PortfolioPerformanceResponse = {
  period: string;
  equity_curve: PortfolioPerformancePoint[];
};

/** `GET /api/portfolio/performance?period=1M|3M|6M|1Y|5Y` — portfolio equity curve. */
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

export type IndexHistoryPoint = { date: string; close: number };

export type IndexHistoryResponse = {
  symbol: string;
  period: string;
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
