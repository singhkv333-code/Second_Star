"use client";

/**
 * Bulk and block deals — the only public surface that names who traded size.
 *
 * This was a date rail with two rows under each date, and it was the third
 * thing on the page built out of exactly that: a label, a bar, a number. The
 * page does not need a fourth list — it needs this section to answer the
 * question the list could not, which is WHERE in the price history the size
 * changed hands.
 *
 * So the deals are plotted against price and time. A block at ₹774 in June and
 * one at ₹992 the previous November are the same row in a list and a very
 * different picture on an axis, and the axis is the picture the reader wants.
 *
 * Buy and sell legs of one block sit at identical coordinates, so the two
 * sides are drawn as separate series with opposite pixel offsets — otherwise
 * the second leg paints exactly over the first and half the deals vanish.
 */

import dynamic from "next/dynamic";
import * as React from "react";

import type { Deal, DealsResponse } from "@/lib/api";
import { EmptyNote, PanelHead } from "./chrome";

const EChart = dynamic(() => import("./EChart"), {
  ssr: false,
  loading: () => <div style={{ height: 260 }} />,
});

const BUY = "#4F8A5B";
const SELL = "#C4643F";

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

/** Client names arrive in block capitals with the exchange's own settlement id
 *  attached ("… MTBJ400045828"). The id is not part of anyone's name.
 *
 *  Cased uniformly. Keeping the exchange's vowel-less abbreviations in capitals
 *  produced "The Mtbj LTD. As TRST For GOVRNMNT Pension INVSTMNT Fund", which
 *  shouts in six places instead of none; only true initialisms are held back. */
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
  const [showList, setShowList] = React.useState(false);

  const deals = data.deals ?? [];

  /** Net and gross by counterparty. A fund that bought and sold the same
   *  quantity nets to nothing and belongs low in the list however loud its
   *  two rows looked; gross decides the ordering, net decides the colour. */
  const parties = React.useMemo(() => {
    const m = new Map<string, { name: string; net: number; gross: number; n: number }>();
    deals.forEach((d) => {
      const name = cleanClient(d.client);
      const v = d.value ?? 0;
      const signed = d.side?.toUpperCase() === "BUY" ? v : -v;
      const cur = m.get(name) ?? { name, net: 0, gross: 0, n: 0 };
      cur.net += signed;
      cur.gross += v;
      cur.n += 1;
      m.set(name, cur);
    });
    return [...m.values()].sort((a, b) => b.gross - a.gross);
  }, [deals]);

  const option = React.useMemo(() => (deals.length ? scatterOption(deals) : null), [deals]);

  if (!data.available || !deals.length) {
    return <EmptyNote>No bulk or block deals reported for this symbol.</EmptyNote>;
  }

  const maxNet = Math.max(...parties.map((p) => Math.abs(p.net)), 1);
  const topParties = parties.slice(0, 6);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead
        title="Bulk and block deals"
        right={
          <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 11.5, color: "var(--text-secondary)" }}>
            <Key tone={BUY} label="Buy" />
            <Key tone={SELL} label="Sell" />
          </div>
        }
      />

      {option ? (
        <EChart option={option} height={260} ariaLabel="Bulk and block deals by price and date" />
      ) : null}

      {/* Who was on the other side, netted. This is the part a list of matched
          pairs actively hides: both legs look equally large, and only the sum
          says which way a fund actually moved. */}
      <div style={{ borderTop: "1px solid var(--glass-border)", paddingTop: 14 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
          <span style={{ fontSize: 10.5, fontWeight: 650, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>
            Counterparties
          </span>
          <button
            type="button"
            onClick={() => setShowList((v) => !v)}
            style={{
              border: "none", background: "transparent", padding: 0, cursor: "pointer",
              fontFamily: "var(--font-ui)", fontSize: 11.5, color: "var(--text-secondary)",
            }}
          >
            {showList ? "Hide every deal" : `Show all ${deals.length} deals`}
          </button>
        </div>

        <div className="deal-parties" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", columnGap: 28 }}>
          {topParties.map((p) => {
            const tone = p.net > 0 ? BUY : p.net < 0 ? SELL : "var(--text-tertiary)";
            const half = Math.min(50, (Math.abs(p.net) / maxNet) * 50);
            return (
              <div
                key={p.name}
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0,1fr) 88px 82px",
                  gap: 12,
                  alignItems: "center",
                  minHeight: 36,
                  borderTop: "1px solid var(--glass-border)",
                }}
              >
                <span
                  style={{ fontSize: 12, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  title={p.name}
                >
                  {p.name}
                </span>
                {/* Diverging from a fixed centre: the side is the finding. */}
                <span style={{ position: "relative", height: 14, display: "block" }}>
                  <span style={{ position: "absolute", inset: 0, top: 5, height: 4, borderRadius: 2, background: "var(--bg-elevated)" }} />
                  <span style={{ position: "absolute", left: "50%", top: 0, width: 1, height: 14, background: "var(--glass-border-hover)" }} />
                  <span
                    style={{
                      position: "absolute", top: 5, height: 4, borderRadius: 2, background: tone,
                      left: p.net >= 0 ? "50%" : `${50 - half}%`, width: `${half}%`,
                    }}
                  />
                </span>
                <span style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 11.5, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: tone }}>
                  {crore(p.net)}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {showList ? <DealList deals={deals} /> : null}

      <style>{`
        @media (max-width: 720px) {
          .deal-parties { grid-template-columns: 1fr !important; }
        }
        @media (max-width: 640px) {
          .deal-day { grid-template-columns: 1fr !important; gap: 6px !important; }
        }
      `}</style>
    </div>
  );
}

function Key({ tone, label }: { tone: string; label: string }): React.ReactElement {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: tone }} />
      {label}
    </span>
  );
}

