"use client";

/**
 * StrategyLineChart — the PRIMARY "Strategy vs Nifty" chart on a View detail /
 * expression detail. A real recharts series whose x-axis is the SEQUENTIAL
 * in-position trading-day index ("days in market"), NOT calendar time: the
 * strategy is only deployed during event/season windows, so the curve is the
 * EPISODE-GATED, in-position concatenated path (its endpoint equals the headline
 * return). Calendar time has gaps between episodes, so we never draw a date axis.
 *   - Strategy line  → SOLID, var(--pivot-blue)
 *   - Nifty 50 line  → DASHED, var(--text-tertiary) gray
 *   - optional extra compared strategies → solid, muted accent palette
 *   - faint vertical separators at each new episode's first in-market day
 *
 * v2 design language: ROUNDED container, calm, no gridline clutter, tabular
 * numerals, axis labels >= 13px, a small inline legend. Empty / too-short series
 * renders an HONEST "chart unavailable" card — never a fabricated line.
 *
 * Works light + dark via useTokenColors (re-reads on .dark class toggle).
 */

import * as React from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useTokenColors } from "../use-token-color";

/** One point on an equity curve, as served by the views API. */
export type EquityPoint = {
  t: string;
  strategy: number;
  benchmark: number;
};

/** An additional named strategy line to overlay for comparison. */
export type CompareSeries = {
  label: string;
  series: EquityPoint[];
};

const MUTED_ACCENTS = [
  "var(--pivot-blue)",
  "#8b5cf6",
  "#0ea5e9",
  "#f59e0b",
];

const STRATEGY_KEY = "strategy";
const BENCH_KEY = "benchmark";
const compareKey = (i: number): string => `cmp_${i}`;

const accentFor = (i: number): string =>
  MUTED_ACCENTS[(i + 1) % MUTED_ACCENTS.length] ?? "var(--pivot-blue)";

type Row = Record<string, number | string>;

/** Merge the strategy/benchmark series + any compare series into one
 * recharts-ready row array keyed by timestamp. */
function buildRows(
  series: EquityPoint[],
  compareSeries: CompareSeries[],
): Row[] {
  const byT = new Map<string, Row>();
  for (const p of series) {
    byT.set(p.t, { t: p.t, [STRATEGY_KEY]: p.strategy, [BENCH_KEY]: p.benchmark });
  }
  compareSeries.forEach((cs, i) => {
    for (const p of cs.series) {
      const row = byT.get(p.t) ?? { t: p.t };
      row[compareKey(i)] = p.strategy;
      byT.set(p.t, row);
    }
  });
  return Array.from(byT.values()).sort((a, b) => {
    // The x key is the in-position index ("0","1",…,"10"…) — sort NUMERICALLY so
    // "10" doesn't land before "2". Fall back to string order for any non-index t.
    const na = Number(a.t);
    const nb = Number(b.t);
    if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
    return String(a.t).localeCompare(String(b.t));
  });
}

function fmtDayLabel(t: string): string {
  // x is the in-position trading-day index; show it as "Day N" in the tooltip.
  const n = Number(t);
  return Number.isFinite(n) ? `Day ${n}` : t;
}

function fmtCurrency(v: number): string {
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

function LegendDot({
  color,
  dashed,
  label,
  secondary,
}: {
  color: string;
  dashed?: boolean;
  label: string;
  secondary: string;
}): React.ReactElement {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 7,
        fontFamily: "var(--font-display)",
        fontSize: 13,
        fontWeight: 500,
        color: secondary,
        whiteSpace: "nowrap",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 18,
          height: 0,
          borderTop: dashed
            ? `2px dashed ${color}`
            : `2.5px solid ${color}`,
          borderRadius: 2,
        }}
      />
      {label}
    </span>
  );
}

