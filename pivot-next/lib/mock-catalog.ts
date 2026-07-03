/**
 * Mock step-type catalog used by the frontend as a graceful fallback when
 * `GET /api/step-types` is unreachable (offline dev / unit tests).
 *
 * Mirrors the response shape from docs/API_CONTRACT.md §8.1 exactly.
 * Labels, descriptions, groups, and compat blocks are ported verbatim from
 * docs/plans/WORKFLOW_EDITOR_PLAN.html (the STEPS source-of-execution object).
 *
 * When the real endpoint is reachable the mock is never used — `getStepTypes()`
 * in lib/api.ts falls back here only on fetch error.
 */

import type { StepCompat, StepTypeCatalog, StepTypeDef } from "@/lib/types";

const objectSchema = (
  properties: Record<string, unknown>,
  required: string[] = [],
): StepTypeDef["config_schema"] => ({
  type: "object",
  properties,
  required,
});

const noConfig = objectSchema({}, []);

const operatorEnum = {
  type: "string",
  enum: ["==", "!=", ">", "<", ">=", "<="],
};

// ---------------------------------------------------------------------------
// Shared requires objects — ported verbatim from WORKFLOW_EDITOR_PLAN.html
// ---------------------------------------------------------------------------

const NEEDS_POS: StepCompat = {
  any_of: ["position_open"],
  ambient: "positions",
  label: "an open position",
  warn: "needs a position — open one earlier, or it must already be in your portfolio",
};

const NEEDS_ORD: StepCompat = {
  any_of: ["pending_orders"],
  ambient: "pending_orders",
  label: "a pending order",
  warn: "needs a pending order — place one earlier, or have one resting in your account",
};

const NEEDS_SYMS: StepCompat = {
  any_of: ["data:screen", "data:movers"],
  label: "a symbols list",
  warn: "give an inline symbol list, or add Screen stocks / Top movers first",
};

const NEEDS_BOOL: StepCompat = {
  any_of: ["data:news"],
  label: "a yes/no value",
  warn: "add a step that yields a true/false value first (e.g. Recent news)",
};

// ---------------------------------------------------------------------------
// Triggers
// ---------------------------------------------------------------------------

const triggerSchedule: StepTypeDef = {
  step_type: "trigger.schedule",
  category: "trigger",
  group: "Schedule & time",
  label: "On a schedule",
  description:
    "Run on a repeating clock — e.g. every weekday 9:20 AM, or every 30 minutes.",
  icon: "clock",
  max_retries: 0,
  trigger_only: true,
  config_schema: objectSchema(
    {
      cron: { type: "string", description: "Cron expression, 5-field" },
      timezone: {
        type: "string",
        description: "IANA timezone, e.g. Asia/Kolkata",
        default: "Asia/Kolkata",
      },
    },
    ["cron", "timezone"],
  ),
  output_schema: null,
  compat: { produces: [], requires: [], consumes: [] },
};

const triggerPrice: StepTypeDef = {
  step_type: "trigger.price",
  category: "trigger",
  group: "Price, indicators & exits",
  label: "When price crosses a level",
  description:
    "Fire when a symbol's last price crosses above or below a level you set.",
  icon: "trending-up",
  max_retries: 0,
  trigger_only: true,
  config_schema: objectSchema(
    {
      symbol: { type: "string" },
      operator: {
        type: "string",
        enum: [">", "<", "crosses_above", "crosses_below"],
      },
      value: { type: "number" },
      exchange: { type: "string", default: "NSE" },
    },
    ["symbol", "operator", "value", "exchange"],
  ),
  output_schema: null,
  compat: { produces: [], requires: [], consumes: [] },
};

