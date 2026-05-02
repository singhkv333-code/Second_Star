/**
 * IndicatorBacktestCard — inline chat card for single-symbol indicator
 * backtest results.
 *
 * Rendered when chat returns raw_data._render_hint === "indicator_backtest_chart".
 * Two stacked Recharts charts: top is price + buy/sell markers, bottom
 * is the indicator series with its threshold line. Metrics row at top.
 */
"use client";

import * as React from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export type IndicatorBacktestPayload = {
  symbol: string;
  indicator: "rsi" | "sma" | "ema";
  indicator_period: number;
  operator: string;
  threshold: number;
  period_label: string;
  price_curve: Array<{ t: string; v: number }>;
  equity_curve: Array<{ t: string; v: number }>;
  indicator_curve: Array<{ t: string; v: number }>;
  signals: Array<{ t: string; side: "buy" | "sell"; price: number; indicator_value: number | null }>;
  metrics: {
    total_return_pct: number;
    cagr_pct: number;
    max_drawdown_pct: number;
    hit_rate_pct: number;
    n_trades: number;
    n_wins: number;
    starting_capital: number;
    ending_value: number;
  };
  bench_buy_hold_return_pct: number;
};

type Props = { payload: IndicatorBacktestPayload };

const fmtINR = (n: number): string =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(n);