/** Every deal, grouped by day. Behind a toggle: it is the audit trail, not the
 *  headline, and it was the headline for exactly as long as it took to read it
 *  once. */
function DealList({ deals }: { deals: Deal[] }): React.ReactElement {
  const days = React.useMemo(() => {
    const out: { day: string; rows: Deal[] }[] = [];
    deals.forEach((d) => {
      const last = out[out.length - 1];
      if (last && last.day === d.d) last.rows.push(d);
      else out.push({ day: d.d, rows: [d] });
    });
    return out;
  }, [deals]);

  return (
    <div style={{ borderTop: "1px solid var(--glass-border)" }}>
      {days.map(({ day, rows }) => (
        <div
          key={day}
          className="deal-day"
          style={{
            display: "grid",
            gridTemplateColumns: "120px minmax(0, 1fr)",
            gap: 18,
            padding: "10px 0",
            borderBottom: "1px solid var(--glass-border)",
          }}
        >
          <div style={{ fontSize: 11.5, fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)", paddingTop: 1 }}>
            {dayLabel(day)}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
            {rows.map((d, i) => {
              const isBuy = d.side?.toUpperCase() === "BUY";
              return (
                <div key={`${d.client}-${d.side}-${i}`} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 16, alignItems: "baseline" }}>
                  <span style={{ display: "flex", alignItems: "baseline", gap: 8, minWidth: 0 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.05em", color: isBuy ? BUY : SELL, flexShrink: 0 }}>
                      {isBuy ? "BUY" : "SELL"}
                    </span>
                    <span
                      style={{ fontSize: 12.5, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={d.client}
                    >
                      {cleanClient(d.client)}
                    </span>
                    {d.kind === "bulk" ? (
                      <span style={{ fontSize: 10, color: "var(--text-tertiary)", flexShrink: 0 }}>bulk</span>
                    ) : null}
                  </span>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                    {d.qty?.toLocaleString("en-IN")} @ ₹{d.price?.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
                    <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>  {crore(d.value)}</span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Deals against price and time.
 *
 *  Bubble area — not radius — tracks deal value, so a block ten times the size
 *  looks ten times the size. ECharts sizes by diameter, hence the square root.
 */
function scatterOption(deals: Deal[]): Record<string, unknown> {
  const values = deals.map((d) => d.value ?? 0);
  const maxV = Math.max(...values, 1);
  const size = (v: number): number => 6 + Math.sqrt(v / maxV) * 22;

  const point = (d: Deal) => ({
    value: [`${d.d}T00:00:00`, d.price ?? 0],
    symbolSize: size(d.value ?? 0),
    // Bulk deals are the smaller, more frequent event; drawing them hollow
    // keeps them present without letting them crowd the blocks.
    itemStyle: {
      color: d.side?.toUpperCase() === "BUY" ? BUY : SELL,
      opacity: d.kind === "bulk" ? 0.45 : 0.78,
      borderColor: d.side?.toUpperCase() === "BUY" ? BUY : SELL,
      borderWidth: d.kind === "bulk" ? 1 : 0,
    },
    deal: d,
  });

  const buys = deals.filter((d) => d.side?.toUpperCase() === "BUY").map(point);
  const sells = deals.filter((d) => d.side?.toUpperCase() !== "BUY").map(point);

  return {
    grid: { left: 8, right: 16, top: 18, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "item",
      formatter: (p: { data?: { deal?: Deal } }) => {
        const d = p.data?.deal;
        if (!d) return "";
        const side = d.side?.toUpperCase() === "BUY" ? "Buy" : "Sell";
        return [
          `<div style="font-weight:600;margin-bottom:2px">${cleanClient(d.client)}</div>`,
          `<div>${side} · ${d.kind} · ${dayLabel(d.d)}</div>`,
          `<div>${d.qty?.toLocaleString("en-IN")} @ ₹${d.price?.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</div>`,
          `<div style="font-weight:600">${crore(d.value)}</div>`,
        ].join("");
      },
    },
    xAxis: {
      type: "time",
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { fontSize: 10.5, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { fontSize: 10.5, formatter: (v: number) => `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}` },
      splitLine: { lineStyle: { type: "dashed", opacity: 0.5 } },
    },
    series: [
      // Opposite offsets: both legs of a block share a date and a price, and
      // without this the second one paints exactly over the first.
      { name: "Buy", type: "scatter", data: buys, symbolOffset: [3, 0], emphasis: { scale: 1.15 } },
      { name: "Sell", type: "scatter", data: sells, symbolOffset: [-3, 0], emphasis: { scale: 1.15 } },
    ],
  };
}
