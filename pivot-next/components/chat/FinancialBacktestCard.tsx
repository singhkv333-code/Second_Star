"use client";

/**
 * FinancialBacktestCard — chat-side render for the SQL fundamentals
 * backtester (`POST /api/backtest/expr/run`). Mounted by ChatDemo when
 * `raw_data._render_hint === "financial_backtest_chart"` after an NL
 * prompt like "backtest pe_ratio < 15 from 2020-01-01 to 2024-12-31".
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
import { AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types — must match backend/routers/chat.py::_run_expr_backtest::raw_data
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

const SANS_FONT =
  "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Inter, Roboto, sans-serif";

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

  const positive = metrics.total_return_pct >= 0;
  const stratColor = positive ? "#10b981" : "#ef4444";
  const benchColor = "#9ca3af";

  return (
    <div
      className="w-full max-w-2xl overflow-hidden rounded-xl border border-border/60 bg-card shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
      style={{ fontFamily: SANS_FONT }}
      data-testid="financial-backtest-card"
    >
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-5 pb-4">
        <div className="min-w-0 flex-1">
          <span className="inline-flex items-center rounded-md bg-foreground/[0.04] px-1.5 py-0.5 text-[10px] font-medium tracking-tight text-muted-foreground">
            Fundamentals backtest
          </span>
          <code
            className="mt-2 block truncate font-mono text-[12.5px] text-foreground bg-muted/40 rounded-md px-2 py-1.5 border border-border/50"
            title={expression}
          >
            {expression}
          </code>
          <p className="mt-2 text-[11.5px] text-muted-foreground tabular-nums">
            {start} → {end} · rebalance {rebalance}
          </p>
        </div>
        <div className="text-right shrink-0">
          <div
            className="text-[26px] leading-none font-semibold tabular-nums tracking-[-0.02em]"
            style={{ color: stratColor }}
          >
            {fmtPct(metrics.total_return_pct)}
          </div>
          <div className="mt-1.5 text-[10px] font-medium tracking-[0.06em] uppercase text-muted-foreground/80">
            Total return
          </div>
        </div>
      </div>

      {/* Metrics strip */}
      <div
        className="grid grid-cols-3 border-y border-border/50 sm:grid-cols-6"
        data-testid="financial-backtest-metrics"
      >
        <Metric label="CAGR" value={fmtPct(metrics.cagr_pct)} />
        <Metric label="Max DD" value={`${metrics.max_drawdown_pct.toFixed(1)}%`} />
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
        <Metric label="Rebalances" value={String(rebalances.length)} last />
      </div>

      {/* Equity curve */}
      <div className="px-5 py-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-[10px] font-medium tracking-[0.06em] uppercase text-muted-foreground/80">
            Equity curve
          </span>
          <span className="flex items-center gap-3 text-[10.5px]">
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
              <CartesianGrid strokeDasharray="2 4" opacity={0.18} vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: "rgb(107 114 128)" }}
                axisLine={false}
                tickLine={false}
                minTickGap={50}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "rgb(107 114 128)" }}
                axisLine={false}
                tickLine={false}
                width={60}
              />
              <Tooltip
                contentStyle={{
                  fontSize: "11px",
                  borderRadius: "8px",
                  padding: "6px 10px",
                  border: "1px solid rgba(0,0,0,0.08)",
                  fontFamily: SANS_FONT,
                }}
              />
              <Area
                type="monotone"
                dataKey="strategy"
                stroke={stratColor}
                fill={stratColor}
                fillOpacity={0.1}
                strokeWidth={1.75}
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
          className="border-t border-amber-500/20 bg-amber-50/60 dark:bg-amber-500/[0.06] px-5 py-3 space-y-1.5"
          data-testid="financial-backtest-warnings"
        >
          {warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-2">
              <AlertCircle
                className="mt-0.5 h-3 w-3 shrink-0 text-amber-600 dark:text-amber-400"
                aria-hidden="true"
              />
              <p className="text-[11px] leading-relaxed text-amber-900 dark:text-amber-300">
                {w}
              </p>
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
  last,
}: {
  label: string;
  value: string;
  last?: boolean;
}): React.ReactElement {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 px-3 py-3",
        !last && "border-r border-border/50",
      )}
    >
      <div className="text-[10px] font-medium tracking-tight text-muted-foreground">
        {label}
      </div>
      <div className="text-[13px] font-semibold tabular-nums text-foreground tracking-tight">
        {value}
      </div>
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
    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
      <span
        className="inline-block h-1.5 w-3 rounded-sm"
        style={{ background: color }}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}
