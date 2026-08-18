"use client";

/**
 * Delivery and open interest — whether a move was real.
 *
 * Price says a stock fell. It does not say whether anyone actually took
 * delivery of it, or whether the futures book grew into the fall. Those two
 * series answer that, and neither is derivable from the chart above.
 *
 * Delivery is drawn as bars against its own 20-day median rather than as a
 * line: the question is "was TODAY unusual", which is a comparison to a level,
 * and a line invites reading a trend into what is mostly noise. Open interest
 * is a line because it genuinely is a stock that accumulates.
 */

import dynamic from "next/dynamic";
import * as React from "react";

import type { FlowsResponse } from "@/lib/api";
import { EmptyNote, PanelHead, Segmented } from "./chrome";
import { num } from "./FinTable";

const EChart = dynamic(() => import("./EChart"), {
  ssr: false,
  loading: () => <div style={{ height: 240 }} />,
});

const DELIV = "#4F8A5B";
const DELIV_HI = "#C0A03C";
const OI_LINE = "#C4643F";

function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

/** Indian short scale — the counts here run to crores of shares. */
function compact(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e7) return `${(v / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${(v / 1e5).toFixed(2)} L`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return v.toLocaleString("en-IN");
}

type View = "delivery" | "oi";

export function FlowsPanel({ data }: { data: FlowsResponse }): React.ReactElement {
  const [view, setView] = React.useState<View>("delivery");
  const s = data.summary;

  const option = React.useMemo(() => {
    if (view === "delivery") {
      const rows = data.delivery.filter((r) => r.deliv_per !== null);
      return rows.length ? deliveryOption(rows, s?.delivery_median_20d ?? null) : null;
    }
    const rows = data.oi.filter((r) => r.oi !== null);
    return rows.length ? oiOption(rows) : null;
  }, [view, data, s]);

  if (!data.available || !s) {
    return <EmptyNote>No delivery or open-interest history available for this symbol.</EmptyNote>;
  }

  const aboveMedian =
    s.delivery_pct !== null && s.delivery_median_20d !== null
      ? s.delivery_pct - s.delivery_median_20d
      : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead
        title="Delivery and open interest"
        right={
          <Segmented
            value={view}
            options={[
              { value: "delivery", label: "Delivery" },
              { value: "oi", label: "Open interest" },
            ]}
            onChange={(v) => setView(v as View)}
          />
        }
      />

      {/* The readout sits on one rule, the way the technical panel's readings
          do — four numbers that share a scale of importance, not four cards. */}
      <div
        className="flows-stats"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
          borderTop: "1px solid var(--glass-border)",
          borderBottom: "1px solid var(--glass-border)",
        }}
      >
        <Stat
          label="Delivered"
          value={s.delivery_pct !== null ? num(s.delivery_pct, { dp: 2, pct: true }) : "—"}
          note={
            aboveMedian !== null ? (
              <span style={{ color: aboveMedian >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}>
                {aboveMedian >= 0 ? "+" : "−"}{Math.abs(aboveMedian).toFixed(2)} pp vs 20-day
              </span>
            ) : null
          }
        />
        <Stat label="Shares delivered" value={compact(s.delivered)} note={`of ${compact(s.volume)} traded`} divided />
        <Stat
          label="Futures OI"
          value={compact(s.oi)}
          note={
            s.oi_chg !== null && s.oi_chg !== undefined ? (
              <span style={{ color: s.oi_chg >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}>
                {s.oi_chg >= 0 ? "+" : "−"}{compact(Math.abs(s.oi_chg))} on the day
              </span>
            ) : null
          }
          divided
        />
        <Stat label="Trades" value={compact(s.trades)} note={s.date ? shortDate(s.date) : null} divided />
      </div>

      {option ? (
        <EChart
          option={option}
          height={240}
          ariaLabel={view === "delivery" ? "Delivery percentage by day" : "Futures open interest by day"}
        />
      ) : null}

      <style>{`
        @media (max-width: 720px) {
          .flows-stats { grid-template-columns: repeat(2, minmax(0,1fr)) !important; }
        }
      `}</style>
    </div>
  );
}

