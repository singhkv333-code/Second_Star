"use client";



import * as React from "react";

import type { CompanyScores, ScoreQuadrant } from "@/lib/api";
import { PanelHead } from "./chrome";
import { AnalystConsensus } from "./AnalystConsensus";

const BAND_COLOR: Record<string, string> = {
  good: "var(--color-profit)",
  watch: "var(--color-warn)",
  risk: "var(--color-loss)",
};


function rupees(v: number): string {
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

function formatValue(q: ScoreQuadrant): string {
  if (q.value === null) return "—";
  if (q.format === "rupees") return rupees(q.value);
  // U+2212. A hyphen set at 38px sits too high and too short to read as a
  // sign — at this size the difference between -6.30 and −6.30 is the
  // difference between a typo and a negative number.
  const minus = (t: string) => t.replace(/^-/, "−");
  if (q.format === "pct") return minus(`${q.value.toFixed(1)}%`);
  return minus(q.value.toFixed(2));
}


function subline(q: ScoreQuadrant, price: number | null): string | null {
  if (q.value === null) return q.unavailable_reason;
  if (q.key === "graham" && price) {
    const gap = (price - q.value) / q.value * 100;
    return gap >= 0
      ? `Market price ${gap.toFixed(0)}% above model value`
      : `Market price ${Math.abs(gap).toFixed(0)}% below model value`;
  }
  if (q.key === "ohlson" && q.probability_pct !== undefined) {
    return `${q.probability_pct.toFixed(q.probability_pct < 1 ? 2 : 1)}% distress odds`;
  }
  if (q.key === "dupont" && q.margin_pct !== undefined) {
    return `${q.margin_pct.toFixed(1)}% × ${q.asset_turnover?.toFixed(2)} × ${q.equity_multiplier?.toFixed(2)}`;
  }
  return q.verdict ?? null;
}

export function SolvencyValuePanel({ data, price }: { data: CompanyScores; price: number | null }): React.ReactElement | null {
  const [selected, setSelected] = React.useState("");
  if (!data.available || !data.quadrants.length) return null;
  const active = data.quadrants.find(q => q.key === selected) ?? data.quadrants.find(q => q.value !== null) ?? data.quadrants[0]!;
  return <div>
    <PanelHead title="Solvency and value" right={<span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{data.period} · {data.basis === "standalone" ? "Standalone" : "Consolidated"}</span>} />
    <div className="solvency-layout" style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr) minmax(250px,.9fr)", gap: 24, marginTop: 16, alignItems: "start" }}>
      <div>{data.quadrants.map(q => <button key={q.key} type="button" aria-pressed={q.key === active.key} onClick={() => setSelected(q.key)}
        style={{ width: "100%", display: "grid", gridTemplateColumns: "1fr auto", gap: "5px 18px", textAlign: "left", padding: "14px 12px", border: 0, borderBottom: "1px solid var(--glass-border)", background: q.key === active.key ? "var(--surface-hover)" : "transparent", cursor: "pointer", color: "var(--text-primary)", borderRadius: 4 }}>
        <span style={{ fontSize: 13, fontWeight: 550 }}>{q.label}</span>
        <strong style={{ fontSize: 22, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{formatValue(q)}</strong>
        <span style={{ gridColumn: "1 / -1", fontSize: 11, color: q.band ? BAND_COLOR[q.band] : "var(--text-secondary)" }}>{subline(q, price)}</span>
      </button>)}</div>
      <div>
        <div style={{ fontSize: 12, fontWeight: 600, padding: "14px 0" }}>{active.label} · underlying ratios</div>
        {active.radar?.length ? active.radar.map(axis => <div key={axis.key} title={axis.detail} style={{ display: "flex", justifyContent: "space-between", gap: 20, padding: "12px 0", borderTop: "1px solid var(--glass-border)", fontSize: 12 }}>
          <span style={{ color: "var(--text-secondary)" }}>{axis.label}</span><strong style={{ fontVariantNumeric: "tabular-nums" }}>{axis.display}</strong>
        </div>) : <p style={{ fontSize: 12, color: "var(--text-secondary)" }}>{active.unavailable_reason || "Model inputs unavailable."}</p>}
      </div>
      <AnalystConsensus symbol={data.symbol} price={price} />
    </div>
    <style>{`@media(max-width:1100px){.solvency-layout{grid-template-columns:repeat(2,minmax(0,1fr))!important}}@media(max-width:720px){.solvency-layout{grid-template-columns:1fr!important;gap:16px!important}}`}</style>
  </div>;
}
