/**
 * BacktestTab — Phase 2 smoke tests.
 * Covers: renders builder, field chips load, run button, error state.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BacktestTab } from "@/components/BacktestTab";
import * as api from "@/lib/api";

const MOCK_FIELDS: api.BacktestFieldsResponse = {
  base_fields: [
    { name: "pe_ratio", kind: "base", description: "P/E ratio", unit: "x", statement: "income", ttm_eligible: false },
    { name: "roe", kind: "base", description: "Return on equity", unit: "%", statement: "income", ttm_eligible: true },
  ],
  computed_fields: [
    { name: "fcf_yield", kind: "computed", description: "FCF yield", unit: "%", expr: "fcf / market_cap" },
  ],
  specials: ["price"],
  ttm_suffix_note: "Append _ttm to TTM-eligible fields.",
};

const MOCK_RESULT: api.BacktestResult = {
  expression: "pe_ratio < 15",
  start: "2018-01-01",
  end: "2024-12-31",
  rebalance: "Q",
  metrics: {
    cagr_pct: 14.5,
    sharpe: 1.2,
    max_drawdown_pct: 22.3,
    calmar: 0.65,
    turnover_pct: 80,
    hit_rate_pct: 54,
    n_unique_companies: 42,
    total_return_pct: 185,
  },
  equity_curve: [
    { date: "2018-01-01", value: 1000000 },
    { date: "2024-12-31", value: 2850000 },
  ],
  benchmark_curve: [
    { date: "2018-01-01", value: 1000000 },
    { date: "2024-12-31", value: 1800000 },
  ],
  rebalances: [
    {
      date: "2018-03-31",
      entered: [{ symbol: "RELIANCE", weight: 0.05 }],
      exited: [],
    },
  ],
  n_trades: 120,
  universe_audit: [
    { name: "Reliance Industries", sc_id: "500325", pe_ratio: 12.4, roe: 9.1 },
  ],
  leaf_fields: ["pe_ratio", "roe"],
  referenced_fields: ["pe_ratio", "roe"],
  warnings: [],
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("BacktestTab", () => {
  it("renders the expression textarea and run button", async () => {
    vi.spyOn(api, "getBacktestFields").mockResolvedValue({ data: MOCK_FIELDS });
    render(<BacktestTab />);
    expect(screen.getByTestId("backtest-tab")).toBeInTheDocument();
    expect(screen.getByTestId("bt-expr-input")).toBeInTheDocument();
    expect(screen.getByTestId("bt-run-btn")).toBeInTheDocument();
  });

  it("loads field chips from API and renders them", async () => {
    vi.spyOn(api, "getBacktestFields").mockResolvedValue({ data: MOCK_FIELDS });
    render(<BacktestTab />);
    await waitFor(() => {
      expect(screen.getByTestId("field-chip-pe_ratio")).toBeInTheDocument();
      expect(screen.getByTestId("field-chip-roe")).toBeInTheDocument();
      expect(screen.getByTestId("field-chip-fcf_yield")).toBeInTheDocument();
    });
  });

  it("clicking a field chip appends it to the expression", async () => {
    vi.spyOn(api, "getBacktestFields").mockResolvedValue({ data: MOCK_FIELDS });
    render(<BacktestTab />);
    await waitFor(() => screen.getByTestId("field-chip-pe_ratio"));
    fireEvent.click(screen.getByTestId("field-chip-pe_ratio"));
    const input = screen.getByTestId("bt-expr-input") as HTMLTextAreaElement;
    expect(input.value).toBe("pe_ratio");
  });

  it("run button is disabled when expression is empty", async () => {
    vi.spyOn(api, "getBacktestFields").mockResolvedValue({ data: MOCK_FIELDS });
    render(<BacktestTab />);
    expect(screen.getByTestId("bt-run-btn")).toBeDisabled();
  });

  it("shows results after successful run", async () => {
    vi.spyOn(api, "getBacktestFields").mockResolvedValue({ data: MOCK_FIELDS });
    vi.spyOn(api, "runBacktest").mockResolvedValue({ data: MOCK_RESULT });
    render(<BacktestTab />);

    fireEvent.change(screen.getByTestId("bt-expr-input"), {
      target: { value: "pe_ratio < 15" },
    });
    fireEvent.click(screen.getByTestId("bt-run-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("bt-results")).toBeInTheDocument();
    });
    expect(screen.getByTestId("equity-curve-chart")).toBeInTheDocument();
    expect(screen.getByTestId("drawdown-chart")).toBeInTheDocument();
    expect(screen.getByTestId("metrics-row")).toBeInTheDocument();
    expect(screen.getByTestId("rebalance-log")).toBeInTheDocument();
    expect(screen.getByTestId("audit-appendix")).toBeInTheDocument();
  });

  it("shows error state when runBacktest fails", async () => {
    vi.spyOn(api, "getBacktestFields").mockResolvedValue({ data: MOCK_FIELDS });
    vi.spyOn(api, "runBacktest").mockResolvedValue({
      error: { code: "validation_error", message: "Invalid expression" },
    });
    render(<BacktestTab />);

    fireEvent.change(screen.getByTestId("bt-expr-input"), {
      target: { value: "bad ~~~ expr" },
    });
    fireEvent.click(screen.getByTestId("bt-run-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("bt-error")).toBeInTheDocument();
      expect(screen.getByText("Invalid expression")).toBeInTheDocument();
    });
  });
});
