/**
 * StockDetailPage — Phase 3 smoke tests.
 * Covers: loading state, quote header renders, chart renders, error state,
 * side panel tabs, news pane, related agents pane.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { StockDetailPage } from "@/components/StockDetailPage";
import * as api from "@/lib/api";
import type { StockQuote, SparklineResponse } from "@/lib/api";

const MOCK_QUOTE: StockQuote = {
  symbol: "RELIANCE",
  name: "Reliance Industries Ltd",
  exchange: "NSE",
  ltp: 2854.5,
  change: 38.2,
  change_pct: 1.36,
  open: 2820.0,
  high: 2870.0,
  low: 2815.0,
  prev_close: 2816.3,
  w52_high: 3024.9,
  w52_low: 2180.0,
  volume: 4389201,
  market_cap: 19340000000000,
  pe_ratio: 26.4,
  sector: "Energy",
};

const MOCK_SPARKLINE: SparklineResponse = {
  symbol: "RELIANCE",
  range: "1M",
  interval: "1d",
  points: [
    { t: "2024-11-01", v: 2780 },
    { t: "2024-11-30", v: 2854.5 },
  ],
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getStockAutomations").mockResolvedValue({
    data: { items: [] },
  });
  vi.spyOn(api, "getNews").mockResolvedValue({ data: { items: [] } });
  vi.spyOn(api, "listWorkflows").mockResolvedValue({
    data: { items: [], next_cursor: null },
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("StockDetailPage", () => {
  it("shows loading skeleton before data resolves", () => {
    vi.spyOn(api, "getStockQuote").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "getSparkline").mockReturnValue(new Promise(() => {}));
    render(<StockDetailPage symbol="RELIANCE" />);
    expect(screen.getByTestId("quote-header-skeleton")).toBeInTheDocument();
  });

  it("renders quote header on happy path", async () => {
    vi.spyOn(api, "getStockQuote").mockResolvedValue({ data: MOCK_QUOTE });
    vi.spyOn(api, "getSparkline").mockResolvedValue({ data: MOCK_SPARKLINE });
    render(<StockDetailPage symbol="RELIANCE" />);
    await waitFor(() =>
      expect(screen.getByTestId("quote-header")).toBeInTheDocument(),
    );
    expect(screen.getByText("RELIANCE")).toBeInTheDocument();
    expect(screen.getByText("Reliance Industries Ltd")).toBeInTheDocument();
  });

  it("renders main chart card", async () => {
    vi.spyOn(api, "getStockQuote").mockResolvedValue({ data: MOCK_QUOTE });
    vi.spyOn(api, "getSparkline").mockResolvedValue({ data: MOCK_SPARKLINE });
    render(<StockDetailPage symbol="RELIANCE" />);
    await waitFor(() =>
      expect(screen.getByTestId("main-chart")).toBeInTheDocument(),
    );
  });

  it("renders side panel with tabs", async () => {
    vi.spyOn(api, "getStockQuote").mockResolvedValue({ data: MOCK_QUOTE });
    vi.spyOn(api, "getSparkline").mockResolvedValue({ data: MOCK_SPARKLINE });
    render(<StockDetailPage symbol="RELIANCE" />);
    await waitFor(() =>
      expect(screen.getByTestId("side-panel")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("side-tab-fundamentals")).toBeInTheDocument();
    expect(screen.getByTestId("side-tab-news")).toBeInTheDocument();
    expect(screen.getByTestId("side-tab-agents")).toBeInTheDocument();
  });

  it("switching to News tab renders news pane", async () => {
    vi.spyOn(api, "getStockQuote").mockResolvedValue({ data: MOCK_QUOTE });
    vi.spyOn(api, "getSparkline").mockResolvedValue({ data: MOCK_SPARKLINE });
    vi.spyOn(api, "getNews").mockResolvedValue({
      data: {
        items: [
          {
            id: "n1",
            title: "Reliance Q3 results beat estimates",
            source: "Economic Times",
            url: "https://example.com/n1",
            published_at: "2024-11-15T09:00:00Z",
            summary: null,
          },
        ],
      },
    });
    render(<StockDetailPage symbol="RELIANCE" />);
    await waitFor(() =>
      expect(screen.getByTestId("side-panel")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("side-tab-news"));
    await waitFor(() =>
      expect(screen.getByTestId("news-pane")).toBeInTheDocument(),
    );
    expect(screen.getByText("Reliance Q3 results beat estimates")).toBeInTheDocument();
  });

  it("shows quote error when API fails", async () => {
    vi.spyOn(api, "getStockQuote").mockResolvedValue({
      error: { code: "not_found", message: "Symbol not found: BADTICKER" },
    });
    vi.spyOn(api, "getSparkline").mockResolvedValue({ data: MOCK_SPARKLINE });
    render(<StockDetailPage symbol="BADTICKER" />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeInTheDocument(),
    );
    expect(screen.getByText("Symbol not found: BADTICKER")).toBeInTheDocument();
  });

  it("range button changes sparkline fetch", async () => {
    const sparkSpy = vi
      .spyOn(api, "getSparkline")
      .mockResolvedValue({ data: MOCK_SPARKLINE });
    vi.spyOn(api, "getStockQuote").mockResolvedValue({ data: MOCK_QUOTE });
    render(<StockDetailPage symbol="RELIANCE" />);
    await waitFor(() =>
      expect(screen.getByTestId("main-chart")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("range-1Y"));
    await waitFor(() =>
      expect(sparkSpy).toHaveBeenCalledWith("RELIANCE", "1Y"),
    );
  });
});
