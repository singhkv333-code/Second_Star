import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import {
  WorkflowDraftCard,
  draftToWorkflow,
  type WorkflowDraft,
} from "@/components/chat/WorkflowDraftCard";
import * as api from "@/lib/api";

const DEMO_DRAFT: WorkflowDraft = {
  name: "RELIANCE 3:55 PM buy",
  description: "Every weekday at 3:55 PM IST, buy 10 shares.",
  steps: [
    { step_type: "trigger.schedule", label: "Every weekday at 3:55 PM IST", config: {} },
    { step_type: "fetch.portfolio", label: "Get portfolio", config: {} },
    { step_type: "condition.numeric", label: "Buying power > ₹50k", config: {} },
    { step_type: "action.place_order", label: "Buy 10 RELIANCE", config: {} },
    { step_type: "notify.message", label: "Email confirmation", config: {} },
  ],
  rationale: "Mapped to a scheduled trigger workflow.",
  warnings: [],
  _render_hint: "workflow_draft_card",
};

describe("WorkflowDraftCard", () => {
  it("renders the draft name and description", () => {
    render(<WorkflowDraftCard draft={DEMO_DRAFT} onOpenEditor={vi.fn()} />);
    expect(screen.getByText("RELIANCE 3:55 PM buy")).toBeInTheDocument();
    expect(screen.getByText("Every weekday at 3:55 PM IST, buy 10 shares.")).toBeInTheDocument();
  });

  it("shows all 5 steps", () => {
    render(<WorkflowDraftCard draft={DEMO_DRAFT} onOpenEditor={vi.fn()} />);
    expect(screen.getByText("Every weekday at 3:55 PM IST")).toBeInTheDocument();
    expect(screen.getByText("Buy 10 RELIANCE")).toBeInTheDocument();
  });

  it("truncates steps beyond MAX_VISIBLE_STEPS with a count", () => {
    const longDraft: WorkflowDraft = {
      ...DEMO_DRAFT,
      steps: [
        ...DEMO_DRAFT.steps,
        { step_type: "notify.log", label: "Log step 6", config: {} },
        { step_type: "wait.delay", label: "Delay", config: {} },
      ],
    };
    render(<WorkflowDraftCard draft={longDraft} onOpenEditor={vi.fn()} />);
    expect(screen.getByText("+2 more steps")).toBeInTheDocument();
  });

  it("shows warnings when present", () => {
    const draftWithWarning: WorkflowDraft = {
      ...DEMO_DRAFT,
      warnings: ["LLM fallback — review every field"],
    };
    render(<WorkflowDraftCard draft={draftWithWarning} onOpenEditor={vi.fn()} />);
    expect(screen.getByText("LLM fallback — review every field")).toBeInTheDocument();
  });

  it("calls onOpenEditor with the draft when button clicked", () => {
    const onOpenEditor = vi.fn();
    render(<WorkflowDraftCard draft={DEMO_DRAFT} onOpenEditor={onOpenEditor} />);
    fireEvent.click(screen.getByTestId("open-in-editor-button"));
    expect(onOpenEditor).toHaveBeenCalledWith(DEMO_DRAFT);
  });

  it("shows the rationale behind a 'Why this?' disclosure", () => {
    render(<WorkflowDraftCard draft={DEMO_DRAFT} onOpenEditor={vi.fn()} />);
    // Rationale is hidden by default to keep the card visually calm —
    // the user opts in via a disclosure button.
    expect(
      screen.queryByText("Mapped to a scheduled trigger workflow."),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /why this/i }));
    expect(
      screen.getByText("Mapped to a scheduled trigger workflow."),
    ).toBeInTheDocument();
  });
});

