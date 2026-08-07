"use client";

/**
 * Segment mix — where the money comes from, and how that has moved.
 *
 * The data holds several breakdowns per company, not one: Reliance carries
 * product-wise, location-wise, operating-profit-wise, capex and assets. So
 * this panel is a selector over breakdowns rather than a single chart, which
 * is also why the title comes from the data (`breakdown`) instead of being
 * hardcoded to "Revenue mix" — half of them are not revenue.
 *
 * The stacked area is the argument. A donut of today's split says what the
 * company is; the series says what it is BECOMING, which is the only version
 * of this chart worth the space. The donut stays as a right-hand readout of
 * the latest column.
 */

import dynamic from "next/dynamic";
import * as React from "react";

import type { MixChart, MixResponse } from "@/lib/api";
import { EmptyNote, PanelHead, Segmented } from "./chrome";
import { num } from "./FinTable";

// ECharts touches `window` at import time — it must never reach a server
// render. "use client" alone does not prevent that; ssr:false does.
const EChart = dynamic(() => import("./EChart"), {
  ssr: false,
  loading: () => <div style={{ height: 280 }} />,
});

export function MixPanel({ data }: { data: MixResponse }): React.ReactElement {
  const charts = data.charts ?? [];
  // Open on the breakdown that says the most, not the one that happens to be
  // first. TCS's "Product Wise Break-Up" is a 98/2 split — technically a
  // breakdown, visually a solid block — while "Verticals" carries eight
  // segments and the actual shape of the business. Segment count is a decent
  // proxy for information here, and ties keep source order.
  const richest = React.useMemo(
    () => charts.reduce<MixChart | undefined>(
      (best, c) => (!best || c.series.length > best.series.length ? c : best),
      undefined),
    [charts],
  );
  const [id, setId] = React.useState<string>(String(richest?.id ?? charts[0]?.id ?? "0"));
  const chart: MixChart | undefined =
    charts.find((c) => String(c.id) === id) ?? richest ?? charts[0];

  const option = React.useMemo(() => (chart ? stackedOption(chart) : null), [chart]);

  if (!charts.length || !chart) {
    return <EmptyNote>No segment breakdown available for this company.</EmptyNote>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead
        title="Segment mix"
        sub={`${charts.length} breakdown${charts.length === 1 ? "" : "s"} · share of total, %`}
      />

      {charts.length > 1 ? (
        <div style={{ overflowX: "auto", paddingBottom: 2 }}>
          <div style={{ width: "max-content" }}>
            <Segmented
              value={id}
              onChange={setId}
              options={charts.map((c) => ({ value: String(c.id), label: c.title }))}
            />
          </div>
        </div>
      ) : null}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 2.1fr) minmax(0, 1fr)",
          gap: 12,
        }}
        className="mix-grid"
      >
        <div
          style={{
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            background: "var(--bg-primary)",
            padding: "12px 8px 4px 12px",
          }}
        >
          {option ? (
            <EChart
              option={option}
              height={280}
              ariaLabel={`${chart.title}: share of total by segment over time`}
            />
          ) : null}
        </div>

        <div
          style={{
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            background: "var(--bg-primary)",
            padding: "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>Latest split</div>
          {chart.current.length ? (
            chart.current.map((c, i) => (
              <div key={c.name} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <span
                    style={{
                      fontSize: 12,
                      color: "var(--text-primary)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={c.name}
                  >
                    {c.name}
                  </span>
                  <span
                    style={{
                      fontSize: 12,
                      fontWeight: 600,
                      fontVariantNumeric: "tabular-nums",
                      color: "var(--text-primary)",
                    }}
                  >
                    {num(c.pct, { dp: 1, pct: true })}
                  </span>
                </div>
                {/* A bar rather than a donut slice: shares this small are
                    easier to compare along a common baseline than around a
                    circle, and it costs no extra chart. */}
                <div style={{ height: 4, borderRadius: 2, background: "var(--bg-elevated)" }}>
                  <div
                    style={{
                      width: `${Math.min(100, c.pct)}%`,
                      height: "100%",
                      borderRadius: 2,
                      background: RAMP[i % RAMP.length],
                    }}
                  />
                </div>
              </div>
            ))
          ) : (
            <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
              No current split reported.
            </div>
          )}
        </div>
      </div>

      {data.source_name ? (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          Segment data for {data.source_name}.
        </div>
      ) : null}

      <style>{`
        @media (max-width: 720px) {
          .mix-grid { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
  );
}

// Kept in sync with EChart's palette by construction — the swatch beside the
// chart has to be the same colour as the band it names.
const RAMP = [
  "var(--pivot-blue)", "#7C9885", "#C08552", "#5C6B87", "#9A6A8F",
  "#4E8098", "#B08968", "#6B8F71", "#8C7A9B", "#A8763E",
];

/** Build the stacked-area option for one breakdown.
 *
 *  Segments do not all share a time axis — a segment the company started
 *  reporting in 2021 has fewer points than one it has reported since 2015 —
 *  so the union of timestamps forms the axis and each series is aligned onto
 *  it, with gaps left null rather than zero. A zero would draw a band
 *  collapsing to nothing, which reads as "this segment earned nothing" rather
 *  than "this segment was not reported yet".
 */
function stackedOption(chart: MixChart): Record<string, unknown> {
  const times = Array.from(
    new Set(chart.series.flatMap((s) => s.points.map((p) => p.t))),
  ).sort((a, b) => a - b);
  const byTime = (s: MixChart["series"][number]) => {
    const m = new Map(s.points.map((p) => [p.t, p.pct]));
    return times.map((t) => (m.has(t) ? m.get(t)! : null));
  };
  // Both en-IN and en-GB render September as "Sept" while every other month
  // is three characters, so one axis label sits wider than the rest. Sliced
  // rather than switching locale again — the width has to be uniform whatever
  // ICU decides a short month is.
  const labels = times.map((t) => {
    const d = new Date(t);
    const m = d.toLocaleString("en-GB", { month: "short" }).slice(0, 3);
    return `${m} '${String(d.getFullYear()).slice(2)}`;
  });

  return {
    grid: { left: 8, right: 10, top: 10, bottom: 46, containLabel: true },
    legend: {
      type: "scroll", bottom: 0, itemWidth: 9, itemHeight: 9,
      itemGap: 12, textStyle: { fontSize: 11 },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { width: 1, opacity: 0.5 } },
      valueFormatter: (v: number | null) => (v === null ? "—" : `${v.toFixed(1)}%`),
    },
    xAxis: {
      type: "category", data: labels, boundaryGap: false,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: "value", max: 100, splitNumber: 4,
      axisLabel: { fontSize: 10, formatter: "{value}%" },
      splitLine: { lineStyle: { opacity: 0.35 } },
    },
    series: chart.series.map((s) => ({
      name: s.name,
      type: "line",
      stack: "total",
      areaStyle: { opacity: 0.72 },
      lineStyle: { width: 0 },
      symbol: "none",
      smooth: 0.2,
      connectNulls: false,
      data: byTime(s),
    })),
  };
}
