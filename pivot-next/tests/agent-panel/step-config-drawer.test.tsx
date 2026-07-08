import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StepConfigDrawer } from "@/components/agent-panel/StepConfigDrawer";
import { MOCK_CATALOG } from "@/lib/mock-catalog";
import type { Step, StepTypeDef, Workflow } from "@/lib/types";

function makeWorkflow(steps: Step[]): Workflow {
  return {
    id: "w1",
    name: "Test workflow",
    description: null,
    status: "draft",
    version: 1,
    single_instance: true,
    created_at: "2026-05-02T00:00:00Z",
    updated_at: "2026-05-02T00:00:00Z",
    activated_at: null,
    last_run_at: null,
    next_run_at: null,
    steps,
  };
}

function findCatalogEntry(stepType: string): StepTypeDef {
  const entry = MOCK_CATALOG.step_types.find((s) => s.step_type === stepType);
  if (!entry) throw new Error(`Catalog missing ${stepType}`);
  return entry;
}

function makeStep(stepType: string, idx: number, config: Record<string, unknown> = {}): Step {
  return {
    id: `s-${stepType}-${idx}`,
    step_index: idx,
    step_type: stepType,
    label: null,
    config,
  };
}

describe("StepConfigDrawer — schema rendering", () => {
  for (const def of MOCK_CATALOG.step_types) {
    it(`renders form for ${def.step_type} without throwing`, () => {
      const step = makeStep(def.step_type, 0);
      const workflow = makeWorkflow([step]);
      render(
        <StepConfigDrawer
          step={step}
          catalogEntry={def}
          workflow={workflow}
          onSave={() => ({})}
          onClose={() => {}}
        />,
      );
      // Header is always present.
      expect(screen.getByText(def.label)).toBeInTheDocument();
    });
  }
});

describe("StepConfigDrawer — submit flow", () => {
  it("emits the correct config object on a valid submit", async () => {
    const def = findCatalogEntry("trigger.schedule");
    const step = makeStep("trigger.schedule", 0, {
      cron: "55 15 * * 1-5",
      timezone: "Asia/Kolkata",
    });
    const workflow = makeWorkflow([step]);
    const submissions: Record<string, unknown>[] = [];
    const onSave = (config: Record<string, unknown>) => {
      submissions.push(config);
      return {};
    };
    const onClose = vi.fn();

    render(
      <StepConfigDrawer
        step={step}
        catalogEntry={def}
        workflow={workflow}
        onSave={onSave}
        onClose={onClose}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(submissions.length).toBeGreaterThan(0));
    expect(submissions[0]).toMatchObject({
      cron: "55 15 * * 1-5",
      timezone: "Asia/Kolkata",
    });
    expect(onClose).toHaveBeenCalled();
  });

  it("surfaces a 422 API error and highlights the offending field", async () => {
    const def = findCatalogEntry("trigger.schedule");
    const step = makeStep("trigger.schedule", 0, {
      cron: "55 15 * * 1-5",
      timezone: "Asia/Kolkata",
    });
    const workflow = makeWorkflow([step]);
    const onSave = vi.fn(() => ({
      error: {
        code: "validation_error",
        message: "Bad cron expression",
        details: { step_index: 0, field: "cron" },
      },
    }));

    render(
      <StepConfigDrawer
        step={step}
        catalogEntry={def}
        workflow={workflow}
        onSave={onSave}
        onClose={() => {}}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() =>
      expect(screen.getAllByText(/Bad cron expression/).length).toBeGreaterThan(0),
    );
    await waitFor(() =>
      expect(screen.getByTestId("field-error-cron")).toBeInTheDocument(),
    );
  });

  it("rejects a string field with an unknown ref namespace at submit", async () => {
    const def = findCatalogEntry("notify.message");
    const step = makeStep("notify.message", 1, {
      channel: "email",
      template: "{{ pirate.flag }}",
      vars: {},
    });
    const workflow = makeWorkflow([
      makeStep("trigger.schedule", 0, { cron: "0 9 * * *", timezone: "UTC" }),
      step,
    ]);
    const onSave = vi.fn(() => ({}));

    render(
      <StepConfigDrawer
        step={step}
        catalogEntry={def}
        workflow={workflow}
        onSave={onSave}
        onClose={() => {}}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    // onSave never called because ref check fails first.
    await waitFor(() =>
      expect(screen.getAllByText(/unknown namespace/i).length).toBeGreaterThan(0),
    );
    expect(onSave).not.toHaveBeenCalled();
  });

  it("Cmd+Enter submits the form", async () => {
    const def = findCatalogEntry("notify.log");
    const step = makeStep("notify.log", 0, { message: "hello" });
    const workflow = makeWorkflow([step]);
    const onSave = vi.fn(() => ({}));

    render(
      <StepConfigDrawer
        step={step}
        catalogEntry={def}
        workflow={workflow}
        onSave={onSave}
        onClose={() => {}}
      />,
    );

    fireEvent.keyDown(window, { key: "Enter", metaKey: true });
    await waitFor(() => expect(onSave).toHaveBeenCalled());
  });

  it("Esc invokes onClose", async () => {
    const def = findCatalogEntry("notify.log");
    const step = makeStep("notify.log", 0, { message: "hello" });
    const workflow = makeWorkflow([step]);
    const onClose = vi.fn();

    render(
      <StepConfigDrawer
        step={step}
        catalogEntry={def}
        workflow={workflow}
        onSave={() => ({})}
        onClose={onClose}
      />,
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
