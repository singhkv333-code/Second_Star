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
  /**
   * Server-computed lint diagnostics for the saved steps (errors + warnings +
   * info), so the editor can surface advisories on load without a round-trip.
   * Absent on older payloads → editor falls back to a mount-time lint call.
   */
  diagnostics?: Diagnostic[];
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

/**
 * One entry in a step type's requires[] array — describes a capability the
 * step needs from prior steps or from the user's ambient portfolio.
 * Mirrors the HTML STEPS[*].requires shape and the backend compat.py rule.
 */
export type StepCompat = {
  /** Capability tags that satisfy this requirement if present in the flow. */
  any_of: string[];
  /**
   * Ambient-flag name (e.g. "positions", "pending_orders") — if the engine's
   * ambient state carries this flag the requirement is satisfied without an
   * in-flow producer.
   */
  ambient?: string | null;
  /** Short human label, e.g. "an open position". */
  label: string;
  /** Warning message shown when the requirement is unmet. */
  warn: string;
};

export type StepTypeDef = {
  step_type: string;
  category: StepCategory["id"];
  /**
   * Sub-group within the category, used as the picker heading.
   * e.g. "Schedule & time", "Exits & protection".
   */
  group?: string;
  label: string;
  description: string;
  /** lucide-react icon name, e.g. "clock", "wallet". */
  icon: string;
  max_retries: number;
  trigger_only: boolean;
  config_schema: ConfigSchema;
  output_schema: ConfigSchema | null;
  /**
   * Connection logic — capability tags this step produces, requirements it
   * declares, and tags it consumes (clears) from accumulated world state.
   * Absent on old catalog entries → treated as permissive (no constraints).
   */
  compat?: {
    /** Tags added to the accumulated capability set when this step runs. */
    produces: string[];
    /**
     * Ordered list of capability requirements. Each entry is satisfied when
     * any tag in `any_of` is in the accumulated set, or when the matching
     * `ambient` flag is present in the engine's AmbientState.
     */
    requires: StepCompat[];
    /** Tags removed from the accumulated capability set after this step runs. */
    consumes: string[];
  };
};

// ---------------------------------------------------------------------------
// Diagnostics (returned by POST /api/workflows/lint and GET /api/workflows/{id})
// ---------------------------------------------------------------------------

/**
 * A single diagnostic from the backend's `lint_workflow` engine.
 * Mirrors the `Diagnostic` shape in pivot/backend/workflows/compat.py.
 *
 * `severity`:
 *   - "error"   — graph-internal contradiction; blocks activation
 *   - "warning" — likely-wrong but legitimately possible; never blocks
 *   - "info"    — advisory nudge; never blocks
 *
 * `code` values:
 *   - "ref_forward"          — reference to a step that comes later / doesn't exist
 *   - "ref_bad_path"         — reference to a field absent from the producing step's output_schema
 *   - "ref_type"             — reference to a field whose type mismatches the consumer
 *   - "needs_position"       — requires an open position, not satisfied by prior steps or ambient
 *   - "needs_pending_orders" — requires a pending order, not satisfied by prior steps or ambient
 *   - "needs_symbols"        — requires a symbols list (e.g. a screen/movers fetch) not satisfied
 *   - "needs_boolean"        — requires a yes/no value from a prior step, not satisfied
 *   - "trigger_placement"    — non-trigger at index 0, or trigger not in a branch-start slot
 *   - "empty_branch"         — a branch produces no action or notify step
 *   - "dead_branch"          — a trigger starts a branch that has no reachable steps
 *   - "unknown_step_type"    — step_type not found in the registry
 */
export type Diagnostic = {
  /** Zero-based index of the step that triggered this diagnostic. */
  step_index: number;
  severity: "error" | "warning" | "info";
  code:
    | "ref_forward"
    | "ref_bad_path"
    | "ref_type"
    | "needs_position"
    | "needs_pending_orders"
    | "needs_symbols"
    | "needs_boolean"
    | "trigger_placement"
    | "empty_branch"
    | "dead_branch"
    | "unknown_step_type"
    | string; // forward-compat — new codes from the backend should not crash the FE
  /** Human-readable message shown on the step card. */
  message: string;
  /** The offending config field name, if the diagnostic is field-specific. */
  field?: string | null;
  /**
   * One-click apply target — a stringified patch the FE can apply to fix the
   * issue (Phase 4 feature; may be absent in Phase 1-3).
   */
  suggested_fix?: string | null;
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
  as_of?: string | null;
  /** "nse" | "trendlyne" — which feed produced these multiples. */
  source?: string;
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
  /** Trendlyne: allotment date + status for listing-soon IPOs. */
  allotment_date?: string | null;
  allotment_status?: string | null;
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
  /** Which feeds populated this card, e.g. ["nse","trendlyne"]. */
  data_sources?: string[];
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
  /** Trendlyne enrichment (optional — present when the feed carries it). */
  subscription?: IpoSubscriptionBreakdown | null;
  rhp_url?: string | null;
  allotment_date?: string | null;
  allotment_status?: string | null;
  market_cap_cr?: number | null;
  min_investment?: number | null;
  /** False for Trendlyne-only IPOs with no NSE symbol (can't register/automate). */
  registerable?: boolean;
  /** ["nse"] | ["trendlyne"] | ["nse","trendlyne"]. */
  sources?: string[];
};