export function StrategyLineChart({
  series,
  compareSeries = [],
  benchmarkLabel = "Nifty 50",
  strategyLabel = "Strategy",
  episodeBoundaries = [],
  height = 240,
}: {
  series?: EquityPoint[] | null;
  compareSeries?: CompareSeries[];
  benchmarkLabel?: string;
  strategyLabel?: string;
  /** In-position indices where each new episode starts (faint stitch markers). */
  episodeBoundaries?: number[];
  height?: number;
}): React.ReactElement {
  const c = useTokenColors({
    blue: "--pivot-blue",
    tertiary: "--text-tertiary",
    secondary: "--text-secondary",
    border: "--glass-border",
    bg: "--bg-base",
    ink: "--text-primary",
  });

  const safeSeries = Array.isArray(series) ? series : [];
  const validCompare = (compareSeries ?? []).filter(
    (cs) => Array.isArray(cs.series) && cs.series.length >= 2,
  );

  // Honest empty / too-short state — never draw a fabricated line.
  if (safeSeries.length < 2) {
    return (
      <div
        style={{
          border: `1px solid ${c.border}`,
          borderRadius: 16,
          padding: "28px 20px",
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: c.bg,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 15,
            color: c.tertiary,
          }}
        >
          Chart unavailable
        </span>
      </div>
    );
  }

  const rows = buildRows(safeSeries, validCompare);

  return (
    <div
      style={{
        border: `1px solid ${c.border}`,
        borderRadius: 16,
        padding: "16px 12px 8px",
        background: c.bg,
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div style={{ width: "100%", height }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
            <CartesianGrid
              stroke={c.border}
              strokeOpacity={0.5}
              horizontal
              vertical={false}
            />
            {episodeBoundaries
              .filter((b) => b > 0)
              .map((b) => (
                <ReferenceLine
                  key={`ep-${b}`}
                  x={String(b)}
                  stroke={c.border}
                  strokeOpacity={0.55}
                  strokeDasharray="2 4"
                  ifOverflow="hidden"
                />
              ))}
            <XAxis
              dataKey="t"
              tick={false}
              tickLine={false}
              axisLine={{ stroke: c.border }}
              height={22}
              label={{
                value: "Days in market →",
                position: "insideBottom",
                offset: 2,
                style: {
                  fontSize: 12,
                  fill: c.tertiary,
                  fontFamily: "var(--font-display)",
                },
              }}
            />
            <YAxis
              tick={{
                fontSize: 13,
                fill: c.tertiary,
                fontFamily: "var(--font-display)",
              }}
              tickFormatter={(v: number) =>
                `₹${(v / 1000).toFixed(0)}k`
              }
              tickLine={false}
              axisLine={false}
              width={48}
              domain={["auto", "auto"]}
            />
            <Tooltip
              contentStyle={{
                borderRadius: 12,
                border: `1px solid ${c.border}`,
                background: c.bg,
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fontVariantNumeric: "tabular-nums",
                color: c.ink,
              }}
              labelFormatter={(t) => fmtDayLabel(String(t))}
              formatter={(value: number, name: string) => {
                let label = name;
                if (name === STRATEGY_KEY) label = strategyLabel;
                else if (name === BENCH_KEY) label = benchmarkLabel;
                else {
                  const idx = Number(name.replace("cmp_", ""));
                  label = validCompare[idx]?.label ?? name;
                }
                return [fmtCurrency(value), label];
              }}
              cursor={{ stroke: c.border, strokeWidth: 1 }}
            />
            <Line
              type="monotone"
              dataKey={BENCH_KEY}
              stroke={c.tertiary}
              strokeWidth={1.75}
              strokeDasharray="5 4"
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
            {validCompare.map((cs, i) => (
              <Line
                key={compareKey(i)}
                type="monotone"
                dataKey={compareKey(i)}
                stroke={accentFor(i)}
                strokeWidth={1.75}
                strokeOpacity={0.7}
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            ))}
            <Line
              type="monotone"
              dataKey={STRATEGY_KEY}
              stroke={c.blue}
              strokeWidth={2.5}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "8px 16px",
          paddingLeft: 8,
        }}
      >
        <LegendDot color={c.blue} label={strategyLabel} secondary={c.secondary} />
        <LegendDot
          color={c.tertiary}
          dashed
          label={benchmarkLabel}
          secondary={c.secondary}
        />
        {validCompare.map((cs, i) => (
          <LegendDot
            key={cs.label}
            color={accentFor(i)}
            label={cs.label}
            secondary={c.secondary}
          />
        ))}
      </div>
    </div>
  );
}

export default StrategyLineChart;
