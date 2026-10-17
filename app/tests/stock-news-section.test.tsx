import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

const getNewsMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ getNews: getNewsMock }));

import { StockNewsSection } from "@/components/stock/StockNewsSection";

describe("StockNewsSection", () => {
  beforeEach(() => {
    getNewsMock.mockReset();
    getNewsMock.mockResolvedValue({ data: { symbol: "RELIANCE", items: [] } });
  });

  it("renders attributed headlines and links to the original article", async () => {
    getNewsMock.mockResolvedValue({ data: { symbol: "RELIANCE", items: [{
      title: "Reliance announces a new energy investment",
      publisher: "Reuters",
      url: "https://example.com/reliance-news",
      published_at: "2026-09-06T06:00:00Z",
      thumbnail: null,
      thumbnail_kind: null,
      summary: "The company detailed the next stage of its investment programme.",
    }] } });

    render(<StockNewsSection symbol="RELIANCE" companyName="Reliance Industries Ltd" exchange="NSE" />);

    const headline = await screen.findByRole("link", { name: /Reliance announces a new energy investment/ });
    expect(headline).toHaveAttribute("href", "https://example.com/reliance-news");
    expect(screen.getByText("Reuters")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "News" })).toBeInTheDocument();
    expect(screen.queryByText(/Yahoo Finance/i)).not.toBeInTheDocument();
    expect(getNewsMock).toHaveBeenCalledWith(
      "RELIANCE", "NSE", 8, "Reliance Industries Ltd",
    );
  });

  it("shows an honest empty state when no coverage is available", async () => {
    render(<StockNewsSection symbol="RELIANCE" companyName="Reliance Industries Ltd" exchange="NSE" />);
    const section = await screen.findByRole("region", { name: "News" });
    expect(within(section).getByText("No recent coverage found")).toBeInTheDocument();
  });
});
