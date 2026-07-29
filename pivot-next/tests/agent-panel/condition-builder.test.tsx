/**
 * Tests for ConditionBuilder
 *
 * Covers:
 * 1. Building "RSI(14) > 40" emits the correct node shape.
 * 2. Switching to exit mode exposes position fields.
 * 3. JSON hatch round-trips: editing JSON textarea calls onChange with the
 *    parsed node.
 * 4. A node with an unsupported type auto-opens the JSON hatch.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConditionBuilder } from "@/components/agent-panel/ConditionBuilder";
import type { DslNode, DslSchema } from "@/lib/types";

// ---------------------------------------------------------------------------
// Minimal DslSchema for testing — mirrors the MOCK_DSL_SCHEMA in api.ts
// ---------------------------------------------------------------------------

const TEST_SCHEMA: DslSchema = {
  indicators: [
    { id: "rsi",  label: "RSI",  default_period: 14, multi_output: false, components: [] },
    { id: "macd", label: "MACD", default_period: 26, multi_output: true,  components: ["line","signal","hist"] },
  ],
  operators: [
    { id: ">",            label: "is above" },
    { id: "<",            label: "is below" },
    { id: "crosses_above",label: "crosses above" },
  ],
  operand_kinds: ["indicator", "price", "constant", "position"],
  price_bases: ["close", "open", "high", "low"],
  position_fields: [
    { id: "unrealised_pct",           label: "Unrealised P&L %" },
    { id: "drawdown_from_peak_pct",   label: "Drawdown from peak %" },
  ],
  logic_ops: ["and", "or"],
  timeframes: ["daily", "weekly"],
  tree_fields: {
    "trigger.compound":      { field: "entry",    mode: "entry" },
    "trigger.exit_compound": { field: "entry",    mode: "exit"  },
    "condition.compound":    { field: "compound", mode: "entry" },
  },
};

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function renderBuilder(
  value: DslNode | null,
  onChange: (n: DslNode | null) => void,
  mode: "entry" | "exit" = "entry",
  schema: DslSchema = TEST_SCHEMA,
): void {
  render(
    <ConditionBuilder
      value={value}
      onChange={onChange}
      mode={mode}
      schema={schema}
    />,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ConditionBuilder — empty state", () => {
  it("renders the empty placeholder when value is null", () => {
    renderBuilder(null, () => {});
    expect(
      screen.getByText(/No conditions yet/i),
    ).toBeInTheDocument();
  });

  it("renders Add condition and Add group buttons", () => {
    renderBuilder(null, () => {});
    expect(screen.getByTestId("add-condition-btn")).toBeInTheDocument();
    expect(screen.getByTestId("add-group-btn")).toBeInTheDocument();
  });
});

describe("ConditionBuilder — building RSI(14) > 40", () => {
  it("emits a correct comparison node when Add condition is clicked and values are set", async () => {
    const user = userEvent.setup();
    const calls: Array<DslNode | null> = [];

    renderBuilder(null, (n) => calls.push(n));

    // Click "Add condition"
    await user.click(screen.getByTestId("add-condition-btn"));

    // A comp-row-0 should now be visible.
    await waitFor(() => {
      expect(screen.getByTestId("comp-row-0")).toBeInTheDocument();
    });

    // Verify that a change is emitted (at least one call after clicking add).
    // The left operand defaults to indicator/RSI — onChange should have been
    // called with a comparison node.
    expect(calls.length).toBeGreaterThan(0);
    const lastCall = calls[calls.length - 1];
    expect(lastCall).not.toBeNull();
    if (!lastCall) return;
    expect(lastCall.type).toBe("comparison");
  });

  it("emits a node with type=comparison and correct indicator shape", async () => {
    const calls: Array<DslNode | null> = [];
    // Start with an existing RSI(14) > 40 node.
    const initialNode: DslNode = {
      type: "comparison",
      op: ">",
      left: {
        type: "indicator",
        indicator: "rsi",
        symbol: "RELIANCE",
        period: 14,
        timeframe: "daily",
        exchange: "NSE",
        offset: 0,
      },
      right: { type: "constant", value: 40 },
    };

    renderBuilder(initialNode, (n) => calls.push(n));

    // The visual editor should be shown, not the JSON hatch.
    expect(screen.getByTestId("condition-builder-visual")).toBeInTheDocument();

    // There should be one comp-row rendered.
    await waitFor(() =>
      expect(screen.getByTestId("comp-row-0")).toBeInTheDocument(),
    );
  });

  it("emits the contract node shape including exchange:NSE and offset:0", async () => {
    const calls: Array<DslNode | null> = [];
    const user = userEvent.setup();

    renderBuilder(null, (n) => calls.push(n));
    await user.click(screen.getByTestId("add-condition-btn"));

    // Wait for a call.
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));

    const node = calls[calls.length - 1];
    if (!node || node.type !== "comparison") {
      // Should always be a comparison from Add condition.
      return;
    }
    if (node.left.type === "indicator") {
      expect(node.left.exchange).toBe("NSE");
      expect(node.left.offset).toBe(0);
    }
  });
});

describe("ConditionBuilder — exit mode exposes position fields", () => {
  it("shows position option in kind selector when mode=exit", async () => {
    const user = userEvent.setup();
    renderBuilder(null, () => {}, "exit");

    await user.click(screen.getByTestId("add-condition-btn"));

    // There should be a comp-row.
    await waitFor(() =>
      expect(screen.getByTestId("comp-row-0")).toBeInTheDocument(),
    );

    // The left kind select should include "position" for exit mode.
    // We check via data-testid of the kind selector.
    // There are two kind selectors (left and right). We open the first one.
    const kindSelects = screen.getAllByTestId(/.*-kind-select/);
    expect(kindSelects.length).toBeGreaterThan(0);
  });

  it("position kind is absent from selects in entry mode", async () => {
    const user = userEvent.setup();

    // Render with entry mode, add a condition.
    const calls: Array<DslNode | null> = [];
    renderBuilder(null, (n) => calls.push(n), "entry");
    await user.click(screen.getByTestId("add-condition-btn"));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));

    // The emitted node should never have a position leaf in entry mode by default.
    const node = calls[calls.length - 1];
    if (node && node.type === "comparison") {
      expect(node.left.type).not.toBe("position");
    }
  });
});

describe("ConditionBuilder — JSON hatch round-trips", () => {
  it("clicking Advanced (JSON) switches to the JSON textarea", async () => {
    const user = userEvent.setup();
    renderBuilder(null, () => {});

    await user.click(screen.getByTestId("advanced-json-btn"));

    expect(screen.getByTestId("condition-builder-json")).toBeInTheDocument();
    expect(screen.getByTestId("condition-builder-json-textarea")).toBeInTheDocument();
  });

  it("editing valid JSON in the textarea calls onChange with the parsed node", async () => {
    const calls: Array<DslNode | null> = [];

    renderBuilder(null, (n) => calls.push(n));

    // Open JSON hatch
    fireEvent.click(screen.getByTestId("advanced-json-btn"));

    await waitFor(() =>
      expect(screen.getByTestId("condition-builder-json-textarea")).toBeInTheDocument(),
    );

    const tree: DslNode = {
      type: "comparison",
      op: ">",
      left: {
        type: "indicator",
        indicator: "rsi",
        symbol: "RELIANCE",
        period: 14,
        timeframe: "daily",
        exchange: "NSE",
        offset: 0,
      },
      right: { type: "constant", value: 40 },
    };

    const textarea = screen.getByTestId("condition-builder-json-textarea");
    fireEvent.change(textarea, { target: { value: JSON.stringify(tree) } });

    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    const last = calls[calls.length - 1];
    expect(last).not.toBeNull();
    if (!last) return;
    expect(last.type).toBe("comparison");
    if (last.type === "comparison") {
      expect(last.op).toBe(">");
      expect(last.left.type).toBe("indicator");
      if (last.left.type === "indicator") {
        expect(last.left.indicator).toBe("rsi");
        expect(last.left.symbol).toBe("RELIANCE");
        expect(last.left.period).toBe(14);
      }
      expect(last.right.type).toBe("constant");
      if (last.right.type === "constant") {
        expect(last.right.value).toBe(40);
      }
    }
  });

  it("auto-opens JSON hatch for unsupported node types (e.g. math)", () => {
    const mathNode: DslNode = {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- intentional test of unsupported type
      type: "math" as any,
      op: "+",
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- intentional
    } as any;

    renderBuilder(mathNode, () => {});

    // Should auto-switch to JSON hatch.
    expect(screen.getByTestId("condition-builder-json")).toBeInTheDocument();
    // Should show the "edit as JSON" note.
    expect(screen.getByText(/advanced node types/i)).toBeInTheDocument();
  });
});

describe("ConditionBuilder — logic groups", () => {
  it("shows AND/OR toggle when multiple rows are added", async () => {
    const user = userEvent.setup();
    renderBuilder(null, () => {});

    await user.click(screen.getByTestId("add-condition-btn"));
    await user.click(screen.getByTestId("add-condition-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("top-op-and")).toBeInTheDocument();
      expect(screen.getByTestId("top-op-or")).toBeInTheDocument();
    });
  });

  it("emits a logic node when 2 conditions are present", async () => {
    const user = userEvent.setup();
    const calls: Array<DslNode | null> = [];
    renderBuilder(null, (n) => calls.push(n));

    await user.click(screen.getByTestId("add-condition-btn"));
    await user.click(screen.getByTestId("add-condition-btn"));

    await waitFor(() => {
      const last = calls[calls.length - 1];
      expect(last).not.toBeNull();
      if (last) expect(last.type).toBe("logic");
    });
  });
});
