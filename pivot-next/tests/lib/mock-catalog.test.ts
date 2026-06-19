import { describe, expect, it } from "vitest";
import { MOCK_CATALOG, findStepType } from "@/lib/mock-catalog";

/**
 * All step types present in the SPEC-aligned mock catalog.
 * Deprecated types (action.set_stoploss, action.set_takeprofit,
 * action.squareoff_all, action.squareoff_symbol, action.squareoff_all_intraday,
 * fetch.day_open, fetch.prior_close, fetch.rolling_high, fetch.rolling_low)
 * are hidden from the catalog (backend marks them deprecated=True) — they must
 * NOT appear here.
 */
const EXPECTED_STEP_TYPES = [
  // Triggers
  "trigger.schedule",
  "trigger.price",
  "trigger.indicator",
  "trigger.event",
  "trigger.manual",
  "trigger.webhook",
  // Fetches — non-deprecated
  "fetch.quote",
  "fetch.price_reference",
  "fetch.rolling_extreme",
  "fetch.indicator",
  "fetch.fundamental",
  "fetch.portfolio",
  "fetch.news",
  "fetch.screener",
  "fetch.top_movers",
  "fetch.intraday_pnl",
  // Conditions
  "condition.numeric",
  "condition.boolean",
  "condition.market_status",
  "condition.position",
  "condition.time_window",
  // Actions — non-deprecated
  "action.place_order",
  "action.cancel_orders",
  "action.set_protective",
  "action.squareoff",
  "action.allocate_basket",
  "action.allocate_notional",
  "action.place_option_strategy",
  "action.arm_ipo_intent",
  "action.update_watchlist",
  // Communication
  "notify.message",
  "notify.log",
  "wait.approval",
  // Control
  "wait.delay",
  "control.skip_if",
];

/**
 * Per ARCHITECTURE.md §7 invariant 3: per-step retry budgets are
 * non-negotiable. Backend asserts the same in
 * `tests/workflows/test_registry.py::test_max_retries_match_invariant_3`.
 * If this map drifts from the backend invariant, mock-driven previews show
 * a different retry count than the engine actually executes — a Day-5 bomb.
 */
const EXPECTED_MAX_RETRIES_BY_TYPE: Record<string, number> = {
  "trigger.schedule": 0,
  "trigger.price": 0,
  "trigger.indicator": 0,
  "trigger.event": 0,
  "trigger.manual": 0,
  "trigger.webhook": 0,
  "fetch.quote": 3,
  "fetch.price_reference": 3,
  "fetch.rolling_extreme": 3,
  "fetch.indicator": 3,
  "fetch.fundamental": 3,
  "fetch.portfolio": 3,
  "fetch.news": 3,
  "fetch.screener": 3,
  "fetch.top_movers": 3,
  "fetch.intraday_pnl": 3,
  "condition.numeric": 0,
  "condition.boolean": 0,
  "condition.market_status": 0,
  "condition.position": 0,
  "condition.time_window": 0,
  "action.place_order": 1,
  "action.cancel_orders": 1,
  "action.set_protective": 1,
  "action.squareoff": 1,
  "action.allocate_basket": 1,
  "action.allocate_notional": 1,
  "action.place_option_strategy": 1,
  "action.arm_ipo_intent": 1,
  "action.update_watchlist": 1,
  "notify.message": 2,
  "notify.log": 2,
  "wait.approval": 0,
  "wait.delay": 0,
  "control.skip_if": 0,
};

/**
 * Backend `output_schema` truth for the step types whose output shapes
 * are contract-locked. Source: `pivot/backend/workflows/steps/{actions,notify}.py`.
 */
const EXPECTED_OUTPUT_PROPS_BY_TYPE: Record<string, string[]> = {
  "action.place_order": ["order_id", "status", "client_request_id"],
  "action.cancel_orders": ["cancelled_count", "order_ids"],
  "action.set_protective": ["trigger_id", "client_request_id"],
  "notify.message": ["channel", "delivered"],
};

