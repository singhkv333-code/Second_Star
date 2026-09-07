import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { MixResponse } from "@/lib/api";
import { MixPanel } from "@/components/stock/MixPanel";
vi.mock("next/dynamic", () => ({ default: () => ({ ariaLabel, option }: { ariaLabel: string; option: unknown }) => <div role="img" aria-label={ariaLabel} data-option={JSON.stringify(option)} /> }));
const data: MixResponse = { symbol: "TEST", available: true, source_name: "Test disclosures", charts: [{ id: 1, title: "Product Wise Break-Up", current: [{ name: "Small", pct: 20 }, { name: "Large", pct: 80 }], series: [{ name: "Small", points: [{ t: 1704067200000, pct: 20 }] }, { name: "Large", points: [{ t: 1672531200000, pct: 70 }, { t: 1704067200000, pct: 80 }] }] }] };
it("orders the split by share while keeping chart colours attached to names", () => {
  const { container } = render(<MixPanel data={data} />);
  expect(container.querySelector(".mix-segment-name")?.textContent).toBe("Large");
  expect(screen.getByText("80.0%")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Over time" }));
  const option = JSON.parse(screen.getByRole("img").getAttribute("data-option")!);
  expect(option.series[0].data).toEqual([null, 20]);
  expect(option.series[1].data).toEqual([70, 80]);
  expect(option.series[0].type).toBe("bar");
});
it("uses a custom selector with shortened breakdown labels", () => {
  render(<MixPanel data={{ ...data, charts: [...data.charts, { ...data.charts[0]!, id: 2, title: "Operating Profit Break-Up - Organized Retail" }] }} />);
  expect(screen.getByRole("combobox", { name: "Segment breakdown" }).tagName).toBe("BUTTON");
});
