/**
 * Tests for AppShell — left sidebar navigation + tab switching.
 *
 * Covers sidebar rendering, default tab (chat), click-to-switch behavior,
 * URL hash sync, and the chat demo surface. We don't deep-test the individual
 * tabs (each has its own test file); we just verify AppShell mounts the right
 * content for the active tab.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppShell } from "@/components/AppShell";
import * as api from "@/lib/api";

// AppShell calls useRouter()/usePathname() at mount; jsdom has no Next app
// router, so stub next/navigation to a no-op router.
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

beforeEach(() => {
  vi.restoreAllMocks();
  // Start fresh on the default tab — clear the URL hash.
  if (typeof window !== "undefined") {
    window.history.replaceState(null, "", "#");
  }
  // Stub fetches so children don't hit the network.
  vi.spyOn(api, "listWorkflows").mockResolvedValue({
    data: { items: [], next_cursor: null },
  });
  vi.spyOn(api, "listRuns").mockResolvedValue({
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
  // Stub new market / me endpoints used by DashboardTab + ActiveAgentsRail
  vi.spyOn(api, "getMarketIndices").mockResolvedValue({
    data: { items: [] },
  });
  vi.spyOn(api, "getMe").mockResolvedValue({
    data: { id: "u1", email: "demo@example.com", full_name: "Demo" },
  });
  // Conversations list (sidebar) + trading-mode probe — relative-URL fetches
  // jsdom can't resolve, so stub them.
  vi.spyOn(api, "listConversations").mockResolvedValue({
    data: { items: [], next_cursor: null },
  });
  vi.spyOn(api, "getAccountMode").mockResolvedValue({
    data: { mode: "paper" },
  });
});

describe("AppShell", () => {
  it("renders the sidebar navigation", () => {
    render(<AppShell />);
    const nav = screen.getByTestId("sidebar-nav");
    expect(nav).toBeInTheDocument();
    expect(screen.getByTestId("nav-chat")).toBeInTheDocument();
    expect(screen.getByTestId("nav-agents")).toBeInTheDocument();
    expect(screen.getByTestId("nav-calendar")).toBeInTheDocument();
    expect(screen.getByTestId("nav-portfolio")).toBeInTheDocument();
    // The old Dashboard tab is now the Chat tab.
    expect(screen.queryByTestId("nav-dashboard")).toBeNull();
  });

  it("defaults to the Chat tab", async () => {
    render(<AppShell />);
    expect(screen.getByTestId("nav-chat")).toHaveAttribute(
      "aria-current", "page",
    );
    await waitFor(() =>
      expect(screen.getByTestId("dashboard-tab")).toBeInTheDocument(),
    );
  });

  it("clicking a nav item switches the active panel + updates URL hash", async () => {
    render(<AppShell />);
    fireEvent.click(screen.getByTestId("nav-portfolio"));
    expect(screen.getByTestId("nav-portfolio")).toHaveAttribute(
      "aria-current", "page",
    );
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-tab")).toBeInTheDocument(),
    );
    expect(window.location.hash).toBe("#portfolio");
  });

  it("Chat tab hosts the chat composer + dashboard intro", () => {
    render(<AppShell />);
    // Chat is the default tab, so the chat surface is already mounted.
    expect(screen.getByTestId("dashboard-tab")).toBeInTheDocument();
    expect(screen.getByTestId("chat-demo")).toBeInTheDocument();
    expect(screen.getByTestId("chat-textarea")).toBeInTheDocument();
    expect(screen.getByTestId("chat-submit-btn")).toBeInTheDocument();
    // Mode pills (Automation / Agent / Backtest) live below the composer.
    expect(screen.getByTestId("mode-automation")).toBeInTheDocument();
    expect(screen.getByTestId("mode-agent")).toBeInTheDocument();
    expect(screen.getByTestId("mode-backtest")).toBeInTheDocument();
  });

  it("Portfolio nav item mounts the portfolio tab", async () => {
    render(<AppShell />);
    fireEvent.click(screen.getByTestId("nav-portfolio"));
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-tab")).toBeInTheDocument(),
    );
  });

  it("respects an initial URL hash on mount", async () => {
    window.history.replaceState(null, "", "#views");
    render(<AppShell />);
    await waitFor(() =>
      expect(screen.getByTestId("views-tab")).toBeInTheDocument(),
    );
  });

  it("ignores an unknown URL hash and falls back to chat", async () => {
    window.history.replaceState(null, "", "#nonsense");
    render(<AppShell />);
    expect(screen.getByTestId("nav-chat")).toHaveAttribute(
      "aria-current", "page",
    );
    await waitFor(() =>
      expect(screen.getByTestId("dashboard-tab")).toBeInTheDocument(),
    );
  });

  // ── Metric strip tests ──────────────────────────────────────────────

  it("shows metric strip loading skeleton initially", () => {
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

  it("hides metric strip on portfolio error (does not block navigation)", async () => {
    vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({
      error: { code: "internal_error", message: "Service unavailable" },
    });
    render(<AppShell />);
    await waitFor(() =>
      expect(screen.queryByTestId("metric-strip")).not.toBeInTheDocument(),
    );
    // Navigation still renders
    expect(screen.getByTestId("sidebar-nav")).toBeInTheDocument();
  });

  // ── Theme toggle tests ──────────────────────────────────────────────

  it("renders the theme toggle button", () => {
    render(<AppShell />);
    expect(screen.getByTestId("theme-toggle")).toBeInTheDocument();
  });

  it("clicking theme toggle switches aria-label", () => {
    render(<AppShell />);
    const btn = screen.getByTestId("theme-toggle");
    expect(btn).toHaveAttribute("aria-label", "Switch to dark mode");
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-label", "Switch to light mode");
  });
});
