/**
 * The global header remains outside the sidebar/content row on every route.
 * Chart swaps only the header's middle content: its iframe toolbar is lifted
 * into that slot while the shared shell keeps the wordmark, avatar and seam.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppShell } from "@/components/AppShell";
import * as api from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(),
    back: vi.fn(), forward: vi.fn(), refresh: vi.fn(),
  }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(
    new Response(JSON.stringify({ detail: "not mocked" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    }),
  )));
  if (typeof window !== "undefined") window.history.replaceState(null, "", "#");
  vi.spyOn(api, "listWorkflows").mockResolvedValue({ data: { items: [], next_cursor: null } });
  vi.spyOn(api, "listRuns").mockResolvedValue({ data: { items: [], next_cursor: null } });
  vi.spyOn(api, "getScheduledRuns").mockResolvedValue({ data: { items: [] } });
  vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({
    data: { total_value: 0, invested_value: 0, total_pnl: 0, total_pnl_pct: 0, day_pnl: 0, num_holdings: 0 },
  });
  vi.spyOn(api, "getPortfolioHoldings").mockResolvedValue({ data: [] });
  vi.spyOn(api, "getMarketIndices").mockResolvedValue({ data: { items: [] } });
  vi.spyOn(api, "getMe").mockResolvedValue({ data: { id: "u1", email: "d@e.com", full_name: "Demo" } });
  vi.spyOn(api, "listConversations").mockResolvedValue({ data: { items: [], next_cursor: null } });
  vi.spyOn(api, "getAccountMode").mockResolvedValue({ data: { mode: "paper" } });
});

const rail = () => screen.getByTestId("sidebar-nav");

describe("shared chart shell alignment", () => {
  it("shows the shared shell bar on a normal surface", () => {
    const { container } = render(<AppShell />);
    expect(container.querySelector(".top-header")).not.toBeNull();
    expect(container.querySelector(".top-header--chart")).toBeNull();
  });

  it("keeps the shared bar and selects its chart-content variant", async () => {
    const { container } = render(<AppShell />);
    fireEvent.click(screen.getByTestId("nav-chart"));
    await waitFor(() => expect(screen.getByTestId("nav-chart")).toHaveAttribute("aria-current", "page"));

    expect(container.querySelector(".top-header")).toHaveClass("top-header--chart");
    expect(screen.getByTestId("brand-home-link")).toBeInTheDocument();
    expect(screen.getByTestId("account-menu-trigger")).toBeInTheDocument();
    expect(screen.queryByTestId("global-search")).toBeNull();
    expect(screen.queryByTestId("metric-strip")).toBeNull();
  });

  it("keeps the sidebar as a normal sibling below the header", async () => {
    const { container } = render(<AppShell />);
    fireEvent.click(screen.getByTestId("nav-chart"));
    const pane = await waitFor(() => {
      const value = container.querySelector(".chart-shell-pane");
      expect(value).not.toBeNull();
      return value;
    });

    expect(container.querySelector(".shell-row--chart")).toBeNull();
    expect(rail().parentElement).toContainElement(pane as HTMLElement);
  });

  it("does not send the obsolete overlay-rail inset to the chart", async () => {
    const { container } = render(<AppShell />);
    fireEvent.click(screen.getByTestId("nav-chart"));
    const frame = await waitFor(() => {
      const value = container.querySelector('iframe[title="Chart"]');
      expect(value).not.toBeNull();
      return value as HTMLIFrameElement;
    });

    const sent: Array<Record<string, unknown>> = [];
    Object.defineProperty(frame, "contentWindow", {
      configurable: true,
      value: { postMessage: (message: Record<string, unknown>) => sent.push(message) },
    });
    window.dispatchEvent(new MessageEvent("message", {
      data: { type: "chart:ready", symbol: "RELIANCE" },
      origin: window.location.origin,
      source: frame.contentWindow as unknown as Window,
    }));

    await waitFor(() => expect(sent.some((message) => message.type === "pivot:theme")).toBe(true));
    expect(sent.some((message) => message.type === "pivot:railpad")).toBe(false);
  });

  it("restores the default header content when leaving the chart", async () => {
    const { container } = render(<AppShell />);
    fireEvent.click(screen.getByTestId("nav-chart"));
    await waitFor(() => expect(container.querySelector(".top-header--chart")).not.toBeNull());

    fireEvent.click(screen.getByTestId("nav-portfolio"));
    await waitFor(() => expect(container.querySelector(".top-header--chart")).toBeNull());
    expect(screen.getByTestId("global-search")).toBeInTheDocument();
  });
});
