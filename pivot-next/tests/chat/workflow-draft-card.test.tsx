import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  WorkflowDraftCard,
  draftToWorkflow,
  type WorkflowDraft,
} from "@/components/chat/WorkflowDraftCard";

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

  it("shows the rationale", () => {
    render(<WorkflowDraftCard draft={DEMO_DRAFT} onOpenEditor={vi.fn()} />);
    expect(screen.getByText("Mapped to a scheduled trigger workflow.")).toBeInTheDocument();
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
