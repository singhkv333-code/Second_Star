import { describe, expect, it } from "vitest";
import { buildPageContext } from "@/lib/pageContext";

const SUMMARY = {
  total_value: 870144,
  invested_value: 709567,
  total_pnl: 160577,
  total_pnl_pct: 22.63,
  day_pnl: 2568,
  num_holdings: 8,
  cash_available: 11633,
};

describe("buildPageContext", () => {
  it("gives the portfolio tab its numbers and its reach", () => {
    const ctx = buildPageContext("portfolio", { summary: SUMMARY, tradingMode: "paper" });
    expect(ctx?.surface).toBe("Portfolio");
    // The facts a glanceable question needs, without a tool hop.
    expect(ctx?.section).toContain("8 holdings");
    expect(ctx?.section).toContain("+22.63%");
    expect(ctx?.section).toContain("paper book");
    // The reach, which is what stops "I can't see your holdings".
    expect(ctx?.available_data).toContain("sector allocation");
  });

  it("keeps the block small enough to ride on every turn", () => {
    const ctx = buildPageContext("portfolio", { summary: SUMMARY, tradingMode: "paper" });
    // Well inside the backend's 240-char field cap, which is the real ceiling.
    expect((ctx?.section ?? "").length).toBeLessThan(240);
    expect((ctx?.available_data ?? []).length).toBeLessThanOrEqual(16);
  });

  it("omits the portfolio line entirely when the metric strip has nothing", () => {
    // A summary that never loaded must not become "value —, 0 holdings":
    // a fabricated zero is worse than an absent line.
    const ctx = buildPageContext("portfolio", {});
    expect(ctx?.section).toBeUndefined();
    expect(ctx?.available_data).toContain("holdings");
  });

  it("says plainly when no broker is connected", () => {
    expect(buildPageContext("brokers", { brokersConnected: [] })?.section)
      .toBe("no brokers connected yet");
  });

  it("reports connected brokers and whether live orders are armed", () => {
    const ctx = buildPageContext("brokers", {
      brokersConnected: ["Zerodha", "Groww"],
      brokersLiveArmed: false,
    });
    expect(ctx?.section).toBe("connected: Zerodha, Groww; live orders off");
  });

  it("lets a focused security outrank the tab it is viewed from", () => {
    const ctx = buildPageContext("portfolio", {
      summary: SUMMARY,
      symbol: "RELIANCE",
      name: "Reliance Industries",
    });
    expect(ctx?.surface).toBe("Company page");
    expect(ctx?.entity).toEqual({
      kind: "security", symbol: "RELIANCE", name: "Reliance Industries",
    });
    // The company page's own reach replaces the portfolio's.
    expect(ctx?.available_data).toContain("quarterly results");
    expect(ctx?.available_data).not.toContain("sector allocation");
  });

  it("gives every tab a distinct surface, so the model can tell them apart", () => {
    const tabs = ["home", "portfolio", "agents", "screener", "brokers", "chat"] as const;
    const surfaces = tabs.map((t) => buildPageContext(t)?.surface);
    expect(new Set(surfaces).size).toBe(tabs.length);
  });
});
