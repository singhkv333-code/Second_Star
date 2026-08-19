"use client";

/**
 * Shareholding — who owns the company, and which way they are moving.
 *
 * Same shape as the segment mix above it: the stacked series is the argument,
 * the right-hand column reads the latest column out. That is deliberate — two
 * panels answering "what is this made of, and how is that changing" should not
 * invent two different ways of showing it.
 *
 * The colours are NOT the shared chart ramp. That ramp opens on the product's
 * blue, and every band here is an owner class the reader has to hold in their
 * head across three views (the stack, the split bars, the holder rows), so the
 * mapping is fixed by LABEL rather than by index. A company with no promoter
 * would otherwise shift every other class one colour to the left and quietly
 * repaint the whole section.
 */

import dynamic from "next/dynamic";
import * as React from "react";

import type { ShareholdingResponse } from "@/lib/api";
import { EmptyNote, PanelHead } from "./chrome";
import { num } from "./FinTable";

const EChart = dynamic(() => import("./EChart"), {
  ssr: false,
  loading: () => <div style={{ height: 300 }} />,
});

/** Fixed by owner class, not by position. */
const OWNER_COLOR: Record<string, string> = {
  "Promoters": "#8A6D3B",
  "Foreign institutions": "#C4643F",
  "Domestic institutions": "#4F8A5B",
  "Non-institutions": "#C0A03C",
  "Non-promoter non-public": "#7A7268",
};
const OWNER_ORDER = [
  "Promoters", "Foreign institutions", "Domestic institutions",
  "Non-institutions", "Non-promoter non-public",
];

/** The holder bucket arrives as an XBRL member name in PascalCase. */
function holderClass(bucket: string | null): string {
  if (!bucket) return "";
  return bucket
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/Or U[Tt][Ii]/, "/ UTI")
    .replace("Institutions Foreign Portfolio Investor One", "FPI category I")
    .replace("Institutions Foreign Portfolio Investor Two", "FPI category II")
    .replace("Mutual Funds", "Mutual fund");
}

function quarterLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  // en-IN abbreviates September as "Sept", which is four characters where
  // every other month is three — enough to make one axis tick sit wider than
  // its neighbours. Clipped to three so the axis stays even.
  const mon = d.toLocaleDateString("en-IN", { month: "short" }).slice(0, 3);
  return `${mon} ${d.toLocaleDateString("en-IN", { year: "2-digit" })}`;
}