const triggerIndicator: StepTypeDef = {
  step_type: "trigger.indicator",
  category: "trigger",
  group: "Price, indicators & exits",
  label: "When an indicator crosses a level",
  description:
    "Fire when a technical indicator (RSI, SMA, EMA, MACD…) crosses a threshold.",
  icon: "activity",
  max_retries: 0,
  trigger_only: true,
  config_schema: objectSchema(
    {
      symbol: { type: "string" },
      indicator: { type: "string", enum: ["rsi", "sma", "ema", "macd"] },
      period: { type: "integer", minimum: 1 },
      operator: operatorEnum,
      value: { type: "number" },
    },
    ["symbol", "indicator", "period", "operator", "value"],
  ),
  output_schema: null,
  compat: { produces: [], requires: [], consumes: [] },
};

const triggerEvent: StepTypeDef = {
  step_type: "trigger.event",
  category: "trigger",
  group: "Events & external",
  label: "When a news event happens",
  description:
    "Fire when a news article confirms an event you describe — e.g. 'RBI announces a repo-rate cut'.",
  icon: "newspaper",
  max_retries: 0,
  trigger_only: true,
  config_schema: objectSchema(
    {
      event_type: {
        type: "string",
        enum: ["rbi_rate_decision", "company_results", "fii_flow"],
      },
      filter: { type: "object" },
    },
    ["event_type"],
  ),
  output_schema: null,
  compat: { produces: ["data:news"], requires: [], consumes: [] },
};

const triggerManual: StepTypeDef = {
  step_type: "trigger.manual",
  category: "trigger",
  group: "Events & external",
  label: "Manual (Run now)",
  description: "Never fires on its own — runs only when you press Run now.",
  icon: "play",
  max_retries: 0,
  trigger_only: true,
  config_schema: noConfig,
  output_schema: null,
  compat: { produces: [], requires: [], consumes: [] },
};

const triggerWebhook: StepTypeDef = {
  step_type: "trigger.webhook",
  category: "trigger",
  group: "Events & external",
  label: "On a webhook",
  description:
    "Fire when an external system POSTs to this workflow's unique URL; the payload is available to later steps.",
  icon: "webhook",
  max_retries: 0,
  trigger_only: true,
  config_schema: noConfig,
  output_schema: null,
  compat: { produces: ["webhook_payload"], requires: [], consumes: [] },
};

// ---------------------------------------------------------------------------
// Fetches
// ---------------------------------------------------------------------------

const fetchQuote: StepTypeDef = {
  step_type: "fetch.quote",
  category: "fetch",
  group: "Quotes & price levels",
  label: "Live quote",
  description: "The latest price, OHLC and volume for a symbol.",
  icon: "line-chart",
  max_retries: 3,
  trigger_only: false,
  config_schema: objectSchema(
    {
      symbol: { type: "string" },
      exchange: { type: "string", default: "NSE" },
    },
    ["symbol", "exchange"],
  ),
  output_schema: objectSchema({
    ltp: { type: "number" },
    open: { type: "number" },
    high: { type: "number" },
    low: { type: "number" },
    close: { type: "number" },
    volume: { type: "number" },
    asof: { type: "string" },
  }),
  compat: { produces: ["data:quote", "data:price_level"], requires: [], consumes: [] },
};

const fetchIndicator: StepTypeDef = {
  step_type: "fetch.indicator",
  category: "fetch",
  group: "Indicators",
  label: "Indicator value",
  description: "Compute a technical indicator (RSI, SMA, EMA, MACD and more).",
  icon: "activity",
  max_retries: 3,
  trigger_only: false,
  config_schema: objectSchema(
    {
      symbol: { type: "string" },
      indicator: { type: "string", enum: ["rsi", "sma", "ema", "macd"] },
      period: { type: "integer", minimum: 1 },
    },
    ["symbol", "indicator", "period"],
  ),
  output_schema: objectSchema({
    value: { type: "number" },
    computed_at: { type: "string" },
  }),
  compat: { produces: ["data:indicator"], requires: [], consumes: [] },
};

