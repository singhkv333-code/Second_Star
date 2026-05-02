/**
 * Tests for AppShell — top-level navigation + tab switching.
 *
 * Covers tab rendering, default tab, click-to-switch behavior,
 * URL hash sync, and the chat demo surface. We don't deep-test
 * the individual tabs (each has its own test file); we just verify
 * AppShell mounts the right one for the active tab.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppShell } from "@/components/AppShell";
import * as api from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
  // Each test starts fresh on the default tab — clear the URL hash.
  if (typeof window !== "undefined") {
    window.history.replaceState(null, "", "#");
  }
  // Stub fetches so children don't hit the network.
  vi.spyOn(api, "listWorkflows").mockResolvedValue({
    data: { items: [], next_cursor: null },
  });
  vi.spyOn(api, "getScheduledRuns").mockResolvedValue({
    data: { items: [] },
  });
  vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({
    data: {
      total_value: 0, invested_value: 0, total_pnl: 0,
      total_pnl_pct: 0, day_pnl: 0, num_holdings: 0,
    },
  });
  vi.spyOn(api, "getPortfolioHoldings").mockResolvedValue({ data: [] });
  vi.spyOn(api, "proposeWorkflow").mockResolvedValue({
    data: {
      name: "Test Draft",
      description: null,
      steps: [],
      rationale: null,
      warnings: [],
    },
  });
});

describe("AppShell", () => {
  it("renders the four-tab strip", () => {
    render(<AppShell />);
    const strip = screen.getByTestId("tab-strip");
    expect(strip).toBeInTheDocument();
    expect(screen.getByTestId("tab-chat")).toBeInTheDocument();
    expect(screen.getByTestId("tab-agents")).toBeInTheDocument();
    expect(screen.getByTestId("tab-calendar")).toBeInTheDocument();
    expect(screen.getByTestId("tab-portfolio")).toBeInTheDocument();
  });

  it("defaults to the Agents tab", async () => {
    render(<AppShell />);
    expect(screen.getByTestId("tab-agents")).toHaveAttribute(
      "aria-current", "page",
    );
    await waitFor(() =>
      expect(screen.getByTestId("agents-tab")).toBeInTheDocument(),
    );
  });

  it("clicking a tab switches the active panel + updates URL hash", async () => {
    render(<AppShell />);
    fireEvent.click(screen.getByTestId("tab-portfolio"));
    expect(screen.getByTestId("tab-portfolio")).toHaveAttribute(
      "aria-current", "page",
    );
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-tab")).toBeInTheDocument(),
    );
    expect(window.location.hash).toBe("#portfolio");
  });

  it("Chat tab renders the demo surface with a textarea", () => {
    render(<AppShell />);
    fireEvent.click(screen.getByTestId("tab-chat"));
    expect(screen.getByTestId("chat-demo")).toBeInTheDocument();
    expect(screen.getByTestId("chat-textarea")).toBeInTheDocument();
    expect(screen.getByTestId("chat-submit-btn")).toBeInTheDocument();
  });

  it("Calendar tab mounts when its tab is active", async () => {
    render(<AppShell />);
    fireEvent.click(screen.getByTestId("tab-calendar"));
    await waitFor(() =>
      expect(screen.getByTestId("calendar-tab")).toBeInTheDocument(),
    );
  });

  it("respects an initial URL hash on mount", async () => {
    window.history.replaceState(null, "", "#calendar");
    render(<AppShell />);
    await waitFor(() =>
      expect(screen.getByTestId("calendar-tab")).toBeInTheDocument(),
    );
  });

  it("ignores an unknown URL hash and falls back to default", () => {
    window.history.replaceState(null, "", "#nonsense");
    render(<AppShell />);
    expect(screen.getByTestId("tab-agents")).toHaveAttribute(
      "aria-current", "page",
    );
  });

  // ── #40 metric strip tests ──────────────────────────────────────────

  it("shows metric strip loading skeleton initially", () => {
    // getPortfolioSummary won't resolve synchronously
    vi.spyOn(api, "getPortfolioSummary").mockReturnValue(new Promise(() => {}));
    render(<AppShell />);
    expect(screen.getByTestId("metric-strip-loading")).toBeInTheDocument();
  });

  it("shows metric strip data when portfolio summary resolves", async () => {
    vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({
      data: {
        total_value: 500000,
        invested_value: 400000,
        total_pnl: 100000,
        total_pnl_pct: 25,
        day_pnl: 5000,
        num_holdings: 3,
      },
    });
    render(<AppShell />);
    await waitFor(() =>
      expect(screen.getByTestId("metric-strip")).toBeInTheDocument(),
    );
  });

  it("hides metric strip on portfolio error (does not block tabs)", async () => {
    vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({
      error: { code: "internal_error", message: "Service unavailable" },
    });
    render(<AppShell />);
    await waitFor(() =>
      expect(screen.queryByTestId("metric-strip")).not.toBeInTheDocument(),
    );
    // Tabs still work
    expect(screen.getByTestId("tab-strip")).toBeInTheDocument();
  });

  // ── #41 theme toggle tests ──────────────────────────────────────────

  it("renders the theme toggle button", () => {
    render(<AppShell />);
    expect(screen.getByTestId("theme-toggle")).toBeInTheDocument();
  });

  it("clicking theme toggle switches aria-label", () => {
    render(<AppShell />);
    const btn = screen.getByTestId("theme-toggle");
    // Default is light (matchMedia stub returns false for dark)
    expect(btn).toHaveAttribute("aria-label", "Switch to dark mode");
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-label", "Switch to light mode");
  });
});
