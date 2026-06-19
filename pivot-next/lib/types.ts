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

// ---------------------------------------------------------------------------
// IPO Application (chat card payload + persisted row)
// ---------------------------------------------------------------------------

export type IpoCategory =
  | "retail"
  | "snii"
  | "bnii"
  | "shareholder"
  | "employee";

export type IpoBidPriceMode = "cutoff" | "fixed";

export type IpoStatus = "upcoming" | "open" | "closed";

export type IpoType = "mainboard" | "sme";

export type IpoPriceBand = {
  min: number;
  max: number;
  is_fixed: boolean;
};

/**
 * Per-category subscription data from NSE's ipo-active-category endpoint.
 * All values are "times subscribed" floats (e.g. 2.1 = 2.1×).
 * A category with no datum is null — never fabricated as 0.
 */
export type IpoSubscription = {
  qib: number | null;
  nii: number | null;
  rii: number | null;
  employee: number | null;
  shareholder: number | null;
  overall: number | null;
  /** ISO timestamp (IST) when the data was fetched from NSE. */
  as_of?: string;
};

/** Response from GET /ipo-subscription/{symbol}. */
export type IpoSubscriptionResponse = {
  symbol: string;
  subscription: IpoSubscription | null;
  as_of?: string;
  source?: string;
  note?: string;
};

/** Locked (server-computed) fields from the IPO data feed. */
export type IpoLockedFields = {
  price_band: IpoPriceBand | null;
  lot_size: number | null;
  open_date: string;
  close_date: string;
  issue_size: string;
  rhp_url: string | null;
  registrar: string | null;
  allotment_deeplink: string | null;
  listing_date: string | null;
  /** Structured per-category subscription data. Null until IPO is open and data is available. */
  subscription: IpoSubscription | null;
};

/** Editable fields that the user can change before registering intent. */
export type IpoEditableFields = {
  category: IpoCategory;
  quantity_lots: number;
  bid_price_mode: IpoBidPriceMode;
  bid_price: number | null;
  upi_id: string;
};

/** Validation metadata server sends alongside the payload. */
export type IpoValidation = {
  min_lots: number;
  lot_size: number | null;
  amount_estimate_at_cutoff: number | null;
  retail_max_amount: number;
  sme_bypasses_retail_cap: boolean;
  upi_cap: number;
  cutoff_allowed: boolean;
  price_band: IpoPriceBand | null;
  category_options: IpoCategory[];
};

/**
 * Full payload the chat tool returns in `raw_data` when
 * `_render_hint === "ipo_application_card"`.
 */
export type IpoApplicationPayload = {
  _render_hint: "ipo_application_card";
  symbol: string;
  name: string;
  type: IpoType;
  status: IpoStatus;
  locked: IpoLockedFields;
  editable: IpoEditableFields;
  kyc: null;
  validation: IpoValidation;
  automatable: boolean;
  conversation_id: string | null;
  disclaimer: string;
};

/** Request body for `POST /ipo-applications`. */
export type IpoRegisterRequest = {
  ipo_symbol: string;
  category: IpoCategory;
  quantity_lots: number;
  bid_price_mode: IpoBidPriceMode;
  bid_price?: number;
  upi_id_masked?: string;
  conversation_id?: string | null;
};

/** Persisted row returned from `POST /ipo-applications` and the list endpoint. */
export type IpoApplication = {
  id: number;
  ipo_symbol: string;
  ipo_name: string | null;
  ipo_type: IpoType;
  category: IpoCategory;
  quantity_lots: number;
  lot_size: number;
  bid_price_mode: IpoBidPriceMode;
  bid_price: number | null;
  amount_estimate: number;
  upi_id_masked: string | null;
  status: "registered" | "withdrawn" | "intent_armed" | "applied" | "blocked" | "allotted" | "not_allotted" | "rejected";
  autonomous: boolean;
  paper_mode: boolean;
  stale: boolean;
  conversation_id: string | null;
  source: string;
  created_at: string;
  updated_at: string;
};

/** Response from `POST /ipo-applications`. */
export type IpoRegisterResponse = {
  application: IpoApplication;
  duplicate?: boolean;
  /** Present only when `duplicate` — points at the prior open intent. */
  replace_offer?: { previous_id: number; note: string };
  stale?: boolean;
  note?: string;
  /** P3: present (non-null) only when the user is in paper mode — the
   *  simulated IPO allocation written alongside the intent. */
  paper_simulation?: PaperIpoAllocation | null;
};

/** Response from `POST /ipo-applications/{id}/withdraw`. */
export type IpoWithdrawResponse = {
  application: IpoApplication;
};

/** Response from `GET /users/ipo-applications`. */
export type IpoApplicationsListResponse = {
  items: IpoApplication[];
  count?: number;
  /** Always "estimated amount you'll need" — never "blocked". */
  amount_label?: string;
};

// ---------------------------------------------------------------------------
// IPO Calendar — GET /ipo-calendar
// ---------------------------------------------------------------------------

