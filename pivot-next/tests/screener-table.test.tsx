import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import * as screenerApi from "@/lib/screenerApi";
import type { ScreenerStock } from "@/lib/screenerApi";
import { StockTable } from "@/components/screener/StockTable";
import { Sparkline } from "@/components/screener/Sparkline";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/screener",
}));

function stock(over: Partial<ScreenerStock> = {}): ScreenerStock {
  return {
    symbol: "RELIANCE", name: "Reliance Industries Limited", sector: "energy",
    market_cap_cr: 1789000, price: 1322.4, change_pct: 1.5, change_abs: 19.55,
    day_open: 1305, day_high: 1330.2, day_low: 1301.05, prev_close: 1302.85,
    volume: 13452118, pe: 25, roe: 8.93, roce: 9.17, de: 0.41,
    one_year_pct: -2.74, div_yield: null, logo_url: null, ...over,
  };
}

const noSort = { by: "market_cap_cr", dir: "desc" as const };

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(screenerApi, "getScreenerSparklines").mockResolvedValue({
    data: { series: { RELIANCE: [1300, 1310, 1305, 1322.4] }, source: "test" },
  });
});

describe("Screener table", () => {
  it("renders the identity, the structured numbers and the new columns", async () => {
    render(<StockTable rows={[stock()]} sectorLabel={() => "Energy"} sort={noSort} onSort={vi.fn()} />);

    const row = screen.getByText("RELIANCE").closest("tr") as HTMLElement;
    const cells = within(row);
    expect(cells.getByText("NSE")).toBeInTheDocument();
    expect(cells.getByText(/Reliance Industries Limited/)).toBeInTheDocument();
    expect(cells.getByText("₹1,322.40")).toBeInTheDocument();
    // A real minus sign, an explicit plus, and Indian grouping on volume.
    expect(cells.getByText("+19.55")).toBeInTheDocument();
    expect(cells.getByText("+1.50%")).toBeInTheDocument();
    expect(cells.getByText("−2.74%")).toBeInTheDocument();
    expect(cells.getByText("1.35 Cr")).toBeInTheDocument();   // volume, in crore shares
    expect(cells.getByText("₹1,305.00")).toBeInTheDocument();  // Open
    expect(cells.getByText("₹1,302.85")).toBeInTheDocument();  // Prev close
    expect(cells.getByText("17.89 L Cr")).toBeInTheDocument(); // market cap
    expect(cells.getByText("9.2%")).toBeInTheDocument();       // ROCE — new
    expect(cells.getByText("0.41")).toBeInTheDocument();       // D/E — new
  });

  it("asks for sparklines once per symbol, never again on re-render", async () => {
    const spy = vi.spyOn(screenerApi, "getScreenerSparklines");
    const rows = [stock()];
    const { rerender } = render(
      <StockTable rows={rows} sectorLabel={() => "Energy"} sort={noSort} onSort={vi.fn()} />,
    );
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    rerender(<StockTable rows={rows} sectorLabel={() => "Energy"} sort={noSort} onSort={vi.fn()} />);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("sorts only on columns the server can order", () => {
    const onSort = vi.fn();
    render(<StockTable rows={[stock()]} sectorLabel={() => "Energy"} sort={noSort} onSort={onSort} />);
    screen.getByText("Price").click();
    expect(onSort).toHaveBeenCalledWith("price");
    onSort.mockClear();
    // Volume is served per page but cannot be ordered across the universe.
    screen.getByText("Volume").click();
    expect(onSort).not.toHaveBeenCalled();
  });

  it("shows an em-dash for a missing metric rather than a zero", () => {
    render(
      <StockTable
        rows={[stock({ pe: null, roce: null, volume: null, change_abs: null })]}
        sectorLabel={() => "Energy"} sort={noSort} onSort={vi.fn()}
      />,
    );
    const row = screen.getByText("RELIANCE").closest("tr") as HTMLElement;
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(4);
  });
});

describe("Number formats", () => {
  it("renders each column in the unit it is quoted in", () => {
    render(
      <StockTable
        rows={[stock({
          market_cap_cr: 1789000,   // 17.89 lakh crore
          volume: 13452118,         // 1.35 crore shares
          price: 0.85,              // a sub-rupee name still shows its paise
          pe: 25, roe: 8.93, roce: 9.17, de: 0.41,
        })]}
        sectorLabel={() => "Energy"} sort={noSort} onSort={vi.fn()}
      />,
    );
    const row = screen.getByText("RELIANCE").closest("tr") as HTMLElement;
    const c = within(row);
    expect(c.getByText("17.89 L Cr")).toBeInTheDocument();
    expect(c.getByText("1.35 Cr")).toBeInTheDocument();
    expect(c.getByText("₹0.85")).toBeInTheDocument();
    expect(c.getByText("25.0")).toBeInTheDocument();    // P/E, one decimal
    expect(c.getByText("8.9%")).toBeInTheDocument();    // ROE
    expect(c.getByText("9.2%")).toBeInTheDocument();    // ROCE
    expect(c.getByText("0.41")).toBeInTheDocument();    // D/E, two decimals
  });

  it("puts a lakh-crore market cap and a thousand-crore one in different units", () => {
    render(
      <StockTable rows={[stock({ symbol: "SMALL", market_cap_cr: 4200 })]}
        sectorLabel={() => "X"} sort={noSort} onSort={vi.fn()} />,
    );
    expect(screen.getByText("4.20 K Cr")).toBeInTheDocument();
  });
});

describe("Sparkline", () => {
  it("colours against the previous close, not the first print of the day", () => {
    // Gaps down to 90 then rallies to 98 — a green SERIES inside a red DAY.
    const { container } = render(<Sparkline points={[90, 94, 98]} baseline={100} />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-label", "down on the day");
  });

  it("reserves its box while the series is still in flight", () => {
    const { container } = render(<Sparkline points={undefined} width={72} height={30} />);
    const box = container.firstElementChild as HTMLElement;
    expect(box.style.width).toBe("72px");
    expect(box.style.height).toBe("30px");
  });
});
