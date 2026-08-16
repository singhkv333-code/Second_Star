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
      <PanelHead title="Segment mix" />

      {/* The breakdowns laid out flat rather than folded into a select. There
          are rarely more than four, and a company's own choice of how to cut
          itself up is worth seeing at once — a dropdown hides three of them
          behind a click and needs a "Breakdown" label to explain itself. With
          the options visible the selected one names the chart, so the repeated
          title and the line describing what a stacked area is both go. */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        {charts.length > 1 ? (
          <Segmented
            value={id}
            options={charts.map((c) => ({ value: String(c.id), label: c.title }))}
            onChange={setId}
          />
        ) : (
          <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-secondary)" }}>{chart.title}</div>
        )}
        <ChangeSummary chart={chart} />
      </div>

      {/* No cards. The chart and the split are two columns of one section, and
          a border around each said they were two separate objects that happen
          to sit side by side. The only line left is the one that separates
          them, which is the one that carries meaning. */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 2.1fr) minmax(0, 1fr)",
          gap: 28,
        }}
        className="mix-grid"
      >
        <div style={{ minWidth: 0 }}>
          {option ? (
            <EChart
              option={option}
              height={320}
              ariaLabel={`${chart.title}: share of total by segment over time`}
            />
          ) : null}
        </div>

        <div
          className="mix-split"
          style={{
            borderLeft: "1px solid var(--glass-border)",
            paddingLeft: 24,
            display: "flex",
            flexDirection: "column",
            gap: 9,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-tertiary)" }}><span>Latest reported split</span><span>Share</span></div>
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

      <style>{`
        @media (max-width: 720px) {
          .mix-grid { grid-template-columns: 1fr !important; gap: 20px !important; }
          /* the divider becomes the seam ABOVE the split once they stack */
          .mix-split {
            border-left: none !important;
            border-top: 1px solid var(--glass-border);
            padding-left: 0 !important;
            padding-top: 16px;
          }
        }
      `}</style>
    </div>
  );
}

function ChangeSummary({ chart }: { chart: MixChart }): React.ReactElement | null {
  const changes = chart.series.map((series) => {
    const pts = series.points;
    return pts.length > 1 ? { name: series.name, delta: pts[pts.length - 1]!.pct - pts[pts.length - 2]!.pct } : null;
  }).filter((v): v is { name: string; delta: number } => v !== null).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  if (!changes.length) return null;
  const move = changes[0]!;
  return <div style={{ fontSize: 11.5, color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}><span style={{ color: move.delta >= 0 ? "var(--color-profit)" : "var(--color-loss)", fontWeight: 600 }}>{move.delta >= 0 ? "+" : ""}{move.delta.toFixed(1)} pp</span> {move.name} vs prior report</div>;
}

// Kept in sync with EChart's palette by construction — the swatch beside the
// chart has to be the same colour as the band it names.
const RAMP = [
  "var(--pivot-blue)", "#2E9AA8", "#4FA46B", "#8FA83E", "#D0A02C",
  "#DB7F3C", "#CE5F55", "#B85D86", "#8C63AE", "#5B6FB5",
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
      axisPointer: { type: "line", lineStyle: { width: 1, opacity: 0.45 } },
      valueFormatter: (v: number | null) => (v === null ? "—" : `${v.toFixed(1)}%`),
      // A segment the company did not report that period is absent from the
      // tooltip rather than listed as "—". Ten rows of em-dash is how a
      // company that reports three segments gets a tooltip the height of the
      // chart, which is what pushed the old one over the whole panel.
      order: "valueDesc",
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
      // Opaque, with a hairline of the page's own background drawn along each
      // band's top edge. Translucent bands stacked ten deep mix into each
      // other and every colour drifts toward the one beneath it, which is the
      // other half of why this chart read as muddy. A solid fill keeps each
      // segment the colour its swatch says it is, and the separator is what
      // replaces the opacity as the thing that tells two bands apart.
      areaStyle: { opacity: 1 },
      lineStyle: { width: 1.25, color: "#fff", opacity: 0.9 },
      symbol: "none",
      smooth: 0.2,
      connectNulls: false,
      emphasis: { disabled: true },
      data: byTime(s),
    })),
  };
}
