"use client";

/**
 * Bulk and block deals — the only public surface that names who traded size.
 *
 * Both legs of a block are separate rows in the source and stay separate here.
 * Collapsing a matched pair into one line would hide which side a named fund
 * was on, which is the entire reason to look at this table.
 *
 * A deal is a moment, so the layout is a timeline rather than a grid: the date
 * carries the left rail, and the rows under it are what happened that day.
 */

import * as React from "react";

import type { Deal, DealsResponse } from "@/lib/api";
import { EmptyNote, PanelHead, Segmented } from "./chrome";

const BUY = "var(--color-profit)";
const SELL = "var(--color-loss)";

function dayLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

function crore(v: number | null): string {
  if (v === null || v === undefined) return "—";
  if (Math.abs(v) >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
  if (Math.abs(v) >= 1e5) return `₹${(v / 1e5).toFixed(2)} L`;
  return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

/** Client names arrive in block capitals with the exchange's own account
 *  suffixes attached ("… MTBJ400045828"). The suffix is a settlement id, not
 *  part of anyone's name, so it is dropped and the rest cased back.
 *
 *  Cased UNIFORMLY. The obvious refinement — keep the vowel-less abbreviations
 *  the exchange uses ("TRST", "INVSTMNT") in capitals — produced rows reading
 *  "The Mtbj LTD. As TRST For GOVRNMNT Pension INVSTMNT Fund", which shouts in
 *  six places instead of one. A name that is uniformly quiet is easier to read
 *  than one that tries to preserve a source's own inconsistency. Only true
 *  initialisms are held back.
 */
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
      // Capitalise the first LETTER, not the first character — "(singapore)"
      // starts with a bracket, and charAt(0) left it lower-case.
      return w.replace(/[a-z]/, (c) => c.toUpperCase());
    })
    .join(" ");
}

type Filter = "all" | "block" | "bulk";

export function DealsPanel({ data }: { data: DealsResponse }): React.ReactElement {
  const [filter, setFilter] = React.useState<Filter>("all");
  const [all, setAll] = React.useState(false);

  const deals = React.useMemo(
    () => (filter === "all" ? data.deals : data.deals.filter((d) => d.kind === filter)),
    [data.deals, filter],
  );

  // Grouped by day, newest first — the source is already in that order.
  const days = React.useMemo(() => {
    const out: { day: string; rows: Deal[] }[] = [];
    deals.forEach((d) => {
      const last = out[out.length - 1];
      if (last && last.day === d.d) last.rows.push(d);
      else out.push({ day: d.d, rows: [d] });
    });
    return out;
  }, [deals]);

  const shown = all ? days : days.slice(0, 6);
  const kinds = new Set(data.deals.map((d) => d.kind));

  if (!data.available || !data.deals.length) {
    return <EmptyNote>No bulk or block deals reported for this symbol.</EmptyNote>;
  }

  const biggest = Math.max(...data.deals.map((d) => d.value ?? 0), 1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead
        title="Bulk and block deals"
        right={
          kinds.size > 1 ? (
            <Segmented
              value={filter}
              options={[
                { value: "all", label: "All" },
                { value: "block", label: "Block" },
                { value: "bulk", label: "Bulk" },
              ]}
              onChange={(v) => setFilter(v as Filter)}
            />
          ) : undefined
        }
      />

      <div style={{ borderTop: "1px solid var(--glass-border)" }}>
        {shown.map(({ day, rows }) => (
          <div
            key={day}
            className="deal-day"
            style={{
              display: "grid",
              gridTemplateColumns: "128px minmax(0, 1fr)",
              gap: 20,
              padding: "13px 0",
              borderBottom: "1px solid var(--glass-border)",
            }}
          >
            <div style={{ fontSize: 11.5, fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)", paddingTop: 2 }}>
              {dayLabel(day)}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 9, minWidth: 0 }}>
              {rows.map((d, i) => (
                <DealRow key={`${d.client}-${d.side}-${i}`} deal={d} biggest={biggest} />
              ))}
            </div>
          </div>
        ))}
      </div>

      {days.length > 6 ? (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          style={{
            alignSelf: "flex-start",
            border: "none",
            background: "transparent",
            padding: 0,
            cursor: "pointer",
            fontFamily: "var(--font-ui)",
            fontSize: 11.5,
            color: "var(--text-secondary)",
          }}
        >
          {all ? "Show recent" : `Show all ${days.length} days`}
        </button>
      ) : null}

      <style>{`
        @media (max-width: 640px) {
          .deal-day { grid-template-columns: 1fr !important; gap: 8px !important; }
        }
      `}</style>
    </div>
  );
}

function DealRow({ deal, biggest }: { deal: Deal; biggest: number }): React.ReactElement {
  const isBuy = deal.side?.toUpperCase() === "BUY";
  const tone = isBuy ? BUY : SELL;
  const width = Math.max(3, ((deal.value ?? 0) / biggest) * 100);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 16, alignItems: "center" }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          {/* The side is the first thing read, so it carries the colour and
              nothing else on the row competes for it. */}
          <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: "0.05em", color: tone, flexShrink: 0 }}>
            {isBuy ? "BUY" : "SELL"}
          </span>
          <span
            style={{
              fontSize: 12.5,
              color: "var(--text-primary)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={deal.client}
          >
            {cleanClient(deal.client)}
          </span>
          {deal.kind === "bulk" ? (
            <span style={{ fontSize: 10, color: "var(--text-tertiary)", flexShrink: 0 }}>bulk</span>
          ) : null}
        </div>
        {/* The bar is the deal's size relative to the largest on record, which
            is the only way a ₹785 Cr block and a ₹4 Cr one read differently at
            a glance. */}
        <div style={{ height: 3, marginTop: 5, borderRadius: 2, background: "var(--bg-elevated)" }}>
          <div style={{ width: `${width}%`, height: "100%", borderRadius: 2, background: tone, opacity: 0.7 }} />
        </div>
      </div>
      <div style={{ textAlign: "right", flexShrink: 0 }}>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 12.5, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: "var(--text-primary)" }}>
          {crore(deal.value)}
        </div>
        <div style={{ fontSize: 10.5, fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)" }}>
          {deal.qty?.toLocaleString("en-IN")} @ ₹{deal.price?.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
        </div>
      </div>
    </div>
  );
}
