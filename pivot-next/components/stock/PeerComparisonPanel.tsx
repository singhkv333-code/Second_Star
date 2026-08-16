"use client";

/**
 * Peer comparison — one table, four readings of it.
 *
 * The panel is deliberately NOT a chart with a table behind a toggle. A peer
 * set is read by comparing rows against each other down a column, and that is
 * a table's job; a grouped bar chart of six companies across eight metrics is
 * the same numbers with the comparison made harder.
 *
 * Four tabs rather than a column picker. "Customise fields" put the reader in
 * charge of assembling a view before they could read one — the useful views
 * are known, so they are named: what it is worth (Overall), how it has traded
 * (Performance), what it earns (Fundamentals), and where it sits (Technicals).
 *
 * Six peers, ranked by market cap by the server. Past about half a dozen names
 * a reader stops comparing and starts scrolling.
 *
 * Every number is reported. Fundamentals come from the filings database and
 * price columns from a year of daily closes, both computed server-side — the
 * panel formats and never derives, so it cannot disagree with the chart above
 * it or the chat beside it.
 */

import * as React from "react";
import Link from "next/link";

import { CompanyLogo } from "@/components/CompanyLogo";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";
import { getStockPeers, type PeerComparisonResponse, type PeerPrice } from "@/lib/api";
import { isError } from "@/lib/types";
import { EmptyNote, PanelHead, PanelSkeleton, Segmented } from "./chrome";

// One request covers all four tabs: eight fundamentals (the server's cap) plus
// the price block it now returns unasked. Switching tabs is then a re-render,
// not a round trip.
const FIELDS = [
  "market_cap", "net_profit", "roe", "net_profit_margin",
  "debt_to_equity", "price_to_book", "ev_to_ebitda", "eps_basic",
];

type TabId = "overall" | "performance" | "fundamentals" | "technicals";
const TABS: { value: TabId; label: string }[] = [
  { value: "overall", label: "Overall" },
  { value: "performance", label: "Performance" },
  { value: "fundamentals", label: "Fundamentals" },
  { value: "technicals", label: "Technicals" },
];

/** A column knows how to pull its own number and how to print it. `tone`
 *  marks the columns where a sign carries meaning — a return is red or green,
 *  a P/E is neither. */
type Col = {
  id: string;
  label: string;
  get: (row: Row) => number | null;
  fmt: (v: number) => string;
  tone?: boolean;
};

type Row = PeerComparisonResponse["peers"][number];

const px = (r: Row): PeerPrice | undefined => r.price;

const inr = (v: number): string =>
  v.toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const pct = (v: number): string => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
