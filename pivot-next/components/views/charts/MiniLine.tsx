"use client";

/**
 * MiniLine — a compact, axis-less sparkline for the View gallery card. Strategy
 * line in the accent (var(--pivot-blue)) over a faint Nifty reference line.
 * ~120x44, rounded, no ticks/labels/tooltip. Empty / too-short series renders
 * nothing (a thin placeholder), never a fabricated line.
 *
 * The series is the EPISODE-GATED in-position curve (point.t is a sequential
 * in-market day index, not a date); the sparkline plots points by array order,
 * so it is naturally index-based and needs no date handling.
 *
 * Pure inline SVG (no recharts) so it stays cheap to render many of them in a
 * gallery. Light + dark via useTokenColors.
 */

import * as React from "react";
import type { EquityPoint } from "./LineChart";
import { useTokenColors } from "../use-token-color";

function pathFor(
  values: number[],
  w: number,
  h: number,
  pad: number,
): string {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const innerW = w - pad * 2;
  const innerH = h - pad * 2;
  const n = values.length;
  return values
    .map((v, i) => {
      const x = pad + (n === 1 ? 0 : (i / (n - 1)) * innerW);
      const y = pad + innerH - ((v - min) / span) * innerH;
      return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

export function MiniLine({
  series,
  width = 120,
  height = 44,
}: {
  series?: EquityPoint[] | null;
  width?: number;
  height?: number;
}): React.ReactElement {
  const c = useTokenColors({
    blue: "--pivot-blue",
    tertiary: "--text-tertiary",
    border: "--glass-border",
  });

  const safe = Array.isArray(series) ? series : [];

  if (safe.length < 2) {
    // Placeholder: a faint baseline, no fabricated curve.
    return (
      <div
        aria-hidden
        style={{
          width,
          height,
          borderRadius: 10,
          background: "transparent",
          display: "flex",
          alignItems: "center",
        }}
      >
        <div
          style={{
            width: "100%",
            height: 0,
            borderTop: `1px dashed ${c.border}`,
          }}
        />
      </div>
    );
  }

  const pad = 4;
  const stratPath = pathFor(
    safe.map((p) => p.strategy),
    width,
    height,
    pad,
  );
  const benchPath = pathFor(
    safe.map((p) => p.benchmark),
    width,
    height,
    pad,
  );

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Strategy versus Nifty performance sparkline"
      style={{ borderRadius: 10, display: "block", overflow: "visible" }}
    >
      <path
        d={benchPath}
        fill="none"
        stroke={c.tertiary}
        strokeOpacity={0.45}
        strokeWidth={1.25}
        strokeDasharray="3 3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d={stratPath}
        fill="none"
        stroke={c.blue}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default MiniLine;
