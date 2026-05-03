/**
 * Frontend state model — copied from docs/API_CONTRACT.md §11.
 *
 * Hand-written for now; on Day 5 (or earlier) we swap to types generated
 * from the FastAPI OpenAPI spec via `openapi-typescript`. Until then, any
 * drift between this file and API_CONTRACT.md is a contract violation —
 * fix the doc first, then this file.
 */

// ---------------------------------------------------------------------------
// Workflow
// ---------------------------------------------------------------------------

export type WorkflowStatus = "draft" | "active" | "paused" | "archived";

export type Step = {
  id: string;
  step_index: number;
  /** Catalog `step_type` string, e.g. "trigger.schedule" or "control.skip_if". */
  step_type: string;
  label: string | null;
  config: Record<string, unknown>;
};

export type Workflow = {
  id: string;
  name: string;
  description: string | null;
  status: WorkflowStatus;
  version: number;
  single_instance: boolean;
  created_at: string;
  updated_at: string;
  activated_at: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  steps: Step[];
};

/** List-view shape used by `GET /api/workflows`. Omits `steps`. */
export type WorkflowSummary = Omit<Workflow, "steps">;

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------

export type TriggeredBy =
  | "schedule"
  | "manual"
  | "webhook"
  | "price_alert"
  | "indicator_alert"
  | "event_alert";

export type RunStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "awaiting_approval";

export type HaltReason = "condition_not_met" | "time_budget" | null;

export type RunStepStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped"
  | "awaiting_approval";

export type RunStep = {
  step_index: number;
  step_type: string;
  status: RunStepStatus;
  started_at: string | null;
  finished_at: string | null;
  output: Record<string, unknown> | null;
  error_message: string | null;
  attempts: number;
};

/** Full run detail — returned by `GET /api/runs/{id}`. */
export type Run = {
  id: string;
  workflow_id: string;
  workflow_version: number;
  triggered_by: TriggeredBy;
  started_at: string;
  finished_at: string | null;
  status: RunStatus;
  halt_reason: HaltReason;
  error_message: string | null;
  /** Keyed by stringified step_index; "webhook_payload" is also a reserved key. */
  context: Record<string, Record<string, unknown>>;
  steps: RunStep[];
};

/**
 * List-view summary — returned by `GET /api/workflows/{id}/runs` items.
 * Omits `context` and `steps`; adds `step_count` for display.
 */
export type RunSummary = Omit<Run, "context" | "steps"> & {
  step_count: number;
};

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

export type ApprovalDecision = "approved" | "rejected" | null;

export type Approval = {
  id: string;
  run_id: string;
  step_index: number;
  summary: string;
  requested_at: string;
  expires_at: string;
  decision: ApprovalDecision;
  decided_at: string | null;
};

// ---------------------------------------------------------------------------
// Step type catalog
// ---------------------------------------------------------------------------

/** A single category from the catalog response — `id` matches the `category` on every step type. */
export type StepCategory = {
  id: "trigger" | "fetch" | "condition" | "action" | "notify" | "control";
  label: string;
};

/**
 * JSON Schema (draft 2020-12) shape, narrowed to the subset we receive.
 * Kept loose on purpose — the StepConfigDrawer feeds this to a schema-to-zod
 * adapter at runtime. Treated as opaque JSON elsewhere.
 */
export type ConfigSchema = {
  type: "object";
  properties?: Record<string, unknown>;
  required?: string[];
  // Permit any additional JSON-schema field without typing them all.
  [extra: string]: unknown;
};

export type StepTypeDef = {
  step_type: string;
  category: StepCategory["id"];
  label: string;
  description: string;
  /** lucide-react icon name, e.g. "clock", "wallet". */
  icon: string;
  max_retries: number;
  trigger_only: boolean;
  config_schema: ConfigSchema;
  output_schema: ConfigSchema | null;
};

export type StepTypeCatalog = {
  catalog_version: string;
  categories: StepCategory[];
  step_types: StepTypeDef[];
};

// ---------------------------------------------------------------------------
// Errors (envelope from API_CONTRACT.md §2)
// ---------------------------------------------------------------------------

export type ErrorCode =
  | "validation_error"
  | "not_found"
  | "state_conflict"
  | "unauthenticated"
  | "not_yet_available"
  | "internal_error"
  | "rate_limited";

export type ErrorBody = {
  code: ErrorCode | string;
  message: string;
  details?: Record<string, unknown>;
};

/**
 * Result wrapper for the typed API client.
 *
 * Successful responses resolve to `{ data: T }`. Backend errors (any non-2xx
 * with the standard `{ error }` envelope) resolve to `{ error: ErrorBody }`.
 * Network / parse failures throw — callers should catch where relevant.
 */
export type ApiResult<T> = { data: T } | { error: ErrorBody };

export function isError<T>(
  result: ApiResult<T>,
): result is { error: ErrorBody } {
  return "error" in result;
}

// ---------------------------------------------------------------------------
// WebSocket frames (from §10.1)
// ---------------------------------------------------------------------------

export type WsSnapshotFrame = { type: "snapshot"; run: Run };
export type WsStepUpdateFrame = {
  type: "step_update";
  run_id: string;
  step_index: number;
  step: RunStep;
};
export type WsRunUpdateFrame = {
  type: "run_update";
  run_id: string;
  status: RunStatus;
  finished_at: string | null;
  halt_reason: HaltReason;
};
export type WsApprovalRequestedFrame = {
  type: "approval_requested";
  run_id: string;
  approval: Approval;
};
export type WsPingFrame = { type: "ping" };
export type WsPongFrame = { type: "pong" };

export type RunStreamFrame =
  | WsSnapshotFrame
  | WsStepUpdateFrame
  | WsRunUpdateFrame
  | WsApprovalRequestedFrame
  | WsPingFrame;

// ---------------------------------------------------------------------------
// Request bodies for mutating endpoints
// ---------------------------------------------------------------------------

export type CreateWorkflowRequest = {
  name: string;
  description?: string | null;
  single_instance?: boolean;
  steps: Array<{
    step_type: string;
    label: string | null;
    config: Record<string, unknown>;
  }>;
};

export type UpdateWorkflowRequest = Partial<{
  name: string;
  description: string | null;
  single_instance: boolean;
  steps: Array<{
    step_type: string;
    label: string | null;
    config: Record<string, unknown>;
  }>;
}>;

export type ApprovalDecisionRequest = {
  decision: "approved" | "rejected";
};

export type Paginated<T> = {
  items: T[];
  next_cursor: string | null;
};
