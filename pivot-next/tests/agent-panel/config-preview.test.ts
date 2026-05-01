import { describe, expect, it } from "vitest";
import { previewStepConfig } from "@/components/agent-panel/config-preview";
import type { Step } from "@/lib/types";

const make = (step_type: string, config: Record<string, unknown>): Step => ({
  id: "x",
  step_index: 0,
  step_type,
  label: null,
  config,
});

describe("previewStepConfig", () => {
  it("formats trigger.schedule with cron + timezone", () => {
    expect(
      previewStepConfig(
        make("trigger.schedule", {
          cron: "55 15 * * 1-5",
          timezone: "Asia/Kolkata",
        }),
      ),
    ).toBe("55 15 * * 1-5 (Asia/Kolkata)");
  });

  it("formats action.place_order with side, qty, symbol, and approval flag", () => {
    const out = previewStepConfig(
      make("action.place_order", {
        symbol: "RELIANCE",
        side: "buy",
        quantity: 10,
        order_type: "market",
        requires_approval: true,
      }),
    );
    expect(out).toContain("BUY");
    expect(out).toContain("10");
    expect(out).toContain("RELIANCE");
    expect(out).toContain("approval");
  });

  it("preserves ref strings in condition.numeric", () => {
    expect(
      previewStepConfig(
        make("condition.numeric", {
          left: "{{ context.1.buying_power }}",
          operator: ">",
          right: 50000,
        }),
      ),
    ).toBe("{{ context.1.buying_power }} > 50000");
  });

  it("falls back to a generic key=value summary for unknown step types", () => {
    expect(
      previewStepConfig(
        make("custom.unknown", { foo: "bar", n: 3 }),
      ),
    ).toBe("foo=bar, n=3");
  });
});
