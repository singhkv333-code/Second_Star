"use client";

/**
 * Segment mix — where the money comes from, and how that has moved.
 *
 * This was a stacked area with a split-bar column beside it, and it did not
 * survive contact with a real filing. HDFC Bank's retail loan book files
 * eleven segments; eight of them are zero in the latest period, the largest is
 * 55%, and the report dates are irregular (Mar '18, Mar '20, Sep '21 …). A
 * stacked area of that is eleven bands, most of them slivers, with a tooltip
 * listing eight zeroes — and the smooth interpolation between two reports
 * three years apart asserts a continuity the data does not have.
 *
 * So it is ranked small multiples instead, which is the standard answer when a
 * composition has too many categories to stack: every segment gets its own
 * baseline, so a 0.7% line is as readable as a 55% one, and the ordering does
 * the work the stack was failing to do. Each row carries the level (the
 * number), the shape (its own sparkline) and the move (the delta) — which is
 * everything the two old columns said, on one line each.
 *
 * The sparkline is scaled to its OWN range, not a shared 0–100. Shared, every
 * segment under about five percent is a flat line on the floor; per-row, the
 * shape is legible and the magnitude comes from the number beside it. That is
 * the sparkline convention, and it is why the number is never omitted.
 */

import * as React from "react";

import type { MixChart, MixResponse } from "@/lib/api";
import { EmptyNote, PanelHead, Segmented } from "./chrome";
import { num } from "./FinTable";

/** Non-blue, fixed by position within the ranked list. Ranking is stable
 *  across renders, so a segment keeps its colour while the panel is open. */
const RAMP = [
  "#C4643F", "#4F8A5B", "#C0A03C", "#8A6D3B", "#9A5F7A",
  "#5F7A6E", "#B07C4A", "#7A7268", "#8C6F5B", "#6E7F52", "#A8785F",
];

const MAX_ROWS = 8;

function monthLabel(t: number): string {
  const d = new Date(t);
  const m = d.toLocaleString("en-GB", { month: "short" }).slice(0, 3);
  return `${m} '${String(d.getFullYear()).slice(2)}`;
}