const fmtPct = (n: number): string =>
  `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

const opLabel = (op: string): string => {
  switch (op) {
    case "<":
      return "drops below";
    case ">":
      return "rises above";
    case "crosses_below":
      return "crosses below";
    case "crosses_above":
      return "crosses above";
    default:
      return op;
  }
};

export function IndicatorBacktestCard({ payload }: Props): React.ReactElement {
  const {
    symbol,
    indicator,
    indicator_period,
    operator,
    threshold,
    period_label,
    price_curve,
    equity_curve,
    indicator_curve,
    signals,
    metrics,
    bench_buy_hold_return_pct,
  } = payload;

  // Pre-process for Recharts: index by date so dots align across charts.
  const priceData = React.useMemo(
    () =>
      price_curve.map((p) => ({
        date: p.t.slice(0, 10),
        price: p.v,
      })),
    [price_curve],
  );
  const equityData = React.useMemo(
    () =>
      equity_curve.map((p) => ({
        date: p.t.slice(0, 10),
        equity: p.v,
      })),
    [equity_curve],
  );
  const indicatorData = React.useMemo(
    () =>
      indicator_curve.map((p) => ({
        date: p.t.slice(0, 10),
        value: p.v,
      })),
    [indicator_curve],
  );

  const buys = signals.filter((s) => s.side === "buy");
  const sells = signals.filter((s) => s.side === "sell");

  const stratColor =
    metrics.total_return_pct >= 0 ? "rgb(16 185 129)" : "rgb(244 63 94)";
  const benchColor = "rgb(148 163 184)"; // slate-400

  // For SMA/EMA, "threshold" carries the period (50, 200), not a value.
  // The chart should show the indicator series itself as the reference,
  // not a horizontal line. RSI uses a flat horizontal threshold line.
  const isFlatThreshold = indicator === "rsi";

  const conditionLabel =
    indicator === "rsi"
      ? `RSI(${indicator_period}) ${opLabel(operator)} ${threshold}`
      : `Price ${opLabel(operator)} ${indicator.toUpperCase()}(${indicator_period})`;

  return (
    <div
      className="rounded-xl border bg-card p-4 shadow-sm"
      data-testid="indicator-backtest-card"
    >
      {/* Header */}
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="text-base font-semibold">{symbol}</div>
          <div className="text-xs text-muted-foreground">
            {conditionLabel} · {period_label}
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
            Strategy total return
          </div>
        </div>
      </div>

      {/* Metrics strip */}
      <div className="mb-4 grid grid-cols-3 gap-2 sm:grid-cols-6">
        <Metric label="CAGR" value={fmtPct(metrics.cagr_pct)} />
        <Metric label="Max DD" value={`${metrics.max_drawdown_pct.toFixed(1)}%`} />
        <Metric label="Trades" value={String(metrics.n_trades)} />
        <Metric label="Hit rate" value={`${metrics.hit_rate_pct.toFixed(0)}%`} />
        <Metric label="End value" value={fmtINR(metrics.ending_value)} />
        <Metric
          label={`${symbol} buy & hold`}
          value={fmtPct(bench_buy_hold_return_pct)}
        />
      </div>

      {/* Price chart with buy/sell markers */}
      <div className="mb-3">
        <div className="mb-1 text-xs font-medium text-muted-foreground">
          Price + signals
        </div>
        <div className="h-[200px]" data-testid="price-chart">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={priceData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="2 4" opacity={0.3} />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={50} />
              <YAxis
                tick={{ fontSize: 10 }}
                domain={["auto", "auto"]}
                tickFormatter={(v) => `₹${Number(v).toFixed(0)}`}
                width={55}
              />
              <Tooltip
                contentStyle={{ fontSize: 11 }}
                formatter={(v: number) => [`₹${v.toFixed(2)}`, "Price"]}
              />
              <Line
                type="monotone"
                dataKey="price"
                stroke="rgb(99 102 241)"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
              {buys.map((s) => (
                <ReferenceDot
                  key={`b-${s.t}`}
                  x={s.t.slice(0, 10)}
                  y={s.price}
                  r={4}
                  fill="rgb(16 185 129)"
                  stroke="white"
                  strokeWidth={1}
                />
              ))}
              {sells.map((s) => (
                <ReferenceDot
                  key={`s-${s.t}`}
                  x={s.t.slice(0, 10)}
                  y={s.price}
                  r={4}
                  fill="rgb(244 63 94)"
                  stroke="white"
                  strokeWidth={1}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-1 flex items-center gap-3 text-[10px] text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-emerald-500" /> Buy ({buys.length})
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-rose-500" /> Sell ({sells.length})
          </span>
        </div>
      </div>

      {/* Indicator chart with threshold line */}
      <div className="mb-3">
        <div className="mb-1 text-xs font-medium text-muted-foreground">
          {indicator.toUpperCase()}({indicator_period})
        </div>
        <div className="h-[120px]" data-testid="indicator-chart">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={indicatorData} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="2 4" opacity={0.3} />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={50} />
              <YAxis tick={{ fontSize: 10 }} width={45} />
              <Tooltip
                contentStyle={{ fontSize: 11 }}
                formatter={(v: number) => [v.toFixed(2), indicator.toUpperCase()]}
              />
              <Line
                type="monotone"
                dataKey="value"
                stroke="rgb(168 85 247)"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
              {isFlatThreshold && (
                <ReferenceLine
                  y={threshold}
                  stroke="rgb(244 63 94)"
                  strokeDasharray="4 4"
                  label={{
                    value: `${threshold}`,
                    position: "right",
                    fontSize: 10,
                    fill: "rgb(244 63 94)",
                  }}
                />
              )}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Equity curve */}
      <div>
        <div className="mb-1 text-xs font-medium text-muted-foreground">
          Strategy equity vs starting capital
        </div>
        <div className="h-[120px]" data-testid="equity-chart">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={equityData} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="eq-grad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={stratColor} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={stratColor} stopOpacity={0.05} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="2 4" opacity={0.3} />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={50} />
              <YAxis
                tick={{ fontSize: 10 }}
                tickFormatter={(v) => `₹${(Number(v) / 1_00_000).toFixed(0)}L`}
                width={55}
              />
              <Tooltip
                contentStyle={{ fontSize: 11 }}
                formatter={(v: number) => [fmtINR(v), "Equity"]}
              />
              <ReferenceLine
                y={metrics.starting_capital}
                stroke={benchColor}
                strokeDasharray="4 4"
                label={{
                  value: "start",
                  position: "right",
                  fontSize: 10,
                  fill: benchColor,
                }}
              />
              <Area
                type="monotone"
                dataKey="equity"
                stroke={stratColor}
                strokeWidth={1.5}
                fill="url(#eq-grad)"
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      <p className="mt-3 text-[10px] text-muted-foreground">
        Past performance does not guarantee future results. This is automation
        of your instructions, not financial advice.
      </p>
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
    <div className="rounded-md border bg-background p-2">
      <div className="text-[9px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-0.5 text-xs font-semibold tabular-nums">{value}</div>
    </div>
  );
}

