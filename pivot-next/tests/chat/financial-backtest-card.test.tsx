/**
 * Tests for FinancialBacktestCard — chat-side render of the SQL
 * fundamentals backtester (`/api/backtest/expr/run`). Wired via
 * raw_data._render_hint === "financial_backtest_chart".
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  FinancialBacktestCard,
  type FinancialBacktestPayload,
} from "@/components/chat/FinancialBacktestCard";

const PAYLOAD: FinancialBacktestPayload = {
  expression: "pe_ratio < 15 AND roe > 18",
  start: "2020-01-01",
  end: "2024-12-31",
  rebalance: "Q",
  metrics: {
    cagr_pct: 14.2,
    sharpe: 0.92,
    max_drawdown_pct: 18.4,
    calmar: 0.77,
    turnover_pct: 42.0,
    hit_rate_pct: 58,
    n_unique_companies: 42,
    total_return_pct: 96.4,
  },
  equity_curve: [
    { date: "2020-01-01", value: 100000 },
    { date: "2021-06-30", value: 130000 },
    { date: "2023-01-01", value: 150000 },
    { date: "2024-12-31", value: 196400 },
  ],
  benchmark_curve: [
    { date: "2020-01-01", value: 100000 },
    { date: "2021-06-30", value: 118000 },
    { date: "2023-01-01", value: 132000 },
    { date: "2024-12-31", value: 162000 },
  ],
  rebalances: [
    { date: "2020-01-01", entered: [{ symbol: "RELIANCE", weight: 0.5 }], exited: [] },
    { date: "2020-04-01", entered: [], exited: [{ symbol: "RELIANCE" }] },
    { date: "2020-07-01", entered: [{ symbol: "INFY", weight: 0.3 }], exited: [] },
  ],
  n_trades: 87,
  warnings: [],
};

describe("FinancialBacktestCard", () => {
  it("renders header with expression, period, and rebalance frequency", () => {
    render(<FinancialBacktestCard payload={PAYLOAD} />);
    expect(screen.getByTestId("financial-backtest-card")).toBeInTheDocument();
    expect(screen.getByText("pe_ratio < 15 AND roe > 18")).toBeInTheDocument();
    expect(screen.getByText(/2020-01-01 → 2024-12-31/)).toBeInTheDocument();
    expect(screen.getByText(/rebalance Q/)).toBeInTheDocument();
  });

  it("displays formatted total return prominently", () => {
    render(<FinancialBacktestCard payload={PAYLOAD} />);
    expect(screen.getByText(/\+96\.40%/)).toBeInTheDocument();
    expect(screen.getByText(/Total return/i)).toBeInTheDocument();
  });

  it("renders the metrics strip with all 6 fields", () => {
    render(<FinancialBacktestCard payload={PAYLOAD} />);
    const strip = screen.getByTestId("financial-backtest-metrics");
    expect(strip).toBeInTheDocument();
    expect(strip).toHaveTextContent(/CAGR/);
    expect(strip).toHaveTextContent(/Max DD/);
    expect(strip).toHaveTextContent(/Sharpe/);
    expect(strip).toHaveTextContent(/Hit rate/);
    expect(strip).toHaveTextContent(/Trades/);
    expect(strip).toHaveTextContent(/Rebalances/);
  });

  it("formats numeric metrics correctly", () => {
    render(<FinancialBacktestCard payload={PAYLOAD} />);
    expect(screen.getByText("+14.20%")).toBeInTheDocument(); // CAGR
    expect(screen.getByText("18.4%")).toBeInTheDocument(); // Max DD
    expect(screen.getByText("0.92")).toBeInTheDocument(); // Sharpe
    expect(screen.getByText("58%")).toBeInTheDocument(); // Hit rate
    expect(screen.getByText("87")).toBeInTheDocument(); // Trades
    expect(screen.getByText("3")).toBeInTheDocument(); // Rebalances
  });

  it("renders the equity-vs-benchmark chart", () => {
    render(<FinancialBacktestCard payload={PAYLOAD} />);
    expect(screen.getByTestId("financial-equity-chart")).toBeInTheDocument();
    expect(screen.getByText("Strategy")).toBeInTheDocument();
    expect(screen.getByText("Benchmark")).toBeInTheDocument();
  });

  it("renders warnings panel when warnings are present", () => {
    const withWarnings: FinancialBacktestPayload = {
      ...PAYLOAD,
      warnings: ["Fewer than 5 trades — metrics may be unreliable"],
    };
    render(<FinancialBacktestCard payload={withWarnings} />);
    expect(
      screen.getByTestId("financial-backtest-warnings"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Fewer than 5 trades/),
    ).toBeInTheDocument();
  });

  it("hides warnings panel when warnings are empty", () => {
    render(<FinancialBacktestCard payload={PAYLOAD} />);
    expect(
      screen.queryByTestId("financial-backtest-warnings"),
    ).not.toBeInTheDocument();
  });

  it("handles a losing strategy with negative total return", () => {
    const losing: FinancialBacktestPayload = {
      ...PAYLOAD,
      metrics: {
        ...PAYLOAD.metrics,
        total_return_pct: -12.5,
        cagr_pct: -2.7,
      },
    };
    render(<FinancialBacktestCard payload={losing} />);
    expect(screen.getByText("-12.50%")).toBeInTheDocument();
  });

  it("handles null Sharpe / hit_rate gracefully", () => {
    const nullMetrics: FinancialBacktestPayload = {
      ...PAYLOAD,
      metrics: {
        ...PAYLOAD.metrics,
        sharpe: null,
        hit_rate_pct: null,
      },
    };
    render(<FinancialBacktestCard payload={nullMetrics} />);
    // Both null fields should render as em-dash, not "null" or NaN
    const strip = screen.getByTestId("financial-backtest-metrics");
    expect(strip).toHaveTextContent("—");
  });
});
