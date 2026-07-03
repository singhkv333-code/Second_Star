"use client";

/**
 * MonteCarloDistribution — the spread of simulated TERMINAL outcomes when the
 * strategy is re-run on resampled history many times. Drawn as a calm density
 * area: a faint red "loss zone" left of 0, the median marked solid, the 5th and
 * 95th percentiles dashed. A plain caption translates it for a layman:
 *
 *   "If we re-ran this on resampled history N times — middle outcome +X%,
 *    worst 5% −Y%, chance of a loss Z%."
 *
 * No jargon (no "p05 / VaR / percentile" on screen — translated to words). The
 * markers use the API's own p05 / median / p95, never recomputed, so we never
 * fabricate a number. Empty / null → renders nothing (the caller omits it).
 *
 * When the curve is on the underlying (option structures), an honest sub-line
 * names that these are the underlying's outcomes, not the option's own P&L.
 *
 * DESIGN LAW: rounded, border-only, every label >= 13px, tabular numerals,
 * light + dark via useTokenColors.
 */

import * as React from "react";
import {
  Area,
  AreaChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
} from "recharts";
import { useTokenColors } from "../use-token-color";

/** Monte-Carlo block of an expression, as served by the views API. */
export type MonteCarlo = {
  n_sims: number;
  terminal_pct: number[];
  p05: number;
  p25: number;
  median: number;
  p75: number;
  p95: number;
  prob_loss: number;
  basis?: string | null;
};

function signed(v: number, dp = 0): string {
  const s = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${s}${Math.abs(v).toFixed(dp)}%`;
}

/** Bin terminal outcomes into a smooth-ish density for the area chart. */
function bin(values: number[], bins: number): Array<{ x: number; n: number }> {
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (max === min) return [{ x: min, n: values.length }];
  const w = (max - min) / bins;
  const counts = new Array(bins).fill(0);
  for (const v of values) {
    const idx = Math.min(bins - 1, Math.floor((v - min) / w));
    counts[idx] += 1;
  }
  return counts.map((n, i) => ({ x: min + (i + 0.5) * w, n }));
}

export function MonteCarloDistribution({
  mc,
  underlyingSymbol,
  height = 150,
}: {
  mc?: MonteCarlo | null;
  underlyingSymbol?: string | null;
  height?: number;
}): React.ReactElement | null {
  const c = useTokenColors({
    blue: "--pivot-blue",
    loss: "--color-loss",
    ink: "--text-primary",
    secondary: "--text-secondary",
    tertiary: "--text-tertiary",
    border: "--glass-border",
    bgBase: "--bg-base",
  });

  const id = React.useId();

  const values = Array.isArray(mc?.terminal_pct)
    ? mc!.terminal_pct.filter((v) => typeof v === "number")
    : [];
  if (!mc || values.length < 5) return null;

  const data = bin(values, Math.min(28, Math.max(10, Math.round(values.length / 5))));
  const dataMin = data[0]!.x;
  const dataMax = data[data.length - 1]!.x;
  const probLossPct = mc.prob_loss * 100;
  const isUnderlying = mc.basis === "underlying";

  const gradId = `mc-grad-${id}`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={data}
            margin={{ top: 14, right: 8, bottom: 2, left: 8 }}
          >
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={c.blue} stopOpacity={0.32} />
                <stop offset="100%" stopColor={c.blue} stopOpacity={0.04} />
              </linearGradient>
            </defs>

            <XAxis
              type="number"
              dataKey="x"
              domain={[dataMin, dataMax]}
              tickFormatter={(v: number) => signed(v)}
              tick={{
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fill: c.tertiary,
              }}
              axisLine={false}
              tickLine={false}
              tickCount={5}
            />

            {/* Faint red loss zone, only when outcomes can go below 0. */}
            {dataMin < 0 && (
              <ReferenceArea
                x1={dataMin}
                x2={0}
                fill={c.loss}
                fillOpacity={0.07}
                strokeWidth={0}
              />
            )}

            <Area
              type="monotone"
              dataKey="n"
              stroke={c.blue}
              strokeWidth={1.5}
              fill={`url(#${gradId})`}
              isAnimationActive={false}
            />

            {/* 5th / 95th percentile — the realistic worst & best tails. */}
            <ReferenceLine
              x={mc.p05}
              stroke={c.tertiary}
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <ReferenceLine
              x={mc.p95}
              stroke={c.tertiary}
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            {/* Median — the middle outcome. */}
            <ReferenceLine
              x={mc.median}
              stroke={c.ink}
              strokeWidth={1.5}
              label={{
                value: `middle ${signed(mc.median)}`,
                position: "top",
                fill: c.secondary,
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fontWeight: 600,
              }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Worst / best tail labels under the axis, aligned to the ends. */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontFamily: "var(--font-display)",
          fontVariantNumeric: "tabular-nums",
          fontSize: 13,
          color: c.tertiary,
        }}
      >
        <span>Worst 5% &nbsp;{signed(mc.p05)}</span>
        <span>Best 5% &nbsp;{signed(mc.p95)}</span>
      </div>

      <p
        style={{
          margin: 0,
          fontFamily: "var(--font-display)",
          fontVariantNumeric: "tabular-nums",
          fontSize: 13,
          lineHeight: 1.55,
          color: c.secondary,
        }}
      >
        If we re-ran this on resampled history {mc.n_sims.toLocaleString("en-IN")}{" "}
        times — the middle outcome is{" "}
        <strong style={{ color: c.ink, fontWeight: 600 }}>
          {signed(mc.median)}
        </strong>
        , the worst 5% of runs land near {signed(mc.p05)}, and the chance of
        ending in a loss is{" "}
        <strong style={{ color: c.ink, fontWeight: 600 }}>
          {probLossPct < 0.1 ? "under 0.1" : probLossPct.toFixed(probLossPct < 1 ? 1 : 0)}%
        </strong>
        .
      </p>

      {isUnderlying && (
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            lineHeight: 1.5,
            color: c.tertiary,
          }}
        >
          These outcomes are for the underlying
          {underlyingSymbol ? ` (${underlyingSymbol})` : ""}, not the option
          position&rsquo;s own profit or loss.
        </span>
      )}
    </div>
  );
}

export default MonteCarloDistribution;
