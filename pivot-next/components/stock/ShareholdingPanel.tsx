"use client";



import dynamic from "next/dynamic";
import * as React from "react";

import type { ShareholdingResponse } from "@/lib/api";
import { EmptyNote, PanelHead } from "./chrome";
import { num } from "./FinTable";

const EChart = dynamic(() => import("./EChart"), {
  ssr: false,
  loading: () => <div style={{ height: 250 }} />,
});




const OWNER_COLOR: Record<string, string> = {
  "Promoters": "#0e3a48",
  "Foreign institutions": "#2b8098",
  "Domestic institutions": "#5cb8ce",
  "Non-institutions": "#a5dae7",
  "Non-promoter non-public": "#d6eef4",
};
const OWNER_ORDER = [
  "Promoters", "Foreign institutions", "Domestic institutions",
  "Non-institutions", "Non-promoter non-public",
];


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
    () => (quarters.length > 1 ? riverOption(quarters, classes) : null),
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
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
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

      <div className="shp-grid" style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)", gap: 28, alignItems: "start" }}>
        <div style={{ minWidth: 0 }}>
          {option ? <EChart option={option} height={250} ariaLabel="Quarterly ownership percentages" /> : <EmptyNote>Ownership history unavailable.</EmptyNote>}
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", paddingBottom: 10, fontSize: 11, color: "var(--text-secondary)" }}>
            <span>Ownership</span><span>{data.quarter ? quarterLabel(data.quarter) : "Latest filing"}</span>
          </div>
          {groups.map((g) => <details key={g.label} style={{ borderTop: "1px solid var(--glass-border)", padding: "11px 0", fontSize: 12 }}>
            <summary style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: OWNER_COLOR[g.label], flexShrink: 0 }} />
              <span style={{ flex: 1 }}>{g.label}</span>
              <strong style={{ fontVariantNumeric: "tabular-nums" }}>{num(g.pct, { dp: 2, pct: true })}</strong>
              <span aria-hidden style={{ color: "var(--text-tertiary)" }}>⌄</span>
            </summary>
            {g.children.map((child) => <div key={child.label} style={{ display: "flex", justifyContent: "space-between", gap: 16, padding: "9px 0 0 16px", color: "var(--text-secondary)" }}>
              <span>{child.label}</span><span>{num(child.pct, { dp: 2, pct: true })}</span>
            </div>)}
          </details>)}
        </div>
      </div>

      {data.holders.length ? <HolderTable holders={data.holders} /> : null}

      <style>{`
        @media (max-width: 720px) {
          .shp-grid { grid-template-columns: 1fr !important; gap: 20px !important; }
          .shp-split { padding-top: 4px; }
        }
      `}</style>
    </div>
  );
}


function HolderTable({
  holders,
}: {
  holders: ShareholdingResponse["holders"];
}): React.ReactElement {
  const [all, setAll] = React.useState(false);
  const shown = all ? holders : holders.slice(0, 10);

  return (
    <div style={{ marginTop: 4, borderTop: "1px solid var(--glass-border)", paddingTop: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 6 }}>
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

      {/* One line per holder: a name and a number.
       *
       *  It used to be five things — a rule, a name, the holder's class on a
       *  second line, a 46px bar and the percentage — for one fact each, and
       *  four holders cost eight lines of text and four hairlines.
       *
       *  The bar went first: it was scaled against the largest holder, and
       *  with a promoter at 71% beside a fund at 1.3% every other bar was an
       *  empty track. A bar that is always empty is not a reading.
       *
       *  The class line went next. It is real information, so it moves to the
       *  row's title rather than being dropped — but it was a grey second line
       *  under every name, which is the pattern this page is trying not to
       *  have.
       */}
      <div className="shp-holders" style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", columnGap: 40 }}>
        {shown.map((h) => (
          <div
            key={h.name}
            title={holderClass(h.bucket) || undefined}
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              gap: 14,
              padding: "5px 0",
              minWidth: 0,
            }}
          >
            <span
              style={{
                fontSize: 12.5,
                color: "var(--text-primary)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                minWidth: 0,
              }}
            >
              {titleCase(h.name)}
            </span>
            <span style={{
              fontFamily: "var(--font-mono)", fontSize: 12,
              fontWeight: 600, fontVariantNumeric: "tabular-nums",
              color: "var(--text-primary)", whiteSpace: "nowrap",
              flexShrink: 0,
            }}>
              {num(h.pct, { dp: 2, pct: true })}
            </span>
          </div>
        ))}
      </div>

      <style>{`
        @media (max-width: 720px) {
          .shp-holders { grid-template-columns: minmax(0,1fr) !important; }
        }
      `}</style>
    </div>
  );
}


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


function riverOption(quarters: ShareholdingResponse["quarters"], classes: string[]): Record<string, unknown> {
  return {
    color: classes.map((c) => OWNER_COLOR[c] ?? "#8fa3ab"),
    grid: { left: 8, right: 8, top: 34, bottom: 8, containLabel: true },
    legend: { type: "scroll", top: 0, itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 10 } },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => v == null ? "Unavailable" : v.toFixed(2) + "%" },
    xAxis: { type: "category", data: quarters.map((q) => quarterLabel(q.quarter)), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { fontSize: 10, hideOverlap: true } },
    yAxis: { type: "value", max: 100, axisLabel: { formatter: "{value}%", fontSize: 10 }, splitLine: { lineStyle: { opacity: .25 } } },
    series: classes.map((c) => ({ name: c, type: "bar", stack: "ownership", barMaxWidth: 42, data: quarters.map((q) => typeof q[c] === "number" ? q[c] : null) })),
  };
}
