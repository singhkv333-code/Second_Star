"use client";

/**
 * PayoffDiagram — structural option expiry payoff (HEADLINE viz for
 * option_strategy expressions). Draws the intrinsic-value SHAPE anchored at a
 * labeled 0 reference. We never invent a net debit/credit — hence the honest
 * "premium not priced" note.
 *
 * DESIGN LAW: square empty state (border-only), NO gridlines, every tick /
 * label / caption >= 13px, Inter-tabular numerals. Only the nearest breakeven
 * is shown to stop the strip overflowing.
 */

import { useId } from "react";
import {
  Area,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";
import { buildExpiryPayoff, type Leg } from "./payoff-math";
import { Stat, StatStrip } from "../Stat";

const NUM_TICK = {
  fontFamily: "var(--font-display)",
  fontSize: 13,
  fill: "var(--text-tertiary)",
} as const;

export function PayoffDiagram({
  legs,
  underlyingRef,
  height = 180,
  label = "Payoff at expiry (structure)",
  /** When strikes are relative offsets, the x-axis labels read "offset from spot". */
  xMode = "absolute",
}: {
  legs: Leg[];
  underlyingRef?: number | null;
  height?: number;
  label?: string;
  xMode?: "absolute" | "offset";
}): React.ReactElement {
  const rawId = useId();
  const gid = `vpf-${rawId.replace(/[^a-zA-Z0-9]/g, "")}`;

  const result = buildExpiryPayoff(legs);

  if (result.points.length === 0) {
    return (
      <div
        className="rounded-lg"
        style={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: "var(--radius-lg)",
          border: "1px solid var(--glass-border)",
          background: "var(--bg-base)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 15,
            color: "var(--text-tertiary)",
          }}
        >
          Structure not specified
        </span>
      </div>
    );
  }

  const { points, breakevens, maxProfit, maxLoss, capped } = result;

  // Zero-split gradient offset (profit green above 0, loss red below).
  const pnls = points.map((p) => p.pnl);
  const maxP = Math.max(...pnls, 0);
  const minP = Math.min(...pnls, 0);
  const zeroOffset = maxP - minP === 0 ? 0.5 : maxP / (maxP - minP);

  const fmtX = (v: number): string =>
    xMode === "offset"
      ? `${v >= 0 ? "+" : ""}${v.toFixed(0)}`
      : v.toLocaleString("en-IN");

  const spotInView =
    underlyingRef != null &&
    underlyingRef >= points[0]!.s &&
    underlyingRef <= points[points.length - 1]!.s;

  // Show only the nearest breakeven (to spot, else to mid) so the strip never
  // overflows; flag when there are more.
  const ref =
    underlyingRef ?? (points[0]!.s + points[points.length - 1]!.s) / 2;
  const nearestBe =
    breakevens.length === 0
      ? null
      : breakevens.reduce((best, be) =>
          Math.abs(be - ref) < Math.abs(best - ref) ? be : best,
        );
  const beValue =
    nearestBe == null
      ? "—"
      : breakevens.length > 1
        ? `${fmtX(nearestBe)} +${breakevens.length - 1}`
        : fmtX(nearestBe);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <span
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 13,
          fontWeight: 500,
          color: "var(--text-tertiary)",
        }}
      >
        {label}
      </span>

      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} margin={{ top: 14, right: 12, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={`${gid}-fill`} x1="0" y1="0" x2="0" y2="1">
                <stop offset={0} stopColor="var(--color-profit)" stopOpacity={0.32} />
                <stop offset={zeroOffset} stopColor="var(--color-profit)" stopOpacity={0.04} />
                <stop offset={zeroOffset} stopColor="var(--color-loss)" stopOpacity={0.04} />
                <stop offset={1} stopColor="var(--color-loss)" stopOpacity={0.32} />
              </linearGradient>
              <linearGradient id={`${gid}-stroke`} x1="0" y1="0" x2="0" y2="1">
                <stop offset={0} stopColor="var(--color-profit)" />
                <stop offset={zeroOffset} stopColor="var(--color-profit)" />
                <stop offset={zeroOffset} stopColor="var(--color-loss)" />
                <stop offset={1} stopColor="var(--color-loss)" />
              </linearGradient>
            </defs>

            <XAxis
              dataKey="s"
              type="number"
              domain={["dataMin", "dataMax"]}
              tick={NUM_TICK}
              axisLine={false}
              tickLine={false}
              tickFormatter={fmtX}
              minTickGap={48}
            />
            <YAxis hide domain={["dataMin", "dataMax"]} />

            <Area
              type="linear"
              dataKey="pnl"
              stroke={`url(#${gid}-stroke)`}
              strokeWidth={2}
              fill={`url(#${gid}-fill)`}
              dot={false}
              isAnimationActive={false}
            />

            <ReferenceLine
              y={0}
              stroke="var(--glass-border-focus)"
              strokeDasharray="3 3"
              label={{
                value: "0",
                position: "insideLeft",
                fontSize: 13,
                fill: "var(--text-tertiary)",
              }}
            />

            {nearestBe != null && (
              <ReferenceLine
                x={nearestBe}
                stroke="var(--color-warn)"
                strokeWidth={1}
                strokeDasharray="3 3"
                label={{
                  value: "Breakeven",
                  position: "insideTopRight",
                  fontSize: 13,
                  fill: "var(--color-warn)",
                }}
              />
            )}

            {spotInView && (
              <ReferenceLine
                x={underlyingRef!}
                stroke="var(--text-secondary)"
                strokeWidth={1.5}
                label={{
                  value: "Spot",
                  position: "top",
                  fontSize: 13,
                  fill: "var(--text-secondary)",
                  fontWeight: 600,
                }}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <StatStrip cols={3}>
        <Stat
          label="Max profit"
          value={capped.up ? "Capped" : "Open"}
          valueColor="var(--color-profit)"
          valueSize="value"
          sub={
            maxProfit != null
              ? `peak ${maxProfit >= 0 ? "+" : "−"}${Math.abs(maxProfit).toFixed(0)}`
              : undefined
          }
        />
        <Stat
          label="Max loss"
          value={capped.down ? "Defined" : "Open"}
          valueColor="var(--color-loss)"
          valueSize="value"
          sub={maxLoss != null ? `trough ${maxLoss.toFixed(0)}` : undefined}
        />
        <Stat
          label="Breakeven"
          value={beValue}
          valueColor="var(--text-primary)"
          valueSize="value"
          align="end"
        />
      </StatStrip>

      <span
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 13,
          color: "var(--text-tertiary)",
        }}
      >
        Payoff at expiry · structure only — premium not priced
      </span>
    </div>
  );
}
