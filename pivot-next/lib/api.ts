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
  AgentPositions,
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
  ViewPositionItem,
  ViewSummary,
  Workflow,
  WorkflowStatus,
  WorkflowSummary,
} from "@/lib/types";
import { isError } from "@/lib/types";
import type { DslNode, DslSchema, DslDescribeResult } from "@/lib/types";
import { getTradingMode } from "@/lib/trading-mode";
import { refreshAccessToken } from "@/lib/authToken";

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

// In-flight GET de-duplication. Identical concurrent GETs (duplicate effect
// runs, React StrictMode double-invokes in dev, several components fetching the
// same resource) would otherwise race PAST the server-side cache and each pay
// the full network/compute cost. We coalesce them into ONE request and hand
// every caller the same result. Only GETs are coalesced — POST/PATCH/DELETE
// have side effects and must never share. Entries are dropped the moment the
// request settles, so this merges *concurrent* calls only, never caches across
// time (freshness is unchanged).
const _inflightGets = new Map<string, Promise<ApiResult<unknown>>>();

// Short-TTL response cache for read-mostly, mildly-stale-tolerant GETs (e.g.
// the Home tab's indices/quotes/sparklines/portfolio — every one of those was
// re-fetched from scratch on every mount with no cache at all). Unlike
// `_inflightGets` above, this caches ACROSS time, not just concurrent calls:
// a hit within `ttlMs` resolves instantly with no network round trip at all.
// Caches the in-flight PROMISE (not just the settled value) so callers that
// land mid-fetch share the same request instead of firing a duplicate.
// Never use this for anything that must reflect the latest write (orders,
// workflow mutations, etc.) — only for read paths that tolerate a few
// seconds to minutes of staleness.
const _ttlCache = new Map<string, { expiresAt: number; value: Promise<ApiResult<unknown>> }>();

function cached<T>(
  key: string,
  ttlMs: number,
  fetcher: () => Promise<ApiResult<T>>,
): Promise<ApiResult<T>> {
  const now = Date.now();
  const hit = _ttlCache.get(key);
  if (hit && hit.expiresAt > now) return hit.value as Promise<ApiResult<T>>;
  const value = fetcher();
  _ttlCache.set(key, { expiresAt: now + ttlMs, value: value as Promise<ApiResult<unknown>> });
  // A failed fetch (rejection, or a resolved error envelope) must not poison
  // the cache for the full TTL — drop it so the very next call retries
  // instead of replaying the failure to everyone for `ttlMs`.
  void value.then(
    (v) => { if (isError(v)) _ttlCache.delete(key); },
    () => { _ttlCache.delete(key); },
  );
  return value;
}

// Mutations under these mounts change what the Portfolio surfaces (holdings,
// summary, history, header value) show. After one succeeds we drop the
// portfolio-ish TTL-cache entries and broadcast `pivot:portfolio-dirty` so
// keep-alive tabs refetch instead of showing pre-trade data until a reload.
const _PORTFOLIO_MUTATION_RE =
  /\/(orders|paper|workflows|strategies|baskets|views|ipo)\b/;

function _notifyPortfolioMutation(path: string): void {
  if (typeof window === "undefined") return;
  if (!_PORTFOLIO_MUTATION_RE.test(path)) return;
  for (const key of Array.from(_ttlCache.keys())) {
    if (/portfolio|paper|holdings|summary|performance|scores/i.test(key)) {
      _ttlCache.delete(key);
    }
  }
  window.dispatchEvent(new CustomEvent("pivot:portfolio-dirty"));
}

async function _doRequest<T>(
  base: string,
  path: string,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  if ((options.method ?? "GET") !== "GET") {
    const p = _performRequest<T>(base, path, options);
    void p.then(
      (r) => {
        if (!isError(r)) _notifyPortfolioMutation(path);
      },
      () => undefined,
    );
    return p;
  }
  const key = buildUrl(base, path, options.query);
  const existing = _inflightGets.get(key);
  if (existing) return existing as Promise<ApiResult<T>>;
  const p = _performRequest<T>(base, path, options);
  _inflightGets.set(key, p as Promise<ApiResult<unknown>>);
  void p.finally(() => {
    _inflightGets.delete(key);
  });
  return p;
}

