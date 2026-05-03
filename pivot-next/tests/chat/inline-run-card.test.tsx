/**
 * Tests for InlineRunCard — the public.com-style live-run checklist
 * embedded in the chat thread after Save & activate. We mock the
 * useRunStream hook so we can drive the card through every step state
 * without standing up a WS server in the test runtime.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { InlineRunCard } from "@/components/chat/InlineRunCard";
import type { Run, RunStepStatus } from "@/lib/types";

vi.mock("@/lib/use-run-stream", () => ({
  useRunStream: vi.fn(),
}));
vi.mock("@/components/agent-panel/use-step-catalog", () => ({
  useStepCatalog: vi.fn(),
}));
import { useRunStream } from "@/lib/use-run-stream";
import { useStepCatalog } from "@/components/agent-panel/use-step-catalog";

const CATALOG = {
  step_types: [
    { step_type: "trigger.schedule", category: "trigger", label: "On schedule", icon: "calendar-clock" },
    { step_type: "fetch.portfolio", category: "fetch", label: "Get portfolio", icon: "wallet" },
    { step_type: "condition.numeric", category: "condition", label: "Numeric condition", icon: "git-branch" },
    { step_type: "action.place_order", category: "action", label: "Place order", icon: "shopping-cart" },
    { step_type: "notify.message", category: "notify", label: "Send message", icon: "send" },
  ],
};

function step(idx: number, type: string, status: RunStepStatus) {
  return {
    step_index: idx,
    step_type: type,
    status,
    started_at: status === "pending" ? null : "2026-05-03T03:30:00Z",
    finished_at: ["succeeded", "failed", "skipped"].includes(status)
      ? "2026-05-03T03:30:01Z"
      : null,
    output: null,
    error_message: null,
    attempts: 1,
  };
}

function makeRun(status: Run["status"], steps: ReturnType<typeof step>[]): Run {
  return {
    id: "run-abc",
    workflow_id: "wf-1",
    workflow_version: 1,
    triggered_by: "manual",
    started_at: "2026-05-03T03:30:00Z",
    finished_at: status === "running" ? null : "2026-05-03T03:30:05Z",
    status,
    halt_reason: null,
    error_message: null,
    steps,
  };
}

beforeEach(() => {
  vi.mocked(useStepCatalog).mockReturnValue({
    status: "ready",
    catalog: CATALOG as never,
  });
});

describe("InlineRunCard", () => {
  it("shows a loading skeleton until the first snapshot arrives", () => {
    vi.mocked(useRunStream).mockReturnValue({
      run: null, isReconnecting: false, error: null, pendingApprovals: [],
    });
    render(<InlineRunCard runId="run-abc" workflowName="RELIANCE 3:55" />);
    expect(screen.getByTestId("inline-run-skeleton")).toBeInTheDocument();
  });

  it("renders the running state with the public.com-style checklist", () => {
    vi.mocked(useRunStream).mockReturnValue({
      run: makeRun("running", [
        step(0, "trigger.schedule", "succeeded"),
        step(1, "fetch.portfolio", "succeeded"),
        step(2, "condition.numeric", "succeeded"),
        step(3, "action.place_order", "running"),
        step(4, "notify.message", "pending"),
      ]),
      isReconnecting: false, error: null, pendingApprovals: [],
    });

    render(<InlineRunCard runId="run-abc" workflowName="RELIANCE 3:55" />);
    expect(screen.getByTestId("inline-run-card")).toBeInTheDocument();
    expect(screen.getByText(/Running · RELIANCE 3:55/)).toBeInTheDocument();
    // All 5 steps render with correct status data attribute (per the
    // image: 3 succeeded, 1 running, 1 pending).
    expect(screen.getByTestId("inline-step-0")).toHaveAttribute("data-status", "succeeded");
    expect(screen.getByTestId("inline-step-3")).toHaveAttribute("data-status", "running");
    expect(screen.getByTestId("inline-step-4")).toHaveAttribute("data-status", "pending");
  });

  it("renders step labels from the catalog", () => {
    vi.mocked(useRunStream).mockReturnValue({
      run: makeRun("succeeded", [
        step(0, "trigger.schedule", "succeeded"),
        step(1, "action.place_order", "succeeded"),
      ]),
      isReconnecting: false, error: null, pendingApprovals: [],
    });
    render(<InlineRunCard runId="run-abc" workflowName="Demo" />);
    expect(screen.getByText("On schedule")).toBeInTheDocument();
    expect(screen.getByText("Place order")).toBeInTheDocument();
  });

  it("shows a reconnecting indicator when the WS drops", () => {
    vi.mocked(useRunStream).mockReturnValue({
      run: makeRun("running", [step(0, "trigger.schedule", "succeeded")]),
      isReconnecting: true, error: null, pendingApprovals: [],
    });
    render(<InlineRunCard runId="run-abc" workflowName="Demo" />);
    expect(screen.getByTestId("inline-run-reconnecting")).toBeInTheDocument();
  });

  it("renders an error state when the stream fails before any snapshot", () => {
    vi.mocked(useRunStream).mockReturnValue({
      run: null, isReconnecting: false,
      error: { code: "internal_error", message: "WS handshake failed" },
      pendingApprovals: [],
    });
    render(<InlineRunCard runId="run-abc" workflowName="Demo" />);
    expect(screen.getByTestId("inline-run-error")).toBeInTheDocument();
    expect(screen.getByText(/WS handshake failed/)).toBeInTheDocument();
  });

  it("renders a 'View full run' button when onOpenFullView is provided", () => {
    vi.mocked(useRunStream).mockReturnValue({
      run: makeRun("running", [step(0, "trigger.schedule", "running")]),
      isReconnecting: false, error: null, pendingApprovals: [],
    });
    render(
      <InlineRunCard
        runId="run-abc"
        workflowName="Demo"
        onOpenFullView={() => {}}
      />,
    );
    expect(screen.getByTestId("inline-run-open-full")).toBeInTheDocument();
  });
});
