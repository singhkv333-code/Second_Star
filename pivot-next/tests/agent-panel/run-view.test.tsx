import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RunView } from "@/components/agent-panel/RunView";
import { MOCK_CATALOG } from "@/lib/mock-catalog";
import { setBackendSource } from "@/lib/api";
import * as api from "@/lib/api";
import type {
  Approval,
  Run,
  RunStep,
  RunStepStatus,
} from "@/lib/types";
import type { RunStreamCallbacks } from "@/lib/ws";

// Pluggable mock-run-stream module so we can drive frames imperatively
// from each test instead of waiting on timers.
let activeCallbacks: RunStreamCallbacks | null = null;
let lastRunId: string | null = null;

vi.mock("@/lib/mock-run", () => ({
  openMockRunStream: (runId: string, callbacks: RunStreamCallbacks) => {
    activeCallbacks = callbacks;
    lastRunId = runId;
    return {
      close: () => {
        activeCallbacks = null;
      },
      isOpen: () => activeCallbacks !== null,
    };
  },
}));

beforeEach(() => {
  setBackendSource("mock");
  activeCallbacks = null;
  lastRunId = null;
  // Default: decideApproval resolves successfully so approval banners dismiss.
  vi.spyOn(api, "decideApproval").mockResolvedValue({
    data: { id: "ap-1", decision: "approved", decided_at: "2026-05-02T10:01:00Z" },
  });
});

afterEach(() => {
  activeCallbacks = null;
  vi.restoreAllMocks();
});

function emit(action: (cbs: RunStreamCallbacks) => void): void {
  if (!activeCallbacks) throw new Error("Stream not open");
  act(() => action(activeCallbacks!));
}

function makeRun(steps: RunStep[]): Run {
  return {
    id: "run-1",
    workflow_id: "wf-1",
    workflow_version: 1,
    triggered_by: "manual",
    started_at: "2026-05-02T10:00:00Z",
    finished_at: null,
    status: "running",
    halt_reason: null,
    error_message: null,
    context: {},
    steps,
  };
}

function makeStep(idx: number, type: string, status: RunStepStatus): RunStep {
  return {
    step_index: idx,
    step_type: type,
    status,
    started_at: status !== "pending" ? "2026-05-02T10:00:00Z" : null,
    finished_at: status === "succeeded" ? "2026-05-02T10:00:05Z" : null,
    output: status === "succeeded" ? { ok: true } : null,
    error_message: status === "failed" ? "boom" : null,
    attempts: 1,
  };
}

describe("RunView — status colors", () => {
  it("renders one row per step and tags each with its status", () => {
    render(<RunView runId="run-1" catalog={MOCK_CATALOG} />);

    emit((cb) =>
      cb.onSnapshot(
        makeRun([
          makeStep(0, "trigger.schedule", "succeeded"),
          makeStep(1, "fetch.portfolio", "running"),
          makeStep(2, "condition.numeric", "pending"),
          makeStep(3, "action.place_order", "awaiting_approval"),
          makeStep(4, "notify.message", "skipped"),
        ]),
      ),
    );

    expect(screen.getByTestId("run-step-0")).toHaveAttribute(
      "data-status",
      "succeeded",
    );
    expect(screen.getByTestId("run-step-1")).toHaveAttribute(
      "data-status",
      "running",
    );
    expect(screen.getByTestId("run-step-2")).toHaveAttribute(
      "data-status",
      "pending",
    );
    expect(screen.getByTestId("run-step-3")).toHaveAttribute(
      "data-status",
      "awaiting_approval",
    );
    expect(screen.getByTestId("run-step-4")).toHaveAttribute(
      "data-status",
      "skipped",
    );
  });

  it("renders a failed step with its error message in the detail pane", () => {
    render(<RunView runId="run-1" catalog={MOCK_CATALOG} />);
    emit((cb) =>
      cb.onSnapshot(
        makeRun([makeStep(0, "trigger.schedule", "failed")]),
      ),
    );
    // Failed steps auto-expand their detail pane.
    const detail = screen.getByTestId("run-step-0-detail");
    expect(within(detail).getByText("boom")).toBeInTheDocument();
  });
});

describe("RunView — approval banner", () => {
  it("shows Approve / Reject buttons on approval_requested and dismisses on click", async () => {
    render(<RunView runId="run-1" catalog={MOCK_CATALOG} />);
    emit((cb) =>
      cb.onSnapshot(
        makeRun([
          makeStep(0, "trigger.schedule", "succeeded"),
          makeStep(3, "action.place_order", "awaiting_approval"),
        ]),
      ),
    );

    const approval: Approval = {
      id: "ap-1",
      run_id: "run-1",
      step_index: 3,
      summary: "BUY 10 RELIANCE at market",
      requested_at: "2026-05-02T10:00:00Z",
      expires_at: "2026-05-02T10:15:00Z",
      decision: null,
      decided_at: null,
    };
    emit((cb) =>
      cb.onApprovalRequested({
        type: "approval_requested",
        run_id: "run-1",
        approval,
      }),
    );

    const banner = await screen.findByTestId("approval-banner");
    expect(within(banner).getByText(/BUY 10 RELIANCE/)).toBeInTheDocument();

    const approve = within(banner).getByRole("button", { name: /^approve$/i });
    await userEvent.click(approve);

    await waitFor(() =>
      expect(screen.queryByTestId("approval-banner")).toBeNull(),
    );
  });

  it("dismisses the banner when Reject is clicked", async () => {
    render(<RunView runId="run-1" catalog={MOCK_CATALOG} />);
    emit((cb) =>
      cb.onSnapshot(
        makeRun([makeStep(3, "action.place_order", "awaiting_approval")]),
      ),
    );
    const approval: Approval = {
      id: "ap-1",
      run_id: "run-1",
      step_index: 3,
      summary: "Test summary",
      requested_at: "2026-05-02T10:00:00Z",
      expires_at: "2026-05-02T10:15:00Z",
      decision: null,
      decided_at: null,
    };
    emit((cb) =>
      cb.onApprovalRequested({
        type: "approval_requested",
        run_id: "run-1",
        approval,
      }),
    );
    const banner = await screen.findByTestId("approval-banner");
    await userEvent.click(within(banner).getByRole("button", { name: /^reject$/i }));
    await waitFor(() =>
      expect(screen.queryByTestId("approval-banner")).toBeNull(),
    );
  });
});

describe("RunView — reconnecting indicator", () => {
  it("surfaces the reconnecting indicator when the WS drops", () => {
    render(<RunView runId="run-1" catalog={MOCK_CATALOG} />);
    emit((cb) =>
      cb.onSnapshot(
        makeRun([makeStep(0, "trigger.schedule", "running")]),
      ),
    );
    expect(screen.queryByTestId("reconnecting-indicator")).toBeNull();

    emit((cb) => cb.onConnectionStateChange("reconnecting"));

    expect(screen.getByTestId("reconnecting-indicator")).toBeInTheDocument();

    emit((cb) => cb.onConnectionStateChange("open"));

    expect(screen.queryByTestId("reconnecting-indicator")).toBeNull();
  });

  it("renders a loading skeleton until the first snapshot arrives", () => {
    render(<RunView runId="run-1" catalog={MOCK_CATALOG} />);
    expect(screen.getByTestId("run-view-skeleton")).toBeInTheDocument();
    expect(lastRunId).toBe("run-1");
  });
});