async function _performRequest<T>(
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
            // Retry the original request once with the new token. Call the
            // worker directly (NOT the coalescing wrapper): this retry runs
            // while the outer in-flight promise is still pending, so routing
            // back through the wrapper would await its own promise → deadlock.
            return _performRequest<T>(base, path, options);
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
// Voice input (audio → text)
// ---------------------------------------------------------------------------

export type TranscriptionResult = {
  text: string;
  mode: "translate" | "transcribe";
  provider: string;
};

/**
 * Upload a browser MediaRecorder blob to POST /audio/transcribe. Multipart
 * body, so this bypasses the JSON `request` wrapper (which force-sets
 * Content-Type: application/json) and does its own fetch against the legacy
 * root mount with the same bearer token. mode="translate" (the default)
 * returns ENGLISH text whatever language was spoken — the working language
 * of chat and company search.
 */
export async function transcribeAudio(
  blob: Blob,
  options: { mode?: "translate" | "transcribe" } = {},
): Promise<ApiResult<TranscriptionResult>> {
  const mode = options.mode ?? "translate";
  // Filename extension mirrors the recorded container so whisper's sniffing
  // has a consistent hint (Safari records mp4/aac, Chrome/Firefox webm/opus).
  const ext = blob.type.includes("mp4")
    ? "mp4"
    : blob.type.includes("ogg")
      ? "ogg"
      : "webm";
  const form = new FormData();
  form.append("file", blob, `recording.${ext}`);

  const token = await authTokenProvider();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(
    buildUrl(getLegacyBase(), "/audio/transcribe", { mode }),
    { method: "POST", headers, body: form, cache: "no-store" },
  );

  let parsed: unknown = null;
  try {
    parsed = await res.json();
  } catch {
    // Non-JSON body — fall through to the error branch below.
  }

  if (!res.ok || parsed === null) {
    // Bare-mounted route → FastAPI default shape: detail is either a plain
    // string or the {code, message} dict our _errors helpers raise.
    const detail = (parsed as { detail?: unknown } | null)?.detail;
    const detailMessage =
      typeof detail === "string"
        ? detail
        : typeof (detail as { message?: unknown } | undefined)?.message ===
            "string"
          ? (detail as { message: string }).message
          : undefined;
    return {
      error: {
        code: `http_${res.status}`,
        message:
          detailMessage ?? `Voice transcription failed (status ${res.status})`,
      },
    };
  }
  return { data: parsed as TranscriptionResult };
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
  interval?: string;
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

/** Open positions this agent's own trades opened, with returns since fill. */
export function getAgentPositions(
  workflowId: string,
): Promise<ApiResult<AgentPositions>> {
  return request<AgentPositions>(
    `/workflows/${encodeURIComponent(workflowId)}/positions`,
  );
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
  /** Pre-formatted in trigger's tz, e.g. "3:15 PM IST" */
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
  /** PER-SHARE day move (LTP − prev close). NOT the position's total day P&L.
   *  Callers derive prev close as `last_price − day_change`. */
  day_change: number;
  day_change_percentage: number;
  /** Rich sector label from the backend (hand-map → screener universe →
   *  "Other"); F&O contracts read "F&O", ETFs "ETF". Present on both the live
   *  `/portfolio/holdings` and paper `/paper/holdings` reads. */
  sector?: string | null;
  /** "large" | "mid" | "small" | null — same thresholds as the screener's
   *  market-cap tiers; null when the symbol has no market-cap data. */
  market_cap_tier?: "large" | "mid" | "small" | null;
};

export type PortfolioSummary = {
  total_value: number;
  invested_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  day_pnl: number;
  num_holdings: number;
  /** Free cash (buying power). Present in paper mode; may be undefined for a
   *  real/Kite summary that doesn't carry a margin figure — render only when
   *  defined so we never show a fake ₹0 cash. */
  cash_available?: number;
};

/** `GET /portfolio/summary` — backed by Kite (mock when KITE_API_KEY is empty).
 *  In paper mode this reads the paper book and adapts it to the SAME shape so
 *  every consumer (metric strip, Portfolio tab) renders identically. */
export function getPortfolioSummary(): Promise<ApiResult<PortfolioSummary>> {
  const mode = getTradingMode();
  return cached(`portfolio-summary:${mode}`, 10_000, () => {
    if (mode === "paper") {
      return getPaperSummary().then((r) =>
        isError(r) ? r : { data: adaptPaperSummary(r.data) },
      );
    }
    return requestLegacy<PortfolioSummary>("/portfolio/summary");
  });
}

/** `GET /portfolio/holdings` — list of Holdings (mock data in test mode).
 *  In paper mode this reads the paper positions, adapted to Holding[]. */
export function getPortfolioHoldings(): Promise<ApiResult<Holding[]>> {
  const mode = getTradingMode();
  return cached(`portfolio-holdings:${mode}`, 10_000, () => {
    if (mode === "paper") {
      return getPaperHoldings().then((r) =>
        isError(r) ? r : { data: r.data.map(adaptPaperHolding) },
      );
    }
    return requestLegacy<Holding[]>("/portfolio/holdings");
  });
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
      cash_available: 0,
    };
  }
  return {
    total_value: p.nav, // NAV = cash + reserved + positions market value
    invested_value: p.invested,
    total_pnl: p.total_pnl,
    total_pnl_pct: p.total_pnl_pct,
    day_pnl: p.day_pnl,
    num_holdings: p.num_positions,
    cash_available: p.cash_available, // free buying power (₹ not deployed)
  };
}

