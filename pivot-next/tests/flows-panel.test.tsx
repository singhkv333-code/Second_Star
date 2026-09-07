import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { FlowsResponse } from "@/lib/api";
import { FlowsPanel } from "@/components/stock/FlowsPanel";

vi.mock("next/dynamic", () => ({ default: () => ({ ariaLabel, option }: { ariaLabel: string; option: unknown }) => <div role="img" aria-label={ariaLabel} data-option={JSON.stringify(option)} /> }));

const data: FlowsResponse = {
  symbol: "TEST", available: true,
  summary: { date: "2026-07-27", delivery_pct: 59, delivery_median_20d: 50, volume: 100000, delivered: 59000, trades: 1000, oi: 16500, oi_chg: 6500, close: 100 },
  delivery: [{ d: "2026-07-27", close: 100, qty: 100000, deliv_qty: 59000, deliv_per: 59, trades: 1000 }],
  oi: [{ d: "2026-07-27", oi: 16500, oi_chg: 6500 }],
};

it("switches between delivery and futures history", () => {
  render(<FlowsPanel data={data} />);
  expect(screen.getByText("20-day median 50.0%")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Delivery percentage by day" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Open interest" }));
  expect(screen.getByRole("img", { name: "Futures open interest by day" })).toBeInTheDocument();
  expect(screen.queryByText("20-day median 50.0%")).not.toBeInTheDocument();
});

it("does not imply above-median delivery when the baseline is missing", () => {
  render(<FlowsPanel data={{ ...data, summary: { ...data.summary!, delivery_median_20d: null } }} />);
  const option = JSON.parse(screen.getByRole("img").getAttribute("data-option")!);
  expect(option.series[0].markLine).toBeUndefined();
  expect(option.series[0].data[0].itemStyle.color).toBe("#4F8A5B");
});

it("opens available OI history by default and explains missing delivery", () => {
  render(<FlowsPanel data={{ ...data, delivery: [] }} />);
  expect(screen.getByRole("img", { name: "Futures open interest by day" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Delivery" }));
  expect(screen.getByText("Delivery history unavailable.")).toBeInTheDocument();
});
