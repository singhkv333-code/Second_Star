import { describe, expect, it } from "vitest";
import { MOCK_CATALOG, findStepType } from "@/lib/mock-catalog";

const EXPECTED_STEP_TYPES = [
  "trigger.schedule",
  "trigger.price",
  "trigger.indicator",
  "trigger.event",
  "trigger.manual",
  "trigger.webhook",
  "fetch.quote",
  "fetch.indicator",
  "fetch.fundamental",
  "fetch.portfolio",
  "fetch.news",
  "condition.numeric",
  "condition.market_status",
  "condition.position",
  "condition.time_window",
  "action.place_order",
  "action.cancel_orders",
  "action.set_stoploss",
  "action.update_watchlist",
  "notify.message",
  "notify.log",
  "wait.approval",
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
  "fetch.indicator": 3,
  "fetch.fundamental": 3,
  "fetch.portfolio": 3,
  "fetch.news": 3,
  "condition.numeric": 0,
  "condition.market_status": 0,
  "condition.position": 0,
  "condition.time_window": 0,
  "action.place_order": 1,
  "action.cancel_orders": 1,
  "action.set_stoploss": 1,
  "action.update_watchlist": 1,
  "notify.message": 2,
  "notify.log": 2,
  "wait.approval": 0,
  "wait.delay": 0,
  "control.skip_if": 0,
};

/**
 * Backend `output_schema` truth for the four step types whose mock
 * shapes drifted in Day 1. Locking parity here so the Day-5 wire-up is
 * trivial. Source: `pivot/backend/workflows/steps/{actions,notify}.py`.
 */
const EXPECTED_OUTPUT_PROPS_BY_TYPE: Record<string, string[]> = {
  "action.place_order": ["order_id", "status", "client_request_id"],
  "action.cancel_orders": ["cancelled_count", "order_ids"],
  "action.set_stoploss": ["trigger_id", "client_request_id"],
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
  "fetch.indicator": "fetch",
  "fetch.fundamental": "fetch",
  "fetch.portfolio": "fetch",
  "fetch.news": "fetch",
  "condition.numeric": "condition",
  "condition.market_status": "condition",
  "condition.position": "condition",
  "condition.time_window": "condition",
  "action.place_order": "action",
  "action.cancel_orders": "action",
  "action.set_stoploss": "action",
  "action.update_watchlist": "action",
  "notify.message": "notify",
  "notify.log": "notify",
  "wait.approval": "notify",
  "wait.delay": "control",
  "control.skip_if": "control",
};

describe("MOCK_CATALOG", () => {
  it("includes all 24 v1 step types", () => {
    const types = MOCK_CATALOG.step_types.map((s) => s.step_type).sort();
    expect(types).toEqual([...EXPECTED_STEP_TYPES].sort());
  });

  it("uses only the six canonical categories", () => {
    expect(MOCK_CATALOG.categories.map((c) => c.id).sort()).toEqual(
      ["action", "condition", "control", "fetch", "notify", "trigger"],
    );
  });

  it("assigns the canonical category to every step type", () => {
    for (const def of MOCK_CATALOG.step_types) {
      expect(def.category).toBe(EXPECTED_CATEGORY_BY_TYPE[def.step_type]);
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
      expect(def.trigger_only).toBe(isTriggerType);
    }
  });

  it("findStepType returns the matching def or undefined", () => {
    expect(findStepType(MOCK_CATALOG, "fetch.portfolio")?.label).toBe(
      "Get portfolio",
    );
    expect(findStepType(MOCK_CATALOG, "does.not.exist")).toBeUndefined();
  });

  it("max_retries matches ARCHITECTURE.md §7 invariant 3 for every step type", () => {
    for (const def of MOCK_CATALOG.step_types) {
      expect(def.max_retries).toBe(EXPECTED_MAX_RETRIES_BY_TYPE[def.step_type]);
    }
  });

  it("output_schema parity with backend registry for the 4 step types Day-1 had drift on", () => {
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
});