const EXPECTED_CATEGORY_BY_TYPE: Record<string, string> = {
  "trigger.schedule": "trigger",
  "trigger.price": "trigger",
  "trigger.indicator": "trigger",
  "trigger.event": "trigger",
  "trigger.manual": "trigger",
  "trigger.webhook": "trigger",
  "fetch.quote": "fetch",
  "fetch.price_reference": "fetch",
  "fetch.rolling_extreme": "fetch",
  "fetch.indicator": "fetch",
  "fetch.fundamental": "fetch",
  "fetch.portfolio": "fetch",
  "fetch.news": "fetch",
  "fetch.screener": "fetch",
  "fetch.top_movers": "fetch",
  "fetch.intraday_pnl": "fetch",
  "condition.numeric": "condition",
  "condition.boolean": "condition",
  "condition.market_status": "condition",
  "condition.position": "condition",
  "condition.time_window": "condition",
  "action.place_order": "action",
  "action.cancel_orders": "action",
  "action.set_protective": "action",
  "action.squareoff": "action",
  "action.allocate_basket": "action",
  "action.allocate_notional": "action",
  "action.place_option_strategy": "action",
  "action.arm_ipo_intent": "action",
  "action.update_watchlist": "action",
  "notify.message": "notify",
  "notify.log": "notify",
  "wait.approval": "notify",
  "wait.delay": "control",
  "control.skip_if": "control",
};

/** Expected group labels per the SPEC 17-group taxonomy. */
const EXPECTED_GROUP_BY_TYPE: Record<string, string> = {
  "trigger.schedule": "Schedule & time",
  "trigger.price": "Price, indicators & exits",
  "trigger.indicator": "Price, indicators & exits",
  "trigger.event": "Events & external",
  "trigger.manual": "Events & external",
  "trigger.webhook": "Events & external",
  "fetch.quote": "Quotes & price levels",
  "fetch.price_reference": "Quotes & price levels",
  "fetch.rolling_extreme": "Quotes & price levels",
  "fetch.indicator": "Indicators",
  "fetch.fundamental": "Research & screens",
  "fetch.portfolio": "Portfolio & P&L",
  "fetch.news": "Research & screens",
  "fetch.screener": "Research & screens",
  "fetch.top_movers": "Research & screens",
  "fetch.intraday_pnl": "Portfolio & P&L",
  "condition.numeric": "Compare values",
  "condition.boolean": "Compare values",
  "condition.market_status": "Gates",
  "condition.position": "Gates",
  "condition.time_window": "Gates",
  "action.place_order": "Orders",
  "action.cancel_orders": "Orders",
  "action.set_protective": "Exits & protection",
  "action.squareoff": "Exits & protection",
  "action.allocate_basket": "Baskets",
  "action.allocate_notional": "Baskets",
  "action.place_option_strategy": "Special",
  "action.arm_ipo_intent": "Special",
  "action.update_watchlist": "Special",
  "notify.message": "Notifications",
  "notify.log": "Notifications",
  "wait.approval": "Approvals",
  "wait.delay": "Flow",
  "control.skip_if": "Flow",
};

