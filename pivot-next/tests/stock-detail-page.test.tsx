import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { StockDetailPage } from "@/components/StockDetailPage";
import * as api from "@/lib/api";
import type { StockQuote } from "@/lib/api";

vi.mock("@/hooks/useLiveQuote", () => ({ useLiveQuote: () => ({ ltp: null, isLive: false }) }));
vi.mock("@/components/chart/StockPriceChart", () => ({ StockPriceChart: () => <div aria-label="Price chart" /> }));
vi.mock("@/components/WatchlistBookmark", () => ({ WatchlistBookmark: () => <button aria-label="Bookmark" /> }));
vi.mock("@/components/stock/DeepSections", () => ({ DeepSections: () => <div>Research data</div> }));
vi.mock("@/components/stock/TechnicalPanel", () => ({ TechnicalPanel: () => <div>Technical Analysis</div> }));
const quote: StockQuote = {
  symbol: "RELIANCE", name: "Reliance Industries Ltd", exchange: "NSE", ltp: 2854.5,
  change: 38.2, change_pct: 1.36, open: 2820, high: 2870, low: 2815,
  prev_close: 2816.3, w52_high: 3024.9, w52_low: 2180, volume: 4389201,
  market_cap: 19340000000000, pe_ratio: 26.4, sector: "Energy", source: "yfinance",
};
beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getStockQuote").mockResolvedValue({ data: quote });
  vi.spyOn(api, "getSparkline").mockResolvedValue({ data: { symbol: "RELIANCE", range: "5Y", interval: "1d", points: [{ t: "2025-01-01", v: 2780 }, { t: "2025-02-01", v: 2854.5 }] } });
  vi.spyOn(api, "getResearchPrices").mockResolvedValue({ error: { code: "unavailable", message: "Unavailable" } });
  vi.spyOn(api, "getOhlc").mockResolvedValue({ data: { symbol: "RELIANCE", range: "5Y", interval: "1d", source: "yfinance", bars: [] } });
  vi.spyOn(api, "getFinancials").mockResolvedValue({ data: { available: false, company: null, latest: {}, history: {}, profile: null, source: "unavailable" } });
  vi.spyOn(api, "getStockQuarters").mockResolvedValue({ error: { code: "unavailable", message: "Unavailable" } });
  vi.spyOn(api, "getStatement").mockResolvedValue({ error: { code: "unavailable", message: "Unavailable" } });
});
describe("Stock research page", () => {
  it("renders quote data and explicitly labels fallback data", async () => {
    render(<StockDetailPage symbol="RELIANCE" />);
    const header = await screen.findByTestId("quote-header");
    expect(within(header).getByRole("heading", { name: quote.name })).toBeInTheDocument();
    expect(screen.getByText("yfinance · end-of-day / delayed")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Company snapshot")).getByText("26.4×")).toBeInTheDocument();
  });
  it("provides working anchors for every main research section", async () => {
    const { container } = render(<StockDetailPage symbol="RELIANCE" />);
    const nav = await screen.findByRole("navigation", { name: "Company research sections" });
    for (const link of within(nav).getAllByRole("link")) {
      expect(container.querySelector(link.getAttribute("href")!)).not.toBeNull();
    }
    expect(screen.getByText("Company Overview")).toBeInTheDocument();
    expect(screen.getByText("Research data")).toBeInTheDocument();
  });
  it("keeps the range control interactive", async () => {
    render(<StockDetailPage symbol="RELIANCE" />);
    await screen.findByTestId("quote-header");
    fireEvent.click(screen.getAllByTestId("range-1W")[0]!);
    await waitFor(() => expect(api.getSparkline).toHaveBeenCalledWith("RELIANCE", "1W"));
  });
  it("shows unavailable values without invented figures", async () => {
    vi.spyOn(api, "getStockQuote").mockResolvedValue({ data: { ...quote, pe_ratio: null, market_cap: null } });
    render(<StockDetailPage symbol="RELIANCE" />);
    const snapshot = await screen.findByLabelText("Company snapshot");
    expect(within(snapshot).getByText("Unavailable")).toBeInTheDocument();
    expect(within(snapshot).getByText("—")).toBeInTheDocument();
  });
  it("does not offer company research or orders for an index", async () => {
    vi.spyOn(api, "getStockQuote").mockResolvedValue({ data: { ...quote, is_index: true } });
    render(<StockDetailPage symbol="RELIANCE" />);
    await screen.findByTestId("quote-header");
    expect(screen.queryByRole("button", { name: "Buy" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Financials" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Company snapshot")).not.toBeInTheDocument();
  });
  it("reports quote failures", async () => {
    vi.spyOn(api, "getStockQuote").mockResolvedValue({ error: { code: "not_found", message: "Symbol unavailable" } });
    render(<StockDetailPage symbol="BAD" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Symbol unavailable");
  });
});