export function ShareholdingPanel({
  data,
}: {
  data: ShareholdingResponse;
}): React.ReactElement {
  const quarters = data.quarters ?? [];
  const groups = data.groups ?? [];

  // Which owner classes this company actually files. A class present in one
  // quarter and absent in another still gets a band — the gap is drawn null.
  const classes = React.useMemo(
    () => OWNER_ORDER.filter((c) => quarters.some((q) => typeof q[c] === "number")),
    [quarters],
  );

  const option = React.useMemo(
    () => (quarters.length > 1 ? stackedOption(quarters, classes) : null),
    [quarters, classes],
  );

  // The one movement worth naming, computed the same way the mix panel does it.
  const shift = React.useMemo(() => {
    if (quarters.length < 2) return null;
    const last = quarters[quarters.length - 1]!;
    const prev = quarters[quarters.length - 2]!;
    const moves = classes
      .map((c) => {
        const a = last[c], b = prev[c];
        return typeof a === "number" && typeof b === "number"
          ? { name: c, delta: a - b } : null;
      })
      .filter((v): v is { name: string; delta: number } => v !== null)
      .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
    return moves[0] ?? null;
  }, [quarters, classes]);

  if (!data.available || !groups.length) {
    return <EmptyNote>No shareholding filings available for this company.</EmptyNote>;
  }

  const pledge = data.pledge_pct;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead
        title="Shareholding"
        right={
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            {pledge !== null && pledge !== undefined ? (
              <span
                style={{
                  fontSize: 11.5,
                  fontVariantNumeric: "tabular-nums",
                  color: pledge > 0 ? "var(--color-warn)" : "var(--text-secondary)",
                }}
              >
                Promoter pledge <strong style={{ fontWeight: 600 }}>{num(pledge, { dp: 2, pct: true })}</strong>
              </span>
            ) : null}
            {shift ? (
              <span style={{ fontSize: 11.5, color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>
                <span style={{ color: shift.delta >= 0 ? "var(--color-profit)" : "var(--color-loss)", fontWeight: 600 }}>
                  {shift.delta >= 0 ? "+" : ""}{shift.delta.toFixed(2)} pp
                </span>{" "}
                {shift.name} vs prior quarter
              </span>
            ) : null}
          </div>
        }
      />

      <div
        className="shp-grid"
        style={{ display: "grid", gridTemplateColumns: "minmax(0, 2.1fr) minmax(0, 1fr)", gap: 28 }}
      >
        {/* The chart takes its height from the row, which the readout beside
            it sets. Fixed at 300 it left a half-screen of white under itself
            for any company that files a deep breakdown — the two columns are
            one object, and one of them ending two-thirds early reads as a
            rendering fault rather than as a chart that happens to be short.
            The floor keeps it usable once the columns stack on a phone, where
            the row is no longer sized by anything. */}
        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", minHeight: 300 }}>
          {option ? (
            <div style={{ flex: 1, minHeight: 300 }}>
              <EChart option={option} height="100%" ariaLabel="Shareholding by owner class over time" />
            </div>
          ) : null}
        </div>

        {/* The latest column, two levels deep. The parent carries the bar; the
            children sit under it as plain rows, because a second bar at a
            second scale inside the first one is a chart, not a readout. */}
        <div
          className="shp-split"
          style={{
            borderLeft: "1px solid var(--glass-border)",
            paddingLeft: 24,
            display: "flex",
            flexDirection: "column",
            gap: 14,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-tertiary)" }}>
            <span>{data.quarter ? quarterLabel(data.quarter) : "Latest"}</span>
            <span>Share</span>
          </div>

          {groups.map((g) => (
            <div key={g.label} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ fontSize: 12, color: "var(--text-primary)" }}>{g.label}</span>
                <span style={{ fontSize: 12, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: "var(--text-primary)" }}>
                  {num(g.pct, { dp: 2, pct: true })}
                </span>
              </div>
              <div style={{ height: 4, borderRadius: 2, background: "var(--bg-elevated)" }}>
                <div
                  style={{
                    width: `${Math.min(100, g.pct)}%`,
                    height: "100%",
                    borderRadius: 2,
                    background: OWNER_COLOR[g.label] ?? "#7A7268",
                  }}
                />
              </div>
              {g.children.length ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 2 }}>
                  {g.children.map((c) => (
                    <div key={c.label} style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{c.label}</span>
                      <span style={{ fontSize: 11, fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)" }}>
                        {num(c.pct, { dp: 2, pct: true })}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      {data.holders.length ? <HolderTable holders={data.holders} /> : null}

      <style>{`
        @media (max-width: 720px) {
          .shp-grid { grid-template-columns: 1fr !important; gap: 20px !important; }
          .shp-split {
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

/** Named holders above the disclosure threshold.
 *
 *  Two columns of rows rather than one long list: at ten-plus holders a single
 *  column pushes the section past a screen for a table nobody scrolls, and the
 *  ranking is still readable read-down-then-across.
 */
function HolderTable({
  holders,
}: {
  holders: ShareholdingResponse["holders"];
}): React.ReactElement {
  const [all, setAll] = React.useState(false);
  const shown = all ? holders : holders.slice(0, 10);
  const max = Math.max(...holders.map((h) => h.pct ?? 0), 1);

  return (
    <div style={{ marginTop: 4, borderTop: "1px solid var(--glass-border)", paddingTop: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
        <span style={{ fontSize: 10.5, fontWeight: 650, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>
          Named holders
        </span>
        {holders.length > 10 ? (
          <button
            type="button"
            onClick={() => setAll((v) => !v)}
            style={{
              border: "none", background: "transparent", cursor: "pointer",
              fontFamily: "var(--font-ui)", fontSize: 11.5, color: "var(--text-secondary)",
            }}
          >
            {all ? "Show top 10" : `Show all ${holders.length}`}
          </button>
        ) : null}
      </div>

      <div className="shp-holders" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", columnGap: 28 }}>
        {shown.map((h) => (
          <div
            key={h.name}
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0,1fr) auto",
              gap: 12,
              alignItems: "center",
              minHeight: 34,
              borderTop: "1px solid var(--glass-border)",
            }}
          >
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-primary)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={h.name}
              >
                {titleCase(h.name)}
              </div>
              <div style={{ fontSize: 10.5, color: "var(--text-secondary)" }}>{holderClass(h.bucket)}</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ width: 46, height: 4, borderRadius: 2, background: "var(--bg-elevated)" }}>
                <div
                  style={{
                    width: `${((h.pct ?? 0) / max) * 100}%`,
                    height: "100%",
                    borderRadius: 2,
                    background: "#4F8A5B",
                  }}
                />
              </div>
              <span style={{ width: 48, textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: "var(--text-primary)" }}>
                {num(h.pct, { dp: 2, pct: true })}
              </span>
            </div>
          </div>
        ))}
      </div>

      <style>{`
        @media (max-width: 720px) {
          .shp-holders { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
  );
}

/** Filings name holders in block capitals. Rendered raw they shout down every
 *  other row on the page, so they are cased back — with the initialisms that
 *  genuinely are acronyms left alone. */
const KEEP_CAPS = new Set([
  "ETF", "UTI", "LIC", "NPS", "SBI", "HDFC", "ICICI", "IDFC", "AMC", "A/C",
  "PLC", "LTD", "INC", "NV", "BV", "SA", "AG", "US", "UK", "EM", "MF", "II", "III", "IV",
]);
function titleCase(s: string): string {
  return s
    .toLowerCase()
    .split(/\s+/)
    .map((w) => {
      const bare = w.replace(/[^A-Za-z/]/g, "").toUpperCase();
      if (KEEP_CAPS.has(bare)) return w.toUpperCase();
      return w.replace(/[a-z]/, (c) => c.toUpperCase());
    })
    .join(" ");
}

/** Stacked area over quarters.
 *
 *  Stacked rather than overlaid because the classes sum to the whole company:
 *  the reader's question is "how is the cake divided", and overlaid lines make
 *  that a mental addition. The 100% ceiling is fixed so a quarter that filed a
 *  class the others did not cannot rescale the whole chart.
 */
function stackedOption(
  quarters: ShareholdingResponse["quarters"],
  classes: string[],
): Record<string, unknown> {
  const axis = quarters.map((q) => quarterLabel(q.quarter));
  return {
    color: classes.map((c) => OWNER_COLOR[c] ?? "#7A7268"),
    // The legend sits ABOVE the plot, not below it. Below, `containLabel`
    // reserves only enough room for the axis text, so the legend was drawn
    // straight through the quarter labels. Above, it has the top band to
    // itself and the axis keeps its own line. `right: 20` is for the last
    // tick, which is centred on the final point and otherwise loses its year
    // over the edge of the canvas.
    grid: { left: 8, right: 20, top: 34, bottom: 4, containLabel: true },
    legend: {
      type: "scroll", top: 0, left: 0, itemWidth: 8, itemHeight: 8, itemGap: 16,
      icon: "circle", textStyle: { fontSize: 11 },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line" },
      valueFormatter: (v: number | null) => (v === null || v === undefined ? "—" : `${v.toFixed(2)}%`),
    },
    xAxis: {
      type: "category",
      data: axis,
      boundaryGap: false,
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { fontSize: 10.5 },
    },
    yAxis: {
      type: "value",
      max: 100,
      splitNumber: 4,
      axisLabel: { fontSize: 10.5, formatter: "{value}%" },
      splitLine: { lineStyle: { type: "dashed", opacity: 0.5 } },
    },
    series: classes.map((c) => ({
      name: c,
      type: "line",
      stack: "owners",
      areaStyle: { opacity: 0.9 },
      lineStyle: { width: 0 },
      symbol: "none",
      smooth: 0.2,
      emphasis: { focus: "series" },
      data: quarters.map((q) => (typeof q[c] === "number" ? q[c] : null)),
    })),
  };
}
