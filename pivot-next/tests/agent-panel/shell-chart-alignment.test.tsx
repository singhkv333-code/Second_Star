/**
 * One top bar per surface, and furniture that does not move between them.
 *
 * The chart tab shows a SINGLE top bar, and it is the chart's own — symbol,
 * interval, indicators, undo/redo — standing in for the shell's TopHeader
 * rather than stacking beneath it. That is why AppShell suppresses TopHeader
 * on this one tab.
 *
 * The cost of that substitution is alignment. The chart's bar lives inside
 * the iframe, which begins to the RIGHT of the nav rail, so on the chart tab
 * the rail has no bar above it: its icons rode a full bar-height higher than
 * on Home, Chat or Portfolio, and the whole left column jumped when you
 * opened a chart. The rail therefore pads itself down by one bar on exactly
 * that surface (`data-below-standin-header`, styled in globals.css).
 *
 * These tests pin the contract: one bar, and the flag set on precisely the
 * surfaces that have no shell bar of their own.
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
const offset = () => rail().getAttribute("data-below-standin-header");

describe("one top bar, aligned furniture", () => {
  it("shows the shell's bar on a normal surface, with the rail un-offset", () => {
    const { container } = render(<AppShell />);
    expect(container.querySelector(".top-header")).not.toBeNull();
    // A bar of our own is above the rail already; nothing to compensate for.
    expect(offset()).toBe("false");
  });

  it("hands the top row to the chart's own bar, and offsets the rail to match", async () => {
    const { container } = render(<AppShell />);

    fireEvent.click(screen.getByTestId("nav-chart"));
    await waitFor(() =>
      expect(screen.getByTestId("nav-chart")).toHaveAttribute("aria-current", "page"),
    );

    // ONE bar: the shell's steps aside so the chart's is not a second row.
    await waitFor(() => expect(container.querySelector(".top-header")).toBeNull());
    // ...and the rail drops by one bar so its icons keep the same baseline.
    expect(offset()).toBe("true");
  });

  it("lets the chart frame span the full width, with the rail overlaid on top", async () => {
    const { container } = render(<AppShell />);
    const row = () => container.querySelector(".shell-row--chart");

    // Side-by-side on a normal surface: the rail takes a column of its own.
    expect(row()).toBeNull();

    fireEvent.click(screen.getByTestId("nav-chart"));
    await waitFor(() => expect(row()).not.toBeNull());

    // On the chart the row is flagged for the overlay layout, and the rail is
    // still a child of it (positioned over the frame, not removed).
    expect(row()!.contains(rail())).toBe(true);
  });

  it("tells the chart how wide the overlaying rail is, once it is ready", async () => {
    const { container } = render(<AppShell />);
    fireEvent.click(screen.getByTestId("nav-chart"));
    const frame = await waitFor(() => {
      const f = container.querySelector('iframe[title="Chart"]');
      expect(f).not.toBeNull();
      return f as HTMLIFrameElement;
    });

    // The chart cannot measure a rail that lives in the parent document, so
    // the shell states it; without it the chart's tools sit under the rail.
    // Capture what the frame is sent by standing in for its contentWindow.
    const sent: Array<Record<string, unknown>> = [];
    Object.defineProperty(frame, "contentWindow", {
      configurable: true,
      value: { postMessage: (m: Record<string, unknown>) => { sent.push(m); } },
    });

    // The chart announces itself; that is what unblocks the shell's messages.
    window.dispatchEvent(new MessageEvent("message", {
      data: { type: "chart:ready", symbol: "RELIANCE" },
      origin: window.location.origin,
      source: frame.contentWindow as unknown as Window,
    }));

    await waitFor(() =>
      expect(sent.some((m) => m.type === "pivot:railpad")).toBe(true),
    );
    const pad = sent.find((m) => m.type === "pivot:railpad")!;

    // The width is whatever the rail actually reserves, and below lg it
    // reserves NOTHING: there the rail is a drawer that floats over the page
    // on demand, so insetting the chart by 48px would leave a dead strip
    // beside a rail that is not there. jsdom reports no media-query match, so
    // this run exercises that narrow case and must report 0. On desktop the
    // same expression yields SIDEBAR_RAIL_W.
    expect(pad.width).toBe(0);
  });

  it("restores the shell bar and drops the offset when leaving the chart", async () => {
    const { container } = render(<AppShell />);

    fireEvent.click(screen.getByTestId("nav-chart"));
    await waitFor(() => expect(offset()).toBe("true"));

    fireEvent.click(screen.getByTestId("nav-portfolio"));
    await waitFor(() =>
      expect(container.querySelector(".top-header")).not.toBeNull(),
    );
    // The compensation is scoped to the chart, not left switched on.
    expect(offset()).toBe("false");
  });
});
