"use client";



import * as React from "react";

import type { DealsResponse } from "@/lib/api";
import { EmptyNote, PanelHead } from "./chrome";

// The earth pair this started on — a muted moss against a terracotta — sat at
// nearly the same lightness and chroma, so the two legs of a block overlapped
// into one brown disc and the legend was doing all the work. These are far
// apart in hue and hold their own on either ground.
const BUY = "#30A46C";
const SELL = "#E5484D";

function dayLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

function crore(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
  return `${sign}₹${a.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}


const ACRONYMS = new Set([
  "ETF", "UTI", "LIC", "SBI", "NPS", "HDFC", "ICICI", "IDFC", "AMC",
  "LLP", "PTE", "PLC", "ODI", "LP", "NV", "BV", "SA", "AG", "II", "III",
]);

function cleanClient(raw: string): string {
  const stripped = raw.replace(/\s+[A-Z]{2,6}\d{6,}\s*$/, "").trim();
  return stripped
    .toLowerCase()
    .split(/\s+/)
    .map((w) => {
      const bare = w.replace(/[^a-z]/g, "").toUpperCase();
      if (ACRONYMS.has(bare)) return w.toUpperCase();
      return w.replace(/[a-z]/, (c) => c.toUpperCase());
    })
    .join(" ");
}

export function DealsPanel({ data }: { data: DealsResponse }): React.ReactElement {
  const [all, setAll] = React.useState(false);
  const [kind, setKind] = React.useState("all");
  const deals = [...(data.deals ?? [])].filter(d => kind === "all" || d.kind === kind).sort((a,b) => b.d.localeCompare(a.d));
  const shown = all ? deals : deals.slice(0, 8);
  return <div>
    <PanelHead title="Bulk and block deals" right={<select aria-label="Deal type" value={kind} onChange={e => { setKind(e.target.value); setAll(false); }} style={{ background: "var(--bg-secondary)", border: "1px solid var(--glass-border)", borderRadius: 8, padding: "7px 12px", fontSize: 12 }}><option value="all">All deals</option><option value="bulk">Bulk</option><option value="block">Block</option></select>} />
    {!deals.length ? <EmptyNote>No {kind === "all" ? "bulk or block" : kind} deals reported.</EmptyNote> : <div style={{ overflowX: "auto", marginTop: 16 }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 650 }}>
        <thead><tr>{["Date", "Participant", "Type", "Side", "Quantity", "Price", "Value"].map((h,i) => <th key={h} style={{ textAlign: i > 3 ? "right" : "left", padding: "10px 12px", fontWeight: 500, color: "var(--text-secondary)" }}>{h}</th>)}</tr></thead>
        <tbody>{shown.map((d,i) => <tr key={i} style={{ borderBottom: "1px solid var(--glass-border)" }}>
          <td style={{ padding: "13px 12px", whiteSpace: "nowrap", color: "var(--text-secondary)" }}>{dayLabel(d.d)}</td>
          <td style={{ padding: "13px 12px", maxWidth: 330 }} title={d.client}>{cleanClient(d.client)}</td>
          <td style={{ padding: "13px 12px", textTransform: "capitalize", color: "var(--text-secondary)" }}>{d.kind}</td>
          <td style={{ padding: "13px 12px", color: d.side?.toUpperCase() === "BUY" ? BUY : d.side?.toUpperCase() === "SELL" ? SELL : "var(--text-secondary)", fontWeight: 600 }}>{d.side || "—"}</td>
          <td style={{ padding: "13px 12px", textAlign: "right" }}>{d.qty?.toLocaleString("en-IN") ?? "—"}</td>
          <td style={{ padding: "13px 12px", textAlign: "right", whiteSpace: "nowrap" }}>{d.price == null ? "—" : "₹" + d.price.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</td>
          <td style={{ padding: "13px 12px", textAlign: "right", whiteSpace: "nowrap", fontWeight: 550 }}>{crore(d.value)}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    {deals.length > 8 && <button type="button" onClick={() => setAll(v => !v)} style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)" }}>{all ? "Show recent deals" : `Show all ${deals.length} deals`}</button>}
  </div>;
}
