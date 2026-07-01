/**
 * Tests for PortfolioTab — read-only holdings + P&L view.
 *
 * Covers loading, error-with-retry, empty-portfolio empty state,
 * happy path with metric strip + sortable holdings table, default
 * sort (value desc), and clicking a column header to flip sort.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { PortfolioTab } from "@/components/agent-panel/PortfolioTab";
import * as api from "@/lib/api";
import type { Holding, PortfolioSummary, PortfolioPerformanceResponse } from "@/lib/api";

const MOCK_PERF: PortfolioPerformanceResponse = {
  period: "1Y",
  points: [
    { t: "2024-01-01T00:00:00", v: 100000 },
    { t: "2024-12-31T00:00:00", v: 115000 },
  ],
  starting_value: 100000,
  ending_value: 115000,
  total_return: 15000,
  total_return_pct: 15,
};

const SUMMARY: PortfolioSummary = {
  total_value: 230456,
  invested_value: 200000,
  total_pnl: 30456,
  total_pnl_pct: 15.23,
  day_pnl: 1245,
  num_holdings: 3,
};

const HOLDINGS: Holding[] = [
  {
    tradingsymbol: "INFY", exchange: "NSE",
    quantity: 10, average_price: 1450, last_price: 1523,
    pnl: 730, day_change: 12.5, day_change_percentage: 0.83,
  },
  {
    tradingsymbol: "TCS", exchange: "NSE",
    quantity: 5, average_price: 3200, last_price: 3356,
    pnl: 780, day_change: -8.2, day_change_percentage: -0.24,
  },
  {
    tradingsymbol: "HDFCBANK", exchange: "NSE",
    quantity: 20, average_price: 1580, last_price: 1643,
    pnl: 1260, day_change: 5.4, day_change_percentage: 0.33,
  },
];

beforeEach(() => {
  vi.restoreAllMocks();
  // Performance chart is always stubbed to avoid network calls in tests
  vi.spyOn(api, "getPortfolioPerformance").mockResolvedValue({ data: MOCK_PERF });
  vi.spyOn(api, "getIndexHistory").mockResolvedValue({
    data: { symbol: "NIFTY50", range: "1Y", interval: "1wk", points: [] },
  });
});

describe("PortfolioTab", () => {
  it("shows loading skeleton while data is in flight", () => {
    vi.spyOn(api, "getPortfolioSummary").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "getPortfolioHoldings").mockReturnValue(new Promise(() => {}));
    render(<PortfolioTab />);
    expect(screen.getByTestId("portfolio-loading")).toBeInTheDocument();
  });

  it("renders metric strip + holdings table on happy path", async () => {
    vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({ data: SUMMARY });
    vi.spyOn(api, "getPortfolioHoldings").mockResolvedValue({ data: HOLDINGS });
    render(<PortfolioTab />);

    await waitFor(() =>
      expect(screen.getByTestId("holdings-table")).toBeInTheDocument(),
    );
    // Holdings table
    const table = screen.getByTestId("holdings-table");
    expect(table).toBeInTheDocument();
    expect(screen.getByTestId("holding-INFY")).toBeInTheDocument();
    expect(screen.getByTestId("holding-TCS")).toBeInTheDocument();
    expect(screen.getByTestId("holding-HDFCBANK")).toBeInTheDocument();
    // Performance chart is rendered
    expect(screen.getByTestId("performance-chart")).toBeInTheDocument();
  });

  it("default sort is value desc — HDFCBANK (largest value) first", async () => {
    vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({ data: SUMMARY });
    vi.spyOn(api, "getPortfolioHoldings").mockResolvedValue({ data: HOLDINGS });
    render(<PortfolioTab />);

    await waitFor(() =>
      expect(screen.getByTestId("holdings-table")).toBeInTheDocument(),
    );
    const rows = screen.getAllByRole("row").slice(1); // skip header
    // HDFCBANK value: 20 * 1643 = 32860 (largest)
    // INFY value: 10 * 1523 = 15230
    // TCS value: 5 * 3356 = 16780
    // Order by value desc: HDFCBANK > TCS > INFY
    expect(rows[0]).toHaveTextContent("HDFCBANK");
    expect(rows[1]).toHaveTextContent("TCS");
    expect(rows[2]).toHaveTextContent("INFY");
  });

  it("clicking the Symbol header sorts by symbol asc", async () => {
    vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({ data: SUMMARY });
    vi.spyOn(api, "getPortfolioHoldings").mockResolvedValue({ data: HOLDINGS });
    render(<PortfolioTab />);

    await waitFor(() =>
      expect(screen.getByTestId("holdings-table")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("sort-tradingsymbol"));
    const rows = screen.getAllByRole("row").slice(1);
    // Symbol ASC: HDFCBANK, INFY, TCS
    expect(rows[0]).toHaveTextContent("HDFCBANK");
    expect(rows[1]).toHaveTextContent("INFY");
    expect(rows[2]).toHaveTextContent("TCS");
  });

  it("renders empty state when holdings is []", async () => {
    vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({
      data: { ...SUMMARY, num_holdings: 0 },
    });
    vi.spyOn(api, "getPortfolioHoldings").mockResolvedValue({ data: [] });
    render(<PortfolioTab />);

    await waitFor(() =>
      expect(screen.getByTestId("portfolio-empty")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("holdings-table")).not.toBeInTheDocument();
  });

  it("renders error state with the API message + Retry button", async () => {
    vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({
      error: { code: "internal_error", message: "Kite session expired" },
    });
    vi.spyOn(api, "getPortfolioHoldings").mockResolvedValue({ data: [] });
    render(<PortfolioTab />);

    await waitFor(() =>
      expect(screen.getByTestId("portfolio-error")).toBeInTheDocument(),
    );
    expect(screen.getByText("Kite session expired")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /retry/i }),
    ).toBeInTheDocument();
  });

  it("Retry button re-issues the fetch on click", async () => {
    const sumSpy = vi
      .spyOn(api, "getPortfolioSummary")
      .mockResolvedValueOnce({
        error: { code: "internal_error", message: "transient" },
      })
      .mockResolvedValueOnce({ data: SUMMARY });
    const holdSpy = vi
      .spyOn(api, "getPortfolioHoldings")
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({ data: HOLDINGS });

    render(<PortfolioTab />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-error")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() =>
      expect(screen.getByTestId("holdings-table")).toBeInTheDocument(),
    );
    expect(sumSpy).toHaveBeenCalledTimes(2);
    expect(holdSpy).toHaveBeenCalledTimes(2);
  });
});
