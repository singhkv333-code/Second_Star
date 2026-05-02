/**
 * Tests for AppShell — top-level navigation + tab switching.
 *
 * Covers tab rendering, default tab, click-to-switch behavior,
 * URL hash sync, and the chat placeholder copy. We don't deep-test
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

  it("Chat tab renders the placeholder explainer", () => {
    render(<AppShell />);
    fireEvent.click(screen.getByTestId("tab-chat"));
    expect(screen.getByTestId("chat-placeholder")).toBeInTheDocument();
    expect(
      screen.getByText(/legacy frontend/i),
    ).toBeInTheDocument();
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
});