const fetchFundamental: StepTypeDef = {
  step_type: "fetch.fundamental",
  category: "fetch",
  group: "Research & screens",
  label: "Fundamental metric",
  description:
    "A fundamental like P/E, ROE, market cap or D/E — or a custom formula over them.",
  icon: "book-open",
  max_retries: 3,
  trigger_only: false,
  config_schema: objectSchema(
    {
      symbol: { type: "string" },
      metric: { type: "string", enum: ["pe", "roe", "mcap", "de"] },
    },
    ["symbol", "metric"],
  ),
  output_schema: objectSchema({
    value: { type: "number" },
    period_end: { type: "string" },
    source: { type: "string" },
  }),
  compat: { produces: ["data:fundamental"], requires: [], consumes: [] },
};

const fetchPortfolio: StepTypeDef = {
  step_type: "fetch.portfolio",
  category: "fetch",
  group: "Portfolio & P&L",
  label: "Your portfolio",
  description: "Your holdings, buying power and total value.",
  icon: "wallet",
  max_retries: 3,
  trigger_only: false,
  config_schema: noConfig,
  output_schema: objectSchema({
    holdings: { type: "array" },
    buying_power: { type: "number" },
    total_value: { type: "number" },
  }),
  compat: { produces: ["data:portfolio"], requires: [], consumes: [] },
};

const fetchNews: StepTypeDef = {
  step_type: "fetch.news",
  category: "fetch",
  group: "Research & screens",
  label: "Recent news",
  description:
    "Recent articles for your keywords, optionally scored against an event you describe.",
  icon: "newspaper",
  max_retries: 3,
  trigger_only: false,
  config_schema: objectSchema(
    {
      symbol_or_query: { type: "string" },
      limit: { type: "integer", minimum: 1, maximum: 50, default: 10 },
    },
    ["symbol_or_query"],
  ),
  output_schema: objectSchema({
    articles: { type: "array" },
    avg_sentiment: { type: "number" },
  }),
  compat: { produces: ["data:news"], requires: [], consumes: [] },
};

// ---------------------------------------------------------------------------
// Conditions
// ---------------------------------------------------------------------------

const conditionNumeric: StepTypeDef = {
  step_type: "condition.numeric",
  category: "condition",
  group: "Compare values",
  label: "Compare numbers",
  description:
    "Continue only if two numbers (or earlier-step values) satisfy your comparison — e.g. price ≥ 2500.",
  icon: "git-branch",
  max_retries: 0,
  trigger_only: false,
  config_schema: objectSchema(
    {
      left: { description: "Number or {{ context.X.path }} ref" },
      operator: operatorEnum,
      right: { description: "Number or {{ context.X.path }} ref" },
    },
    ["left", "operator", "right"],
  ),
  output_schema: objectSchema({ passed: { type: "boolean" } }),
  compat: { produces: [], requires: [], consumes: [] },
};

const conditionMarketStatus: StepTypeDef = {
  step_type: "condition.market_status",
  category: "condition",
  group: "Gates",
  label: "Market is open / closed",
  description:
    "Continue only when the NSE market is in the state you pick (open, closed, pre, post).",
  icon: "circle-dot",
  max_retries: 0,
  trigger_only: false,
  config_schema: objectSchema(
    {
      require: { type: "string", enum: ["open", "closed", "pre", "post"] },
    },
    ["require"],
  ),
  output_schema: objectSchema({ passed: { type: "boolean" } }),
  compat: { produces: [], requires: [], consumes: [] },
};

const conditionPosition: StepTypeDef = {
  step_type: "condition.position",
  category: "condition",
  group: "Gates",
  label: "Position is held / not held",
  description:
    "Continue based on whether a symbol is currently in your portfolio.",
  icon: "package",
  max_retries: 0,
  trigger_only: false,
  config_schema: objectSchema(
    {
      symbol: { type: "string" },
      require: { type: "string", enum: ["held", "not_held"] },
    },
    ["symbol", "require"],
  ),
  output_schema: objectSchema({ passed: { type: "boolean" } }),
  compat: { produces: [], requires: [], consumes: [] },
};

