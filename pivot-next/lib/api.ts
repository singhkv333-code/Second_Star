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

async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  const base = getBaseUrl();
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
