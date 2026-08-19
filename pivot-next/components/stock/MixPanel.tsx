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

  const option = React.useMemo(() => (chart ? bumpOption(chart) : null), [chart]);

  if (!charts.length || !chart) {
    return <EmptyNote>No segment breakdown available for this company.</EmptyNote>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead title="Segment mix" />

      {/* The breakdowns laid out flat rather than folded into a select: a
          company's own choice of how to cut itself up is worth seeing at once,
          and with the options visible the selected one names the chart.

          That holds while they FIT. This panel used to assume "rarely more
          than four" and Infosys files twenty-nine — "Location Wise Break-Up
          — Financial Services", "— Retail", and so on — which laid out as a
          nine-row, 193px wall of tabs above a 320px chart. Past a handful the
          strip stops being a set of options you can see at once and becomes
          the thing you have to read before you reach the chart, so it folds
          into one control. The threshold is where the strip still holds a
          line or two, not a number picked for its own sake. */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        {charts.length > 6 ? (
          <select
            value={id}
            onChange={(e) => setId(e.target.value)}
            aria-label="Breakdown"
            style={{
              maxWidth: "min(100%, 420px)",
              border: "none",
              borderBottom: "1px solid var(--glass-border)",
              background: "transparent",
              padding: "3px 2px",
              fontFamily: "var(--font-ui)",
              fontSize: 12.5,
              fontWeight: 600,
              color: "var(--text-primary)",
              cursor: "pointer",
            }}
          >
            {charts.map((c) => (
              <option key={c.id} value={String(c.id)}>{c.title}</option>
            ))}
          </select>
        ) : charts.length > 1 ? (
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
              ariaLabel={`${chart.title}: segments ranked by share over time`}
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

/** Build the bump chart for one breakdown.
 *
 *  A bump chart answers the question a stacked area could not: not "how big is
 *  each segment" but "which one is winning, and when did that change". The
 *  stack drew eight bands whose thickness the reader had to compare across a
 *  moving baseline; here every segment is a line on a rank axis, and a
 *  crossing IS the finding — the quarter a vertical overtook another is a
 *  place where two lines visibly swap.
 *
 *  The rank axis is INVERTED, which is the one rule of the form: rank 1 sits
 *  at the top, where a reader expects the leader.
 *
 *  Rank is computed per period among the segments REPORTED in that period. A
 *  segment the company had not begun disclosing is null rather than last —
 *  ranking it below the others would draw a line climbing from the floor and
 *  invent a rise that never happened.
 */
function bumpOption(chart: MixChart): Record<string, unknown> {
  const times = Array.from(
    new Set(chart.series.flatMap((s) => s.points.map((p) => p.t))),
  ).sort((a, b) => a - b);

  const labels = times.map((t) => {
    const d = new Date(t);
    const m = d.toLocaleString("en-GB", { month: "short" }).slice(0, 3);
    return `${m} '${String(d.getFullYear()).slice(2)}`;
  });

  // pct by segment by time, then a rank per time over the segments present.
  const pctAt = new Map<string, Map<number, number>>();
  chart.series.forEach((s) => {
    pctAt.set(s.name, new Map(s.points.map((p) => [p.t, p.pct])));
  });

  const rankAt = new Map<string, (number | null)[]>();
  chart.series.forEach((s) => rankAt.set(s.name, []));
  times.forEach((t) => {
    const present = chart.series
      .map((s) => ({ name: s.name, pct: pctAt.get(s.name)?.get(t) }))
      .filter((r): r is { name: string; pct: number } => typeof r.pct === "number")
      .sort((a, b) => b.pct - a.pct);
    const rank = new Map(present.map((r, i) => [r.name, i + 1]));
    chart.series.forEach((s) => rankAt.get(s.name)!.push(rank.get(s.name) ?? null));
  });

  const n = chart.series.length;

  return {
    // Room on the right for the end labels, which is where a bump chart is
    // read from: the reader finds the line they care about by its name at the
    // finish, then traces it back.
    grid: { left: 8, right: 132, top: 12, bottom: 40, containLabel: true },
    legend: {
      type: "scroll", bottom: 0, itemWidth: 9, itemHeight: 9,
      itemGap: 12, icon: "circle", textStyle: { fontSize: 11 },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { width: 1, opacity: 0.45 } },
      formatter: (params: unknown) => {
        const rows = (Array.isArray(params) ? params : [params]) as {
          axisValue?: string; seriesName?: string; data?: number | null; color?: string;
        }[];
        const when = rows[0]?.axisValue ?? "";
        const t = times[labels.indexOf(when)];
        const body = rows
          .filter((r) => r.data !== null && r.data !== undefined)
          .sort((a, b) => (a.data as number) - (b.data as number))
          .map((r) => {
            const share = t !== undefined ? pctAt.get(r.seriesName ?? "")?.get(t) : undefined;
            return `<div style="display:flex;gap:12px;justify-content:space-between">
              <span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${r.color};margin-right:6px"></span>#${r.data} ${r.seriesName}</span>
              <strong>${typeof share === "number" ? `${share.toFixed(1)}%` : "—"}</strong></div>`;
          })
          .join("");
        return `<div style="font-weight:600;margin-bottom:3px">${when}</div>${body}`;
      },
    },
    xAxis: {
      type: "category", data: labels, boundaryGap: false,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      // The rule of the form.
      inverse: true,
      min: 1, max: n, interval: 1,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { fontSize: 10, formatter: (v: number) => `#${v}` },
      splitLine: { lineStyle: { opacity: 0.3 } },
    },
    series: chart.series.map((s) => ({
      name: s.name,
      type: "line",
      // Straight between ranks, not smoothed: a spline through rank positions
      // draws an overshoot that reads as a crossing which did not happen.
      smooth: false,
      symbol: "circle",
      symbolSize: 8,
      lineStyle: { width: 2.5 },
      // A rank line must break where the segment was not reported, or it
      // teleports across the gap.
      connectNulls: false,
      emphasis: { focus: "series", lineStyle: { width: 4 } },
      endLabel: {
        show: true,
        fontSize: 10.5,
        distance: 8,
        formatter: "{a}",
        // Long vertical names would otherwise run past the canvas edge.
        overflow: "truncate",
        width: 120,
      },
      data: rankAt.get(s.name),
    })),
  };
}
