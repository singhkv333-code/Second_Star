import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import type { StatementResponse, OhlcResponse } from "@/lib/api";
import { ValuationHistoryPanel } from "@/components/stock/ValuationHistoryPanel";
import { CapitalAllocationPanel } from "@/components/stock/CapitalAllocationPanel";
import { BenchmarkPerformancePanel } from "@/components/stock/BenchmarkPerformancePanel";
vi.mock("next/dynamic", () => ({ default: () => ({ ariaLabel }: { ariaLabel: string }) => <div role="img" aria-label={ariaLabel} /> }));
const data: StatementResponse = { available: true, company: null, basis: "consolidated", statement: "ratios", source: "moneycontrol", unit: "Rs. Cr.", periods: ["Mar 26", "Mar 25"], rows: [
  { line_item: "Price/BV (X)", section: null, values: { "Mar 26": 4, "Mar 25": 2 }, value_texts: {} },
  { line_item: "EV/EBITDA (X)", section: null, values: { "Mar 26": 20, "Mar 25": 10 }, value_texts: {} },
] };
const bars: OhlcResponse = { symbol: "TEST", range: "5Y", interval: "1d", source: "kite", price_basis: "unadjusted", bars: [
  { t: "2025-01-01", o: 100, h: 100, l: 100, c: 100, v: 0 },
  { t: "2026-01-01", o: 120, h: 120, l: 120, c: 120, v: 0 },
] };
beforeEach(() => { vi.restoreAllMocks(); vi.spyOn(api, "getStatement").mockResolvedValue({ data }); vi.spyOn(api, "getResearchPrices").mockResolvedValue({ data: bars }); });
describe("Research section interactions", () => {
  it("switches the valuation metric and retains its dated observation label", async () => {
    render(<ValuationHistoryPanel symbol="TEST" />);
    await screen.findAllByText("4.00×");
    fireEvent.click(screen.getByRole("button", { name: "EV/EBITDA" }));
    expect(screen.getAllByText("20.00×")[0]).toBeInTheDocument();
    expect(screen.getByText(/not a live valuation/)).toBeInTheDocument();
  });
  it("exposes request failures with a working retry", async () => {
    vi.spyOn(api, "getStatement").mockResolvedValueOnce({ error: { code: "unavailable", message: "Unavailable" } }).mockResolvedValue({ data });
    render(<ValuationHistoryPanel symbol="TEST" />);
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    expect((await screen.findAllByText("4.00×"))[0]).toBeInTheDocument();
  });
  it("does not retain the previous company's figures while a new symbol loads", async () => {
    const { rerender } = render(<ValuationHistoryPanel symbol="OLD" />);
    await screen.findAllByText("4.00×");
    vi.spyOn(api, "getStatement").mockReturnValue(new Promise(() => {}));
    rerender(<ValuationHistoryPanel symbol="NEW" />);
    expect(screen.queryByText("4.00×")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
  });
  it("keeps missing cash flow as a visible unavailable state", async () => {
    vi.spyOn(api, "getStatement").mockResolvedValue({ data: { ...data, available: false, rows: [] } });
    render(<CapitalAllocationPanel symbol="TEST" />);
    expect(await screen.findByText("Reported cash-flow history is unavailable for this company.")).toBeInTheDocument();
  });
  it("falls back to one provider for both benchmark series", async () => {
    const prices = vi.spyOn(api, "getResearchPrices").mockResolvedValueOnce({ data: bars }).mockResolvedValueOnce({ data: { ...bars, source: "yfinance" } }).mockResolvedValue({ data: { ...bars, source: "yfinance" } });
    render(<BenchmarkPerformancePanel symbol="TEST" exchange="NSE" />);
    await screen.findByText("yfinance · EOD daily closes");
    expect(prices).toHaveBeenCalledWith("TEST", "NSE", "yfinance");
    expect(prices).toHaveBeenCalledWith("NIFTY 50", "NSE", "yfinance");
    fireEvent.click(screen.getByRole("button", { name: "Drawdown" }));
    expect(screen.getByRole("img", { name: /drawdown/ })).toBeInTheDocument();
  });
  it("recovers when the primary history request fails", async () => {
    const prices = vi.spyOn(api, "getResearchPrices").mockResolvedValueOnce({ error: { code: "unavailable", message: "History unavailable" } }).mockResolvedValueOnce({ data: bars }).mockResolvedValue({ data: { ...bars, source: "yfinance" } });
    render(<BenchmarkPerformancePanel symbol="TEST" exchange="NSE" />);
    await screen.findByText("yfinance · EOD daily closes");
    expect(prices).toHaveBeenCalledWith("TEST", "NSE", "yfinance");
    expect(prices).toHaveBeenCalledWith("NIFTY 50", "NSE", "yfinance");
  });
  it("labels an unavailable range and keeps the return table honest", async () => {
    render(<BenchmarkPerformancePanel symbol="TEST" exchange="NSE" />);
    await screen.findByRole("button", { name: "5Y" });
    fireEvent.click(screen.getByRole("button", { name: "5Y" }));
    expect(screen.getByText("A full 5Y comparison is unavailable. Choose a shorter period.")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("—").length).toBeGreaterThan(0);
  });
  it("rejects a legacy endpoint response without explicit adjustment metadata", async () => {
    vi.spyOn(api, "getResearchPrices").mockResolvedValue({ data: { ...bars, price_basis: undefined } });
    render(<BenchmarkPerformancePanel symbol="TEST" exchange="NSE" />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("This data could not be loaded."));
  });
});
