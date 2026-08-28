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
import { Plus } from "lucide-react";

import { CompanyLogo } from "@/components/CompanyLogo";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";
import { getStockPeers, type PeerComparisonResponse, type PeerPrice } from "@/lib/api";
import { isError } from "@/lib/types";
import { EmptyNote, PanelHead, PanelSkeleton, Segmented } from "./chrome";

// One request covers every tab AND anything the reader adds later: the whole
// server catalog, plus the price block it returns unasked. Switching tabs or
// adding a column is then a re-render, not a round trip — which is the only
// reason a custom column can appear instantly.
//
// Bank-only fields ride along too. They come back null for a company that
// files no NPA, and a column of em-dashes is not offered: `AVAILABLE` below
// drops any field this peer set has no number for.
const FIELDS = [
  "market_cap", "revenue", "net_profit", "roe", "roce", "net_profit_margin",
  "debt_to_equity", "price_to_book", "ev_to_ebitda", "current_ratio",
  "interest_coverage", "dividend_payout", "eps_basic", "book_value_per_share",
  "gross_npa_pct", "net_npa_pct", "net_interest_margin",
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
  /** Which band of the header this column sits under. The Overall tab reads
   *  across four unrelated questions — what it costs, what it earns, what it
   *  owes, how it has traded — and eight ungrouped columns made the reader
   *  work out which was which from the labels alone. */
  group?: string;
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

const one = (v: number): string => `${v.toFixed(1)}%`;
const two = (v: number): string => v.toFixed(2);
const whole = (v: number): string => v.toLocaleString("en-IN", { maximumFractionDigits: 0 });

const C = {
  price: { id: "price", label: "Price", get: (r: Row) => px(r)?.price ?? null, fmt: inr, group: "Price" },
  mcap: { id: "mcap", label: "Mkt cap ₹cr", get: (r: Row) => r.values.market_cap ?? null, fmt: cr, group: "Size" },
  rev: { id: "rev", label: "Revenue ₹cr", get: (r: Row) => r.values.revenue ?? null, fmt: whole, group: "Size" },
  np: { id: "np", label: "Net profit ₹cr", get: (r: Row) => r.values.net_profit ?? null, fmt: whole, group: "Size" },
  pb: { id: "pb", label: "P/B", get: (r: Row) => r.values.price_to_book ?? null, fmt: mult, group: "Valuation" },
  ev: { id: "ev", label: "EV / EBITDA", get: (r: Row) => r.values.ev_to_ebitda ?? null, fmt: mult, group: "Valuation" },
  roe: { id: "roe", label: "ROE", get: (r: Row) => r.values.roe ?? null, fmt: one, group: "Returns" },
  roce: { id: "roce", label: "ROCE", get: (r: Row) => r.values.roce ?? null, fmt: one, group: "Returns" },
  npm: { id: "npm", label: "Net margin", get: (r: Row) => r.values.net_profit_margin ?? null, fmt: one, group: "Returns" },
  de: { id: "de", label: "Debt / equity", get: (r: Row) => r.values.debt_to_equity ?? null, fmt: mult, group: "Balance sheet" },
  cur: { id: "cur", label: "Current ratio", get: (r: Row) => r.values.current_ratio ?? null, fmt: mult, group: "Balance sheet" },
  icov: { id: "icov", label: "Interest cover", get: (r: Row) => r.values.interest_coverage ?? null, fmt: mult, group: "Balance sheet" },
  eps: { id: "eps", label: "EPS ₹", get: (r: Row) => r.values.eps_basic ?? null, fmt: two, group: "Per share" },
  bvps: { id: "bvps", label: "Book value ₹", get: (r: Row) => r.values.book_value_per_share ?? null, fmt: two, group: "Per share" },
  payout: { id: "payout", label: "Dividend payout", get: (r: Row) => r.values.dividend_payout ?? null, fmt: one, group: "Per share" },
  gnpa: { id: "gnpa", label: "Gross NPA", get: (r: Row) => r.values.gross_npa_pct ?? null, fmt: one, group: "Asset quality" },
  nnpa: { id: "nnpa", label: "Net NPA", get: (r: Row) => r.values.net_npa_pct ?? null, fmt: one, group: "Asset quality" },
  nim: { id: "nim", label: "NIM", get: (r: Row) => r.values.net_interest_margin ?? null, fmt: one, group: "Asset quality" },
  r1m: { id: "r1m", label: "1M", get: (r: Row) => px(r)?.ret_1m ?? null, fmt: pct, tone: true, group: "Returns" },
  r3m: { id: "r3m", label: "3M", get: (r: Row) => px(r)?.ret_3m ?? null, fmt: pct, tone: true, group: "Returns" },
  r6m: { id: "r6m", label: "6M", get: (r: Row) => px(r)?.ret_6m ?? null, fmt: pct, tone: true, group: "Returns" },
  r1y: { id: "r1y", label: "1Y", get: (r: Row) => px(r)?.ret_1y ?? null, fmt: pct, tone: true, group: "Returns" },
  rsi: { id: "rsi", label: "RSI 14", get: (r: Row) => px(r)?.rsi14 ?? null, fmt: (v: number) => v.toFixed(0), group: "Technicals" },
  d50: { id: "d50", label: "vs 50 DMA", get: (r: Row) => px(r)?.vs_50dma ?? null, fmt: pct, tone: true, group: "Technicals" },
  d200: { id: "d200", label: "vs 200 DMA", get: (r: Row) => px(r)?.vs_200dma ?? null, fmt: pct, tone: true, group: "Technicals" },
  hi52: { id: "hi52", label: "From 52w high", get: (r: Row) => px(r)?.from_52w_high ?? null, fmt: pct, tone: true, group: "Technicals" },
} satisfies Record<string, Col>;

/** Every column a reader can add, in the order the picker lists them. */
const ALL_COLS: Col[] = Object.values(C);

/** Eight numeric columns is what fits beside the company name at a laptop
 *  width without the table needing a scrollbar. A view is only worth naming if
 *  it can be read in one glance, and a glance does not include a horizontal
 *  scroll — so a tab is capped here and the rest of the catalog is reachable
 *  through the picker, where the reader is choosing to trade width for detail.
 */
const MAX_TAB_COLS = 8;

const COLUMNS: Record<TabId, Col[]> = {
  // Overall is the tab most people never leave, so it carries one column from
  // each question rather than several from two of them: what it costs, what it
  // earns on capital, what it owes, how it has traded. EV/EBITDA and EPS moved
  // off it to make that fit — both are a click away under Fundamentals.
  overall: [C.price, C.mcap, C.pb, C.roe, C.roce, C.npm, C.de, C.r1y],
  performance: [C.price, C.r1m, C.r3m, C.r6m, C.r1y, C.hi52],
  fundamentals: [C.mcap, C.rev, C.np, C.eps, C.roe, C.npm, C.de, C.pb],
  technicals: [C.price, C.rsi, C.d50, C.d200, C.hi52],
};

/** Columns the reader added, kept across visits and companies.
 *
 *  Per browser rather than per account: it is a reading preference, not data,
 *  and a round trip to store it would be a worse trade than losing it on a new
 *  device. Guarded because Safari in private mode throws on access. */
const EXTRA_KEY = "pivot_peer_extra_cols";

function loadExtra(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(EXTRA_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

export function PeerComparisonPanel({ symbol }: { symbol: string }): React.ReactElement {
  const [tab, setTab] = React.useState<TabId>("overall");
  const [data, setData] = React.useState<PeerComparisonResponse | null>(null);
  const [sort, setSort] = React.useState<{ col: string; dir: 1 | -1 } | null>(null);
  // Read after mount, never during render: localStorage on the server is
  // undefined and a first render that disagrees with the server's is a
  // hydration error.
  const [extra, setExtra] = React.useState<string[]>([]);
  const [picking, setPicking] = React.useState(false);
  React.useEffect(() => { setExtra(loadExtra()); }, []);

  const saveExtra = React.useCallback((next: string[]) => {
    setExtra(next);
    try { window.localStorage.setItem(EXTRA_KEY, JSON.stringify(next)); } catch { /* private mode */ }
  }, []);

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

  // A column is only offered, and only added, when this peer set actually has
  // a number for it — otherwise "add NIM" on a software sector adds a column
  // of em-dashes and reads as a broken feature rather than an absent metric.
  const hasValue = React.useCallback(
    (c: Col) => (data?.peers ?? []).some((p) => c.get(p) !== null && c.get(p) !== undefined),
    [data],
  );

  // Sliced, so the cap is enforced rather than merely documented — a column
  // added to a tab above without counting cannot quietly bring back the
  // scrollbar. Columns the reader adds are NOT capped: that scroll is a trade
  // they chose to make.
  const base = COLUMNS[tab].slice(0, MAX_TAB_COLS);
  const cols = React.useMemo(() => {
    const seen = new Set(base.map((c) => c.id));
    const added = extra
      .map((id) => ALL_COLS.find((c) => c.id === id))
      .filter((c): c is Col => !!c && !seen.has(c.id));
    return [...base, ...added];
  }, [base, extra]);

  const offerable = React.useMemo(
    () => ALL_COLS.filter((c) => !cols.some((x) => x.id === c.id) && hasValue(c)),
    [cols, hasValue],
  );

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
      <PanelHead title="Peer Comparison" />

      {/* The same tab strip the Financial Performance panel uses, sitting on
          the hairline the table hangs from. The picker rides at the far end:
          the four tabs are the views we think are worth naming, and this is
          the admission that a reader may want a fifth. */}
      <div style={{
        borderBottom: "1px solid var(--glass-border)",
        display: "flex", alignItems: "flex-end", justifyContent: "space-between",
        gap: 12, flexWrap: "wrap",
      }}>
        <Segmented value={tab} options={TABS} onChange={(v) => { setTab(v as TabId); setSort(null); }} underline />
        <ColumnPicker
          open={picking}
          onOpen={setPicking}
          offerable={offerable}
          added={extra}
          onAdd={(id) => saveExtra([...extra, id])}
          onClear={() => saveExtra([])}
        />
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-ui)" }}>
          <thead>
            {/* The band row. Runs of one are left blank rather than labelled —
                a "group" of a single column is a second label for it. */}
            <tr>
              <th style={{ ...head, paddingLeft: 0, paddingBottom: 2 }} />
              {groupSpans(cols).map((g, i) => (
                <th
                  key={`${g.label}-${i}`}
                  colSpan={g.span}
                  style={{
                    ...head, paddingBottom: 2, textAlign: "center",
                    fontSize: 9.5, letterSpacing: "0.08em",
                    color: "var(--text-tertiary)",
                    opacity: g.span > 1 ? 1 : 0,
                  }}
                >
                  {g.label}
                </th>
              ))}
            </tr>
            <tr>
              <th style={{ ...head, textAlign: "left", paddingLeft: 0 }}>Company</th>
              {cols.map((c) => {
                const isExtra = extra.includes(c.id) && !COLUMNS[tab].some((b) => b.id === c.id);
                return (
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
                    {/* Only a column the reader added can be removed. The four
                        named views are the product's opinion; taking a column
                        out of one of them would leave the tab lying about
                        which view it is. */}
                    {isExtra ? (
                      <button
                        type="button"
                        aria-label={`Remove ${c.label}`}
                        onClick={(e) => { e.stopPropagation(); saveExtra(extra.filter((x) => x !== c.id)); }}
                        style={{
                          marginLeft: 5, border: "none", background: "transparent",
                          padding: 0, cursor: "pointer", color: "var(--text-tertiary)",
                          fontSize: 12, lineHeight: 1, verticalAlign: "middle",
                        }}
                      >
                        ×
                      </button>
                    ) : null}
                  </th>
                );
              })}
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
                        padding: "10px 10px", textAlign: "center",
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

/** Consecutive columns sharing a group, collapsed into header spans. */
function groupSpans(cols: Col[]): { label: string; span: number }[] {
  const out: { label: string; span: number }[] = [];
  cols.forEach((c) => {
    const label = c.group ?? "";
    const last = out[out.length - 1];
    if (last && last.label === label) last.span += 1;
    else out.push({ label, span: 1 });
  });
  return out;
}

/** Add-a-column control.
 *
 *  A menu rather than a settings screen: the whole interaction is "which one",
 *  and the list is the catalog minus what is already on screen minus what this
 *  peer set has no number for. Closes on outside click and on Escape, because
 *  a menu that only closes by re-clicking its own trigger is a menu people
 *  leave open.
 */
function ColumnPicker({
  open, onOpen, offerable, added, onAdd, onClear,
}: {
  open: boolean;
  onOpen: (v: boolean) => void;
  offerable: Col[];
  added: string[];
  onAdd: (id: string) => void;
  onClear: () => void;
}): React.ReactElement | null {
  const host = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent): void => {
      if (host.current && !host.current.contains(e.target as Node)) onOpen(false);
    };
    const esc = (e: KeyboardEvent): void => { if (e.key === "Escape") onOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open, onOpen]);

  if (!offerable.length && !added.length) return null;

  // Grouped in the menu the way they are grouped in the header, so "where
  // would this land" is answered before it is added.
  const byGroup = new Map<string, Col[]>();
  offerable.forEach((c) => {
    const g = c.group ?? "Other";
    byGroup.set(g, [...(byGroup.get(g) ?? []), c]);
  });

  return (
    <div ref={host} style={{ position: "relative", paddingBottom: 6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        {added.length ? (
          <button
            type="button"
            onClick={onClear}
            style={{
              border: "none", background: "transparent", padding: 0, cursor: "pointer",
              fontFamily: "var(--font-ui)", fontSize: "var(--sd-f115)", color: "var(--text-secondary)",
            }}
          >
            Reset
          </button>
        ) : null}
        {offerable.length ? (
          <button
            type="button"
            aria-expanded={open}
            onClick={() => onOpen(!open)}
            style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              padding: "4px 11px", borderRadius: 99,
              border: "1px solid var(--glass-border)", background: "transparent",
              cursor: "pointer", fontFamily: "var(--font-ui)",
              fontSize: "var(--sd-f115)", fontWeight: 500, color: "var(--text-secondary)",
              whiteSpace: "nowrap",
            }}
          >
            <Plus size={12} aria-hidden="true" />
            Add ratio
          </button>
        ) : null}
      </div>

      {open && offerable.length ? (
        <div
          role="menu"
          style={{
            position: "absolute", right: 0, top: "100%", zIndex: 30,
            marginTop: 6, minWidth: 210, maxHeight: 320, overflowY: "auto",
            padding: "6px 0",
            background: "var(--bg-primary)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            boxShadow: "0 8px 28px rgba(15,18,22,.12)",
          }}
        >
          {[...byGroup.entries()].map(([group, list]) => (
            <div key={group}>
              <div style={{
                padding: "7px 12px 3px", fontSize: "var(--sd-f10)", fontWeight: 650,
                letterSpacing: "0.06em", textTransform: "uppercase",
                color: "var(--text-tertiary)",
              }}>
                {group}
              </div>
              {list.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  role="menuitem"
                  onClick={() => { onAdd(c.id); onOpen(false); }}
                  style={{
                    display: "block", width: "100%", textAlign: "left",
                    padding: "6px 12px", border: "none", background: "transparent",
                    cursor: "pointer", fontFamily: "var(--font-ui)",
                    fontSize: "var(--sd-f125)", color: "var(--text-primary)",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-secondary)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                >
                  {c.label}
                </button>
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const head: React.CSSProperties = {
  // Longhand, because the group-band row overrides paddingBottom and
  // paddingLeft on top of this. React warns when a shorthand and a longhand
  // for the same property both change across a rerender, and it is right to:
  // which one wins depends on key order, so the band's padding was a coin
  // flip on re-render.
  paddingTop: 8,
  paddingRight: 10,
  paddingBottom: 8,
  paddingLeft: 10,
  // Centred, and the cells below match. Right-alignment is the rule for a
  // column of magnitudes read against each other, but these columns are
  // different units in different formats — a percentage beside a multiple
  // beside a price — so a shared right edge lined up decimal points that mean
  // nothing to each other while leaving the header floating off its numbers.
  textAlign: "center",
  fontSize: 10.5,
  fontWeight: 600,
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  whiteSpace: "nowrap",
  color: "var(--text-tertiary)",
};
