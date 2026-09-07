"use client";

import { useMemo } from "react";

/**
 * The shape of one session, at thumbnail size.
 *
 * WHY NO CHART LIBRARY
 *
 * This repo already carries three (ECharts, Recharts, Lightweight Charts) and
 * none of them is the right tool for forty 64x28 thumbnails in a scrolling
 * table: each one mounts a chart instance, an option object and — for two of
 * the three — a canvas per row. Measured against the alternative, this is one
 * `<path>` per row and no runtime at all. A sparkline has no axes, no legend,
 * no tooltip and no interaction; it is a polyline, and the professional answer
 * for a polyline in a table cell is a polyline.
 *
 * THE BASELINE IS THE PREVIOUS CLOSE, NOT THE FIRST POINT
 *
 * A day's shape means "up or down ON THE DAY", and the day starts at
 * yesterday's close, not at the first print of this morning. Drawing the fill
 * from `series[0]` would paint a gap-down morning that then rallied as a green
 * session. When `baseline` is unavailable we fall back to the first point and
 * the colour becomes "shape of the series", which is the honest weaker claim.
 */

type Props = {
  points: number[] | undefined;
  /** Previous close. Decides both the colour and where the fill is measured
   *  from. Falls back to `points[0]` when absent. */
  baseline?: number | null;
  width?: number;
  height?: number;
};

export function Sparkline({
  points,
  baseline,
  width = 72,
  height = 30,
}: Props): React.ReactElement {
  const geom = useMemo(() => {
    if (!points || points.length < 2) return null;

    // `noUncheckedIndexedAccess` is on, so points[0] is number|undefined even
    // after the length check. Filtering to finite values also drops any null
    // the wire smuggled through, which is the same guard by another name.
    const pts = points.filter((v) => Number.isFinite(v));
    if (pts.length < 2) return null;
    const first = pts[0] as number;
    const base =
      baseline != null && Number.isFinite(baseline) ? baseline : first;
    // The baseline has to be inside the drawn range or the reference line
    // leaves the box — a gap-down open puts prev-close above every point.
    const lo = Math.min(...pts, base);
    const hi = Math.max(...pts, base);
    const span = hi - lo || 1;

    // Half a stroke of padding top and bottom: at 30px tall, a high that
    // touches y=0 gets its top pixel clipped by the cell.
    const pad = 2;
    const h = height - pad * 2;
    const x = (i: number) => (i / (pts.length - 1)) * width;
    const y = (v: number) => pad + (1 - (v - lo) / span) * h;

    const line = pts.map((v, i) => `${x(i)},${y(v)}`).join(" ");
    const last = pts[pts.length - 1] as number;
    return {
      line,
      area: `${x(0)},${y(base)} ${line} ${x(pts.length - 1)},${y(base)}`,
      baseY: y(base),
      lastX: x(pts.length - 1),
      lastY: y(last),
      up: last >= base,
    };
  }, [points, baseline, width, height]);

  // A reserved, EMPTY box rather than nothing. The sparklines arrive on a
  // second request; if the cell had no size until they landed, every row in
  // the table would shift when they did.
  if (!geom) {
    return <span style={{ display: "inline-block", width, height }} aria-hidden />;
  }

  const stroke = geom.up ? "var(--color-profit, #059669)" : "var(--color-loss, #dc2626)";
  const id = `spark-${geom.up ? "u" : "d"}`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={geom.up ? "up on the day" : "down on the day"}
      style={{ display: "block", overflow: "visible" }}
    >
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.31" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0.025" />
        </linearGradient>
      </defs>
      <polygon points={geom.area} fill={`url(#${id})`} />
      {/* Yesterday's close, so a flat-looking line still says which side of
          the day it is on. */}
      <line
        x1={0}
        x2={width}
        y1={geom.baseY}
        y2={geom.baseY}
        stroke={stroke}
        strokeOpacity="0.35"
        strokeWidth="1"
        strokeDasharray="2 2"
      />
      <polyline
        points={geom.line}
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={geom.lastX} cy={geom.lastY} r="1.05" fill={stroke} />
    </svg>
  );
}
