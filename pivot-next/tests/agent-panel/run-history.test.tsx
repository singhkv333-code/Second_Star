import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { RunHistory } from "@/components/agent-panel/RunHistory";
import * as api from "@/lib/api";
import type { RunSummary, Paginated } from "@/lib/types";

const makeRun = (
  id: string,
  status: RunSummary["status"] = "succeeded",
): RunSummary => ({
  id,
  workflow_id: "wf-1",
  workflow_version: 1,
  triggered_by: "manual",
  started_at: "2026-05-01T10:00:00Z",
  finished_at: "2026-05-01T10:00:12Z",
  status,
  halt_reason: null,
  error_message: null,
  step_count: 5,
});

const PAGE_1: Paginated<RunSummary> = {
  items: [makeRun("run-aaa"), makeRun("run-bbb"), makeRun("run-ccc")],
  next_cursor: "cursor-2",
};

const PAGE_2: Paginated<RunSummary> = {
  items: [makeRun("run-ddd")],
  next_cursor: null,
};

const EMPTY_PAGE: Paginated<RunSummary> = {
  items: [],
  next_cursor: null,
};

describe("RunHistory", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading skeletons initially", () => {
    vi.spyOn(api, "listRuns").mockReturnValue(new Promise(() => {}));
    render(<RunHistory workflowId="wf-1" onSelectRun={vi.fn()} />);
    expect(screen.getByTestId("run-history-loading")).toBeInTheDocument();
  });

  it("renders a list of runs after loading", async () => {
    vi.spyOn(api, "listRuns").mockResolvedValue({ data: PAGE_1 });
    render(<RunHistory workflowId="wf-1" onSelectRun={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("run-history")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("run-row-run-aaa")).toBeInTheDocument();
    expect(screen.getByTestId("run-row-run-bbb")).toBeInTheDocument();
  });

  it("shows empty state when no runs", async () => {
    vi.spyOn(api, "listRuns").mockResolvedValue({ data: EMPTY_PAGE });
    render(<RunHistory workflowId="wf-1" onSelectRun={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("run-history-empty")).toBeInTheDocument(),
    );
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
  });

  it("shows error state when API fails", async () => {
    vi.spyOn(api, "listRuns").mockResolvedValue({
      error: { code: "internal_error", message: "DB unavailable" },
    });
    render(<RunHistory workflowId="wf-1" onSelectRun={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("run-history-error")).toBeInTheDocument(),
    );
    expect(screen.getByText("DB unavailable")).toBeInTheDocument();
  });

  it("calls onSelectRun with the run id when a row is clicked", async () => {
    const onSelectRun = vi.fn();
    vi.spyOn(api, "listRuns").mockResolvedValue({ data: PAGE_1 });
    render(<RunHistory workflowId="wf-1" onSelectRun={onSelectRun} />);
    await waitFor(() =>
      expect(screen.getByTestId("run-row-run-aaa")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("run-row-run-aaa"));
    expect(onSelectRun).toHaveBeenCalledWith("run-aaa");
  });

  it("shows 'Load more' button when next_cursor is present", async () => {
    vi.spyOn(api, "listRuns").mockResolvedValue({ data: PAGE_1 });
    render(<RunHistory workflowId="wf-1" onSelectRun={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("load-more-button")).toBeInTheDocument(),
    );
  });

  it("loads next page when 'Load more' is clicked", async () => {
    const spy = vi
      .spyOn(api, "listRuns")
      .mockResolvedValueOnce({ data: PAGE_1 })
      .mockResolvedValueOnce({ data: PAGE_2 });
    render(<RunHistory workflowId="wf-1" onSelectRun={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("load-more-button")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("load-more-button"));
    await waitFor(() =>
      expect(screen.getByTestId("run-row-run-ddd")).toBeInTheDocument(),
    );
    expect(spy).toHaveBeenCalledTimes(2);
    // Second call should pass the cursor
    expect(spy).toHaveBeenNthCalledWith(2, "wf-1", expect.objectContaining({ cursor: "cursor-2" }));
  });
});
