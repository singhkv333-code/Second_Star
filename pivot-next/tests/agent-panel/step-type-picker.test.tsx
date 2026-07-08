import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StepTypePicker } from "@/components/agent-panel/StepTypePicker";
import { MOCK_CATALOG } from "@/lib/mock-catalog";
import type { Step } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Minimal Step factory for tests. */
function mkStep(step_index: number, step_type: string): Step {
  return {
    id: `test-${step_index}`,
    step_index,
    step_type,
    label: null,
    config: {},
  };
}

describe("StepTypePicker", () => {
  // ── Hard structural filter ──────────────────────────────────────────────

  it("renders all non-trigger step types when insertIndex > 0", () => {
    render(
      <StepTypePicker
        open
        insertIndex={1}
        catalog={MOCK_CATALOG}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    const expectedCount = MOCK_CATALOG.step_types.filter((d) => !d.trigger_only).length;
    const items = screen.getAllByTestId(/^step-picker-item-/);
    expect(items.length).toBe(expectedCount);
    // No trigger.* item should appear.
    for (const item of items) {
      const tid = item.getAttribute("data-testid") ?? "";
      expect(tid.startsWith("step-picker-item-trigger.")).toBe(false);
    }
  });

  it("at insertIndex === 0 only the trigger step types are visible", () => {
    render(
      <StepTypePicker
        open
        insertIndex={0}
        catalog={MOCK_CATALOG}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    const expectedCount = MOCK_CATALOG.step_types.filter((d) => d.trigger_only).length;
    const items = screen.getAllByTestId(/^step-picker-item-/);
    expect(items.length).toBe(expectedCount);
    for (const item of items) {
      const tid = item.getAttribute("data-testid") ?? "";
      expect(tid.startsWith("step-picker-item-trigger.")).toBe(true);
    }
  });

  // ── Group sub-headings ──────────────────────────────────────────────────

  it("renders group headings for all non-trigger categories at insertIndex > 0", () => {
    render(
      <StepTypePicker
        open
        insertIndex={1}
        catalog={MOCK_CATALOG}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    // Each category appears in at least one bucket — look for any group with
    // that category ID regardless of bucket (testId is bucket-category).
    const allGroupTestIds = screen
      .getAllByTestId(/^step-picker-group-/)
      .map((el) => el.getAttribute("data-testid") ?? "");

    const hasCategory = (cat: string) =>
      allGroupTestIds.some((tid) => tid.endsWith(`-${cat}`));

    expect(hasCategory("fetch")).toBe(true);
    expect(hasCategory("condition")).toBe(true);
    expect(hasCategory("action")).toBe(true);
    expect(hasCategory("notify")).toBe(true);
    expect(hasCategory("control")).toBe(true);
    // Trigger category must NOT appear at insertIndex > 0.
    expect(hasCategory("trigger")).toBe(false);
  });

  // ── Capability buckets ──────────────────────────────────────────────────

  it("places all triggers in Recommended bucket at insertIndex === 0", () => {
    render(
      <StepTypePicker
        open
        insertIndex={0}
        catalog={MOCK_CATALOG}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    expect(
      screen.getByTestId("step-picker-bucket-recommended"),
    ).toBeInTheDocument();
    // At trigger slot no Available or Needs-setup bucket should render.
    expect(screen.queryByTestId("step-picker-bucket-available")).toBeNull();
    expect(screen.queryByTestId("step-picker-bucket-needs-setup")).toBeNull();
  });

  it("after a place_order step, set_protective goes to Recommended", () => {
    const priorSteps: Step[] = [
      mkStep(0, "trigger.schedule"),
      mkStep(1, "action.place_order"),
    ];
    render(
      <StepTypePicker
        open
        insertIndex={2}
        steps={priorSteps}
        catalog={MOCK_CATALOG}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    // action.place_order produces position_open → set_protective requires
    // position_open → should appear in Recommended.
    const bucketRec = screen.getByTestId("step-picker-bucket-recommended");
    expect(bucketRec).toBeInTheDocument();

    const stopItem = screen.getByTestId("step-picker-item-action.set_protective");
    expect(stopItem).toBeInTheDocument();
  });

  it("without a prior position producer, set_protective goes to Needs setup", () => {
    const priorSteps: Step[] = [
      mkStep(0, "trigger.schedule"),
    ];
    render(
      <StepTypePicker
        open
        insertIndex={1}
        steps={priorSteps}
        catalog={MOCK_CATALOG}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    // No prior position producer — set_protective must be in Needs setup.
    const needsBucket = screen.getByTestId("step-picker-bucket-needs-setup");
    expect(needsBucket).toBeInTheDocument();

    const stopItem = screen.getByTestId("step-picker-item-action.set_protective");
    expect(stopItem).toBeInTheDocument();
    // The warn text should be visible on the row (overrides description).
    expect(stopItem.textContent).toContain("needs a position");
  });

  it("Needs-setup items remain clickable (hybrid strictness)", async () => {
    const onSelect = vi.fn();
    const priorSteps: Step[] = [mkStep(0, "trigger.schedule")];
    render(
      <StepTypePicker
        open
        insertIndex={1}
        steps={priorSteps}
        catalog={MOCK_CATALOG}
        onSelect={onSelect}
        onClose={() => {}}
      />,
    );
    // Even though set_protective is in Needs setup, clicking it should fire onSelect.
    await userEvent.click(
      screen.getByTestId("step-picker-item-action.set_protective"),
    );
    expect(onSelect).toHaveBeenCalled();
    const arg = onSelect.mock.calls[0]?.[0] as { step_type?: string };
    expect(arg?.step_type).toBe("action.set_protective");
  });

  // ── Search ──────────────────────────────────────────────────────────────

  it("search narrows the visible items by step_type, label, or description", async () => {
    render(
      <StepTypePicker
        open
        insertIndex={1}
        catalog={MOCK_CATALOG}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );

    const input = screen.getByRole("combobox");
    await userEvent.type(input, "place_order");

    expect(
      screen.getByTestId("step-picker-item-action.place_order"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("step-picker-item-fetch.portfolio"),
    ).toBeNull();
  });

  // ── onSelect / onClose ──────────────────────────────────────────────────

  it("invokes onSelect with the chosen def and closes", async () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <StepTypePicker
        open
        insertIndex={1}
        catalog={MOCK_CATALOG}
        onSelect={onSelect}
        onClose={onClose}
      />,
    );

    await userEvent.click(
      screen.getByTestId("step-picker-item-fetch.portfolio"),
    );
    expect(onSelect).toHaveBeenCalled();
    const arg = onSelect.mock.calls[0]?.[0] as { step_type?: string };
    expect(arg?.step_type).toBe("fetch.portfolio");
    expect(onClose).toHaveBeenCalled();
  });
});
