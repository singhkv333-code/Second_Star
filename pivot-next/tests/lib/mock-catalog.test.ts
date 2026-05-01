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
});