function adaptPaperHolding(h: PaperHolding): Holding {
  return {
    tradingsymbol: h.symbol,
    exchange: "NSE", // paper book has no exchange field; it is NSE-only
    quantity: h.quantity,
    // Show the price actually PAID (ex-charges), not the charge-inclusive cost
    // basis — so a fresh buy reads Avg == LTP (P&L ≈ 0) instead of an instant
    // "loss" equal to the entry charges. Falls back to avg_cost pre-upgrade.
    average_price: h.buy_price ?? h.avg_cost,
    last_price: h.last_price ?? h.buy_price ?? h.avg_cost, // unmarked lot → book
    pnl: h.unrealized_pnl,
    // `Holding.day_change` is PER-SHARE (matches the live `/portfolio/holdings`
    // shape, which the Portfolio table uses to back out prev close as
    // `last_price − day_change`). The paper book's `day_pnl` is the position's
    // TOTAL day move, so divide by quantity — otherwise the table's Day P&L is
    // off by a factor of `quantity` (a 10-share lot showed ~10× the real move).
    day_change: h.quantity !== 0 ? h.day_pnl / h.quantity : 0,
    day_change_percentage:
      h.invested !== 0 ? (h.day_pnl / h.invested) * 100 : 0,
    sector: h.sector,
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

/** Company profile (name/blurb/sector/industry/website/CEO) — sourced from the
 *  yfinance-derived enrich DB first, live yfinance `.info` as a fallback.
 *  Moneycontrol never carries these fields, so `profile` is null only when
 *  neither source could resolve the company at all. */
export type FinancialsProfile = {
  name?: string | null;
  blurb?: string | null;
  sector?: string | null;
  industry?: string | null;
  website?: string | null;
  ceo?: string | null;
};

export type FinancialsResponse = {
  available: boolean;
  company: FinancialsCompany | null;
  latest: Record<string, FinancialsLatestValue | null>;
  history: Record<string, FinancialsHistoryPoint[]>;
  profile: FinancialsProfile | null;
  source: string;
};

/** `GET /api/financials/{symbol}` — fundamentals from the Moneycontrol DB. */
export function getFinancials(symbol: string): Promise<ApiResult<FinancialsResponse>> {
  return request<FinancialsResponse>(`/financials/${encodeURIComponent(symbol)}`);
}

/** One balance-sheet line item across every fetched fiscal year. `section`
 *  is set on the row immediately following a section header (e.g.
 *  "SHAREHOLDER'S FUNDS") and null for plain line items. */
export type BalanceSheetRow = {
  section: string | null;
  line_item: string;
  values: Record<string, number | null>;
  value_texts: Record<string, string | null>;
};

export type BalanceSheetResponse = {
  available: boolean;
  company: FinancialsCompany | null;
  basis: "consolidated" | "standalone";
  unit: string | null;
  periods: string[];
  rows: BalanceSheetRow[];
  source: string;
};

/** `GET /api/financials/{symbol}/balance_sheet` — full MC balance sheet grid
 *  (every line item, section headers, multi-year), sourced only from a real
 *  Moneycontrol scrape — never yfinance, never derived. */
export function getBalanceSheet(
  symbol: string,
  basis: "consolidated" | "standalone" = "consolidated",
): Promise<ApiResult<BalanceSheetResponse>> {
  return request<BalanceSheetResponse>(
    `/financials/${encodeURIComponent(symbol)}/balance_sheet?basis=${basis}`,
  );
}

/** The four line-item grids MC publishes. All four share the balance sheet's
 *  shape, so one table component reads every one of them. */
export type StatementType = "balance_sheet" | "profit_loss" | "cash_flow" | "ratios";

export type StatementResponse = BalanceSheetResponse & { statement: StatementType };

/** `GET /api/financials/{symbol}/statement` — one full statement grid.
 *
 *  `ratios` is in here rather than in a computed-metrics endpoint because MC
 *  files its ratio sheet as a line-item statement like any other: thirty-odd
 *  ratios under Per Share / Profitability / Liquidity / Coverage / Valuation,
 *  already sectioned, already multi-year. Nothing is derived on the client. */
export function getStatement(
  symbol: string,
  type: StatementType,
  basis: "consolidated" | "standalone" = "consolidated",
  years = 10,
): Promise<ApiResult<StatementResponse>> {
  return request<StatementResponse>(
    `/financials/${encodeURIComponent(symbol)}/statement`
      + `?type=${type}&basis=${basis}&years=${years}`,
  );
}

/** One cell of the solvency-and-value matrix: a model, its number, and the
 *  fields that model happens to carry (Altman's five terms, Ohlson's implied
 *  probability, Graham's EPS and book value, DuPont's three legs). A cell with
 *  `value: null` carries the reason instead — a bank has no working capital,
 *  so Altman is not a gap in the data but a model that does not apply. */
export type ScoreQuadrant = {
  key: string;
  label: string;
  caption: string;
  format: "plain" | "pct" | "rupees";
  value: number | null;
  band?: "good" | "watch" | "risk";
  verdict?: string;
  unavailable_reason: string | null;
  terms?: Record<string, number>;
  probability_pct?: number;
  eps?: number;
  book_value_per_share?: number;
  delta_pp?: number;
  margin_pct?: number;
  asset_turnover?: number;
  equity_multiplier?: number;
};

/** One spoke of the radar: a filed ratio, its own display string, and where it
 *  sits against the ceiling that spoke is scaled to. */
export type ScoreAxis = {
  key: string;
  label: string;
  detail: string;
  value: number | null;
  display: string;
  cap: number;
  scaled: number | null;
};

export type CompanyScores = {
  available: boolean;
  symbol: string;
  kind: "corporate" | "bank";
  basis: "consolidated" | "standalone";
  period: string;
  unit: string;
  quadrants: ScoreQuadrant[];
  radar: ScoreAxis[];
  source: string;
  reason?: string;
};

/** `GET /api/financials/{symbol}/scores` — Altman Z, Ohlson O, Graham and
 *  DuPont, plus the five ratios they are built from, every term read out of
 *  ONE period of ONE basis of the same statements the page already quotes. */
export function getCompanyScores(
  symbol: string,
  basis: "consolidated" | "standalone" = "consolidated",
): Promise<ApiResult<CompanyScores>> {
  return request<CompanyScores>(
    `/financials/${encodeURIComponent(symbol)}/scores?basis=${basis}`,
  );
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
  order_type: "MARKET" | "LIMIT" | "GTT" | "SL" | "SL-M" | "OCO";
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
  // Bracket exits (single-leg only): GTT stop-loss / target as a % move from
  // the entry price. Both set → OCO pair (one fills, the other cancels).
  gtt_stoploss_pct?: number | null;
  gtt_target_pct?: number | null;
};

/** One armed bracket exit as reported by POST /orders/register. */
export type RegisteredExit = {
  id: number;
  trigger_price: number;
  status: string;
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
  /** True when the market was closed at placement, so the order is queued as
   *  an after-market order (AMO) and will execute at the next open rather
   *  than filling now. */
  queued?: boolean;
  placed_at: string;
  // Bracket exits — present on single-leg registrations that asked for them.
  exits?: {
    reference_price: number;
    exit_side: string;
    oco_group?: string | null;
    stoploss?: RegisteredExit;
    target?: RegisteredExit;
  } | null;
  exits_error?: string | null;
};

/** Market-session context the register endpoint echoes back so the UI can
 *  tell the user *when* a queued order will run. Present on both response
 *  shapes. */
export type OrderMarketContext = {
  market_open?: boolean;
  /** Human IST label for the next open, e.g. "16 Jul 2026, 09:15 IST". */
  next_open?: string;
};

export type RegisterOrderResponse =
  | (RegisteredOrder & OrderMarketContext)
  | ({ registered: RegisteredOrder[]; count: number } & OrderMarketContext);

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
  offset = 0,
): Promise<ApiResult<OrderHistoryRow[]>> {
  if (getTradingMode() === "paper") {
    return getPaperFills(limit, offset).then((r) =>
      isError(r) ? r : { data: r.data.map(adaptPaperFill) },
    );
  }
  return requestLegacy<OrderHistoryRow[]>("/orders/history", {
    query: { limit, offset },
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

// ── Open (pending / cancellable) orders ───────────────────────────────────
// Orders that haven't executed yet: AMOs queued while the market was closed,
// resting LIMIT / trigger orders, and anything the broker still reports as
// not-yet-complete. Powers the Portfolio → Orders tab. Mode-aware, like the
// history helpers: live reads /orders/open (the TradeLog blotter), paper reads
// /paper/orders (the resting paper book) adapted to the same row shape.

/** A still-open order the user can cancel before it executes. `id` is a string
 *  so the same shape serves both books (live: numeric TradeLog id; paper:
 *  uuid). */
export type OpenOrder = {
  id: string;
  symbol: string;
  exchange: string;
  transaction_type: string; // "BUY" | "SELL"
  order_type: string; // MARKET | LIMIT | SL | GTT | ...
  quantity: number;
  price: number | null;
  trigger_price: number | null;
  status: string;
  /** True when queued as an after-market order (placed while market closed). */
  queued: boolean;
  placed_at: string;
};

/** Live-mode `/orders/open` row (numeric id). */
type OpenOrderLive = Omit<OpenOrder, "id"> & { id: number };

/** `GET /orders/open` (live) or `GET /paper/orders` (paper) — the open-order
 *  blotter, newest first. */
export function getOpenOrders(): Promise<ApiResult<OpenOrder[]>> {
  if (getTradingMode() === "paper") {
    return getPaperOpenOrders().then((r) =>
      isError(r) ? r : { data: r.data.map(adaptPaperOpenOrder) },
    );
  }
  return requestLegacy<OpenOrderLive[]>("/orders/open").then((r) =>
    isError(r)
      ? r
      : { data: r.data.map((o) => ({ ...o, id: String(o.id) })) },
  );
}

function adaptPaperOpenOrder(o: PaperOpenOrder): OpenOrder {
  const st = o.status.toLowerCase();
  // A "resting" MARKET order rests because the market was CLOSED at
  // placement (paper/broker.py's market-hours gate) — that's the same
  // "queued for next open" state as a live AMO. A resting LIMIT/SL order
  // rests for a different reason (price not hit yet), so it stays "Open".
  const queued =
    st === "queued" ||
    st === "pending" ||
    (st === "resting" && o.order_type.toUpperCase() === "MARKET");
  return {
    id: o.id,
    symbol: o.symbol,
    exchange: "NSE",
    transaction_type: o.side,
    order_type: o.order_type,
    quantity: o.quantity,
    price: o.limit_price,
    trigger_price: o.trigger_price,
    status: o.status,
    queued,
    placed_at: o.created_at ?? "",
  };
}

export type CancelOrderResponse = {
  id: number | string;
  symbol: string;
  status: string;
  /** Set when the local row was cancelled but the broker couldn't confirm. */
  broker_note?: string | null;
};

/** `POST /orders/{id}/cancel` (live) or `POST /paper/orders/{id}/cancel`
 *  (paper) — pull an open order before it executes. */
export function cancelOrder(
  id: string,
): Promise<ApiResult<CancelOrderResponse>> {
  const base = getTradingMode() === "paper" ? "/paper/orders" : "/orders";
  return requestLegacy<CancelOrderResponse>(
    `${base}/${encodeURIComponent(id)}/cancel`,
    { method: "POST" },
  );
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
  /**
   * True for a benchmark index (NIFTY 50, SENSEX, BANKNIFTY, INDIAVIX, …).
   * Indices are not tradeable as cash equity — there is no order path — so the
   * detail page renders price + chart only and hides every trade affordance.
   * Absent on older payloads → treat as not-an-index.
   */
  is_index?: boolean;
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
  return cached("market-indices", 15_000, () =>
    request<IndicesResponse>("/markets/indices"),
  );
}

/** `GET /api/markets/quote/{symbol}?exchange=NSE|BSE` — full StockQuote. 404 if unknown. */
export function getStockQuote(
  symbol: string,
  exchange?: "NSE" | "BSE",
): Promise<ApiResult<StockQuote>> {
  return cached(`quote:${symbol}:${exchange ?? ""}`, 15_000, () =>
    request<StockQuote>(`/markets/quote/${encodeURIComponent(symbol)}`, {
      query: exchange ? { exchange } : undefined,
    }),
  );
}

/** `GET /api/markets/sparkline/{symbol}?range=1D|1W|1M|6M|1Y|5Y` — historical close series.
 *  Long TTL: a 1-month-or-longer close series barely moves within a session,
 *  so re-fetching it every mount (every tab switch, every remount) was pure
 *  waste — this is the single biggest win for Home-tab load time, since it's
 *  fetched per index AND per watchlist ticker (up to 11 calls on one mount). */
export function getSparkline(
  symbol: string,
  range: SparklineRange = "1M",
): Promise<ApiResult<SparklineResponse>> {
  return cached(`sparkline:${symbol}:${range}`, 10 * 60_000, () =>
    request<SparklineResponse>(
      `/markets/sparkline/${encodeURIComponent(symbol)}`,
      { query: { range } },
    ),
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
// Metric series — `GET /api/markets/metric-series/{symbol}?metric=pe|market_cap|sales_margin&range=…`
// Returns a time-series of the requested fundamental metric for the chart.
// `available:false` + empty `points` when the data cannot be computed.
// (EV/EBITDA was removed 2026-07: yfinance's shares/EBITDA/debt history is too
// sparse for Indian tickers to ever plot, so the option always came back empty.)
// ---------------------------------------------------------------------------

export type MetricSeriesMetric = "pe" | "market_cap" | "sales_margin";

export type MetricSeriesPoint = {
  t: string;
  v: number;
  /** Only populated for metric="sales_margin" — net-profit-margin %, as-of the
   *  same date as `v` (revenue, ₹ Cr). Shown as a tooltip annotation, not a
   *  separate axis line. */
  margin?: number | null;
};

export type MetricSeriesResponse = {
  symbol: string;
  metric: MetricSeriesMetric;
  range: string;
  available: boolean;
  points: MetricSeriesPoint[];
  source: string;
};

/** `GET /api/markets/metric-series/{symbol}?metric=pe|market_cap|sales_margin&range=…` — fundamental metric series. */
export function getMetricSeries(
  symbol: string,
  metric: MetricSeriesMetric,
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
  return cached("me", 5 * 60_000, () => requestLegacy<UserProfile>("/auth/me"));
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

/** `POST /auth/google` — exchange a Google OAuth access token for Pivot
 *  tokens (find-or-create by verified email, server-verified). */
export async function googleLogin(
  accessToken: string,
): Promise<ApiResult<AuthResponse>> {
  const result = await requestLegacy<AuthResponse>("/auth/google", {
    method: "POST",
    body: { access_token: accessToken },
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
  // Route through the shared, deduped refresh gate in authToken so the 401
  // retry here and the proactive refreshes from the data modules can never
  // fire two concurrent /auth/refresh calls (which would race on refresh-
  // token rotation and log the user out).
  const token = await refreshAccessToken();
  return token !== null;
}

/** `POST /auth/logout` — best-effort server-side session revocation. */
export async function logoutUser(): Promise<void> {
  try {
    await requestLegacy("/auth/logout", { method: "POST" });
  } catch {
    /* best-effort — we clear local tokens regardless */
  }
  clearToken();
  // Drop every TTL-cached response (user profile, portfolio, quotes, ...) —
  // without this a second user logging in on the same tab within a cache
  // window could momentarily see the previous user's cached data.
  _ttlCache.clear();
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
  /**
   * Persisted card payload for assistant turns. Shape:
   *   { _render_hint: "<hint>", card?: { ...raw_data fields... } }
   * `card` may be absent for old rows or oversized payloads.
   * Callers should guard with `tool_payload?.card`.
   */
  tool_payload?: { _render_hint: string; card?: Record<string, unknown> } | null;
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
 * `DELETE /api/conversations/{id}` — delete a conversation (cascades to its
 * messages). 404s are treated as success by callers (already gone).
 */
export function deleteConversation(id: string): Promise<ApiResult<unknown>> {
  return request<unknown>(`/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE",
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
  /** Clean weighted-average BUY price (ex-charges) — what to show as "Avg". */
  buy_price?: number;
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
export function getPaperFills(
  limit = 50,
  offset = 0,
): Promise<ApiResult<PaperFillRow[]>> {
  return requestLegacy<PaperFillRow[]>("/paper/fills", {
    query: { limit, offset },
  });
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
  body?: { activate?: boolean; timing_mode?: string; capital_inr?: number },
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

/** One placed leg reported by POST /api/views/expressions/{id}/place. */
export type ViewPlacedLeg = {
  id: number;
  symbol: string;
  exchange: string;
  transaction_type: string;
  order_type: string;
  quantity: number;
  price: number | null;
  status: string;
  placed_at: string;
};

export type ViewPlaceResponse = {
  registered: ViewPlacedLeg[];
  count: number;
  /** "broker" (live, placed through the connected broker) or "paper". */
  routed_to: "broker" | "paper";
};

/**
 * `POST /api/views/expressions/{expression_id}/place`
 *
 * Places the strategy's concrete, affordable whole-share basket through the
 * user's CONNECTED BROKER (live account) or the paper book — the same routing
 * seam the chat order-confirm uses. User-initiated (register-not-execute): a
 * live account with no broker session gets a 409 asking to connect one. Only
 * equity/ETF baskets are placeable; option/unaffordable strategies return a
 * 422 and the caller falls back to the automation `deployExpression`.
 */
export function placeExpression(
  expressionId: string,
  body?: {
    capital_inr?: number;
    conversation_id?: string;
    /** Per-company share counts from the deploy confirmation modal. Each
     *  symbol must be one of the strategy's own entry names; qty 0 drops it. */
    legs?: { symbol: string; quantity: number }[];
  },
): Promise<ApiResult<ViewPlaceResponse>> {
  return request<ViewPlaceResponse>(
    `/views/expressions/${encodeURIComponent(expressionId)}/place`,
    { method: "POST", body: body ?? {} },
  );
}

// ── immediate multi-asset basket placement (Deploy = execute now, paper) ─────

/** One computed leg of a basket placement (preview + skipped). */
export type BasketFillLeg = {
  symbol: string;
  /** Display name + logo (best-effort; logo_url null → FE renders a monogram). */
  name: string | null;
  logo_url: string | null;
  asset_class: string;
  exchange: string;
  weight: number;
  slice_inr: number;
  mark_inr: number | null;
  quantity: number;
  /** ok | no_price | slice_too_small | short_unsupported | market_closed
   *  | insufficient_buying_power | rejected. */
  status: string;
};

export type BasketPreviewResponse = {
  placeable: boolean;
  routed_to: "paper" | "broker";
  total_inr: number;
  legs: BasketFillLeg[];
  skipped: BasketFillLeg[];
  /** Set (shown as the pop-up reason) only when NOT placeable. */
  reason: string | null;
};

export type BasketPlacedLeg = {
  symbol: string;
  exchange: string;
  quantity: number;
  fill_price: number | null;
  status: string;
  order_id: string | null;
};

export type BasketPlaceResponse = {
  placed: BasketPlacedLeg[];
  count: number;
  routed_to: "paper" | "broker";
  total_inr: number;
  skipped: BasketFillLeg[];
};

/**
 * `POST /api/views/expressions/{id}/place-basket/preview`
 *
 * Computes the exact per-leg whole-share/unit breakdown for a basket
 * expression at `capital_inr`, WITHOUT placing anything. Feeds the deploy
 * confirmation modal. A 422 (with a plain reason) means the expression isn't a
 * placeable basket (option/hedge/pair) — surface that reason in the pop-up.
 */
export function previewPlaceBasket(
  expressionId: string,
  capitalInr: number,
): Promise<ApiResult<BasketPreviewResponse>> {
  return request<BasketPreviewResponse>(
    `/views/expressions/${encodeURIComponent(expressionId)}/place-basket/preview`,
    { method: "POST", body: { capital_inr: capitalInr } },
  );
}

/**
 * `POST /api/views/expressions/{id}/place-basket`
 *
 * Places the basket (multi-asset: Indian equities / US shares / crypto) into
 * the paper book (or the connected broker for a live account) synchronously —
 * NO workflow/agent is created. The user pressed Deploy, so it stays inside
 * register-not-execute. A 422/409 carries the exact reason for the pop-up.
 */
export function placeBasket(
  expressionId: string,
  capitalInr: number,
  conversationId?: string,
): Promise<ApiResult<BasketPlaceResponse>> {
  return request<BasketPlaceResponse>(
    `/views/expressions/${encodeURIComponent(expressionId)}/place-basket`,
    {
      method: "POST",
      body: { capital_inr: capitalInr, conversation_id: conversationId },
    },
  );
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

// ── My Views — the per-user position ledger ─────────────────────────────────

/**
 * `GET /api/views/positions` — every view the user has put a position behind
 * (open first, newest first), each with its live return since entry.
 */
export function listViewPositions(): Promise<
  ApiResult<{ items: ViewPositionItem[] }>
> {
  return request<{ items: ViewPositionItem[] }>("/views/positions");
}

/**
 * `PATCH /api/views/positions/{position_id}` — edit the exit plan
 * (take-profit / stop-loss %) or the declared position size. Send an explicit
 * `null` to clear a level. Ledger levels only — nothing is auto-executed.
 */
export function updateViewPosition(
  positionId: string,
  body: {
    take_profit_pct?: number | null;
    stop_loss_pct?: number | null;
    capital_inr?: number | null;
  },
): Promise<ApiResult<ViewPositionItem>> {
  return request<ViewPositionItem>(
    `/views/positions/${encodeURIComponent(positionId)}`,
    { method: "PATCH", body },
  );
}

/**
 * `POST /api/views/positions/{position_id}/exit` — record a partial
 * (pct < 100) or full exit of the OPEN fraction at current marks.
 * Register-not-execute: the response's `note` reminds the user to place the
 * actual exit orders in their own broker app.
 */
export function exitViewPosition(
  positionId: string,
  pct: number,
): Promise<
  ApiResult<{ position: ViewPositionItem; exited_pct: number; note: string }>
> {
  return request<{
    position: ViewPositionItem;
    exited_pct: number;
    note: string;
  }>(`/views/positions/${encodeURIComponent(positionId)}/exit`, {
    method: "POST",
    body: { pct },
  });
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

/** One resolved security record from POST /api/views/security-meta. */
export type SecurityMeta = {
  symbol: string;
  name: string;
  logo_url: string | null;
  /** in_equity | in_etf | us_equity | us_etf | crypto */
  asset_class: string | null;
  /** INR | USD */
  currency: string | null;
};

/**
 * `POST /api/views/security-meta` — batch-resolve display metadata for any
 * mix of Indian, US, ETF, or crypto symbols (max 200 per call).
 *
 * Returns one row per recognised symbol.  Unresolved symbols are simply
 * absent from the result array — callers must handle missing entries.
 */
export function fetchSecurityMeta(
  symbols: string[],
): Promise<ApiResult<SecurityMeta[]>> {
  return request<SecurityMeta[]>("/views/security-meta", {
    method: "POST",
    body: { symbols },
  });
}

// ---------------------------------------------------------------------------
// Stock detail — deep sections (/api/stock/{symbol}/*)
//
// Everything below the fold on the stock page: quarters, annual-report facts,
// segment mixes, ownership, documents. Served by backend/routers/stock_detail.py
// across three databases.
//
// `getStockSections` comes first and decides the rest: coverage for these
// assets runs from 99% of the universe down to 12%, so the page asks what a
// symbol HAS before it renders a single panel. A section with a zero count is
// not drawn at all — an empty panel reads as a broken page, not as absent data.
// ---------------------------------------------------------------------------

export type SectionCoverage = {
  quarters: { count: number; latest: string | null; bases: number };
  annual_report: {
    count: number; tasks: number; documents: number; latest_period: string | null;
  };
  revenue_mix: { count: number; market_share?: number };
  ownership: { count: number };
  documents: { count: number };
};

export type StockSections = {
  symbol: string;
  isin: string | null;
  sc_id: string | null;
  name: string | null;
  bse_scripcode: string | null;
  coverage: SectionCoverage;
};

export function getStockSections(symbol: string): Promise<ApiResult<StockSections>> {
  return request<StockSections>(`/stock/${encodeURIComponent(symbol)}/sections`);
}

/** One row of `quarterly_metrics`. Every field is PRECOMPUTED in the database —
 *  margins, YoY, QoQ and TTM included — so nothing here is derived on the
 *  client. Deriving it twice is how two parts of a product end up quoting
 *  different numbers for the same quarter. Nulls are common and real:
 *  operating_margin_pct is filled for ~59% of recent rows, EBITDA ~64%. */
export type QuarterRow = {
  period_end: string;
  period_label: string | null;
  basis: string;
  revenue: number | null;
  total_income: number | null;
  other_income: number | null;
  ebitda: number | null;
  ebit: number | null;
  depreciation: number | null;
  interest: number | null;
  employee_cost: number | null;
  raw_material: number | null;
  other_expenses: number | null;
  provisions: number | null;
  exceptional: number | null;
  pbt: number | null;
  tax: number | null;
  net_profit: number | null;
  eps_basic: number | null;
  eps_diluted: number | null;
  operating_margin_pct: number | null;
  ebitda_margin_pct: number | null;
  net_margin_pct: number | null;
  pbt_margin_pct: number | null;
  tax_rate_pct: number | null;
  interest_coverage: number | null;
  revenue_yoy_pct: number | null;
  net_profit_yoy_pct: number | null;
  ebitda_yoy_pct: number | null;
  revenue_qoq_pct: number | null;
  net_profit_qoq_pct: number | null;
  operating_margin_yoy_bps: number | null;
  net_margin_yoy_bps: number | null;
  rev_ttm: number | null;
  np_ttm: number | null;
  eps_ttm: number | null;
  rev_ttm_yoy_pct: number | null;
  np_ttm_yoy_pct: number | null;
  gross_npa_pct: number | null;
  net_npa_pct: number | null;
  roa_pct: number | null;
};

export type QuartersResponse = {
  symbol: string;
  basis: string;
  matched_on: "isin" | "sc_id";
  bases_available: string[];
  quarters: QuarterRow[];
};

export function getStockQuarters(
  symbol: string,
  basis: "consolidated" | "standalone" = "consolidated",
  limit = 20,
): Promise<ApiResult<QuartersResponse>> {
  return request<QuartersResponse>(
    `/stock/${encodeURIComponent(symbol)}/quarters?basis=${basis}&limit=${limit}`,
  );
}

/** A single extracted fact. `page` + `quote` are the point of the section —
 *  they are what lets a reader check the number against the filed document.
 *
 *  `unit_agrees` is a STRING verdict, not a boolean: "agree", "n/a", or
 *  "DISAGREE model=crore deterministic=million". A disagreement means two
 *  independent readings of the unit differ — the same class of error that
 *  produced a 10,000x mistake elsewhere — so it is surfaced, never hidden. */
export type FilingFact = {
  task: string;
  grp: string | null;
  label: string | null;
  value_text: string | null;
  unit_text: string | null;
  value_crore: number | null;
  period: string | null;
  basis: string | null;
  page: number | null;
  quote: string | null;
  grounding: string | null;
  unit_agrees: string | null;
  rollup: string | null;
  note: string | null;
  doc_sha: string | null;
};

export type FilingDocument = {
  sha256: string;
  title: string | null;
  period: string | null;
  filed_at: string | null;
  url: string | null;
  pages: number | null;
};

export type AnnualReportResponse = {
  symbol: string;
  documents: FilingDocument[];
  tasks: {
    task: string;
    label: string;
    count: number;
    groups: { grp: string; facts: FilingFact[] }[];
  }[];
  truncated: boolean;
};

export function getStockAnnualReport(
  symbol: string,
): Promise<ApiResult<AnnualReportResponse>> {
  return request<AnnualReportResponse>(
    `/stock/${encodeURIComponent(symbol)}/annual-report`,
  );
}

/** Segment splits. `charts` is a LIST because a company has several — Reliance
 *  carries seven (product, location, operating profit, capex, assets). Each
 *  carries a current snapshot AND a series per segment, which is what makes it
 *  worth a chart rather than a donut. */
export type MixChart = {
  id: number | null;
  title: string;
  current: { name: string; pct: number }[];
  series: { name: string; points: { t: number; pct: number }[] }[];
};

export type MixResponse = {
  symbol: string;
  available: boolean;
  source_name?: string | null;
  charts: MixChart[];
  market_share?: { name: string; points: { t: number; pct: number }[] }[];
};

export function getStockMix(symbol: string): Promise<ApiResult<MixResponse>> {
  return request<MixResponse>(`/stock/${encodeURIComponent(symbol)}/mix`);
}

export type PeerMetric = {
  id: string;
  label: string;
  unit: "inr" | "crore" | "percent" | "multiple" | "rupee";
};

/** Price facts for one peer, computed server-side from a year of daily closes.
 *  Every field is nullable: a window the listing is too young to cover, or a
 *  price feed that failed, prints an em-dash rather than a fabricated zero. */
export type PeerPrice = {
  price: number | null;
  change_pct: number | null;
  ret_1m: number | null;
  ret_3m: number | null;
  ret_6m: number | null;
  ret_1y: number | null;
  rsi14: number | null;
  vs_50dma: number | null;
  vs_200dma: number | null;
  from_52w_high: number | null;
};

export type PeerComparisonResponse = {
  symbol: string;
  available: boolean;
  sector: string | null;
  fields: PeerMetric[];
  catalog: PeerMetric[];
  peers: {
    sc_id: string;
    symbol: string;
    name: string;
    is_current: boolean;
    values: Record<string, number | null>;
    periods: Record<string, string | null>;
    price?: PeerPrice;
  }[];
  source?: string;
};

export function getStockPeers(symbol: string, fields: string[]): Promise<ApiResult<PeerComparisonResponse>> {
  return request<PeerComparisonResponse>(
    `/stock/${encodeURIComponent(symbol)}/peers?fields=${encodeURIComponent(fields.join(","))}`,
  );
}

export type OwnershipResponse = {
  symbol: string;
  available: boolean;
  long_business_summary?: string | null;
  website?: string | null;
  full_time_employees?: number | null;
  held_percent_institutions?: number | null;
  held_percent_insiders?: number | null;
  institutions_count?: number | null;
  institutions_float_percent?: number | null;
  sector?: string | null;
  industry?: string | null;
  city?: string | null;
  state?: string | null;
  country?: string | null;
  exchange?: string | null;
};

export function getStockOwnership(
  symbol: string,
): Promise<ApiResult<OwnershipResponse>> {
  return request<OwnershipResponse>(`/stock/${encodeURIComponent(symbol)}/ownership`);
}

export type CompanyDocument = {
  doc_type: string;
  category: string | null;
  subcategory: string | null;
  title: string | null;
  doc_date: string | null;
  fin_year: string | null;
  quarter: string | null;
  url: string | null;
  attach_size: string | null;
};

export type DocumentsResponse = {
  symbol: string;
  available: boolean;
  types: { doc_type: string; n: number }[];
  documents: CompanyDocument[];
};

export function getStockDocuments(
  symbol: string,
  docType = "",
  limit = 60,
): Promise<ApiResult<DocumentsResponse>> {
  const t = docType ? `&doc_type=${encodeURIComponent(docType)}` : "";
  return request<DocumentsResponse>(
    `/stock/${encodeURIComponent(symbol)}/documents?limit=${limit}${t}`,
  );
}

// ── shareholding (shp.* XBRL filings) ───────────────────────────────────────

/** One quarter of the stacked series. The top-level bucket keys are dynamic —
 *  a company with no promoter never emits a "Promoters" key at all — so the
 *  row is indexed rather than typed field by field. */
export type ShareholdingQuarter = {
  quarter: string;
  pledge_pct: number | null;
  [bucket: string]: string | number | null;
};

export type ShareholdingGroup = {
  label: string;
  pct: number;
  children: { label: string; pct: number }[];
};

export type ShareholdingHolder = {
  name: string;
  bucket: string | null;
  pct: number | null;
  shares: number | null;
};

export type ShareholdingResponse = {
  symbol: string;
  available: boolean;
  quarter?: string;
  quarters: ShareholdingQuarter[];
  groups?: ShareholdingGroup[];
  pledge_pct?: number | null;
  promoter_pct?: number | null;
  holders: ShareholdingHolder[];
};

export function getShareholding(
  symbol: string,
): Promise<ApiResult<ShareholdingResponse>> {
  return request<ShareholdingResponse>(
    `/stock/${encodeURIComponent(symbol)}/shareholding`,
  );
}

// ── flows: delivery % and futures open interest ─────────────────────────────

export type DeliveryRow = {
  d: string;
  close: number | null;
  qty: number | null;
  deliv_qty: number | null;
  deliv_per: number | null;
  trades: number | null;
};

export type OiRow = { d: string; oi: number | null; oi_chg: number | null };

export type FlowsResponse = {
  symbol: string;
  available: boolean;
  summary: {
    date: string | null;
    delivery_pct: number | null;
    delivery_median_20d: number | null;
    volume: number | null;
    delivered: number | null;
    trades: number | null;
    oi: number | null;
    oi_chg: number | null;
    close: number | null;
  } | null;
  delivery: DeliveryRow[];
  oi: OiRow[];
};

export function getFlows(
  symbol: string,
  days = 180,
): Promise<ApiResult<FlowsResponse>> {
  return request<FlowsResponse>(
    `/stock/${encodeURIComponent(symbol)}/flows?days=${days}`,
  );
}

// ── bulk and block deals ────────────────────────────────────────────────────

export type Deal = {
  d: string;
  kind: string;
  client: string;
  side: string;
  qty: number | null;
  price: number | null;
  value: number | null;
};

export type DealsResponse = { symbol: string; available: boolean; deals: Deal[] };

export function getDeals(
  symbol: string,
  limit = 60,
): Promise<ApiResult<DealsResponse>> {
  return request<DealsResponse>(
    `/stock/${encodeURIComponent(symbol)}/deals?limit=${limit}`,
  );
}

// ── pattern statistics (universe base rates, with a control) ────────────────

export type PatternStat = {
  kind: string;
  family: string;
  interval: string;
  horizon: number;
  n: number;
  n_symbols: number;
  rate: number | null;
  control: number | null;
  edge: number | null;
  se: number | null;
  move: number | null;
};

export type PatternsResponse = {
  available: boolean;
  interval: string;
  horizon: number;
  options: { interval: string; horizon: number }[];
  patterns: PatternStat[];
};

export function getPatterns(
  symbol: string,
  interval = "1d",
  horizon = 20,
): Promise<ApiResult<PatternsResponse>> {
  return request<PatternsResponse>(
    `/stock/${encodeURIComponent(symbol)}/patterns?interval=${encodeURIComponent(interval)}&horizon=${horizon}`,
  );
}
