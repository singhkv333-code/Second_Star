import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CalendarTab } from "@/components/CalendarTab";
import * as api from "@/lib/api";
import type { ScheduledRunsResponse } from "@/lib/api";

const MOCK_ITEMS: ScheduledRunsResponse = {
  items: [
    {
      workflow_id: "wf-1",
      workflow_name: "RELIANCE 3:55 PM buy",
      trigger_type: "trigger.schedule",
      fire_time: new Date(Date.now() + 86400000).toISOString(), // tomorrow
      fire_time_local: "3:55 PM IST",
    },
    {
      workflow_id: "wf-2",
      workflow_name: "Morning check",
      trigger_type: "trigger.schedule",
      fire_time: new Date(Date.now() + 2 * 86400000).toISOString(), // day after tomorrow
      fire_time_local: "9:30 AM IST",
    },
  ],
};

describe("CalendarTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading state initially", () => {
    vi.spyOn(api, "getScheduledRuns").mockReturnValue(new Promise(() => {}));
    render(<CalendarTab onOpenWorkflow={vi.fn()} />);
    expect(screen.getByTestId("calendar-loading")).toBeInTheDocument();
  });

  it("renders month view by default with day cells", async () => {
    vi.spyOn(api, "getScheduledRuns").mockResolvedValue({ data: MOCK_ITEMS });
    render(<CalendarTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("month-view")).toBeInTheDocument(),
    );
  });

  it("shows error state with message", async () => {
    vi.spyOn(api, "getScheduledRuns").mockResolvedValue({
      error: { code: "unauthenticated", message: "Session expired" },
    });
    render(<CalendarTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("calendar-error")).toBeInTheDocument(),
    );
    expect(screen.getByText("Session expired")).toBeInTheDocument();
  });

  it("switches to agenda view", async () => {
    vi.spyOn(api, "getScheduledRuns").mockResolvedValue({ data: MOCK_ITEMS });
    render(<CalendarTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("month-view")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("view-agenda"));
    await waitFor(() =>
      expect(screen.getByTestId("agenda-view")).toBeInTheDocument(),
    );
  });

  it("shows empty state in agenda view when no items", async () => {
    vi.spyOn(api, "getScheduledRuns").mockResolvedValue({
      data: { items: [] },
    });
    render(<CalendarTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("month-view")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("view-agenda"));
    await waitFor(() =>
      expect(screen.getByTestId("calendar-empty")).toBeInTheDocument(),
    );
  });

  it("navigates to previous month", async () => {
    vi.spyOn(api, "getScheduledRuns").mockResolvedValue({ data: { items: [] } });
    render(<CalendarTab onOpenWorkflow={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("month-view")).toBeInTheDocument(),
    );
    const prevBtn = screen.getByTestId("prev-month");
    expect(prevBtn).toBeInTheDocument();
    fireEvent.click(prevBtn);
    // getScheduledRuns should be called again with the new range
    await waitFor(() => expect(api.getScheduledRuns).toHaveBeenCalledTimes(2));
  });

  it("calls onOpenWorkflow when a scheduled run row is clicked in agenda view", async () => {
    const onOpenWorkflow = vi.fn();
    vi.spyOn(api, "getScheduledRuns").mockResolvedValue({ data: MOCK_ITEMS });
    render(<CalendarTab onOpenWorkflow={onOpenWorkflow} />);
    fireEvent.click(screen.getByTestId("view-agenda"));
    await waitFor(() =>
      expect(screen.getByTestId("agenda-view")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("scheduled-run-wf-1"));
    expect(onOpenWorkflow).toHaveBeenCalledWith("wf-1");
  });
});
