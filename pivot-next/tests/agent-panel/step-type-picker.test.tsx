import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StepTypePicker } from "@/components/agent-panel/StepTypePicker";
import { MOCK_CATALOG } from "@/lib/mock-catalog";

describe("StepTypePicker", () => {
  it("renders all 24 v1 step types when insertIndex > 0 (no triggers visible)", () => {
    render(
      <StepTypePicker
        open
        insertIndex={1}
        catalog={MOCK_CATALOG}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    // 24 in catalog - 6 triggers = 18 visible.
    const items = screen.getAllByTestId(/^step-picker-item-/);
    expect(items.length).toBe(18);
    // No trigger.* item should appear.
    for (const item of items) {
      const tid = item.getAttribute("data-testid") ?? "";
      expect(tid.startsWith("step-picker-item-trigger.")).toBe(false);
    }
  });

  it("at insertIndex === 0 only the 6 trigger.* step types are visible", () => {
    render(
      <StepTypePicker
        open
        insertIndex={0}
        catalog={MOCK_CATALOG}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    const items = screen.getAllByTestId(/^step-picker-item-/);
    expect(items.length).toBe(6);
    for (const item of items) {
      const tid = item.getAttribute("data-testid") ?? "";
      expect(tid.startsWith("step-picker-item-trigger.")).toBe(true);
    }
  });

  it("groups items by category, using catalog-supplied category labels", () => {
    render(
      <StepTypePicker
        open
        insertIndex={1}
        catalog={MOCK_CATALOG}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    // All non-trigger categories should have a group rendered.
    expect(
      screen.getByTestId("step-picker-group-fetch"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("step-picker-group-condition"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("step-picker-group-action"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("step-picker-group-notify"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("step-picker-group-control"),
    ).toBeInTheDocument();
    // Trigger group must NOT be present at insertIndex > 0.
    expect(
      screen.queryByTestId("step-picker-group-trigger"),
    ).toBeNull();
  });

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

    // After typing 'place_order', only action.place_order should match — the
    // string is unique enough that cmdk leaves only that single option.
    expect(
      screen.getByTestId("step-picker-item-action.place_order"),
    ).toBeInTheDocument();
    // Other types should be filtered out by cmdk (DOM removes them entirely).
    expect(
      screen.queryByTestId("step-picker-item-fetch.portfolio"),
    ).toBeNull();
  });

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
