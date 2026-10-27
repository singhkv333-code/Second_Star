import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/",
}));
vi.mock("@/hooks/useCompanyLogos", () => ({ useCompanyLogos: () => ({}) }));

import { ScreenResultsCard } from "@/components/chat/ScreenResultsCard";

const rows = Array.from({ length: 100 }, (_, i) => ({
  symbol: `SYM${i}`,
  name: `Company ${i}`,
  market_cap_cr: 2500 + i,
  revenue_growth: 100 - i,
}));

const payload = {
  title: "Growing Mid Caps",
  total_matched: 468,
  results: rows,
  columns: [
    { key: "market_cap_cr", label: "Market Cap", unit: "cr" as const },
    { key: "revenue_growth", label: "Revenue Growth", unit: "pct_signed" as const },
  ],
  applied_filters: [
    { field: "market_cap", op: ">", value: 2000 },
    { field: "revenue_growth", op: ">", value: 15 },
  ],
};

describe("ScreenResultsCard", () => {
  it("shows ten rows of a long screen and the rest on request", () => {
    render(<ScreenResultsCard payload={payload} />);
    // header + rows + the median footer
    expect(screen.getAllByRole("row")).toHaveLength(1 + 10 + 1);
    fireEvent.click(screen.getByText("Show all 100"));
    expect(screen.getAllByRole("row")).toHaveLength(1 + 100 + 1);
  });

  it("hands the screen to the Screener: its names, criteria and the true match count", () => {
    render(<ScreenResultsCard payload={payload} />);
    fireEvent.click(screen.getByRole("button", { name: /open in screener/i }));
    const parked = JSON.parse(
      window.sessionStorage.getItem(Object.keys(window.sessionStorage)[0]!) ?? "{}",
    );
    expect(parked.symbols).toHaveLength(100);
    expect(parked.matched).toBe(100);
    expect(parked.criteria).toBe(
      "Market Cap > ₹2,000 Cr · Revenue Growth > 15% · top 100 of 468 matches",
    );
    expect(window.location.hash).toBe("#screener");
  });

  it("formats values and filter chips by unit", () => {
    render(<ScreenResultsCard payload={payload} />);
    expect(screen.getByText("₹2,500 Cr")).toBeInTheDocument();
    expect(screen.getByText("+100.00%")).toBeInTheDocument();
    expect(screen.getByText("Market Cap > ₹2,000 Cr")).toBeInTheDocument();
    expect(screen.getByText("Revenue Growth > 15%")).toBeInTheDocument();
  });

  it("adds a median of the returned rows, not of the page", () => {
    render(<ScreenResultsCard payload={payload} />);
    const med = screen.getByTestId("screen-median-row");
    expect(med).toHaveTextContent("Median of 100");
    expect(med).toHaveTextContent("₹2,550 Cr"); // (2549 + 2550) / 2, rounded
    expect(med).toHaveTextContent("+50.50%");
  });

  it("a technical scan hands over every name that passed", () => {
    window.sessionStorage.clear();
    render(
      <ScreenResultsCard
        payload={{
          title: "Momentum Near Highs",
          total_matched: 31,
          as_of: "22 Jul 2026",
          symbols: Array.from({ length: 31 }, (_, i) => `S${i}`),
          results: [
            { symbol: "S0", close: 594.9, rsi14: 85.2, pattern: "bull flag, 2d ago" },
            { symbol: "S1", close: 1287.9, rsi14: 61, pattern: null },
          ],
          columns: [
            { key: "close", label: "Price", unit: "inr" },
            { key: "rsi14", label: "RSI(14)", unit: "num" },
            { key: "pattern", label: "Pattern", unit: "text" },
          ],
          applied_filters: [{ field: "rsi14", op: ">", value: 60 }],
        }}
      />,
    );
    expect(screen.getByText("₹594.9")).toBeInTheDocument();
    expect(screen.getByText("bull flag, 2d ago")).toBeInTheDocument();
    // two rows: too few for a median
    expect(screen.queryByTestId("screen-median-row")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /open in screener/i }));
    const parked = JSON.parse(
      window.sessionStorage.getItem(Object.keys(window.sessionStorage)[0]!) ?? "{}",
    );
    expect(parked.symbols).toHaveLength(31);
    expect(parked.criteria).toBe("RSI(14) > 60");
    expect(parked.as_of).toBe("22 Jul 2026");
  });
});
