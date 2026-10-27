import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/hooks/useCompanyLogos", () => ({ useCompanyLogos: () => ({}) }));
vi.mock("@/components/chart/StockPriceChart", () => ({
  StockPriceChart: (p: { seriesDefs: { points: unknown[] }[]; normalize?: boolean }) => (
    <div data-testid="plot" data-normalize={String(p.normalize)}>
      {p.seriesDefs.map((d) => d.points.length).join(",")}
    </div>
  ),
}));

import { SeriesChartCard as ChatChart } from "@/components/chat/SeriesChartCard";

const pts = (vals: number[]) => vals.map((v, i) => ({ t: `202${i}-03-31`, v }));

describe("SeriesChartCard", () => {
  it("draws a comparison from the tool's own series, fetching nothing", () => {
    const f = vi.spyOn(globalThis, "fetch");
    render(
      <ChatChart
        payload={{
          _render_hint: "series_chart_card",
          title: "RELIANCE vs NIFTY 50 · 5y",
          series: [
            { symbol: "RELIANCE", points: pts([100, 105]) },
            { symbol: "NIFTY 50", points: pts([200, 260]) },
          ],
        }}
      />,
    );
    expect(screen.getByTestId("plot")).toHaveTextContent("2,2");
    expect(screen.getByText("+5.00%")).toBeInTheDocument();
    expect(screen.getByText("+30.00%")).toBeInTheDocument();
    expect(f).not.toHaveBeenCalled();
  });

  it("plots a line item raw, in its unit", () => {
    render(
      <ChatChart
        payload={{
          title: "TCS · Profit/Loss For The Period",
          unit: "Rs. Cr.",
          normalize: false,
          series: [{ symbol: "TCS", points: pts([38449, 42303, 49454]) }],
        }}
      />,
    );
    expect(screen.getByTestId("plot")).toHaveAttribute("data-normalize", "false");
    expect(screen.getByText("₹49,454 Cr")).toBeInTheDocument();
    expect(screen.getByText(/Company page/)).toBeInTheDocument();
  });
});
