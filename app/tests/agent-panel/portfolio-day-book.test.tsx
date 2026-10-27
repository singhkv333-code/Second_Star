/**
 * Tests for PortfolioTab's Day book — the positions plays open outside the
 * delivery book. The section shows only with the plays build flag on, so the
 * flag is forced on here (portfolio-tab.test.tsx runs with it off).
 *
 * Covers: open rows and today's closed rows (earlier closed rows left out),
 * derivative naming and currencies, Close calling the server and refetching,
 * and the empty state.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";

vi.mock("@/lib/launch", async (orig) => {
  const real = await orig<typeof import("@/lib/launch")>();
  return { ...real, LAUNCH: { ...real.LAUNCH, plays: true } };
});

import { PortfolioTab } from "@/components/agent-panel/PortfolioTab";
import * as api from "@/lib/api";
import type { DayBook, DayPosition } from "@/lib/api";
import * as portfolioApi from "@/lib/portfolioApi";

const now = Math.floor(Date.now() / 1000);

function pos(over: Partial<DayPosition>): DayPosition {
  return {
    id: 1, play_id: 7, play: "NIFTY 9:20 straddle", symbol: "RELIANCE", venue: "NSE", kind: "equity",
    underlying: "", strike: null, opt: "", side: "long", qty: 10, product: "MIS",
    entry_px: 100, entry_ts: now - 600, mark_px: 101, stale: 0, stop_px: null, target_px: null,
    state: "open", exit_px: null, exit_ts: null, exit_why: "", pnl: null, open_pnl: 10,
    leverage: 1, funding: null, ...over,
  };
}

const BOOK: DayBook = {
  day_pnl: -250.5,
  estimates: "costs and margin are estimates",
  positions: [
    pos({ id: 11, symbol: "NIFTY26OCT22750CE", venue: "NFO", kind: "option", underlying: "NIFTY",
          strike: 22750, opt: "CE", side: "short", qty: 65, entry_px: 120, mark_px: 131, stale: 1,
          stop_px: 150, open_pnl: -715 }),
    pos({ id: 12, symbol: "BTCUSDT", venue: "HL", kind: "perp", underlying: "BTC", product: "PERP",
          leverage: 3, entry_px: 64000, mark_px: 64100, open_pnl: 2490 }),
    pos({ id: 13, state: "closed", exit_px: 104, exit_ts: now - 60, exit_why: "target", pnl: 38.2,
          open_pnl: undefined }),
    // closed long before today: left to the plays' Review
    pos({ id: 14, symbol: "TCS", state: "closed", exit_px: 3300, exit_ts: now - 3 * 86_400,
          exit_why: "stop", pnl: -90, open_pnl: undefined }),
  ],
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(portfolioApi, "getPortfolioPerformance").mockResolvedValue({
    data: { period: "1Y", points: [], starting_value: 0, ending_value: 0, total_return: 0, total_return_pct: 0 },
  });
  vi.spyOn(api, "getOpenOrders").mockResolvedValue({ data: [] });
  vi.spyOn(api, "getCompanyLogos").mockResolvedValue({});
  vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({
    data: { total_value: 0, invested_value: 0, total_pnl: 0, total_pnl_pct: 0, day_pnl: 0, num_holdings: 0 },
  });
  vi.spyOn(api, "getPortfolioHoldings").mockResolvedValue({ data: [] });
});

describe("PortfolioTab day book", () => {
  it("shows open and today's closed positions, named and priced in their own terms", async () => {
    vi.spyOn(api, "getDayBook").mockResolvedValue({ data: BOOK });
    render(<PortfolioTab />);
    const table = await screen.findByTestId("day-book-table");
    const t = within(table);
    expect(t.getByText("NIFTY 22,750 CE")).toBeInTheDocument();
    expect(t.getByText("SHORT")).toBeInTheDocument();
    expect(t.getByText("last close")).toBeInTheDocument();
    expect(t.getByText("stop ₹150")).toBeInTheDocument();
    expect(t.getByText("BTCUSDT 3×")).toBeInTheDocument();
    expect(t.getByText("$64,000")).toBeInTheDocument();
    expect(t.getByText("target")).toBeInTheDocument();
    expect(t.queryByText("TCS")).toBeNull();
    // open rows first, and only they can be closed
    const rows = t.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[2]!).queryByRole("button", { name: /close/i })).toBeNull();
    expect(within(screen.getByTestId("day-book")).getAllByText("−₹250.5")[0]).toBeInTheDocument();
  });

  it("closes a position on the server and reads the book again", async () => {
    const read = vi.spyOn(api, "getDayBook").mockResolvedValue({ data: BOOK });
    const close = vi.spyOn(api, "closeDayPosition").mockResolvedValue({ data: {} });
    render(<PortfolioTab />);
    const table = await screen.findByTestId("day-book-table");
    fireEvent.click(within(table).getByRole("button", { name: "Close short NIFTY 22,750 CE" }));
    await waitFor(() => expect(close).toHaveBeenCalledWith(11));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  });

  it("says plainly when there is nothing in it", async () => {
    vi.spyOn(api, "getDayBook").mockResolvedValue({ data: { ...BOOK, positions: [] } });
    render(<PortfolioTab />);
    expect(await screen.findByTestId("day-book-empty")).toHaveTextContent(
      "No intraday, short or derivative positions today",
    );
  });
});
