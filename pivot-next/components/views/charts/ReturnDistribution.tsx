"use client";

/**
 * ReturnDistribution — the BEST GAIN reached during each past episode (peak,
 * NOT final), drawn as bars centered on a 0 reference. This is the per-episode
 * MFE story, so the honest label says "peak, not final" — never implies these
 * were realised returns. Omitted entirely when there is no data (e.g. a crude
 * leg that never finished).
 *
 * DESIGN LAW: gated behind an "Advanced" disclosure (progressive disclosure,
 * not on by default), SQUARE flat bars + tooltip (no radius, no shadow), every
 * tick / label / caption >= 13px, Inter-tabular numerals.
 */

import * as React from "react";
import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from "recharts";
import { useTokenColors } from "../use-token-color";

/**
 * Self-contained tooltip — square, flat (no shadow), border-only. Does NOT use
 * the shadcn ChartContainer context (these charts render a raw
 * ResponsiveContainer, so useChart() is unavailable).
 */
function DistTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ value?: number | string }>;
  label?: number | string;
}): React.ReactElement | null {
  if (!active || !payload || payload.length === 0) return null;
  const raw = payload[0]?.value;
  const v = typeof raw === "number" ? raw : 0;
  return (
    <div
      className="rounded-md"
      style={{
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        padding: "6px 10px",
        boxShadow: "none",
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 13,
          fontWeight: 500,
          color: "var(--text-tertiary)",
        }}
      >
        Episode {label}
      </span>
      <span
        style={{
          fontFamily: "var(--font-display)",
          fontVariantNumeric: "tabular-nums",
          fontWeight: 600,
          fontSize: 13,
          color: v >= 0 ? "var(--color-profit)" : "var(--color-loss)",
        }}
      >
        {v >= 0 ? "+" : "−"}
        {Math.abs(v).toFixed(1)}%
      </span>
    </div>
  );
}

export function ReturnDistribution({
  values,
  posFrac,
  height = 96,
}: {
  values: number[];
  posFrac?: number | null;
  height?: number;
}): React.ReactElement | null {
  const c = useTokenColors({
    profit: "--color-profit",
    loss: "--color-loss",
    zero: "--glass-border-focus",
  });

  const [open, setOpen] = React.useState(false);

  if (!values || values.length < 1) return null;

  const data = values.map((v, i) => ({ w: i + 1, v }));
  const posCount =
    posFrac != null
      ? Math.round(posFrac * values.length)
      : values.filter((v) => v >= 0).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="rounded-md"
        style={{
          alignSelf: "flex-start",
          fontFamily: "var(--font-display)",
          fontSize: 13,
          fontWeight: 500,
          padding: "6px 12px",
          borderRadius: "var(--radius-md)",
          border: "1px solid var(--glass-border)",
          background: "var(--bg-base)",
          color: "var(--text-secondary)",
          cursor: "pointer",
          transition: "border-color 180ms var(--ease-quartr)",
        }}
      >
        {open ? "Hide per-episode detail" : "Show per-episode detail"}
      </button>

      {open && (
        <>
          <div style={{ height }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 6, right: 4, bottom: 0, left: 4 }}>
                <XAxis
                  dataKey="w"
                  tick={{
                    fontFamily: "var(--font-display)",
                    fontSize: 13,
                    fill: "var(--text-tertiary)",
                  }}
                  axisLine={false}
                  tickLine={false}
                />
                <ReferenceLine y={0} stroke={c.zero} strokeWidth={1} />
                <Tooltip cursor={{ fill: "transparent" }} content={<DistTooltip />} />
                <Bar dataKey="v" isAnimationActive={false} radius={0} maxBarSize={28}>
                  {data.map((d) => (
                    <Cell key={d.w} fill={d.v >= 0 ? c.profit : c.loss} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontVariantNumeric: "tabular-nums",
              fontSize: 13,
              lineHeight: 1.5,
              color: "var(--text-tertiary)",
            }}
          >
            Best gain reached during each past episode (peak, not final) ·{" "}
            {posCount} of {values.length} episodes ended up
          </span>
        </>
      )}
    </div>
  );
}