const conditionTimeWindow: StepTypeDef = {
  step_type: "condition.time_window",
  category: "condition",
  group: "Gates",
  label: "Within a time window",
  description: "Continue only when the current time is inside a window you set.",
  icon: "calendar-clock",
  max_retries: 0,
  trigger_only: false,
  config_schema: objectSchema(
    {
      start_time: { type: "string", description: "HH:MM" },
      end_time: { type: "string", description: "HH:MM" },
      timezone: { type: "string", default: "Asia/Kolkata" },
    },
    ["start_time", "end_time", "timezone"],
  ),
  output_schema: objectSchema({ passed: { type: "boolean" } }),
  compat: { produces: [], requires: [], consumes: [] },
};

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

const actionPlaceOrder: StepTypeDef = {
  step_type: "action.place_order",
  category: "action",
  group: "Orders",
  label: "Place an order",
  description:
    "Buy or sell a symbol — market or limit — via your broker. Approval-gated.",
  icon: "shopping-cart",
  max_retries: 1,
  trigger_only: false,
  config_schema: objectSchema(
    {
      symbol: { type: "string" },
      side: { type: "string", enum: ["buy", "sell"] },
      quantity: { type: "integer", minimum: 1 },
      order_type: { type: "string", enum: ["market", "limit"], default: "market" },
      limit_price: { type: "number" },
      requires_approval: { type: "boolean", default: true },
    },
    ["symbol", "side", "quantity", "order_type"],
  ),
  output_schema: objectSchema(
    {
      order_id: { type: "string" },
      status: { type: "string" },
      client_request_id: { type: "string" },
    },
    ["order_id", "client_request_id"],
  ),
  compat: { produces: ["position_open", "pending_orders"], requires: [], consumes: [] },
};

const actionCancelOrders: StepTypeDef = {
  step_type: "action.cancel_orders",
  category: "action",
  group: "Orders",
  label: "Cancel pending orders",
  description: "Cancel your matching pending orders by symbol and side.",
  icon: "x-octagon",
  max_retries: 1,
  trigger_only: false,
  config_schema: objectSchema({
    symbol_filter: { type: "string" },
    side_filter: { type: "string", enum: ["buy", "sell"] },
  }),
  output_schema: objectSchema(
    {
      cancelled_count: { type: "integer" },
      order_ids: { type: "array", items: { type: "string" } },
    },
    ["cancelled_count"],
  ),
  compat: { produces: [], requires: [NEEDS_ORD], consumes: ["pending_orders"] },
};

// ---------------------------------------------------------------------------
// New parameterized step: action.set_protective
// Replaces deprecated action.set_stoploss + action.set_takeprofit
// ---------------------------------------------------------------------------

const actionSetProtective: StepTypeDef = {
  step_type: "action.set_protective",
  category: "action",
  group: "Exits & protection",
  label: "Set a stop-loss or take-profit",
  description:
    "Protect a holding with a stop-loss or take-profit sell order. Choose kind: stoploss or takeprofit.",
  icon: "shield-alert",
  max_retries: 1,
  trigger_only: false,
  config_schema: objectSchema(
    {
      kind: { type: "string", enum: ["stoploss", "takeprofit"] },
      symbol: { type: "string" },
      trigger_price: { type: "number" },
      trigger_offset_pct: { type: "number" },
      quantity: { type: "integer", minimum: 1 },
    },
    ["kind", "symbol"],
  ),
  output_schema: objectSchema(
    {
      trigger_id: { type: "string" },
      client_request_id: { type: "string" },
    },
    ["trigger_id"],
  ),
  compat: {
    produces: ["protective_order", "pending_orders"],
    requires: [NEEDS_POS],
    consumes: [],
  },
};