/** Trendlyne's raw subscription breakdown (times-subscribed multiples). */
export type IpoSubscriptionBreakdown = {
  total?: number | null;
  retail?: number | null;
  hni?: number | null;
  qib?: number | null;
};

/**
 * Full payload the chat tool returns in `raw_data` when
 * `_render_hint === "ipo_list_card"`.
 */
export type IpoListPayload = {
  _render_hint: "ipo_list_card";
  count: number;
  ipos: IpoListItem[];
  /** "nse" | "trendlyne" | "nse+trendlyne" | "unreachable". */
  source: string;
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
  /** Trendlyne: the listing-day pop (issue → first-day open), distinct from
   *  the current return (listing_gain_pct above). */
  listing_day_gain_pct?: number | null;
  subscription?: IpoSubscriptionBreakdown | null;
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
// Strategy builder + dynamic clarifying-questions
//
// Mirrors pivot/backend/services/strategy_contracts.py 1:1. The wire is
// snake_case (Pydantic v2 model_dump), so — matching every other payload in
// this file (e.g. OptionStrategyPayload) — we consume snake_case directly
// rather than adding a camelCase mapping layer. Keep the string-literal
// unions byte-identical to the backend Literal enums.
//
// Render hints (raw_data._render_hint):
//   "clarify_card"           → raw_data = { clarify: ClarifyCard }
//   "strategy_builder_card"  → raw_data = { ...StrategyBuilderCard }
// ---------------------------------------------------------------------------

// ── Slot vocabularies (closed enums) ───────────────────────────────────────

export type ViewDirection = "bull" | "bear" | "neutral" | "none";
export type ViewTarget = "stock" | "sector" | "index" | "market";
export type Conviction = "low" | "medium" | "high";
export type RiskLevel = "conservative" | "balanced" | "aggressive";
export type Horizon = "tactical" | "medium" | "long";
export type AssetClass = "equity" | "etf_mf" | "options" | "gold";

// ── Builder vocabularies ───────────────────────────────────────────────────

export type WeightingScheme =
  | "equal"
  | "mcap"
  | "risk_parity"
  | "min_variance"
  | "black_litterman"
  | "factor";

export type SelectionGate = "fscore" | "magic_formula" | "multifactor" | "none";

export type SleeveKind = "gold" | "options" | "hedge";

export type GoldInstrumentKind = "sgb" | "etf";

// ── Slot-state (travels in-band on ClarifyCard.session_slot_state) ──────────

export type ViewSlot = {
  direction: ViewDirection;
  target: ViewTarget;
  conviction: Conviction;
};

export type AssetPrefs = {
  allow: AssetClass[];
  deny: AssetClass[];
  exclusions: string[];
};

export type SlotAssumptions = {
  view: boolean;
  risk: boolean;
  horizon: boolean;
  capital_inr: boolean;
  asset_prefs: boolean;
  theme: boolean;
};

export type SlotState = {
  view: ViewSlot;
  risk: RiskLevel;
  horizon: Horizon;
  capital_inr: number | null;
  asset_prefs: AssetPrefs;
  theme: string | null;
  assumed: SlotAssumptions;
};

// ── Clarify payload (raw_data._render_hint === "clarify_card") ──────────────

export type ClarifyOption = {
  id: string;
  label: string;
};

export type ClarifyQuestion = {
  id: string;
  slot: string;
  prompt: string;
  voi: number;
  options: ClarifyOption[];
  free_text: boolean;
  skippable: boolean;
};

export type ClarifyCard = {
  session_slot_state: SlotState;
  total: number;
  index: number;
  questions: ClarifyQuestion[];
};

/** Shape of the raw_data the backend emits under the "clarify_card" hint. */
export type ClarifyCardPayload = {
  _render_hint: "clarify_card";
  clarify: ClarifyCard;
};

/**
 * One recorded answer from a local-paged clarify flow.
 * `value` is the canonical answer sent to the backend (option id, free text, or "skip").
 * `label` is the human-readable label shown in the summary (option label, free text, or "Skipped").
 */
export type ClarifyAnswerRecord = {
  slot: string;
  prompt: string;
  value: string;
  label: string;
};

// ── Strategy-builder payload (hint === "strategy_builder_card") ─────────────

export type StrategyConstituent = {
  symbol: string;
  name: string;
  sector: string;
  weight_pct: number;
  gate_metrics: Record<string, number>;
};

export type GoldInstrument = {
  kind: GoldInstrumentKind;
  symbol: string;
  name: string;
  weight_pct: number;
};

export type Sleeve = {
  kind: SleeveKind;
  pct: number;
  instruments: GoldInstrument[];
  note?: string | null;
};

/**
 * One "you might prefer this instead" alternative strategy (backend
 * `StrategyAlternative`). `title` is the short heading the FE shows (e.g.
 * "Value tilt", "Lower-risk", "Passive"); `detail` is the 1-2 plain-English
 * sentences explaining what it changes and *when the user would prefer it*.
 * Alternatives are suggestions, not selectable legs — rendered as explained
 * text, never as constituents. Nothing here is registered until the user
 * re-asks for one.
 */
export type StrategyAlternative = {
  title: string;
  detail: string;
};

export type StrategyBuilderCard = {
  title: string;
  rationale: string;
  weighting_scheme: WeightingScheme;
  selection_gate: SelectionGate;
  sector_cap: number;
  constituents: StrategyConstituent[];
  sleeves: Sleeve[];
  assumptions: string[];
  /**
   * 1-3 genuinely different strategies the user might prefer instead of the
   * proposed basket. Defaults to `[]` from the backend, so always present.
   */
  alternatives: StrategyAlternative[];
  disclaimer: string;
};

/**
 * Shape of the raw_data the backend emits under the "strategy_builder_card"
 * hint — the StrategyBuilderCard fields are spread at the top level alongside
 * the render hint (NOT nested), mirroring the executor's
 * `{ "_render_hint": ..., **card.model_dump() }`.
 */
export type StrategyBuilderCardPayload = StrategyBuilderCard & {
  _render_hint: "strategy_builder_card";
};

// ---------------------------------------------------------------------------
// DSL condition tree — the visual ConditionBuilder editor
//   GET  /api/workflows/dsl/schema    (operand-picker metadata)
//   POST /api/workflows/dsl/describe  (english readback)
// Node shapes mirror pivot/backend/workflows/dsl/schema.py (the subset the
// builder renders; advanced nodes go through the raw-JSON escape hatch).
// ---------------------------------------------------------------------------

/** A registry-backed indicator value leaf (RSI/MACD/SMA…). */
export type DslIndicatorNode = {
  type: "indicator";
  indicator: string;
  symbol: string;
  period: number;
  timeframe?: "daily" | "weekly";
  exchange?: string;
  offset?: number;
  component?: string | null;
};

/** A bar-component price leaf (close/open/high/low). */
export type DslPriceNode = {
  type: "price";
  symbol: string;
  basis: "close" | "open" | "high" | "low";
  exchange?: string;
  offset?: number;
};

/** A literal number leaf — the right-hand side of most comparisons. */
export type DslConstantNode = {
  type: "constant";
  value: number;
};

/** A position-property leaf — only valid inside an EXIT tree. */
export type DslPositionNode = {
  type: "position";
  field: string;
  basis?: "close" | "low" | "high" | null;
};

export type DslLeafNode =
  | DslIndicatorNode
  | DslPriceNode
  | DslConstantNode
  | DslPositionNode;

/** A binary comparison `left <op> right`. */
export type DslComparisonNode = {
  type: "comparison";
  op: string;
  left: DslNode;
  right: DslNode;
};

/**
 * and / or joining sub-trees. (The grammar also allows `not`, but the visual
 * builder only constructs and/or; a `not` tree reaches the builder only via
 * the raw-JSON escape hatch, which casts.)
 */
export type DslLogicNode = {
  type: "logic";
  op: "and" | "or";
  operands: DslNode[];
};

/** Any node in a condition tree (recursive). */
export type DslNode = DslLeafNode | DslComparisonNode | DslLogicNode;

/** One entry in the DSL schema's `indicators` list. */
export type DslIndicatorMeta = {
  id: string;
  label: string;
  default_period: number;
  multi_output: boolean;
  components: string[];
};

/** An id+label pair (operators, position_fields). */
export type DslLabeled = { id: string; label: string };

/** Which config field holds the tree + whether position leaves are allowed. */
export type DslTreeFieldMeta = { field: string; mode: "entry" | "exit" };

/** Response of `GET /api/workflows/dsl/schema`. */
export type DslSchema = {
  indicators: DslIndicatorMeta[];
  operators: DslLabeled[];
  operand_kinds: string[];
  price_bases: string[];
  position_fields: DslLabeled[];
  logic_ops: string[];
  timeframes: string[];
  tree_fields: Record<string, DslTreeFieldMeta>;
};

/** Response of `POST /api/workflows/dsl/describe`. */
export type DslDescribeResult = { english: string; error?: string | null };
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

// ---------------------------------------------------------------------------
// Multi-broker onboarding — GET /brokers + per-broker connect endpoints.
//
// Mirrors the backend's /brokers router (bare-mounted, no /api prefix, same as
// the legacy /kite router). The FE consumes the snake_case wire directly,
// matching every other payload in this file. Replaces the Kite-only types
// (KiteStatus / KiteLoginUrl / KiteCredentialsStatus) in lib/api.ts.
// ---------------------------------------------------------------------------

/**
 * How a broker holds its session once connected. These are the backend
 * `PersistenceKind` enum *values* on the wire (pivot/backend/brokers/base.py),
 * consumed verbatim — do NOT invent FE-only aliases here, or the display
 * helpers (broker-ui.ts) silently fall through to the wrong copy:
 *   - "daily_oauth"   — re-auth each day (Kite default OAuth access token).
 *   - "api_key_mint"  — a long-lived API key mints a fresh daily token
 *                       (Dhan PIN+TOTP) — unattended, no daily human step.
 *   - "rolling_renew" — a 24h token rolled forward before expiry (Dhan
 *                       RenewToken) — stays connected, no daily login.
 *   - "refresh_token" — silent refresh token (Fyers ~15d) — unattended.
 *   - "totp_login"    — opt-in: stored credentials replay the login (Kite
 *                       advanced auto-login).
 * `string` keeps the FE forward-compatible with kinds the backend adds later
 * (the helpers degrade to the safe "daily login" copy for unknowns).
 */
export type BrokerPersistenceKind =
  | "daily_oauth"
  | "api_key_mint"
  | "rolling_renew"
  | "refresh_token"
  | "totp_login"
  | string;

/** Deep-links the onboarding UI surfaces as one-click "go straight there"
 *  buttons. All optional — only render a button when the link is present. */
export type BrokerDeepLinks = {
  /** Hosted OAuth login page (rare in the static catalog; usually fetched
   *  fresh via GET /brokers/{id}/login_url). */
  login?: string;
  /** "Create an API app" page (Kite developer console). */
  app_create?: string;
  /** The page where the user copies their API key/secret (Dhan). */
  api_key_page?: string;
  /** TOTP / external-authenticator setup page. */
  totp_setup?: string;
  /** Broker API docs. */
  docs?: string;
};

/** Live connection status for one broker, embedded in the catalog row and
 *  returned standalone by every connect/automation/disconnect endpoint. */
export type BrokerStatus = {
  connected: boolean;
  /** True when the backend has no real credentials and is serving stub data —
   *  the picker shows a "Connect (mock)" affordance in this mode. */
  mock_mode: boolean;
  /** Broker-side user id once connected (e.g. Kite user id / Dhan client id). */
  broker_user_id?: string | null;
  /** Resolved persistence mode for THIS connection (may differ from the
   *  catalog default once the user opts into auto-login). */
  persistence_mode?: BrokerPersistenceKind | null;
  /** True when the user enabled unattended/auto-login for this broker. */
  auto_login_opt_in?: boolean | null;
  /** ISO 8601 — when the current token/session expires. Null = no expiry. */
  expires_at?: string | null;
};

/** One broker as described by GET /brokers. */
export type Broker = {
  /** Stable slug — "kite" | "dhan" | …; also the logo filename (/brokers/{id}.svg). */
  id: string;
  name: string;
  /** Server-provided logo path; the UI falls back to /brokers/{id}.svg. */
  logo: string;
  persistence_kind: BrokerPersistenceKind;
  /** True when the broker can run fully unattended (server-refreshed token). */
  supports_unattended: boolean;
  /** True when the broker uses a hosted OAuth login (Kite, Fyers). Preferred
   *  over the id heuristic when deciding the OAuth-vs-api-key connect flow.
   *  Optional for forward-compat with payloads that predate the field. */
  supports_oauth?: boolean;
  /** True when connecting needs a typed API key/secret (Dhan, Kite-advanced). */
  needs_api_key: boolean;
  /** Brand accent (hex), used as a thin tasteful accent — never a full wash. */
  accent: string;
  /** One-line value prop. */
  blurb: string;
  /** Short capability chips, e.g. ["No daily login", "Full automation"]. */
  tags: string[];
  deep_links: BrokerDeepLinks;
  status: BrokerStatus;
};

/** Response of GET /brokers. */
export type BrokersResponse = { brokers: Broker[] };

/** Response of GET /brokers/{broker}/login_url (OAuth brokers, e.g. kite). */
export type BrokerLoginUrl = {
  mock_mode: boolean;
  /** Null in mock mode or when the broker has no OAuth login. */
  login_url: string | null;
  state: string;
};

/** Body for POST /brokers/{broker}/credentials. Fields are broker-specific:
 *  Dhan uses api_key/api_secret (+ optional client_id/pin/totp_secret); Kite's
 *  advanced auto-login uses the same shape with auto_login_opt_in=true. */
export type BrokerCredentialsRequest = {
  api_key?: string;
  api_secret?: string;
  client_id?: string;
  pin?: string;
  totp_secret?: string;
  /** Dhan: a generated access token (kept alive server-side via RenewToken). */
  access_token?: string;
  /** Kite advanced "stay connected" path: the user's Kite account password.
   *  Sent only over the credentials POST; stored encrypted server-side. */
  password?: string;
  auto_login_opt_in?: boolean;
};

/** Body for POST /brokers/{broker}/automation. */
export type BrokerAutomationRequest = { auto_login_opt_in: boolean };

/** One holding row from GET /brokers/{broker}/holdings. Kept loose — different
 *  brokers expose slightly different fields; the preview renders what's there.
 *  Mirrors the legacy Holding shape (Kite-derived) so the table stays familiar. */
export type BrokerHolding = {
  tradingsymbol: string;
  exchange?: string;
  quantity: number;
  average_price?: number;
  last_price?: number;
  pnl?: number;
  day_change?: number;
  day_change_percentage?: number;
};

/** Response of GET /brokers/{broker}/holdings. */
export type BrokerHoldingsResponse = { holdings: BrokerHolding[] };

/** Response of DELETE /brokers/{broker}/session. */
export type BrokerDisconnectResponse = { connected: false };

// ---------------------------------------------------------------------------
// Views — View Markets V2 (GET /api/views, GET /api/views/{id}, …)
//
// Mirrors docs/API_CONTRACT.md "Views" section. All score-ish fields are
// Optional/nullable — the FE must never fabricate a missing value.
// ---------------------------------------------------------------------------

export type ViewStatus =
  | "draft"
  | "developing"
  | "published"
  | "resolved"
  | "archived";

export type ViewType = "EVENT" | "THEME";

export type ExpressionTier = "conservative" | "balanced" | "aggressive";

export type ExpressionKind = string; // open — backend can add new kinds without FE change

export type TrustVerdict =
  | "PROMISING"
  | "UNPROVEN"
  | "NO_EDGE"
  | "INSUFFICIENT_DATA";

export type Grade = "A" | "A-" | "B" | "B-" | "C" | "D" | "F";

/** A confidence dial letter — includes the sentinel "SUPPRESSED" for honest rendering. */
export type Dial = Grade | "SUPPRESSED";

/**
 * One point on a real, backend-computed EPISODE-GATED equity curve. `strategy`
 * and `benchmark` are indexed currency levels (start = 100000); `t` is the
 * SEQUENTIAL in-position trading-day index ("0","1","2",…) — NOT a calendar
 * date — because the strategy is only in the market during event/season
 * windows and calendar time has gaps between episodes. Mirrors the backend
 * CurvePoint model. Used by the gallery mini-line and the detail-page line
 * chart + per-strategy comparison.
 */
export type EquityPoint = {
  t: string;
  strategy: number;
  benchmark: number;
};

/** One named member of a basket expression, with its episode-window return. */
export type Holding = {
  name: string;
  symbol: string;
  return_pct: number;
  /** "long" | "short" — the side held. Optional for forward-compat. */
  position?: string | null;
  /** Weight of this name in the basket (e.g. 16.7 = 16.7%). Optional. */
  weight_pct?: number | null;
};

/**
 * One historical episode row — a past window the strategy was in the market,
 * with its own return vs the benchmark over that window. Real backtest rows;
 * never fabricated. `positive` = the strategy made money that episode.
 */
export type EpisodeRow = {
  label: string;
  date: string;
  return_pct: number;
  benchmark_pct: number;
  positive: boolean;
};

/** Plain "how well past episodes lined up" dial (score 0-100 + letter grade). */
export type HistoricalAlignment = {
  score: number | null;
  letter: string | null;
};

/**
 * Monte-Carlo outcome spread for an expression (null when not simulated).
 * `terminal_pct` is the sorted/percentile-laddered list of simulated terminal
 * returns; p05..p95 are the percentile cut points; `prob_loss` is the share of
 * sims that ended below zero. Real simulated data — never fabricated.
 */
export type MonteCarlo = {
  n_sims: number;
  terminal_pct: number[];
  p05: number;
  p25: number;
  median: number;
  p75: number;
  p95: number;
  prob_loss: number;
};

/** One leg of an option-structure expression (honest, non-fabricated identity). */
export type OptionLeg = {
  action: string; // "BUY" | "SELL"
  option_type: string; // "CE" | "PE"
  strike_rule: string | null;
  delta: number | null;
  strike_offset: number | null;
};

/** A sibling view surfaced as "similar" on the detail page. */
export type SimilarView = {
  id: string;
  short_title: string | null;
};

/**
 * Presentation-only Yes/No stance block, shown under the view title. A
 * reading device — NOT a bet, wager, or clickable contract (V1 scope forbids
 * becoming a prediction exchange). `no.has_trade === false` means the honest
 * "no clean trade" case (e.g. an asymmetric event like Middle-East
 * de-escalation) — render calmly, never as a failure.
 */
export type ViewStanceSide = {
  verdict: string;
  summary: string;
};

export type ViewStanceNoSide = ViewStanceSide & {
  has_trade: boolean;
};

export type ViewStance = {
  yes: ViewStanceSide;
  no: ViewStanceNoSide;
};

/**
 * Which side of the Yes/No stance a gallery-card click intends. Threaded from
 * ViewCard's Yes/No buttons through to ViewDetailPage so the opened detail can
 * scroll to + highlight the chosen side and its deployable strategy. NOT a
 * wager — just navigation intent.
 */
export type StanceIntent = "yes" | "no";

/** One side (basket or benchmark) of a fundamentals comparison. */
export type FundamentalSide = {
  pe: number | null;
  roe: number | null;
};

/** Basket-vs-Nifty fundamentals comparison (null when not computed). */
export type FundamentalComparison = {
  basket: FundamentalSide;
  nifty: FundamentalSide;
};

/** Per-dimension confidence block from GET /api/views/{id}. */
export type ViewConfBlock = {
  score: number | null;
  letter: string | null;
  evidence?: string | null;
};

/** Slim confidence block embedded in ViewSummary (no evidence text). */
export type ViewConfBlockSummary = {
  score: number | null;
  letter: string | null;
};

/** Best-scored expression headline, embedded in ViewSummary. */
export type BestExpression = {
  id: string;
  tier: ExpressionTier;
  expression_kind: ExpressionKind;
  grade: Grade | null;
  trust_verdict: TrustVerdict | null;
  total_return_pct: number | null;
  excess_return_pct: number | null;
  // Clean whitelisted numbers, hoisted out of scores.backtest.nifty_comparison.
  plain_label: string | null;
  nifty_total_pct: number | null;
  n_episodes: number | null;
  pct_episodes_beat: number | null;
  worst_drop_pct: number | null;
  /** Fraction (0..100) of past occurrences with return_pct > 0 — the
   *  benchmark-free replacement headline stat for pct_episodes_beat. */
  pct_positive?: number | null;
  /** Integer count of positive-outcome occurrences (out of n_episodes). */
  n_positive?: number | null;
  // Real backend-computed curve for the gallery mini-line (may be empty).
  equity_curve: EquityPoint[];
};

/** Shape returned by GET /api/views (list item). */
export type ViewSummary = {
  id: string;
  view_type: ViewType;
  title: string;
  thesis: string;
  category: string;
  time_horizon: string | null;
  status: ViewStatus;
  resolution_date: string | null;
  created_at: string;
  published_at: string | null;
  outcome_confidence: ViewConfBlockSummary;
  expression_confidence: ViewConfBlockSummary;
  best_expression: BestExpression | null;
  expression_count: number;
  transmission_count: number;
  follower_count: number;
  is_following: boolean;
  /**
   * True when there is NO finished/headline basket yet (a "developing" idea).
   * Shared source of truth: the gallery card shows "No finished basket" and the
   * detail page frames its numbers as a historical backtest (never as a live,
   * deployable basket) off this same flag — the two surfaces never contradict.
   */
  is_developing: boolean;
  // Layman content layer (the belief in plain English).
  plain_one_liner: string | null;
  plain_summary: string | null;
  /** Punchy, dateless question for the card, e.g. "Will cheaper oil lift India's importers?". */
  short_title: string | null;
  /**
   * Presentation-only Yes/No stance for the gallery card's two-button
   * (Polymarket/Kalshi-style) affordance. Null for views with no authored
   * stance (e.g. live curated views not yet backfilled, or developing ideas) —
   * the card then falls back to a plain "View details" affordance. NEVER a
   * bet/contract: the buttons open the view + route to a real securities
   * expression, they don't price a binary outcome.
   */
  stance?: ViewStance | null;
  /**
   * The single BEST past occurrence of the headline strategy (return % + its
   * label) — the most striking-yet-honest number to surface on the card. Always
   * paired with the typical return so it never over-promises. Null when there is
   * no per-occurrence sample.
   */
  best_episode_pct?: number | null;
  best_episode_label?: string | null;
};

/** One edge in the causal transmission map, ordered by seq. */
export type TransmissionEdge = {
  seq: number;
  from_node: string;
  to_node: string;
  edge_label: string;
  strength: number | null;
  evidence?: string | null;
  // Humanized layer (plain words only — never raw CAAR/t/p evidence).
  from_label: string | null;
  to_label: string | null;
  strength_label: string | null;
  plain_evidence: string | null;
};

/** One "what's priced in" row (option-implied or prediction-market source). */
export type ViewExpectationRow = {
  source: string;
  market_id: string | null;
  expected_value: number | null;
  user_view_value: number | null;
  surprise_sign: "positive" | "negative" | "inline" | null;
  as_of: string | null;
  resolved_value: number | null;
  // Closed-map source label (unknown -> 'Market estimate').
  source_label: string | null;
};

/**
 * Full backtest trust block — every field is optional/nullable because the
 * backend may return partial results or none at all when backtesting hasn't
 * run yet. Mirrors the ExpressionDetail.scores.backtest wire shape.
 */
export type BacktestScores = {
  grade?: Grade | null;
  trust_verdict?: TrustVerdict | null;
  trust_conf?: number | null;
  total_return_pct?: number | null;
  excess_return_pct?: number | null;
  nifty_same_window_pct?: number | null;
  nifty_buy_hold_total_pct?: number | null;
  max_dd_pct?: number | null;
  win_rate?: number | null;
  psr?: number | null;
  dsr?: number | null;
  min_trl?: number | null;
  min_trl_cleared?: boolean | null;
  mc_prob_loss?: number | null;
  mc_dd_p95_pct?: number | null;
  n_obs?: number | null;
  n_events?: number | null;
  n_episodes?: number | null;
  caar_pct?: number | null;
  caar_p?: number | null;
  caar_t?: number | null;
  sub_period_pos_frac?: number | null;
  sub_period_returns_pct?: number[] | null;
  outcome_dial?: Dial | null;
  outcome_score?: number | null;
  expression_dial?: Dial | null;
  expression_score?: number | null;
  version?: number | string | null;
};

/** Composite scores block embedded in ExpressionDetail. */
export type ExpressionScores = {
  backtest?: BacktestScores | null;
  construction_alignment?: number | null;
  alignment_kind?: string | null;
};

/**
 * The structure field is a passthrough of the backend's config.structure dict.
 * We type the well-known sub-fields and allow extras via index signature.
 */
export type ExpressionStructure = {
  scheme?: string;
  n_names?: number;
  weights?: Record<string, number>;
  single_name_cap?: number;
  basket_purity?: number;
  min_names?: number;
  signal?: string;
  hold_bars?: number;
  legs?: unknown[];
  [k: string]: unknown;
};

/** One instrument leg inside an expression. Passed through from the backend
 *  config; tolerant of a bare-symbol string or the richer object form. */
export type ExpressionInstrument = {
  symbol?: string;
  role?: string;
  segment?: string;
  exchange?: string;
  tradeable?: boolean;
  instrument_type?: string;
  note?: string;
  [k: string]: unknown;
};

/** One expression returned inside GET /api/views/{id} detail. */
/** One leg of the modelled option structure (spot normalised to 100). */
export type OptionModelLeg = {
  action: string; // "BUY" | "SELL"
  option_type: string; // "CE" | "PE"
  strike_pct: number; // % moneyness (spot = 100)
  strike_label: string; // "ATM" | "+5%"
};

/** One point of the priced payoff curve: terminal underlying move → P&L as % of
 *  the capital deployed (the debit premium). */
export type OptionModelPayoffPoint = { move_pct: number; pnl_pct: number };

/** REAL Black–Scholes model of the option tier's defined-risk vertical. Every
 *  number is computed (max loss/profit are % of the capital deployed); the
 *  historical return stays "priced at deploy" — this is the payoff SHAPE. */
export type OptionModel = {
  structure: string; // "bull_call_spread" | "bear_put_spread"
  direction: "bullish" | "bearish";
  underlying_label: string | null;
  legs: OptionModelLeg[];
  net_premium_pct: number;
  width_pct: number;
  max_loss_pct: number; // -100 (the debit) for a defined-risk spread
  max_profit_pct: number; // % of capital deployed
  breakeven_move_pct: number;
  pop_pct: number; // lognormal probability of profit at expiry
  net_greeks: { delta: number; gamma: number; vega: number; theta: number };
  vol_used_pct: number;
  horizon_days: number;
  payoff: OptionModelPayoffPoint[];
  basis: string;
  assumptions: string;
};

export type ExpressionDetail = {
  id: string;
  tier: ExpressionTier;
  expression_kind: ExpressionKind;
  label: string;
  rationale: string;
  risk_profile: string;
  capital_intensity: string;
  historical_strength: string;
  time_horizon: string;
  workflow_id: string | null;
  backtest_run_id: string | null;
  instruments: (string | ExpressionInstrument)[];
  warnings: string[];
  disclaimer: string | null;
  structure: ExpressionStructure;
  scores: ExpressionScores | null;
  is_deployable: boolean;
  // Layman content layer + hoisted clean numbers (from scores.backtest.nifty_comparison
  // and scores.backtest.max_dd_pct). Any missing source -> null; FE shows '—'.
  plain_label: string | null;
  plain_one_liner: string | null;
  plain_why: string | null;
  plain_risk: string | null;
  /** Plain words only: 'Low' / 'Low-medium' / 'Medium' — NEVER a rupee figure. */
  capital_label: string | null;
  /** Plain word: 'Not enough data' | 'No edge yet' | 'Unproven' | 'Promising'. */
  trust_badge: string | null;
  /** Plain stock display names for the basket, e.g. ['Britannia','MRF', …]. */
  members: string[];
  n_names: number | null;
  strategy_total_pct: number | null;
  nifty_total_pct: number | null;
  excess_return_pct: number | null;
  n_episodes: number | null;
  pct_episodes_beat: number | null;
  worst_drop_pct: number | null;
  /** Fraction (0..100) of past occurrences with return_pct > 0 — the
   *  benchmark-free replacement headline stat for pct_episodes_beat. */
  pct_positive?: number | null;
  /** Integer count of positive-outcome occurrences (out of n_episodes). */
  n_positive?: number | null;
  // ── honest strategy identity ──
  /** Fun plain strategy name, e.g. "Slow & Steady rural bundle". */
  strategy_name: string | null;
  /** Plain strategy type, e.g. "Basket" / "Pair" / "Call spread". */
  strategy_type: string | null;
  /** Option legs when this expression is an option structure; null otherwise. */
  option_legs: OptionLeg[] | null;
  /** One-line note describing the option structure (null when no legs). */
  option_legs_note: string | null;
  // ── real computed chart + per-holding returns ──
  /** Real backend-computed strategy-vs-benchmark curve (may be empty). */
  equity_curve: EquityPoint[];
  /** Per-name returns for a basket (empty for non-basket expressions). */
  holdings: Holding[];
  /** Underlying symbol for an option structure (null for baskets). */
  underlying_symbol: string | null;
  /** What the curve is measured on, e.g. "in_position_episodes" / "underlying". */
  curve_basis: string | null;
  /** Reward-to-risk ratio (null when not computed). */
  risk_return_ratio: number | null;
  /** Number of episodes the in-position curve concatenates (null when no curve). */
  curve_n_episodes?: number | null;
  /** In-position indices where each new episode starts (for stitch markers). */
  episode_boundaries?: number[];
  // ── per-episode breakdown + hold window + outcome spread ──
  /** Per-episode history (each past window, return vs benchmark). May be empty. */
  episodes?: EpisodeRow[];
  /** How many of the episodes the strategy ended in profit. */
  positive_episodes?: number | null;
  /** Plain-words hold/exit window, e.g. "Held through the Jun–Aug window (~3 months)". */
  exit_period?: string | null;
  /** Plain "how well the history lined up" dial (null when not computed). */
  historical_alignment?: HistoricalAlignment | null;
  /** Monte-Carlo outcome spread (null when not simulated). */
  monte_carlo?: MonteCarlo | null;
  /** REAL per-tier weighting scheme actually used
   *  (min_variance / risk_parity / factor / equal). */
  weight_scheme?: string | null;
  /** REAL modelled Black–Scholes option payoff for the option tier
   *  (null for non-option kinds). */
  option_model?: OptionModel | null;
};

/** Full view detail returned by GET /api/views/{id}. */
export type ViewDetail = ViewSummary & {
  transmission: TransmissionEdge[];
  confidence: {
    outcome: ViewConfBlock;
    expression: ViewConfBlock;
  };
  expectations: ViewExpectationRow[];
  expressions: ExpressionDetail[];
  // Layman content layer.
  /** 3-4 plain sentences, humble, ends 'This is analysis, not financial advice.' */
  plain_thesis: string | null;
  /** The benchmark in plain words, e.g. 'Nifty 50'. */
  benchmark_label: string | null;
  /** Longer plain-English description of the belief (detail hero copy). */
  description: string | null;
  /** Plain takeaways: what drives it / how to play it / main caveat. */
  bullets: string[];
  /** Sibling views surfaced as "similar". */
  similar_views: SimilarView[];
  /** Basket-vs-Nifty fundamentals, or null when not computed. */
  fundamental_comparison: FundamentalComparison | null;
  /** Presentation-only Yes/No stance block (null for views not yet backfilled,
   *  e.g. the 3 frozen live curated views — render nothing in that case). */
  stance?: ViewStance | null;
  /** The honest "what if you're wrong" line — promoted to its own emphasized
   *  line rather than a flat bullet. Null when not computed. */
  caveat?: string | null;
};

// ── My Views — the per-user position ledger ─────────────────────────────────

/** One ledger leg with its live mark (nulls = honestly unpriceable). */
export type ViewPositionLeg = {
  symbol: string | null;
  side: "long" | "short" | string;
  weight: number | null;
  entry_price: number | null;
  last_price: number | null;
  return_pct: number | null;
};

/**
 * Shape returned by GET /api/views/positions — one deployed view expression
 * on the user's ledger, with its live return since entry. Register-not-execute:
 * the ledger records; the user places/exits orders in their own broker app.
 */
export type ViewPositionItem = {
  id: string;
  view_id: string;
  expression_id: string;
  workflow_id: string | null;
  /** The view at a glance — dateless question title + resolution state. */
  view_title: string | null;
  view_status: string | null;
  view_resolved: boolean;
  resolution_date: string | null;
  tier: string | null;
  expression_kind: string | null;
  /** Fun plain strategy name, e.g. "Slow & Steady rural bundle". */
  strategy_name: string | null;
  status: "open" | "exited" | string;
  entry_at: string | null;
  exited_at: string | null;
  /** User-declared size — null means % returns only (never invented). */
  capital_inr: number | null;
  /** 1.0 → 0.0 through partial exits. */
  open_fraction: number;
  take_profit_pct: number | null;
  stop_loss_pct: number | null;
  take_profit_hit: boolean;
  stop_loss_hit: boolean;
  /** Live weighted return since entry (null = unpriceable, never 0). */
  return_pct: number | null;
  unrealized_pnl_inr: number | null;
  open_value_inr: number | null;
  realized_pnl_inr: number | null;
  legs: ViewPositionLeg[];
  exits: Array<{
    at?: string;
    pct_of_open?: number;
    return_pct?: number | null;
    realized_pnl_inr?: number | null;
  }>;
  note: string | null;
};

/** Result of POST /api/views/{id}/compare — ranked tier recommendation. */
export type CompareResult = {
  recommended_tier: ExpressionTier;
  rationale: string;
  tiers: Array<{
    tier: ExpressionTier;
    expression_id: string;
    rank: number;
    score: number | null;
    reason: string;
  }>;
};