/** One IPO entry returned by GET /ipo-calendar. */
export type IpoCalendarItem = {
  ipo_symbol: string;
  name: string;
  open_date: string | null;
  close_date: string | null;
  price_band: string | null;
  status: IpoStatus;
  type: IpoType;
};

/** Response shape for GET /ipo-calendar. */
export type IpoCalendarResponse = {
  count: number;
  items: IpoCalendarItem[];
};

// ---------------------------------------------------------------------------
// Paper IPO Allocation (P3 — simulated allotment record)
// ---------------------------------------------------------------------------

export type PaperIpoAllocation = {
  id: string;
  ipo_symbol: string;
  ipo_name: string | null;
  ipo_type: "mainboard" | "sme";
  lots_applied: number;
  quantity_applied: number;
  amount_applied: number;
  issue_price: number;
  quantity_allotted: number;
  allotment_status: "allotted" | "not_allotted" | "pending";
  allotment_date: string | null;
  listing_date: string | null;
  conversation_id: string | null;
  simulated: boolean;
  created_at: string;
  /** P3.1 — set once the listing-credit poll runs. */
  book_credited: boolean;
  paper_fill_id: string | null;
  listing_price: number | null;
  simulated_pnl: number | null;
  book_note: string | null;
};

// ---------------------------------------------------------------------------
// IPO List Card — chat render hint "ipo_list_card"
// ---------------------------------------------------------------------------

/** One IPO row from the list_upcoming_ipos tool result. */
export type IpoListItem = {
  name: string;
  symbol: string;
  price_band: string | null;
  open_date: string | null;
  close_date: string | null;
  lot_size: number | string | null;
  issue_size: string | null;
  type: "mainboard" | "sme";
  status: "upcoming" | "open" | "closed";
};

/**
 * Full payload the chat tool returns in `raw_data` when
 * `_render_hint === "ipo_list_card"`.
 */
export type IpoListPayload = {
  _render_hint: "ipo_list_card";
  count: number;
  ipos: IpoListItem[];
  source: "nse" | "unreachable";
  note: string | null;
};

// ---------------------------------------------------------------------------
// IPO Listed Card — chat render hint "ipo_listed_card"
// ---------------------------------------------------------------------------

/**
 * Full payload the chat tool returns in `raw_data` when
 * `_render_hint === "ipo_listed_card"`.
 * Carries post-listing performance: issue price → current price → gain %.
 * Any field can be null when data is unavailable — never fabricated.
 */
export type IpoListedPayload = {
  _render_hint: "ipo_listed_card";
  symbol: string;
  name: string;
  type: "mainboard" | "sme";
  issue_price: number | null;
  listing_date: string | null;
  current_price: number | null;
  listing_gain_pct: number | null;
  source: string;
  note: string | null;
};

// ---------------------------------------------------------------------------
// F&O — Option Chain (chat render hint "option_chain_card")
// ---------------------------------------------------------------------------

export type IvStatus =
  | "ok"
  | "no_arb"
  | "no_solution"
  | "wide_spread"
  | "illiquid"
  | "stale";

export type OptionSideQuote = {
  ltp: number;
  bid: number;
  ask: number;
  mid: number;
  oi: number;
  volume: number;
  iv: number | null;
  iv_status: IvStatus;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  tradingsymbol: string;
  instrument_token: number;
};

export type OptionChainRow = {
  strike: number;
  ce: OptionSideQuote | null;
  pe: OptionSideQuote | null;
};

export type ExpectedMove = {
  low: number;
  high: number;
  abs: number;
  pct: number;
} | null;

export type OptionChainPayload = {
  _render_hint: "option_chain_card";
  underlying: string;
  segment: string;
  exchange: string;
  expiry: string;
  expiries: { expiry: string; kind: "weekly" | "monthly" }[];
  spot: number | null;
  forward: number;
  forward_source: "future" | "synthetic" | "spot" | "strike_median";
  lot_size: number | null;
  atm_strike: number;
  expected_move: ExpectedMove;
  /** Chain-level aggregates computed server-side over the ATM slice. */
  max_pain?: number | null;
  pcr_oi?: number | null;
  pcr_volume?: number | null;
  total_call_oi?: number | null;
  total_put_oi?: number | null;
  t_years: number;
  rows: OptionChainRow[];
  research_only: boolean;
  source: "kite" | "mock";
  asof: string;
  disclosure: string;
  conversation_id?: string | null;
};

// ---------------------------------------------------------------------------
// F&O — Option Strategy (chat render hint "option_strategy_card")
// ---------------------------------------------------------------------------

export type StrategyLeg = {
  option_type: "CE" | "PE";
  side: "BUY" | "SELL";
  strike: number;
  tradingsymbol?: string;
  mid?: number;
  iv?: number | null;
  delta?: number | null;
  iv_status?: IvStatus;
};

export type CritiqueFlag = {
  severity: "info" | "warn" | "risk";
  text: string;
};