const actionUpdateWatchlist: StepTypeDef = {
  step_type: "action.update_watchlist",
  category: "action",
  group: "Special",
  label: "Update your watchlist",
  description: "Add or remove a symbol from your watchlist.",
  icon: "list-plus",
  max_retries: 1,
  trigger_only: false,
  config_schema: objectSchema(
    {
      action: { type: "string", enum: ["add", "remove"] },
      symbol: { type: "string" },
    },
    ["action", "symbol"],
  ),
  output_schema: null,
  compat: { produces: [], requires: [], consumes: [] },
};

// ---------------------------------------------------------------------------
// Communication (notify category)
// ---------------------------------------------------------------------------

const notifyMessage: StepTypeDef = {
  step_type: "notify.message",
  category: "notify",
  group: "Notifications",
  label: "Send a notification",
  description: "Send a push notification (email / SMS coming later).",
  icon: "send",
  max_retries: 2,
  trigger_only: false,
  config_schema: objectSchema(
    {
      channel: { type: "string", enum: ["email", "sms", "push"] },
      template: { type: "string" },
      vars: { type: "object" },
    },
    ["channel", "template"],
  ),
  output_schema: objectSchema(
    {
      channel: { type: "string" },
      delivered: { type: "boolean" },
    },
    ["channel", "delivered"],
  ),
  compat: { produces: [], requires: [], consumes: [] },
};

const notifyLog: StepTypeDef = {
  step_type: "notify.log",
  category: "notify",
  group: "Notifications",
  label: "Add a run note",
  description: "Write a line into this run's log — no external message.",
  icon: "file-text",
  max_retries: 2,
  trigger_only: false,
  config_schema: objectSchema(
    {
      message: { type: "string" },
    },
    ["message"],
  ),
  output_schema: objectSchema({ log: { type: "string" } }),
  compat: { produces: [], requires: [], consumes: [] },
};

const waitApproval: StepTypeDef = {
  step_type: "wait.approval",
  category: "notify",
  group: "Approvals",
  label: "Pause for my approval",
  description: "Pause the run until you approve or reject it in the app.",
  icon: "user-check",
  max_retries: 0,
  trigger_only: false,
  config_schema: objectSchema(
    {
      summary: { type: "string" },
      expires_in_minutes: { type: "integer", minimum: 1, default: 15 },
    },
    ["summary", "expires_in_minutes"],
  ),
  output_schema: objectSchema({
    decision: { type: "string", enum: ["approved", "rejected"] },
  }),
  compat: { produces: [], requires: [], consumes: [] },
};

// ---------------------------------------------------------------------------
// Control flow
// ---------------------------------------------------------------------------

const waitDelay: StepTypeDef = {
  step_type: "wait.delay",
  category: "control",
  group: "Flow",
  label: "Wait",
  description: "Pause for a set duration, or until a specific time of day.",
  icon: "timer",
  max_retries: 0,
  trigger_only: false,
  config_schema: objectSchema({
    duration_seconds: { type: "integer", minimum: 1 },
    until_time: { type: "string", description: "HH:MM" },
    timezone: { type: "string", default: "Asia/Kolkata" },
  }),
  output_schema: null,
  compat: { produces: [], requires: [], consumes: [] },
};

const controlSkipIf: StepTypeDef = {
  step_type: "control.skip_if",
  category: "control",
  group: "Flow",
  label: "Skip next step if…",
  description: "Skip the following step when a condition holds.",
  icon: "skip-forward",
  max_retries: 0,
  trigger_only: false,
  config_schema: objectSchema(
    {
      condition: {
        type: "object",
        description: "Numeric / market_status / position config",
      },
    },
    ["condition"],
  ),
  output_schema: objectSchema({ skipped_next: { type: "boolean" } }),
  compat: { produces: [], requires: [], consumes: [] },
};

// ---------------------------------------------------------------------------
// New parameterized step: action.squareoff
// Replaces deprecated action.squareoff_all, action.squareoff_symbol,
// action.squareoff_all_intraday
// ---------------------------------------------------------------------------

