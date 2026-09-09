import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { AnalystConsensus } from "@/components/stock/AnalystConsensus";

beforeEach(() => vi.restoreAllMocks());
it("renders provider consensus and target difference independently", async () => {
  vi.spyOn(api, "getAnalystConsensus").mockResolvedValue({ data: { symbol: "TEST", available: true, score: 2, target: 120, analysts: 12, source: "Yahoo Finance", retrieved_at: "2026-09-05T00:00:00Z" } });
  render(<AnalystConsensus symbol="TEST" price={100} />);
  expect(await screen.findByRole("img", { name: "Analyst consensus: Buy" })).toBeInTheDocument();
  expect(screen.getByText("+20.0%")).toBeInTheDocument();
  expect(screen.getByText("12 analysts")).toBeInTheDocument();
});
it("never draws a neutral rating for missing coverage", async () => {
  vi.spyOn(api, "getAnalystConsensus").mockResolvedValue({ data: { symbol: "TEST", available: false, score: null, target: null, analysts: null, source: "Yahoo Finance", retrieved_at: "2026-09-05T00:00:00Z" } });
  render(<AnalystConsensus symbol="TEST" price={100} />);
  expect(await screen.findByText("Analyst coverage unavailable.")).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
