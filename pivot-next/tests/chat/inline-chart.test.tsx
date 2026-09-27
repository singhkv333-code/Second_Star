import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
// The canvas is ECharts'; here only the option handed to it matters.
vi.mock("next/dynamic", () => ({
  default: () => (p: { option: { series: { data: unknown[] }[] } }) => (
    <div data-testid="echart">{p.option.series.map((s) => s.data.length).join(",")}</div>
  ),
}));

import AssistantMessage from "@/components/chat/AssistantMessage";
import { registerCharts, splitCharts, stripChartMarkers } from "@/lib/inlineCharts";
import { metricTitle } from "@/components/chat/InlineChart";

describe("chart markers", () => {
  it("split the reply where the model placed them", () => {
    expect(splitCharts("Growth held.\n\n[[chart:ab12cd]]\n\nMargins fell.")).toEqual([
      "Growth held.\n\n", { chart: "ab12cd" }, "\n\nMargins fell.",
    ]);
  });

  it("hold back a marker still arriving on the stream", () => {
    expect(splitCharts("Growth held.\n\n[[cha")).toEqual(["Growth held.\n\n"]);
  });

  it("are left out of copied text", () => {
    expect(stripChartMarkers("A\n\n[[chart:ab12cd]]\n\nB")).toBe("A\n\nB");
  });
});

describe("inline charts", () => {
  it("draw between the paragraphs that discuss them", () => {
    registerCharts([{
      _render_hint: "financial_bars", id: "fb0001", title: "Net profit", unit: "Rs. Cr.",
      labels: ["FY24", "FY25", "FY26"], series: [{ symbol: "TCS", values: [46099, 48797, 49454] }],
    }]);
    const { container } = render(
      <AssistantMessage text={"Profit rose.\n\n[[chart:fb0001]]\n\nGrowth slowed."} />,
    );
    const blocks = [...container.firstElementChild!.children].map((el) => el.tagName);
    expect(blocks).toEqual(["P", "FIGURE", "P"]);
    expect(screen.getByText("TCS · Net profit")).toBeInTheDocument();
    expect(screen.getByText("Annual · ₹ crore")).toBeInTheDocument();
    expect(screen.getByText("₹49,454 Cr")).toBeInTheDocument();          // the latest, with its unit
    expect(screen.getByText("3.6% CAGR since FY24")).toBeInTheDocument(); // not a bare "+7.3%"
  });

  it("state a ratio's change in points and a per-share figure in rupees", () => {
    registerCharts([
      { _render_hint: "financial_bars", id: "fb0002", title: "ROE", unit: "%",
        labels: ["FY24", "FY25", "FY26"], series: [{ symbol: "TCS", values: [50.1, 48.2, 45.9] }] },
      { _render_hint: "financial_bars", id: "fb0003", title: "EPS", unit: "₹/share",
        labels: ["FY24", "FY25", "FY26"], series: [{ symbol: "TCS", values: [126, 134, 136] }] },
    ]);
    render(<AssistantMessage text={"[[chart:fb0002]]\n\nand\n\n[[chart:fb0003]]"} />);
    expect(screen.getByText("45.9%")).toBeInTheDocument();
    expect(screen.getByText("−4.2 pts since FY24")).toBeInTheDocument();
    expect(screen.getByText("₹136")).toBeInTheDocument();
    expect(screen.getByText("Annual · ₹ per share")).toBeInTheDocument();
  });

  it("name metrics the way a reader writes them", () => {
    expect(metricTitle("Eps basic")).toBe("EPS");
    expect(metricTitle("roce")).toBe("ROCE");
    expect(metricTitle("Ebitda margin")).toBe("EBITDA margin");
    expect(metricTitle("Net profit")).toBe("Net profit");
  });

  it("draw a skeleton while the reply is still streaming", () => {
    render(<AssistantMessage text={"Profit rose.\n\n[[chart:fb0001]]"} chartsPending />);
    expect(screen.getByTestId("inline-chart-skeleton")).toBeInTheDocument();
    expect(screen.queryByTestId("inline-chart")).toBeNull();
  });

  it("place a valuation band with today's multiple and where it sits", () => {
    registerCharts({ vb0001: {
      _render_hint: "valuation_band", id: "vb0001", title: "TCS · P/E", years: 5,
      current: 15.05, median: 29.45, p25: 24.36, p75: 31.72, percentile: 1,
      points: [{ t: "2025-01-01", v: 30 }, { t: "2026-09-25", v: 15.05 }],
    } });
    render(<AssistantMessage text={"Cheap by its own history.\n\n[[chart:vb0001]]"} />);
    expect(screen.getByText("15.05×")).toBeInTheDocument();
    expect(screen.getByText("1st percentile of 5Y · below its usual range")).toBeInTheDocument();
  });

  it("render nothing for a marker whose chart never arrived", () => {
    const { container } = render(<AssistantMessage text={"Text.\n\n[[chart:zz9999]]"} />);
    expect(container.querySelector("figure")).toBeNull();
    expect(container.textContent).not.toContain("[[");
  });
});