const actionSquareoff: StepTypeDef = {
  step_type: "action.squareoff",
  category: "action",
  group: "Exits & protection",
  label: "Close position(s)",
  description:
    "Exit open position(s) at market. scope=all closes every position; scope=symbol closes one; scope=intraday closes all MIS positions.",
  icon: "x-circle",
  max_retries: 1,
  trigger_only: false,
  config_schema: objectSchema(
    {
      scope: { type: "string", enum: ["all", "symbol", "intraday"] },
      symbol: { type: "string", description: "Required when scope=symbol" },
    },
    ["scope"],
  ),
  output_schema: objectSchema({
    closed_count: { type: "integer" },
    order_ids: { type: "array", items: { type: "string" } },
  }),
  compat: {
    produces: [],
    requires: [NEEDS_POS],
    consumes: ["position_open"],
  },
};

// ---------------------------------------------------------------------------
// Additional steps from the 49-step catalog (HTML spec)
// These are the steps present in the spec but not in the original 24-step set.
// Included here so the mock is a faithful fallback even before the real
// backend catalog lands the full 49.
// ---------------------------------------------------------------------------

const actionAllocateBasket: StepTypeDef = {
  step_type: "action.allocate_basket",
  category: "action",
  group: "Baskets",
  label: "Open a weighted basket",
  description: "Open several long/short legs at set weights in one step.",
  icon: "layers",
  max_retries: 1,
  trigger_only: false,
  config_schema: objectSchema(
    {
      legs: {
        type: "array",
        items: {
          type: "object",
          properties: {
            symbol: { type: "string" },
            side: { type: "string", enum: ["buy", "sell"] },
            weight_pct: { type: "number" },
          },
        },
      },
      budget: { type: "number" },
    },
    ["legs", "budget"],
  ),
  output_schema: objectSchema({ order_ids: { type: "array" } }),
  compat: {
    produces: ["position_open", "pending_orders"],
    requires: [],
    consumes: [],
  },
};

const actionAllocateNotional: StepTypeDef = {
  step_type: "action.allocate_notional",
  category: "action",
  group: "Baskets",
  label: "Split a budget across stocks",
  description:
    "Divide a ₹ budget across a list of symbols (equal or cap-weighted) and place each.",
  icon: "divide-circle",
  max_retries: 1,
  trigger_only: false,
  config_schema: objectSchema(
    {
      budget: { type: "number" },
      weighting: { type: "string", enum: ["equal", "mcap"] },
    },
    ["budget"],
  ),
  output_schema: objectSchema({ order_ids: { type: "array" } }),
  compat: {
    produces: ["position_open", "pending_orders"],
    requires: [NEEDS_SYMS],
    consumes: [],
  },
};

const actionPlaceOptionStrategy: StepTypeDef = {
  step_type: "action.place_option_strategy",
  category: "action",
  group: "Special",
  label: "Place / register an option strategy",
  description:
    "Build a multi-leg option strategy. Paper book fills in simulation; live book registers the intent only — you place it in your broker app. MCX is research-only.",
  icon: "sliders",
  max_retries: 1,
  trigger_only: false,
  config_schema: objectSchema(
    {
      underlying: { type: "string" },
      expiry: { type: "string" },
      template: { type: "string" },
      book: { type: "string", enum: ["paper", "live"] },
      qty_lots: { type: "integer", minimum: 1 },
      legs: { type: "array" },
    },
    ["underlying", "expiry", "template", "legs"],
  ),
  output_schema: objectSchema({ strategy_id: { type: "string" } }, ["strategy_id"]),
  compat: {
    produces: ["position_open", "pending_orders"],
    requires: [],
    consumes: [],
  },
};