export type StrategyCandidate = {
  template: string;
  label: string;
  risk_tag: "conservative" | "moderate" | "aggressive";
  pop: number | null;
  max_loss: number | null;
  max_profit: number | null;
  net_premium: number;
  one_liner: string;
  legs: { option_type: "CE" | "PE"; side: "BUY" | "SELL"; strike: number }[];
};

export type OptionStrategyPayload = {
  _render_hint: "option_strategy_card";
  locked: {
    underlying: string;
    segment: string;
    exchange: string;
    spot: number | null;
    forward: number;
    expiry: string;
    expiry_kind: "weekly" | "monthly";
    lot_size: number;
    research_only: boolean;
    disclosure: string;
  };
  editable: {
    template: string;
    book: "paper" | "live";
    qty_lots: number;
    legs: StrategyLeg[];
  };
  computed: {
    net_premium: number;
    payoff: { s: number; pnl: number }[];
    /** Theoretical "today" (T+0) mark-to-market P&L curve — the smooth
     *  pre-expiry value line drawn over the kinked expiry payoff. */
    payoff_now?: { s: number; pnl: number }[];
    breakevens: number[];
    max_loss: number | null;
    max_profit: number | null;
    pop: number | null;
    net_greeks: { delta: number; gamma: number; theta: number; vega: number };
    capital_required: number;
    margin_estimate: number;
    margin_note: string;
  };
  validation: {
    lot_multiple_ok: boolean;
    min_lots: number;
    max_lots: number;
    liquidity_ok: boolean;
    liquidity_flags: string[];
    expiry_gamma_warn: boolean;
    mcx_execution_blocked: boolean;
    requires_disclosure: boolean;
  };
  critique: {
    verdict: "ok" | "caution" | "risky";
    flags: CritiqueFlag[];
    summary: string;
  };
  candidates: StrategyCandidate[];
  conversation_id?: string | null;
};

// ---------------------------------------------------------------------------
// F&O — Portfolio Greeks (chat render hint "portfolio_greeks_card")
// ---------------------------------------------------------------------------

export type GreeksBucket = {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
};

export type PortfolioGreeksPayload = {
  _render_hint: "portfolio_greeks_card";
  net: GreeksBucket;
  /** FutEq ₹ (signed) */
  delta_notional: number;
  by_underlying: Record<
    string,
    GreeksBucket & { delta_notional: number; positions: number }
  >;
  by_expiry: Record<string, GreeksBucket & { positions: number }>;
  position_count: number;
  /** Tradingsymbols we couldn't mark */
  unmarked: string[];
  /** Empty-state message */
  note?: string;
  /** Small-print provenance */
  basis?: string;
};

// ---------------------------------------------------------------------------
// F&O — Option Strategy register/withdraw request/response
// ---------------------------------------------------------------------------

export type OptionStrategyRegisterRequest = {
  underlying: string;
  expiry: string;
  template: string;
  book: "paper" | "live";
  qty_lots: number;
  legs: { option_type: "CE" | "PE"; side: "BUY" | "SELL"; strike: number }[];
  acknowledge_disclosure: boolean;
  conversation_id?: string | null;
};

export type OptionStrategyRegisterResponse = {
  success: boolean;
  strategy: {
    id: string;
    underlying: string;
    template: string;
    expiry: string;
    book: "paper" | "live";
    status: string;
    qty_lots: number;
    max_loss: number | null;
    max_profit: number | null;
    pop: number | null;
    capital_required: number;
    margin_estimate: number;
    created_at: string;
  } | null;
  error?: string | null;
};

// ---------------------------------------------------------------------------
// F&O — Option Strategy builder: live preview-recompute + chain pickers
// ---------------------------------------------------------------------------

/** Body for POST /option-strategies/compute — a non-persisting recompute. */
export type OptionStrategyComputeRequest = {
  underlying: string;
  expiry: string;
  template?: string;
  qty_lots: number;
  legs: { option_type: "CE" | "PE"; side: "BUY" | "SELL"; strike: number }[];
};

export type OptionStrategyComputeResponse = {
  success: boolean;
  /** Full card payload (locked/editable/computed/validation/critique). */
  payload: OptionStrategyPayload | null;
  error?: string | null;
};

/** One side of a strike in the trimmed builder chain. */
export type OptionChainSliceSide = {
  mid: number;
  iv: number | null;
  delta: number | null;
  oi: number | null;
  iv_status?: IvStatus;
} | null;

export type OptionChainSliceRow = {
  strike: number;
  ce: OptionChainSliceSide;
  pe: OptionChainSliceSide;
};

export type OptionChainSlice = {
  underlying: string;
  segment: string;
  exchange: string;
  spot: number | null;
  forward: number | null;
  expiry: string;
  expiries: { expiry: string; kind: "weekly" | "monthly" }[];
  atm_strike: number | null;
  lot_size: number | null;
  expected_move?: { low: number; high: number; abs: number; pct: number } | null;
  research_only: boolean;
  rows: OptionChainSliceRow[];
};

export type OptionChainSliceResponse = {
  success: boolean;
  chain: OptionChainSlice | null;
  error?: string | null;
};