describe("WorkflowDraftCard — Save & activate", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("saves and activates on click and shows confirmation", async () => {
    const created = {
      id: "wf-123", name: "RELIANCE 3:55 PM buy", description: null,
      status: "draft" as const, version: 1, single_instance: true,
      created_at: "now", updated_at: "now", activated_at: null,
      last_run_at: null, next_run_at: null, steps: [],
    };
    const activated = { ...created, status: "active" as const, activated_at: "now" };
    const createSpy = vi
      .spyOn(api, "createWorkflow")
      .mockResolvedValue({ data: created });
    const activateSpy = vi
      .spyOn(api, "activateWorkflow")
      .mockResolvedValue({ data: activated });

    render(<WorkflowDraftCard draft={DEMO_DRAFT} onOpenEditor={vi.fn()} />);
    fireEvent.click(screen.getByTestId("save-activate-button"));

    await waitFor(() =>
      expect(screen.getByTestId("workflow-saved")).toBeInTheDocument(),
    );
    expect(createSpy).toHaveBeenCalledTimes(1);
    expect(activateSpy).toHaveBeenCalledWith("wf-123");
    expect(screen.getByText(/Saved & activated/)).toBeInTheDocument();
    // Workflow name (not id) is the user-facing identifier in the saved
    // confirmation — the bare uuid was visual noise.
    expect(screen.getByText(/RELIANCE 3:55 PM buy/)).toBeInTheDocument();
  });

  it("shows error message when createWorkflow fails", async () => {
    vi.spyOn(api, "createWorkflow").mockResolvedValue({
      error: { code: "validation_error", message: "Step 0 must be a trigger" },
    });

    render(<WorkflowDraftCard draft={DEMO_DRAFT} onOpenEditor={vi.fn()} />);
    fireEvent.click(screen.getByTestId("save-activate-button"));

    await waitFor(() =>
      expect(screen.getByTestId("workflow-save-error")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Step 0 must be a trigger/)).toBeInTheDocument();
  });

  it("kicks off a manual run and notifies parent with the runId", async () => {
    const created = {
      id: "wf-99", name: "RELIANCE 3:55 PM buy", description: null,
      status: "draft" as const, version: 1, single_instance: true,
      created_at: "now", updated_at: "now", activated_at: null,
      last_run_at: null, next_run_at: null, steps: [],
    };
    const activated = { ...created, status: "active" as const, activated_at: "now" };
    vi.spyOn(api, "createWorkflow").mockResolvedValue({ data: created });
    vi.spyOn(api, "activateWorkflow").mockResolvedValue({ data: activated });
    const runSpy = vi
      .spyOn(api, "runWorkflow")
      .mockResolvedValue({ data: { run_id: "run-xyz" } });
    const onActivatedAndRunning = vi.fn();

    render(
      <WorkflowDraftCard
        draft={DEMO_DRAFT}
        onOpenEditor={vi.fn()}
        onActivatedAndRunning={onActivatedAndRunning}
      />,
    );
    fireEvent.click(screen.getByTestId("save-activate-button"));

    await waitFor(() =>
      expect(onActivatedAndRunning).toHaveBeenCalledTimes(1),
    );
    expect(runSpy).toHaveBeenCalledWith("wf-99");
    expect(onActivatedAndRunning).toHaveBeenCalledWith({
      workflowId: "wf-99",
      workflowName: "RELIANCE 3:55 PM buy",
      runId: "run-xyz",
    });
  });

  it("does not call onActivatedAndRunning when the run kickoff fails", async () => {
    const created = {
      id: "wf-99", name: "RELIANCE 3:55 PM buy", description: null,
      status: "draft" as const, version: 1, single_instance: true,
      created_at: "now", updated_at: "now", activated_at: null,
      last_run_at: null, next_run_at: null, steps: [],
    };
    const activated = { ...created, status: "active" as const, activated_at: "now" };
    vi.spyOn(api, "createWorkflow").mockResolvedValue({ data: created });
    vi.spyOn(api, "activateWorkflow").mockResolvedValue({ data: activated });
    vi.spyOn(api, "runWorkflow").mockResolvedValue({
      error: { code: "conflict", message: "already running" },
    });
    const onActivatedAndRunning = vi.fn();

    render(
      <WorkflowDraftCard
        draft={DEMO_DRAFT}
        onOpenEditor={vi.fn()}
        onActivatedAndRunning={onActivatedAndRunning}
      />,
    );
    fireEvent.click(screen.getByTestId("save-activate-button"));

    await waitFor(() =>
      expect(screen.getByTestId("workflow-saved")).toBeInTheDocument(),
    );
    expect(onActivatedAndRunning).not.toHaveBeenCalled();
  });
});

describe("draftToWorkflow", () => {
  it("converts draft to Workflow with correct step count", () => {
    const wf = draftToWorkflow(DEMO_DRAFT);
    expect(wf.steps).toHaveLength(5);
    expect(wf.name).toBe("RELIANCE 3:55 PM buy");
    expect(wf.status).toBe("draft");
    expect(wf.id).toBe("");
  });

  it("assigns step_index correctly", () => {
    const wf = draftToWorkflow(DEMO_DRAFT);
    wf.steps.forEach((s, i) => {
      expect(s.step_index).toBe(i);
    });
  });

  it("preserves step_type and config", () => {
    const wf = draftToWorkflow(DEMO_DRAFT);
    expect(wf.steps[0]?.step_type).toBe("trigger.schedule");
    expect(wf.steps[3]?.step_type).toBe("action.place_order");
  });
});