const actionArmIpoIntent: StepTypeDef = {
  step_type: "action.arm_ipo_intent",
  category: "action",
  group: "Special",
  label: "Register an IPO application",
  description:
    "Record an IPO application reminder. Pivot never submits a bid — you apply and approve the UPI mandate yourself.",
  icon: "flag",
  max_retries: 1,
  trigger_only: false,
  config_schema: objectSchema(
    {
      ipo_symbol: { type: "string" },
      category: { type: "string", enum: ["retail", "snii", "bnii"] },
      quantity_lots: { type: "integer", minimum: 1 },
    },
    ["ipo_symbol"],
  ),
  output_schema: null,
  compat: { produces: [], requires: [], consumes: [] },
};

const conditionBoolean: StepTypeDef = {
  step_type: "condition.boolean",
  category: "condition",
  group: "Compare values",
  label: "Check a yes/no value",
  description:
    "Continue only if an earlier step's true/false value matches — e.g. news matched = true.",
  icon: "toggle-left",
  max_retries: 0,
  trigger_only: false,
  config_schema: objectSchema(
    {
      ref: { type: "string", description: "{{ context.X.field }} ref" },
      expect: { type: "boolean" },
    },
    ["ref", "expect"],
  ),
  output_schema: objectSchema({ passed: { type: "boolean" } }),
  compat: { produces: [], requires: [NEEDS_BOOL], consumes: [] },
};

const fetchScreener: StepTypeDef = {
  step_type: "fetch.screener",
  category: "fetch",
  group: "Research & screens",
  label: "Screen stocks",
  description:
    "Filter & rank Indian stocks by sector and market cap — returns a symbols list the next step can act on.",
  icon: "filter",
  max_retries: 3,
  trigger_only: false,
  config_schema: objectSchema(
    {
      expression: { type: "string" },
      limit: { type: "integer", minimum: 1, maximum: 100, default: 20 },
    },
    ["expression"],
  ),
  output_schema: objectSchema({
    symbols: { type: "array", items: { type: "string" } },
    count: { type: "integer" },
  }),
  compat: { produces: ["data:screen"], requires: [], consumes: [] },
};

const fetchTopMovers: StepTypeDef = {
  step_type: "fetch.top_movers",
  category: "fetch",
  group: "Research & screens",
  label: "Top gainers / losers",
  description:
    "Today's biggest NIFTY-50 movers — drives 'buy the top gainer', 'short the top loser', etc.",
  icon: "arrow-up-down",
  max_retries: 3,
  trigger_only: false,
  config_schema: objectSchema(
    {
      side: { type: "string", enum: ["gainers", "losers"] },
      limit: { type: "integer", minimum: 1, maximum: 50, default: 5 },
    },
    ["side"],
  ),
  output_schema: objectSchema({
    symbols: { type: "array", items: { type: "string" } },
    movers: { type: "array" },
  }),
  compat: { produces: ["data:movers"], requires: [], consumes: [] },
};

const fetchIntradayPnl: StepTypeDef = {
  step_type: "fetch.intraday_pnl",
  category: "fetch",
  group: "Portfolio & P&L",
  label: "Intraday P&L",
  description:
    "Realised + unrealised P&L across your current holdings.",
  icon: "trending-up",
  max_retries: 3,
  trigger_only: false,
  config_schema: noConfig,
  output_schema: objectSchema({
    realized_pnl: { type: "number" },
    unrealized_pnl: { type: "number" },
    total_pnl: { type: "number" },
  }),
  compat: { produces: ["data:pnl"], requires: [], consumes: [] },
};

// ---------------------------------------------------------------------------
// New parameterized step: fetch.price_reference
// Replaces deprecated fetch.day_open + fetch.prior_close
// ---------------------------------------------------------------------------