function Stat({
  label,
  value,
  note,
  divided = false,
}: {
  label: string;
  value: string;
  note?: React.ReactNode;
  divided?: boolean;
}): React.ReactElement {
  return (
    <div style={{ padding: "14px 18px", borderLeft: divided ? "1px solid var(--glass-border)" : undefined }}>
      <div style={{ fontSize: 10.5, fontWeight: 650, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>
        {label}
      </div>
      <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 19, fontWeight: 600, letterSpacing: "-0.01em", fontVariantNumeric: "tabular-nums", color: "var(--text-primary)" }}>
        {value}
      </div>
      {note ? (
        <div style={{ marginTop: 3, fontSize: 11, fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)" }}>{note}</div>
      ) : null}
    </div>
  );
}

/** Delivery bars, coloured against the running median.
 *
 *  A bar above the median is the event — a day when more of the volume changed
 *  hands for keeps than usually does — so it takes the warmer colour and the
 *  rest recede. The median itself is drawn as a mark line so the comparison is
 *  on the chart rather than in the reader's head.
 */
function deliveryOption(
  rows: { d: string; deliv_per: number | null; close: number | null }[],
  median: number | null,
): Record<string, unknown> {
  const ref = median ?? 0;
  return {
    grid: { left: 8, right: 8, top: 18, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: number | null) => (v === null || v === undefined ? "—" : `${v.toFixed(2)}%`),
    },
    xAxis: {
      type: "category",
      data: rows.map((r) => shortDate(r.d)),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { fontSize: 10.5, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { fontSize: 10.5, formatter: "{value}%" },
      splitLine: { lineStyle: { type: "dashed", opacity: 0.5 } },
    },
    series: [
      {
        name: "Delivery",
        type: "bar",
        barMaxWidth: 7,
        data: rows.map((r) => ({
          value: r.deliv_per,
          itemStyle: {
            color: (r.deliv_per ?? 0) >= ref ? DELIV_HI : DELIV,
            opacity: (r.deliv_per ?? 0) >= ref ? 1 : 0.55,
            borderRadius: [2, 2, 0, 0],
          },
        })),
        // The reference line inherits nothing usable: every bar carries its own
        // itemStyle, so ECharts has no series colour to lend the mark line and
        // it was drawn invisible. Both the colour and the label colour are set
        // outright here.
        markLine: median !== null ? {
          silent: true,
          symbol: "none",
          label: {
            formatter: `20-day median ${median.toFixed(1)}%`,
            fontSize: 10.5,
            position: "insideEndTop",
            color: "var(--text-secondary)",
          },
          lineStyle: { type: "dashed", width: 1, color: "#8A7B4F", opacity: 0.9 },
          data: [{ yAxis: median }],
        } : undefined,
      },
    ],
  };
}

/** Open interest as an area, with the daily change as the second read. */
function oiOption(rows: { d: string; oi: number | null; oi_chg: number | null }[]): Record<string, unknown> {
  return {
    grid: { left: 8, right: 8, top: 18, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: number | null) => compact(v),
    },
    xAxis: {
      type: "category",
      data: rows.map((r) => shortDate(r.d)),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { fontSize: 10.5, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { fontSize: 10.5, formatter: (v: number) => compact(v) },
      splitLine: { lineStyle: { type: "dashed", opacity: 0.5 } },
    },
    series: [
      {
        name: "Open interest",
        type: "line",
        symbol: "none",
        smooth: 0.2,
        lineStyle: { width: 1.6, color: OI_LINE },
        areaStyle: {
          opacity: 0.16,
          color: OI_LINE,
        },
        data: rows.map((r) => r.oi),
      },
    ],
  };
}