const mult = (v: number): string => `${v.toFixed(2)}×`;
/** Market cap arrives in rupees; crore is the unit an Indian reader compares in. */
const cr = (v: number): string =>
  `${(v / 1e7).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

const C = {
  price: { id: "price", label: "Price", get: (r: Row) => px(r)?.price ?? null, fmt: inr },
  mcap: { id: "mcap", label: "Mkt cap ₹cr", get: (r: Row) => r.values.market_cap ?? null, fmt: cr },
  pb: { id: "pb", label: "P/B", get: (r: Row) => r.values.price_to_book ?? null, fmt: mult },
  roe: { id: "roe", label: "ROE", get: (r: Row) => r.values.roe ?? null, fmt: (v: number) => `${v.toFixed(1)}%` },
  npm: { id: "npm", label: "Net margin", get: (r: Row) => r.values.net_profit_margin ?? null, fmt: (v: number) => `${v.toFixed(1)}%` },
  de: { id: "de", label: "Debt / equity", get: (r: Row) => r.values.debt_to_equity ?? null, fmt: mult },
  ev: { id: "ev", label: "EV / EBITDA", get: (r: Row) => r.values.ev_to_ebitda ?? null, fmt: mult },
  np: { id: "np", label: "Net profit ₹cr", get: (r: Row) => r.values.net_profit ?? null, fmt: (v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 0 }) },
  eps: { id: "eps", label: "EPS ₹", get: (r: Row) => r.values.eps_basic ?? null, fmt: (v: number) => v.toFixed(2) },
  r1m: { id: "r1m", label: "1M", get: (r: Row) => px(r)?.ret_1m ?? null, fmt: pct, tone: true },
  r3m: { id: "r3m", label: "3M", get: (r: Row) => px(r)?.ret_3m ?? null, fmt: pct, tone: true },
  r6m: { id: "r6m", label: "6M", get: (r: Row) => px(r)?.ret_6m ?? null, fmt: pct, tone: true },
  r1y: { id: "r1y", label: "1Y", get: (r: Row) => px(r)?.ret_1y ?? null, fmt: pct, tone: true },
  rsi: { id: "rsi", label: "RSI 14", get: (r: Row) => px(r)?.rsi14 ?? null, fmt: (v: number) => v.toFixed(0) },
  d50: { id: "d50", label: "vs 50 DMA", get: (r: Row) => px(r)?.vs_50dma ?? null, fmt: pct, tone: true },
  d200: { id: "d200", label: "vs 200 DMA", get: (r: Row) => px(r)?.vs_200dma ?? null, fmt: pct, tone: true },
  hi52: { id: "hi52", label: "From 52w high", get: (r: Row) => px(r)?.from_52w_high ?? null, fmt: pct, tone: true },
} satisfies Record<string, Col>;

const COLUMNS: Record<TabId, Col[]> = {
  overall: [C.price, C.mcap, C.pb, C.roe, C.npm, C.de, C.r1y],
  performance: [C.price, C.r1m, C.r3m, C.r6m, C.r1y, C.hi52],
  fundamentals: [C.mcap, C.np, C.eps, C.roe, C.npm, C.de, C.pb, C.ev],
  technicals: [C.price, C.rsi, C.d50, C.d200, C.hi52],
};

export function PeerComparisonPanel({ symbol }: { symbol: string }): React.ReactElement {
  const [tab, setTab] = React.useState<TabId>("overall");
  const [data, setData] = React.useState<PeerComparisonResponse | null>(null);
  const [sort, setSort] = React.useState<{ col: string; dir: 1 | -1 } | null>(null);

  // The mark is how a row is found. Six companies in one sector have names
  // that all start the same way — Tata Consultancy, Tata Elxsi — so the glyph
  // is doing real work here, not decoration: it is the only part of the row
  // you can recognise without reading it.
  const peerSymbols = React.useMemo(
    () => data?.peers.map((p) => p.symbol) ?? [], [data]);
  const logos = useCompanyLogos(peerSymbols);

  React.useEffect(() => {
    let dead = false;
    setData(null);
    getStockPeers(symbol, FIELDS)
      .then((r) => { if (!dead && !isError(r)) setData(r.data); })
      .catch(() => {});
    return () => { dead = true; };
  }, [symbol]);

  const cols = COLUMNS[tab];

  const rows = React.useMemo(() => {
    const list = [...(data?.peers ?? [])];
    if (!sort) return list;                    // server order = by market cap
    const col = cols.find((c) => c.id === sort.col);
    if (!col) return list;
    return list.sort((a, b) => {
      const x = col.get(a), y = col.get(b);
      // Nulls sink, whichever way the column is pointed — a missing number is
      // not the smallest number.
      if (x === null && y === null) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return (x - y) * sort.dir;
    });
  }, [data, sort, cols]);

  if (!data) return <PanelSkeleton rows={7} />;
  if (!data.available || !data.peers.length) {
    return <EmptyNote>No comparable sector peers are available for this company.</EmptyNote>;
  }

  const toggle = (id: string): void =>
    setSort((s) => (s?.col === id ? (s.dir === -1 ? { col: id, dir: 1 } : null) : { col: id, dir: -1 }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <PanelHead
        title="Peer comparison"
        sub={data.sector ? `${data.peers.length} largest ${data.sector} companies by market cap` : undefined}
      />

      {/* The same tab strip the Financial Performance panel uses, sitting on
          the hairline the table hangs from. */}
      <div style={{ borderBottom: "1px solid var(--glass-border)" }}>
        <Segmented value={tab} options={TABS} onChange={(v) => { setTab(v as TabId); setSort(null); }} underline />
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-ui)" }}>
          <thead>
            <tr>
              <th style={{ ...head, textAlign: "left", paddingLeft: 0 }}>Company</th>
              {cols.map((c) => (
                <th
                  key={c.id}
                  onClick={() => toggle(c.id)}
                  title={`Sort by ${c.label}`}
                  style={{ ...head, cursor: "pointer", color: sort?.col === c.id ? "var(--text-primary)" : "var(--text-tertiary)" }}
                >
                  {c.label}
                  <span style={{ opacity: sort?.col === c.id ? 1 : 0, marginLeft: 4 }}>
                    {sort?.dir === 1 ? "↑" : "↓"}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.sc_id + r.symbol} style={{ borderTop: "1px solid var(--glass-border)" }}>
                <td style={{ padding: "10px 12px 10px 0", minWidth: 210 }}>
                  <Link
                    href={`/stock/${r.symbol}`}
                    style={{
                      color: "inherit", textDecoration: "none",
                      display: "flex", alignItems: "center", gap: 10,
                    }}
                  >
                    <CompanyLogo
                      logoUrl={logos[r.symbol.toUpperCase()] ?? null}
                      name={r.name}
                      symbol={r.symbol}
                      size={26}
                    />
                    <span style={{ minWidth: 0 }}>
                      <span style={{
                        display: "block", fontSize: 12.5,
                        // The company you are already on is the row you compare
                        // FROM, so it is the one name set in full weight.
                        fontWeight: r.is_current ? 650 : 450,
                        color: "var(--text-primary)",
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      }}>
                        {r.name}
                      </span>
                      <span style={{ display: "block", marginTop: 1, fontSize: 10.5, color: "var(--text-tertiary)" }}>
                        {r.symbol}
                      </span>
                    </span>
                  </Link>
                </td>
                {cols.map((c) => {
                  const v = c.get(r);
                  return (
                    <td
                      key={c.id}
                      className="tabular-nums"
                      style={{
                        padding: "10px 12px", textAlign: "right",
                        fontFamily: "var(--font-mono)", fontSize: 11.5,
                        fontWeight: r.is_current ? 600 : 400,
                        color: v === null
                          ? "var(--text-tertiary)"
                          : c.tone
                            ? v >= 0 ? "var(--color-profit)" : "var(--color-loss)"
                            : "var(--text-primary)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {v === null ? "—" : c.fmt(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: 10.5, color: "var(--text-tertiary)" }}>
        Fundamentals from the latest reported filings · price columns from one year of daily closes
      </div>
    </div>
  );
}

const head: React.CSSProperties = {
  padding: "8px 12px",
  textAlign: "right",
  fontSize: 10.5,
  fontWeight: 600,
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  whiteSpace: "nowrap",
  color: "var(--text-tertiary)",
};
