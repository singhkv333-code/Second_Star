import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/hooks/useCompanyLogos", () => ({ useCompanyLogos: () => ({}) }));
// The canvas chart is the stock page's; here only its inputs matter.
vi.mock("@/components/chart/StockPriceChart", () => ({
  StockPriceChart: (p: { seriesDefs: { points: unknown[] }[] }) => (
    <div data-testid="plot">{p.seriesDefs.map((d) => d.points.length).join(",")}</div>
  ),
}));

import { PriceChartCard } from "@/components/chat/PriceChartCard";

const DAY = 86_400;
const bars = (n: number, from = 100) =>
  Array.from({ length: n }, (_, i) => ({
    t: 1_780_000_000 + i * DAY, o: from + i, h: from + i + 1, l: from + i - 1, c: from + i + 1, v: 1000,
  }));

afterEach(() => vi.restoreAllMocks());

describe("PriceChartCard", () => {
  it("loads our own bars for the range and states the change over it", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ bars: bars(250) })),
    );
    render(<PriceChartCard payload={{ symbols: [{ symbol: "PETRONET", name: "Petronet LNG" }] }} />);
    await waitFor(() => expect(screen.getByTestId("plot")).toHaveTextContent("250"));
    expect(String(f.mock.calls[0]![0])).toContain("/bars?symbol=PETRONET&interval=1d&limit=250");
    expect(screen.getByText("₹350.00")).toBeInTheDocument(); // last close
    expect(screen.getByText(/\+250\.00%/)).toBeInTheDocument(); // 100 → 350
    fireEvent.click(screen.getByRole("tab", { name: "5Y" }));
    await waitFor(() =>
      expect(String(f.mock.calls.at(-1)![0])).toContain("interval=1w&limit=260"),
    );
  });

  it("says so when a symbol has no history, rather than drawing a blank", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
    render(<PriceChartCard payload={{ symbols: [{ symbol: "NOPE1" }, { symbol: "NOPE2" }] }} />);
    await waitFor(() =>
      expect(screen.getByText(/Price history is unavailable/)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /NOPE1 chart/ })).toBeInTheDocument();
  });
});