describe("MOCK_CATALOG", () => {
  it(`includes all ${EXPECTED_STEP_TYPES.length} non-deprecated step types`, () => {
    const types = MOCK_CATALOG.step_types.map((s) => s.step_type).sort();
    expect(types).toEqual([...EXPECTED_STEP_TYPES].sort());
  });

  it("does NOT include any deprecated step types", () => {
    const deprecated = [
      "action.set_stoploss",
      "action.set_takeprofit",
      "action.squareoff_all",
      "action.squareoff_symbol",
      "action.squareoff_all_intraday",
      "fetch.day_open",
      "fetch.prior_close",
      "fetch.rolling_high",
      "fetch.rolling_low",
    ];
    const types = new Set(MOCK_CATALOG.step_types.map((s) => s.step_type));
    for (const d of deprecated) {
      expect(types.has(d), `deprecated step_type ${d} must not appear in catalog`).toBe(false);
    }
  });

  it("uses only the six canonical categories", () => {
    expect(MOCK_CATALOG.categories.map((c) => c.id).sort()).toEqual(
      ["action", "condition", "control", "fetch", "notify", "trigger"],
    );
  });

  it("assigns the canonical category to every step type", () => {
    for (const def of MOCK_CATALOG.step_types) {
      expect(def.category, `category mismatch for ${def.step_type}`).toBe(
        EXPECTED_CATEGORY_BY_TYPE[def.step_type],
      );
    }
  });

  it("assigns the correct group label to every step type (SPEC 17-group taxonomy)", () => {
    for (const def of MOCK_CATALOG.step_types) {
      expect(def.group, `group mismatch for ${def.step_type}`).toBe(
        EXPECTED_GROUP_BY_TYPE[def.step_type],
      );
    }
  });

  it("renames skip_if to control.skip_if (no bare skip_if)", () => {
    const types = MOCK_CATALOG.step_types.map((s) => s.step_type);
    expect(types).toContain("control.skip_if");
    expect(types).not.toContain("skip_if");
  });

  it("only marks trigger.* step types as trigger_only", () => {
    for (const def of MOCK_CATALOG.step_types) {
      const isTriggerType = def.step_type.startsWith("trigger.");
      expect(def.trigger_only, `trigger_only mismatch for ${def.step_type}`).toBe(isTriggerType);
    }
  });

  it("findStepType returns the matching def or undefined", () => {
    expect(findStepType(MOCK_CATALOG, "fetch.portfolio")?.label).toBe(
      "Your portfolio",
    );
    expect(findStepType(MOCK_CATALOG, "does.not.exist")).toBeUndefined();
  });

  it("max_retries matches ARCHITECTURE.md §7 invariant 3 for every step type", () => {
    for (const def of MOCK_CATALOG.step_types) {
      expect(def.max_retries, `max_retries mismatch for ${def.step_type}`).toBe(
        EXPECTED_MAX_RETRIES_BY_TYPE[def.step_type],
      );
    }
  });

  it("output_schema parity with backend registry for key step types", () => {
    for (const [stepType, expectedProps] of Object.entries(
      EXPECTED_OUTPUT_PROPS_BY_TYPE,
    )) {
      const def = findStepType(MOCK_CATALOG, stepType);
      expect(def, `missing catalog entry for ${stepType}`).toBeDefined();
      const out = def!.output_schema;
      expect(out, `${stepType} should have an output_schema`).not.toBeNull();
      const props = Object.keys(
        (out!.properties ?? {}) as Record<string, unknown>,
      ).sort();
      expect(props, `${stepType} output_schema mismatch`).toEqual(
        [...expectedProps].sort(),
      );
    }
  });

  it("action.set_protective has a 'kind' discriminator field in config_schema", () => {
    const def = findStepType(MOCK_CATALOG, "action.set_protective");
    expect(def).toBeDefined();
    const props = def!.config_schema.properties as Record<string, { enum?: string[] }> | undefined;
    expect(props?.kind?.enum).toEqual(["stoploss", "takeprofit"]);
  });

  it("action.squareoff has a 'scope' discriminator field in config_schema", () => {
    const def = findStepType(MOCK_CATALOG, "action.squareoff");
    expect(def).toBeDefined();
    const props = def!.config_schema.properties as Record<string, { enum?: string[] }> | undefined;
    expect(props?.scope?.enum).toEqual(["all", "symbol", "intraday"]);
  });

  it("fetch.price_reference has a 'reference' discriminator field in config_schema", () => {
    const def = findStepType(MOCK_CATALOG, "fetch.price_reference");
    expect(def).toBeDefined();
    const props = def!.config_schema.properties as Record<string, { enum?: string[] }> | undefined;
    expect(props?.reference?.enum).toEqual(["day_open", "prior_close"]);
  });

  it("fetch.rolling_extreme has a 'side' discriminator field in config_schema", () => {
    const def = findStepType(MOCK_CATALOG, "fetch.rolling_extreme");
    expect(def).toBeDefined();
    const props = def!.config_schema.properties as Record<string, { enum?: string[] }> | undefined;
    expect(props?.side?.enum).toEqual(["high", "low"]);
  });
});
