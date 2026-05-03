"use client";

/**
 * FinancialBacktestCard — chat-side render for the SQL fundamentals
 * backtester (`POST /api/backtest/expr/run`). Mounted by ChatDemo when
 * `raw_data._render_hint === "financial_backtest_chart"` after an NL
 * prompt like "backtest pe_ratio < 15 from 2020-01-01 to 2024-12-31".
 *
 * Shape mirrors what the dedicated BacktestTab renders, but compact —
 * we only show:
 *   - header: expression + period + rebalance
 *   - metrics strip: total return, CAGR, max DD, Sharpe, n_trades, rebalance count
 *   - equity curve overlaid with benchmark
 *   - warnings (if any)
 *
 * Trade list / per-rebalance details stay in the BacktestTab; this card
 * is meant to be glanceable inside the chat thread.
 */

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AlertCircle, BarChart3 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types — must match the dict shape built in
// backend/routers/chat.py::_run_expr_backtest::raw_data
// ---------------------------------------------------------------------------

export type FinancialBacktestPoint = { date: string; value: number };

export type FinancialBacktestRebalance = {
  date: string;
  entered: Array<{ symbol: string; weight: number }>;
  exited: Array<{ symbol: string }>;
};

export type FinancialBacktestPayload = {
  expression: string;
  start: string;
  end: string;
  rebalance: string;
  metrics: {
    cagr_pct: number;
    sharpe: number | null;
    max_drawdown_pct: number;
    calmar?: number | null;
    turnover_pct?: number | null;
    hit_rate_pct?: number | null;
    n_unique_companies?: number | null;
    total_return_pct: number;
  };
  equity_curve: FinancialBacktestPoint[];
  benchmark_curve: FinancialBacktestPoint[];
  rebalances: FinancialBacktestRebalance[];
  n_trades: number;
  warnings: string[];
};

const fmtPct = (n: number): string => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

const fmtMaybe = (n: number | null | undefined, suffix = ""): string =>
  n === null || n === undefined ? "—" : `${n.toFixed(2)}${suffix}`;

export function FinancialBacktestCard({
  payload,
}: {
  payload: FinancialBacktestPayload;
}): React.ReactElement {
  const {
    expression,
    start,
    end,
    rebalance,
    metrics,
    equity_curve,
    benchmark_curve,
    rebalances,
    n_trades,
    warnings,
  } = payload;

  // Merge strategy + benchmark into a single recharts-friendly array
  // keyed by date. Both curves should be aligned by the backend, but we
  // tolerate misalignment by indexing.
  const chartData = useMemo(() => {
    const map = new Map<string, { date: string; strategy?: number; benchmark?: number }>();
    for (const p of equity_curve) {
      map.set(p.date, { date: p.date, strategy: p.value });
    }
    for (const p of benchmark_curve) {
      const existing = map.get(p.date);
      if (existing) {
        existing.benchmark = p.value;
      } else {
        map.set(p.date, { date: p.date, benchmark: p.value });
      }
    }
    return Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date));
  }, [equity_curve, benchmark_curve]);

  const stratColor = metrics.total_return_pct >= 0 ? "#10b981" : "#ef4444";
  const benchColor = "#6b7280";

  return (
    <div
      className="rounded-xl border bg-card p-4 shadow-sm w-full max-w-2xl"
      data-testid="financial-backtest-card"
    >
      {/* Header */}
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <BarChart3
              className="h-4 w-4 text-muted-foreground"
              aria-hidden="true"
            />
            <Badge
              variant="outline"
              className="text-[10px] px-1.5 py-0 uppercase tracking-wide"
            >
              Fundamentals backtest
            </Badge>
          </div>
          <code
            className="mt-1 block truncate rounded-md bg-muted px-2 py-0.5 text-[11px] font-mono"
            title={expression}
          >
            {expression}
          </code>
          <div className="mt-1 text-[11px] text-muted-foreground">
            {start} → {end} · rebalance {rebalance}
          </div>
        </div>
        <div className="text-right">
          <div
            className="text-xl font-semibold tabular-nums"
            style={{ color: stratColor }}
          >
            {fmtPct(metrics.total_return_pct)}
          </div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Total return
          </div>
        </div>
      </div>

      {/* Metrics strip */}
      <div
        className="mb-4 grid grid-cols-3 gap-2 sm:grid-cols-6"
        data-testid="financial-backtest-metrics"
      >
        <Metric label="CAGR" value={fmtPct(metrics.cagr_pct)} />
        <Metric
          label="Max DD"
          value={`${metrics.max_drawdown_pct.toFixed(1)}%`}
        />
        <Metric label="Sharpe" value={fmtMaybe(metrics.sharpe)} />
        <Metric
          label="Hit rate"
          value={
            metrics.hit_rate_pct === null || metrics.hit_rate_pct === undefined
              ? "—"
              : `${metrics.hit_rate_pct.toFixed(0)}%`
          }
        />
        <Metric label="Trades" value={String(n_trades)} />
        <Metric label="Rebalances" value={String(rebalances.length)} />
      </div>

      {/* Equity curve overlaid with benchmark */}
      <div className="mb-3">
        <div className="mb-1 flex items-center justify-between text-xs">
          <span className="font-medium text-muted-foreground">
            Equity curve
          </span>
          <span className="flex items-center gap-3 text-[10px]">
            <LegendDot color={stratColor} label="Strategy" />
            <LegendDot color={benchColor} label="Benchmark" />
          </span>
        </div>
        <div className="h-[200px]" data-testid="financial-equity-chart">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={chartData}
              margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="2 4" opacity={0.3} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10 }}
                minTickGap={50}
              />
              <YAxis tick={{ fontSize: 10 }} width={60} />
              <Tooltip
                contentStyle={{
                  fontSize: "11px",
                  borderRadius: "6px",
                  padding: "4px 8px",
                }}
              />
              <Area
                type="monotone"
                dataKey="strategy"
                stroke={stratColor}
                fill={stratColor}
                fillOpacity={0.15}
                strokeWidth={1.5}
                isAnimationActive={false}
                connectNulls
                name="Strategy"
              />
              <Line
                type="monotone"
                dataKey="benchmark"
                stroke={benchColor}
                strokeWidth={1}
                strokeDasharray="3 3"
                dot={false}
                isAnimationActive={false}
                connectNulls
                name="Benchmark"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Warnings */}
      {warnings.length > 0 && (
        <div
          className="rounded-md bg-warning/5 border border-warning/30 px-3 py-2 space-y-1"
          data-testid="financial-backtest-warnings"
        >
          {warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-1.5">
              <AlertCircle
                className="mt-0.5 h-3 w-3 shrink-0 text-warning"
                aria-hidden="true"
              />
              <p className="text-[11px] text-warning">{w}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
}: {
  label: string;
  value: string;
}): React.ReactElement {
  return (
    <div className="rounded-md border bg-card px-2 py-1.5">
      <div className="text-[9px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="text-xs font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function LegendDot({
  color,
  label,
}: {
  color: string;
  label: string;
}): React.ReactElement {
  return (
    <span className="inline-flex items-center gap-1 text-muted-foreground">
      <span
        className={cn("inline-block h-1.5 w-3 rounded-sm")}
        style={{ background: color }}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}
