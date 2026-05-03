/**
 * Tests for IndicatorBacktestCard — the chat-side backtest result chart.
 * Wired via raw_data._render_hint === "indicator_backtest_chart" in
 * ChatDemo. Previously had zero coverage.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  IndicatorBacktestCard,
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

describe("IndicatorBacktestCard", () => {
  it("renders the symbol and the strategy condition headline", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    expect(screen.getByTestId("indicator-backtest-card")).toBeInTheDocument();
    expect(screen.getByText("RELIANCE")).toBeInTheDocument();
    // The condition label is e.g. "RSI(14) drops below 30"
    expect(
      screen.getByText(/drops below/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Jan 2023 — Dec 2024/)).toBeInTheDocument();
  });

  it("renders the metrics strip with all 6 fields", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    expect(screen.getByText("CAGR")).toBeInTheDocument();
    expect(screen.getByText("Max DD")).toBeInTheDocument();
    expect(screen.getByText("Trades")).toBeInTheDocument();
    expect(screen.getByText("Hit rate")).toBeInTheDocument();
    expect(screen.getByText("End value")).toBeInTheDocument();
    expect(screen.getByText(/RELIANCE buy & hold/)).toBeInTheDocument();
  });

  it("displays formatted strategy total return prominently", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    // total_return_pct: 16.0 → "+16.00%"
    expect(screen.getByText(/\+16\.00%/)).toBeInTheDocument();
    expect(screen.getByText(/Strategy total return/i)).toBeInTheDocument();
  });

  it("renders all three charts: price, indicator, equity", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    expect(screen.getByTestId("price-chart")).toBeInTheDocument();
    expect(screen.getByTestId("indicator-chart")).toBeInTheDocument();
    expect(screen.getByTestId("equity-chart")).toBeInTheDocument();
  });

  it("renders trade-count metric matching the signals", () => {
    render(<IndicatorBacktestCard payload={RSI_PAYLOAD} />);
    // 2 trades (2 buy/sell pairs from 4 signals)
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
    // Negative returns get a leading "-" — the formatter outputs "-8.50%"
    expect(screen.getByText("-8.50%")).toBeInTheDocument();
  });
});
