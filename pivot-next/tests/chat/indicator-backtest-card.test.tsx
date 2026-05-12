/**
 * Tests for IndicatorBacktestCard — the chat-side concise widget — and
 * IndicatorBacktestDetail — the full result surface mounted inside the
 * widget's modal. Wired via raw_data._render_hint ===
 * "indicator_backtest_chart" in ChatDemo.
 */
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import {
  IndicatorBacktestCard,
  IndicatorBacktestDetail,
  type IndicatorBacktestPayload,
} from "@/components/chat/IndicatorBacktestCard";

const RSI_PAYLOAD: IndicatorBacktestPayload = {
  symbol: "RELIANCE",
  indicator: "rsi",
  indicator_period: 14,
  operator: "<",
  threshold: 30,
  period_label: "Jan 2023 — Dec 2024",
  price_curve: [
    { t: "2023-01-02T00:00:00Z", v: 2500 },
    { t: "2023-04-01T00:00:00Z", v: 2400 },
    { t: "2023-07-01T00:00:00Z", v: 2600 },
    { t: "2024-01-01T00:00:00Z", v: 2700 },
    { t: "2024-12-29T00:00:00Z", v: 2900 },
  ],
  indicator_curve: [
    { t: "2023-01-02T00:00:00Z", v: 55 },
    { t: "2023-04-01T00:00:00Z", v: 28 },
    { t: "2023-07-01T00:00:00Z", v: 62 },
    { t: "2024-01-01T00:00:00Z", v: 25 },
    { t: "2024-12-29T00:00:00Z", v: 60 },
  ],
  equity_curve: [
    { t: "2023-01-02T00:00:00Z", v: 100000 },
    { t: "2024-12-29T00:00:00Z", v: 116000 },
  ],
  signals: [
    { t: "2023-04-01T00:00:00Z", side: "buy", price: 2400, indicator_value: 28 },
    { t: "2023-07-01T00:00:00Z", side: "sell", price: 2600, indicator_value: 62 },
    { t: "2024-01-01T00:00:00Z", side: "buy", price: 2700, indicator_value: 25 },
    { t: "2024-12-29T00:00:00Z", side: "sell", price: 2900, indicator_value: 60 },
  ],
  metrics: {
    total_return_pct: 16.0,
    cagr_pct: 7.7,
    max_drawdown_pct: 4.2,
    hit_rate_pct: 100,
    n_trades: 2,
    n_wins: 2,
    starting_capital: 100000,
    ending_value: 116000,
  },
  bench_buy_hold_return_pct: 12.5,
};

describe("IndicatorBacktestCard — concise widget", () => {
  it("renders the symbol, condition headline and period", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    expect(screen.getByTestId("indicator-backtest-card")).toBeInTheDocument();
    // Symbol is rendered title-cased in the concise widget (matches the
    // LogicCardChip company-name rhythm).
    expect(screen.getByText("Reliance")).toBeInTheDocument();
    expect(screen.getByText(/drops below/i)).toBeInTheDocument();
    expect(screen.getByText(/Jan 2023 — Dec 2024/)).toBeInTheDocument();
  });

  it("renders the 4 concise metrics: CAGR · Max DD · Trades · Hit rate", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    expect(screen.getByText("CAGR")).toBeInTheDocument();
    expect(screen.getByText("Max DD")).toBeInTheDocument();
    expect(screen.getByText("Trades")).toBeInTheDocument();
    expect(screen.getByText("Hit rate")).toBeInTheDocument();
  });

  it("does NOT surface the secondary stats in the concise widget", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    expect(screen.queryByText("End value")).not.toBeInTheDocument();
    expect(screen.queryByText(/RELIANCE buy & hold/)).not.toBeInTheDocument();
  });

  it("displays formatted strategy total return prominently", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    expect(screen.getByText(/\+16\.00%/)).toBeInTheDocument();
    expect(screen.getByText(/Strategy total return/i)).toBeInTheDocument();
  });

  it("renders the trade count in the Trades stat", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("handles a losing strategy with negative total return", () => {
    const losing: IndicatorBacktestPayload = {
      ...RSI_PAYLOAD,
      metrics: {
        ...RSI_PAYLOAD.metrics,
        total_return_pct: -8.5,
        cagr_pct: -4.2,
        ending_value: 91500,
        n_wins: 0,
        hit_rate_pct: 0,
      },
    };
    render(<IndicatorBacktestCard payload={losing} />);
    expect(screen.getByText("-8.50%")).toBeInTheDocument();
  });

  it("opens the detail modal when View is clicked", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    // Detail is portaled; not in the DOM until the button is clicked.
    expect(screen.queryByTestId("indicator-backtest-detail")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("indicator-backtest-view-btn"));
    expect(screen.getByTestId("indicator-backtest-detail")).toBeInTheDocument();
    // The detail surface exposes the full metric set + all three charts.
    const detail = screen.getByTestId("indicator-backtest-detail");
    expect(within(detail).getByText("End value")).toBeInTheDocument();
    expect(within(detail).getByText(/RELIANCE buy & hold/)).toBeInTheDocument();
    expect(within(detail).getByTestId("price-chart")).toBeInTheDocument();
    expect(within(detail).getByTestId("indicator-chart")).toBeInTheDocument();
    expect(within(detail).getByTestId("equity-chart")).toBeInTheDocument();
  });
});

describe("IndicatorBacktestDetail — full result surface", () => {
  it("renders all 6 detail metrics", () => {
    render(<IndicatorBacktestDetail payload={RSI_PAYLOAD} />);
    expect(screen.getByText("CAGR")).toBeInTheDocument();
    expect(screen.getByText("Max DD")).toBeInTheDocument();
    expect(screen.getByText("Trades")).toBeInTheDocument();
    expect(screen.getByText("Hit rate")).toBeInTheDocument();
    expect(screen.getByText("End value")).toBeInTheDocument();
    expect(screen.getByText(/RELIANCE buy & hold/)).toBeInTheDocument();
  });

  it("renders all three charts: price, indicator, equity", () => {
    render(<IndicatorBacktestDetail payload={RSI_PAYLOAD} />);
    expect(screen.getByTestId("price-chart")).toBeInTheDocument();
    expect(screen.getByTestId("indicator-chart")).toBeInTheDocument();
    expect(screen.getByTestId("equity-chart")).toBeInTheDocument();
  });
});
