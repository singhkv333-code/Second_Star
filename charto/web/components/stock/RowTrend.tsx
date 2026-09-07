"use client";

/**
 * The trend behind one table row, opened on demand.
 *
 * The alternative is a permanent sparkline in every row, which costs a column
 * on every table forever and is too small to read a turning point off. Opening
 * one row at full width instead keeps the table dense by default and gives the
 * series enough room to actually say something when it is asked for.
 *
 * The row's values arrive already FORMATTED — "₹2.47 L Cr", "14.29%", "—" —
 * because that is what the table holds. Parsing them back is deliberate: the
 * chart must plot exactly the numbers the reader can see above it, so it
 * cannot disagree with the row that opened it.
 */

import dynamic from "next/dynamic";
import * as React from "react";

const EChart = dynamic(() => import("./EChart"), {
  ssr: false,
  loading: () => <div style={{ height: 148 }} />,
});

const LINE = "#4F8A5B";
const LINE_DOWN = "#C4643F";

/** Formatted cell → number, keeping the magnitude the suffix encoded so a
 *  series mixing "L Cr" and "Cr" stays on one scale. */
export function parseCell(v: string | null | undefined): number | null {
  if (!v || v === "—") return null;
  const n = parseFloat(v.replace(/[^0-9.\-]/g, ""));
  if (Number.isNaN(n)) return null;
  if (/L\s*Cr/.test(v)) return n * 1e5;
  if (/K\s*Cr/.test(v)) return n * 1e3;
  return n;
}

function short(n: number, pct: boolean): string {
  if (pct) return `${n.toFixed(2)}%`;
  const a = Math.abs(n);
  if (a >= 1e5) return `${(n / 1e5).toFixed(2)} L Cr`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(2)} K Cr`;
  if (a >= 1) return n.toFixed(2);
  return n.toFixed(2);
}

export function RowTrend({
  label,
  periods,
  values,
}: {
  label: string;
  periods: string[];
  values: (string | null)[];
}): React.ReactElement | null {
  const nums = React.useMemo(() => values.map(parseCell), [values]);
  const pct = React.useMemo(() => values.some((v) => !!v && v.includes("%")), [values]);
  const valid = nums.filter((n): n is number => n !== null);

  // Two points is a pair of numbers the reader has already read across the
  // row; it is not a trend and does not earn a chart.
  if (valid.length < 3) return null;

  const first = valid[0]!;
  const last = valid[valid.length - 1]!;
  const rising = last >= first;
  const tone = rising ? LINE : LINE_DOWN;
  const change = first !== 0 ? ((last - first) / Math.abs(first)) * 100 : null;

  const option = {
    grid: { left: 4, right: 12, top: 26, bottom: 2, containLabel: true },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: number | null) => (v === null || v === undefined ? "—" : short(v, pct)),
    },
    xAxis: {
      type: "category",
      data: periods,
      boundaryGap: false,
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { fontSize: 10.5 },
    },
    yAxis: {
      type: "value",
      scale: true,
      splitNumber: 3,
      axisLabel: { fontSize: 10.5, formatter: (v: number) => short(v, pct) },
      splitLine: { lineStyle: { type: "dashed", opacity: 0.5 } },
    },
    series: [
      {
        name: label,
        type: "line",
        smooth: 0.25,
        symbol: "circle",
        symbolSize: 5,
        connectNulls: false,
        lineStyle: { width: 1.8, color: tone },
        itemStyle: { color: tone },
        areaStyle: { color: tone, opacity: 0.12 },
        // The last point is the one the table bolds, so it is the one marked.
        markPoint: {
          symbol: "circle",
          symbolSize: 7,
          silent: true,
          label: { show: false },
          itemStyle: { color: tone, borderColor: "var(--bg-primary)", borderWidth: 2 },
          data: [{ coord: [periods.length - 1, last] }],
        },
        data: nums,
      },
    ],
  };

  return (
    <div style={{ padding: "4px 12px 14px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 2 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>{label}</span>
        {change !== null ? (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11.5,
              fontVariantNumeric: "tabular-nums",
              color: rising ? "var(--color-profit)" : "var(--color-loss)",
            }}
          >
            {rising ? "+" : "−"}{Math.abs(change).toFixed(1)}% over {periods.length} periods
          </span>
        ) : null}
      </div>
      <EChart option={option} height={148} ariaLabel={`${label} over ${periods.length} periods`} />
    </div>
  );
}