const fetchPriceReference: StepTypeDef = {
  step_type: "fetch.price_reference",
  category: "fetch",
  group: "Quotes & price levels",
  label: "Price reference level",
  description:
    "Fetch a reference price level — day open or prior session close — for use in conditions or orders.",
  icon: "bookmark",
  max_retries: 3,
  trigger_only: false,
  config_schema: objectSchema(
    {
      reference: { type: "string", enum: ["day_open", "prior_close"] },
      symbol: { type: "string" },
      exchange: { type: "string", default: "NSE" },
    },
    ["reference", "symbol"],
  ),
  output_schema: objectSchema({ price: { type: "number" }, asof: { type: "string" } }),
  compat: { produces: ["data:price_level"], requires: [], consumes: [] },
};

// ---------------------------------------------------------------------------
// New parameterized step: fetch.rolling_extreme
// Replaces deprecated fetch.rolling_high + fetch.rolling_low
// ---------------------------------------------------------------------------

const fetchRollingExtreme: StepTypeDef = {
  step_type: "fetch.rolling_extreme",
  category: "fetch",
  group: "Quotes & price levels",
  label: "Rolling high / low",
  description:
    "The highest or lowest price over a lookback window — use as a dynamic support/resistance reference.",
  icon: "bar-chart-2",
  max_retries: 3,
  trigger_only: false,
  config_schema: objectSchema(
    {
      side: { type: "string", enum: ["high", "low"] },
      symbol: { type: "string" },
      window: { type: "integer", minimum: 1, description: "Lookback in bars" },
      exchange: { type: "string", default: "NSE" },
    },
    ["side", "symbol", "window"],
  ),
  output_schema: objectSchema({ price: { type: "number" }, asof: { type: "string" } }),
  compat: { produces: ["data:price_level"], requires: [], consumes: [] },
};

// ---------------------------------------------------------------------------
// Catalog assembly
// ---------------------------------------------------------------------------

export const MOCK_CATALOG: StepTypeCatalog = {
  catalog_version: "2026-06-18T00:00:00Z",
  categories: [
    { id: "trigger", label: "Triggers" },
    { id: "fetch", label: "Data fetches" },
    { id: "condition", label: "Conditions" },
    { id: "action", label: "Actions" },
    { id: "notify", label: "Communication" },
    { id: "control", label: "Control flow" },
  ],
  step_types: [
    // Triggers — "Schedule & time"
    triggerSchedule,
    // Triggers — "Price, indicators & exits"
    triggerPrice,
    triggerIndicator,
    // Triggers — "Events & external"
    triggerEvent,
    triggerManual,
    triggerWebhook,
    // Fetches — "Quotes & price levels"
    fetchQuote,
    fetchPriceReference,
    fetchRollingExtreme,
    // Fetches — "Indicators"
    fetchIndicator,
    // Fetches — "Portfolio & P&L"
    fetchPortfolio,
    fetchIntradayPnl,
    // Fetches — "Research & screens"
    fetchFundamental,
    fetchNews,
    fetchScreener,
    fetchTopMovers,
    // Conditions — "Compare values"
    conditionNumeric,
    conditionBoolean,
    // Conditions — "Gates"
    conditionMarketStatus,
    conditionPosition,
    conditionTimeWindow,
    // Actions — "Orders"
    actionPlaceOrder,
    actionCancelOrders,
    // Actions — "Exits & protection"
    actionSetProtective,
    actionSquareoff,
    // Actions — "Baskets"
    actionAllocateBasket,
    actionAllocateNotional,
    // Actions — "Special"
    actionArmIpoIntent,
    actionPlaceOptionStrategy,
    actionUpdateWatchlist,
    // Communication — "Notifications"
    notifyMessage,
    notifyLog,
    // Communication — "Approvals"
    waitApproval,
    // Control flow — "Flow"
    waitDelay,
    controlSkipIf,
  ],
};

/**
 * Lookup a step-type definition by its `step_type` string. Returns undefined
 * if the catalog has no such entry — caller decides how to surface that
 * (typically: render an unknown-step placeholder).
 */
export function findStepType(
  catalog: StepTypeCatalog,
  stepType: string,
): StepTypeDef | undefined {
  return catalog.step_types.find((s) => s.step_type === stepType);
}
