/**
 * Mock step-type catalog used by the frontend until backend ships
 * `GET /api/step-types`. Mirrors the response shape from
 * docs/API_CONTRACT.md §8.1 exactly, including all 24 v1 step types
 * and the canonical `category` mapping from the table in that section.
 *
 * When backend is ready, swap the `getStepTypes()` import in `lib/api.ts`
 * to a real fetch — no other call site changes.
 */

import type { StepTypeCatalog, StepTypeDef } from "@/lib/types";

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

const triggerSchedule: StepTypeDef = {
  step_type: "trigger.schedule",
  category: "trigger",
  label: "On schedule",
  description: "Run on a cron schedule",
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
};

const triggerPrice: StepTypeDef = {
  step_type: "trigger.price",
  category: "trigger",
  label: "On price level",
  description: "Fire when a symbol crosses a price threshold",
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
};

const triggerIndicator: StepTypeDef = {
  step_type: "trigger.indicator",
  category: "trigger",
  label: "On indicator",
  description: "Fire when an indicator crosses a threshold",
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
};

const triggerEvent: StepTypeDef = {
  step_type: "trigger.event",
  category: "trigger",
  label: "On event",
  description: "Fire on RBI, results, or FII flow events",
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
};

const triggerManual: StepTypeDef = {
  step_type: "trigger.manual",
  category: "trigger",
  label: "Manual run only",
  description: "Only runs when you click Run now",
  icon: "play",
  max_retries: 0,
  trigger_only: true,
  config_schema: noConfig,
  output_schema: null,
};

const triggerWebhook: StepTypeDef = {
  step_type: "trigger.webhook",
  category: "trigger",
  label: "On webhook",
  description: "Fire when an external system POSTs to this workflow's webhook URL",
  icon: "webhook",
  max_retries: 0,
  trigger_only: true,
  config_schema: noConfig,
  output_schema: null,
};

const fetchQuote: StepTypeDef = {
  step_type: "fetch.quote",
  category: "fetch",
  label: "Get quote",
  description: "Fetches latest quote for a symbol",
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
};

const fetchIndicator: StepTypeDef = {
  step_type: "fetch.indicator",
  category: "fetch",
  label: "Get indicator value",
  description: "Compute an indicator from quote history",
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
};

const fetchFundamental: StepTypeDef = {
  step_type: "fetch.fundamental",
  category: "fetch",
  label: "Get fundamental",
  description: "Look up a fundamental metric",
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
};

const fetchPortfolio: StepTypeDef = {
  step_type: "fetch.portfolio",
  category: "fetch",
  label: "Get portfolio",
  description: "Fetches holdings, buying power, and total value",
  icon: "wallet",
  max_retries: 3,
  trigger_only: false,
  config_schema: noConfig,
  output_schema: objectSchema({
    holdings: { type: "array" },
    buying_power: { type: "number" },
    total_value: { type: "number" },
  }),
};

const fetchNews: StepTypeDef = {
  step_type: "fetch.news",
  category: "fetch",
  label: "Get news",
  description: "Recent news with average sentiment",
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
};

const conditionNumeric: StepTypeDef = {
  step_type: "condition.numeric",
  category: "condition",
  label: "Numeric condition",
  description: "Compare two numbers (refs allowed). Halts run if false.",
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
};

const conditionMarketStatus: StepTypeDef = {
  step_type: "condition.market_status",
  category: "condition",
  label: "Market status",
  description: "Check if the market is open / closed / pre / post",
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
};

const conditionPosition: StepTypeDef = {
  step_type: "condition.position",
  category: "condition",
  label: "Position held",
  description: "Check whether a symbol is held in the portfolio",
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
};

const conditionTimeWindow: StepTypeDef = {
  step_type: "condition.time_window",
  category: "condition",
  label: "Time window",
  description: "Only continue inside a time-of-day window",
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
};

const actionPlaceOrder: StepTypeDef = {
  step_type: "action.place_order",
  category: "action",
  label: "Place order",
  description: "Submit a buy or sell order via the broker",
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
};

const actionCancelOrders: StepTypeDef = {
  step_type: "action.cancel_orders",
  category: "action",
  label: "Cancel pending orders",
  description: "Cancel matching pending orders",
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
};

const actionSetStoploss: StepTypeDef = {
  step_type: "action.set_stoploss",
  category: "action",
  label: "Set stop-loss",
  description: "Place a stop-loss order on a position",
  icon: "shield-alert",
  max_retries: 1,
  trigger_only: false,
  config_schema: objectSchema(
    {
      symbol: { type: "string" },
      trigger_price: { type: "number" },
      quantity: { type: "integer", minimum: 1 },
    },
    ["symbol", "trigger_price"],
  ),
  output_schema: objectSchema(
    {
      trigger_id: { type: "string" },
      client_request_id: { type: "string" },
    },
    ["trigger_id"],
  ),
};

const actionUpdateWatchlist: StepTypeDef = {
  step_type: "action.update_watchlist",
  category: "action",
  label: "Update watchlist",
  description: "Add or remove a symbol from your watchlist",
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
};

const notifyMessage: StepTypeDef = {
  step_type: "notify.message",
  category: "notify",
  label: "Send message",
  description: "Send an email, SMS, or push notification",
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
};

const notifyLog: StepTypeDef = {
  step_type: "notify.log",
  category: "notify",
  label: "Log message",
  description: "Append a line to the run log (no external side effect)",
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
};

const waitApproval: StepTypeDef = {
  step_type: "wait.approval",
  category: "notify",
  label: "Wait for approval",
  description: "Pause the run until the user approves or rejects",
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
};

const waitDelay: StepTypeDef = {
  step_type: "wait.delay",
  category: "control",
  label: "Wait",
  description: "Sleep for a duration or until a clock time",
  icon: "timer",
  max_retries: 0,
  trigger_only: false,
  config_schema: objectSchema({
    duration_seconds: { type: "integer", minimum: 1 },
    until_time: { type: "string", description: "HH:MM" },
    timezone: { type: "string", default: "Asia/Kolkata" },
  }),
  output_schema: null,
};

const controlSkipIf: StepTypeDef = {
  step_type: "control.skip_if",
  category: "control",
  label: "Skip next step if…",
  description: "Mark the next step as skipped when a condition holds",
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
};

export const MOCK_CATALOG: StepTypeCatalog = {
  catalog_version: "2026-05-02T00:00:00Z",
  categories: [
    { id: "trigger", label: "Triggers" },
    { id: "fetch", label: "Data fetches" },
    { id: "condition", label: "Conditions" },
    { id: "action", label: "Actions" },
    { id: "notify", label: "Communication" },
    { id: "control", label: "Control flow" },
  ],
  step_types: [
    triggerSchedule,
    triggerPrice,
    triggerIndicator,
    triggerEvent,
    triggerManual,
    triggerWebhook,
    fetchQuote,
    fetchIndicator,
    fetchFundamental,
    fetchPortfolio,
    fetchNews,
    conditionNumeric,
    conditionMarketStatus,
    conditionPosition,
    conditionTimeWindow,
    actionPlaceOrder,
    actionCancelOrders,
    actionSetStoploss,
    actionUpdateWatchlist,
    notifyMessage,
    notifyLog,
    waitApproval,
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
