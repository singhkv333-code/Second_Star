import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AgentsTab } from "@/components/agent-panel/AgentsTab";
import * as api from "@/lib/api";
import type { WorkflowSummary, Paginated } from "@/lib/types";

const makeWorkflow = (
  id: string,
  status: WorkflowSummary["status"] = "active",
): WorkflowSummary => ({
  id,
  name: `Agent ${id}`,
  description: `Description for ${id}`,
  status,
  version: 1,
  single_instance: true,
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-01T00:00:00Z",
  activated_at: "2026-05-01T01:00:00Z",
  last_run_at: "2026-05-01T02:00:00Z",
  next_run_at: "2026-05-08T15:55:00Z",
});

const MOCK_LIST: Paginated<WorkflowSummary> = {
  items: [makeWorkflow("wf-1"), makeWorkflow("wf-2", "paused")],
  next_cursor: null,
};

describe("AgentsTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading skeletons initially", () => {
    vi.spyOn(api, "listWorkflows").mockReturnValue(new Promise(() => {}));
    render(<AgentsTab onOpenWorkflow={vi.fn()} />);
    expect(screen.getByTestId("agents-loading")).toBeInTheDocument();
  });

  it("renders a list of workflows after loading", async () => {
    vi.spyOn(api, "listWorkflows").mockResolvedValue({ data: MOCK_LIST });
    render(<AgentsTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("agents-list")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("agent-row-wf-1")).toBeInTheDocument();
    expect(screen.getByTestId("agent-row-wf-2")).toBeInTheDocument();
  });

  it("shows empty state when no agents", async () => {
    vi.spyOn(api, "listWorkflows").mockResolvedValue({
      data: { items: [], next_cursor: null },
    });
    render(<AgentsTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("agents-empty")).toBeInTheDocument(),
    );
    expect(screen.getByText("No agents yet")).toBeInTheDocument();
  });

  it("shows error state with message", async () => {
    vi.spyOn(api, "listWorkflows").mockResolvedValue({
      error: { code: "unauthenticated", message: "Token expired" },
    });
    render(<AgentsTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("agents-error")).toBeInTheDocument(),
    );
    expect(screen.getByText("Token expired")).toBeInTheDocument();
  });

  it("renders filter chips", async () => {
    vi.spyOn(api, "listWorkflows").mockResolvedValue({ data: MOCK_LIST });
    render(<AgentsTab onOpenWorkflow={vi.fn()} />);
    expect(screen.getByTestId("filter-all")).toBeInTheDocument();
    expect(screen.getByTestId("filter-active")).toBeInTheDocument();
    expect(screen.getByTestId("filter-paused")).toBeInTheDocument();
  });

  it("calls onOpenWorkflow after row click fetches the full workflow", async () => {
    const onOpenWorkflow = vi.fn();
    const fullWorkflow = { ...makeWorkflow("wf-1"), steps: [] };
    vi.spyOn(api, "listWorkflows").mockResolvedValue({ data: MOCK_LIST });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.spyOn(api, "getWorkflow").mockResolvedValue({ data: fullWorkflow } as any);
    render(<AgentsTab onOpenWorkflow={onOpenWorkflow} />);
    await waitFor(() =>
      expect(screen.getByTestId("agent-row-wf-1")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("agent-row-wf-1"));
    await waitFor(() => expect(onOpenWorkflow).toHaveBeenCalledWith(fullWorkflow));
  });

  // ── #42 "Create example agent" CTA ──────────────────────────────────

  it("shows 'Create example agent' button in empty state", async () => {
    vi.spyOn(api, "listWorkflows").mockResolvedValue({
      data: { items: [], next_cursor: null },
    });
    render(<AgentsTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("create-example-agent-btn")).toBeInTheDocument(),
    );
  });

  it("clicking 'Create example agent' calls createWorkflow with the demo workflow", async () => {
    vi.spyOn(api, "listWorkflows").mockResolvedValue({
      data: { items: [], next_cursor: null },
    });
    const createdWf = { ...makeWorkflow("new-wf"), steps: [] };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.spyOn(api, "createWorkflow").mockResolvedValue({ data: createdWf } as any);

    render(<AgentsTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("create-example-agent-btn")).toBeInTheDocument(),
    );

    // After clicking, listWorkflows will be called again; return a list now
    vi.spyOn(api, "listWorkflows").mockResolvedValue({
      data: { items: [makeWorkflow("new-wf")], next_cursor: null },
    });

    fireEvent.click(screen.getByTestId("create-example-agent-btn"));

    await waitFor(() =>
      expect(api.createWorkflow).toHaveBeenCalledWith(
        expect.objectContaining({ name: "RELIANCE 3:55 PM buy" }),
      ),
    );
  });

  it("shows seed error when createWorkflow fails", async () => {
    vi.spyOn(api, "listWorkflows").mockResolvedValue({
      data: { items: [], next_cursor: null },
    });
    vi.spyOn(api, "createWorkflow").mockResolvedValue({
      error: { code: "validation_error", message: "Step config invalid" },
    });

    render(<AgentsTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("create-example-agent-btn")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("create-example-agent-btn"));

    await waitFor(() =>
      expect(screen.getByTestId("seed-error")).toBeInTheDocument(),
    );
    expect(screen.getByText("Step config invalid")).toBeInTheDocument();
  });
});