export function MixPanel({ data }: { data: MixResponse }): React.ReactElement {
  const charts = data.charts ?? [];
  // Open on the breakdown that says the most, not the one that happens to be
  // first. TCS's "Product Wise Break-Up" is a 98/2 split — technically a
  // breakdown, visually a solid block — while "Verticals" carries eight
  // segments and the actual shape of the business.
  const richest = React.useMemo(
    () => charts.reduce<MixChart | undefined>(
      (best, c) => (!best || c.series.length > best.series.length ? c : best),
      undefined),
    [charts],
  );
  const [id, setId] = React.useState<string>(String(richest?.id ?? charts[0]?.id ?? "0"));
  const [all, setAll] = React.useState(false);
  const chart: MixChart | undefined =
    charts.find((c) => String(c.id) === id) ?? richest ?? charts[0];

  /** One row per segment: its history, its latest level, its move. */
  const rows = React.useMemo(() => {
    if (!chart) return [];
    const current = new Map(chart.current.map((c) => [c.name, c.pct]));
    return chart.series
      .map((s) => {
        const pts = [...s.points].sort((a, b) => a.t - b.t);
        const last = pts[pts.length - 1];
        const prev = pts[pts.length - 2];
        return {
          name: s.name,
          pct: current.get(s.name) ?? last?.pct ?? 0,
          delta: last && prev ? last.pct - prev.pct : null,
          points: pts,
        };
      })
      .sort((a, b) => b.pct - a.pct);
  }, [chart]);

  const span = React.useMemo(() => {
    if (!chart) return null;
    const ts = chart.series.flatMap((s) => s.points.map((p) => p.t)).sort((a, b) => a - b);
    return ts.length > 1 ? { from: ts[0]!, to: ts[ts.length - 1]! } : null;
  }, [chart]);

  if (!charts.length || !chart) {
    return <EmptyNote>No segment breakdown available for this company.</EmptyNote>;
  }

  const shown = all ? rows : rows.slice(0, MAX_ROWS);
  const biggestMove = rows
    .filter((r) => r.delta !== null)
    .sort((a, b) => Math.abs(b.delta!) - Math.abs(a.delta!))[0];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead title="Segment mix" />

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        {charts.length > 6 ? (
          <select
            value={id}
            onChange={(e) => { setId(e.target.value); setAll(false); }}
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
            onChange={(v) => { setId(v); setAll(false); }}
          />
        ) : (
          <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-secondary)" }}>{chart.title}</div>
        )}
        {biggestMove?.delta != null ? (
          <div style={{ fontSize: 11.5, color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>
            <span style={{ color: biggestMove.delta >= 0 ? "var(--color-profit)" : "var(--color-loss)", fontWeight: 600 }}>
              {biggestMove.delta >= 0 ? "+" : ""}{biggestMove.delta.toFixed(1)} pp
            </span>{" "}
            {biggestMove.name} vs prior report
          </div>
        ) : null}
      </div>

      <div style={{ borderTop: "1px solid var(--glass-border)" }}>
        {/* The span sits on the header row, so each sparkline does not have to
            carry its own axis to say what window it covers. */}
        <div
          className="mix-row"
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(120px, 300px) 132px minmax(0, 1fr) 64px 66px",
            gap: 16,
            alignItems: "center",
            padding: "8px 0",
            fontSize: 10.5,
            fontWeight: 650,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            color: "var(--text-tertiary)",
            borderBottom: "1px solid var(--glass-border)",
          }}
        >
          <span>Segment</span>
          <span style={{ textAlign: "center" }}>
            {span ? `${monthLabel(span.from)} — ${monthLabel(span.to)}` : "History"}
          </span>
          {/* Spacer. The name and its sparkline belong together on the left;
              the numbers belong on the right. Without a flexible cell between
              them the name column absorbs the whole panel width and pushes the
              sparkline a third of a metre away from the thing it describes. */}
          <span />
          <span style={{ textAlign: "right" }}>Share</span>
          <span style={{ textAlign: "right" }}>Change</span>
        </div>

        {shown.map((r, i) => (
          <div
            key={r.name}
            className="mix-row"
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(120px, 300px) 132px minmax(0, 1fr) 64px 66px",
              gap: 16,
              alignItems: "center",
              minHeight: 40,
              borderTop: i ? "1px solid var(--glass-border)" : undefined,
            }}
          >
            <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
              <span style={{ width: 7, height: 7, borderRadius: 2, flexShrink: 0, background: RAMP[i % RAMP.length] }} />
              <span
                style={{ fontSize: 12.5, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                title={r.name}
              >
                {r.name}
              </span>
            </span>

            <Spark points={r.points} color={RAMP[i % RAMP.length]!} />

            <span />

            <span style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12.5, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: "var(--text-primary)" }}>
              {num(r.pct, { dp: 1, pct: true })}
            </span>

            <span
              style={{
                textAlign: "right",
                fontFamily: "var(--font-mono)",
                fontSize: 11.5,
                fontVariantNumeric: "tabular-nums",
                color: r.delta === null ? "var(--text-tertiary)"
                  : r.delta > 0 ? "var(--color-profit)"
                  : r.delta < 0 ? "var(--color-loss)" : "var(--text-secondary)",
              }}
            >
              {r.delta === null ? "—" : `${r.delta > 0 ? "+" : r.delta < 0 ? "−" : ""}${Math.abs(r.delta).toFixed(1)} pp`}
            </span>
          </div>
        ))}
      </div>

      {rows.length > MAX_ROWS ? (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          style={{
            alignSelf: "flex-start", border: "none", background: "transparent", padding: 0,
            cursor: "pointer", fontFamily: "var(--font-ui)", fontSize: 11.5, color: "var(--text-secondary)",
          }}
        >
          {all ? `Show top ${MAX_ROWS}` : `Show all ${rows.length}`}
        </button>
      ) : null}

      <style>{`
        @media (max-width: 640px) {
          .mix-row { grid-template-columns: minmax(0,1fr) 78px 0 56px 58px !important; gap: 10px !important; }
        }
      `}</style>
    </div>
  );
}

/** One segment's own history, drawn to its own range.
 *
 *  Inline SVG rather than eleven chart instances: at this size a canvas buys
 *  nothing and costs a renderer per row. Points are placed by TIME, not by
 *  index — the reports are years apart in places, and spacing them evenly
 *  would draw a three-year gap the same width as a six-month one.
 */
function Spark({
  points,
  color,
}: {
  points: { t: number; pct: number }[];
  color: string;
}): React.ReactElement {
  const W = 132;
  const H = 26;
  const PAD = 2;

  if (points.length < 2) return <span style={{ width: W }} />;

  const t0 = points[0]!.t;
  const t1 = points[points.length - 1]!.t;
  const tSpan = t1 - t0 || 1;
  const lo = Math.min(...points.map((p) => p.pct));
  const hi = Math.max(...points.map((p) => p.pct));
  // A segment that never moves would divide by zero and, drawn to its own
  // range, would also become a full-height band saying nothing. Flat gets a
  // flat line through the middle.
  const vSpan = hi - lo;
  const x = (t: number) => PAD + ((t - t0) / tSpan) * (W - PAD * 2);
  const y = (v: number) =>
    vSpan === 0 ? H / 2 : H - PAD - ((v - lo) / vSpan) * (H - PAD * 2);

  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.pct).toFixed(1)}`).join(" ");
  const area = `${line} L${x(t1).toFixed(1)},${H} L${x(t0).toFixed(1)},${H} Z`;
  const last = points[points.length - 1]!;

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-hidden="true" style={{ display: "block", margin: "0 auto" }}>
      <path d={area} fill={color} opacity={0.14} />
      <path d={line} fill="none" stroke={color} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(last.t)} cy={y(last.pct)} r={2.1} fill={color} />
    </svg>
  );
}
