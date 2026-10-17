import { describe, expect, it } from "vitest";
import type { OhlcResponse, StatementResponse } from "@/lib/api";
import { alignPrices, cashBridge, performance, ratioHistory, tradingDate, windowPrices } from "@/components/stock/researchMath";
function statement(rows: Record<string, (number | null)[]>, periods = ["Mar 26", "Mar 25", "Mar 24"]): StatementResponse {
  return { available: true, company: null, statement: "cash_flow", source: "moneycontrol", basis: "consolidated", unit: "Rs. Cr.", periods,
    rows: Object.entries(rows).map(([line_item, values]) => ({ line_item, section: null, values: Object.fromEntries(periods.map((p, i) => [p, values[i] ?? null])), value_texts: {} })) };
}
const prices = (dates: string[], values: number[]): OhlcResponse => ({ symbol: "TEST", source: "kite", price_basis: "unadjusted", interval: "1d", range: "5Y", bars: dates.map((t, i) => ({ t, o: values[i]!, h: values[i]!, l: values[i]!, c: values[i]!, v: 0 })) });

describe("Reported valuation history", () => {
  it("preserves missing years and excludes non-positive ratios from statistics", () => {
    const result = ratioHistory(statement({ "Price/BV (X)": [8, null, 4, -2] }, ["Mar 26", "Mar 25", "Mar 24", "Mar 23"]), "pb");
    expect(result.points.map((p) => p.value)).toEqual([null, 4, null, 8]);
    expect(result.median).toBe(6); expect(result.q1).toBe(5); expect(result.q3).toBe(7);
    expect(result.latest).toEqual({ period: "Mar 26", value: 8 });
  });
  it("does not synthesize P/E by inverting rounded earnings yield", () => {
    expect(ratioHistory(statement({ "Earnings Yield": [.02, .02, .01] }), "pe").valid).toEqual([]);
  });
});
describe("Cash reconciliation", () => {
  const rows = { "Cash And Cash Equivalents Begin of Year": [100], "Net CashFlow From Operating Activities": [30], "Net Cash Used In Investing Activities": [-180], "Net Cash Used From Financing Activities": [60], "Cash And Cash Equivalents End Of Year": [10] };
  it("handles cash crossing zero and retains the sign of outflows", () => {
    const result = cashBridge(statement(rows), "Mar 26");
    expect(result.steps[2]).toMatchObject({ label: "Investing", start: 130, end: -50, value: -180 });
    expect(result.steps[3]).toMatchObject({ start: -50, end: 10 });
    expect(result.residual).toBe(0);
  });
  it("does not turn a missing operating total into zero", () => {
    const result = cashBridge(statement({ ...rows, "Net CashFlow From Operating Activities": [null] }), "Mar 26");
    expect(result.steps).toEqual([]); expect(result.values.operating).toBeNull();
  });
  it("labels an unexplained difference instead of inventing a use of cash", () => {
    const result = cashBridge(statement({ ...rows, "Cash And Cash Equivalents End Of Year": [15] }), "Mar 26");
    expect(result.steps.at(-2)).toMatchObject({ label: "Unclassified", value: 5 });
  });
});
describe("Benchmark comparison", () => {
  it("aligns on Indian trading dates, not UTC date prefixes", () => {
    expect(tradingDate("2025-01-01T18:30:00Z")).toBe("2025-01-02");
    const a = prices(["2025-01-01T18:30:00Z", "2025-01-03"], [100, 110]);
    const b = prices(["2025-01-02T09:15:00+05:30", "2025-01-04"], [200, 205]);
    expect(alignPrices(a, b)).toEqual([{ date: "2025-01-02", stock: 100, benchmark: 200 }]);
  });
  it("rejects incompatible provider and adjustment bases", () => {
    const a = prices(["2025-01-01"], [100]);
    expect(alignPrices(a, { ...a, source: "yfinance" })).toEqual([]);
    expect(alignPrices(a, { ...a, price_basis: "provider" })).toEqual([]);
  });
  it("does not report a full year for a recent listing", () => {
    expect(windowPrices([{ date: "2026-07-01", stock: 100, benchmark: 100 }, { date: "2026-09-01", stock: 110, benchmark: 105 }], "1Y")).toEqual([]);
  });
  it("calculates returns and drawdown from matching closes", () => {
    const result = performance([{ date: "2025-01-01", stock: 100, benchmark: 100 }, { date: "2025-06-01", stock: 80, benchmark: 110 }, { date: "2026-01-01", stock: 120, benchmark: 115 }], "stock")!;
    expect(result.total).toBeCloseTo(20); expect(result.drawdown).toBeCloseTo(-20); expect(result.cagr).toBeCloseTo(20, 1);
  });
  it("uses a shared date near the calendar anchor and rejects large gaps", () => {
    const points = [{ date: "2025-01-01", stock: 100, benchmark: 100 }, { date: "2025-08-31", stock: 110, benchmark: 110 }, { date: "2025-09-30", stock: 120, benchmark: 120 }];
    // August 30 is a weekend: August 31 starts within the tolerance only if
    // it is the first available observation; otherwise the stale Jan anchor fails.
    expect(windowPrices(points, "1M")).toEqual([]);
  });
});
