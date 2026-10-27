"use client";

import { useEffect, useMemo, useReducer, useState } from "react";
import { useRouter } from "next/navigation";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";

import { Check, ChevronsUpDown } from "lucide-react";

import { CompanyLogo } from "@/components/CompanyLogo";
import { StockHoverActions } from "@/components/StockHoverActions";
import { Sparkline } from "@/components/screener/Sparkline";
import { type ScreenerStock } from "@/lib/screenerApi";
import {
  getSparkline,
  requestSparklines,
  subscribeSparklines,
} from "@/lib/sparklineStore";

/**
 * The screener grid.
 *
 * TANSTACK TABLE, AND WHAT IT IS ACTUALLY FOR HERE
 *
 * Not for sorting — the sort lives on the server, because a client sort would
 * only order the page you can see and silently claim to be ordering 2,500
 * names. It is here for column DEFINITION: one place that owns each column's
 * width, alignment, header and cell, so the header row and the body row cannot
 * drift apart. The previous table kept those in two lists and a switch
 * statement three hundred lines away from each other.
 *
 * WHICH COLUMNS CAN BE SORTED, AND WHY THE REST SHOW NO AFFORDANCE
 *
 * Only the ones the backend can order across the whole universe. Volume,
 * change-in-rupees, ROCE, D/E and the day range are served for the rows on the
 * page but cannot be ordered globally, so they get no sort control rather than
 * a control that would reorder eight rows and look broken.
 */

// ── formatting ───────────────────────────────────────────────────────
// Indian digit grouping throughout: 12,34,567 is what a number looks like to
// the person reading this screen.

const DASH = "—";

const fmtPrice = (v: number | null): string =>
  v == null ? DASH : `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function fmtCr(v: number | null): string {
  if (v == null) return DASH;
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)} L Cr`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)} K Cr`;
  return `${v.toFixed(0)} Cr`;
}

/** Share counts, in the units an Indian desk says out loud. */
function fmtVol(v: number | null): string {
  if (v == null) return DASH;
  if (v >= 1e7) return `${(v / 1e7).toFixed(2)} Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)} L`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)} K`;
  return String(v);
}

/** A real minus sign, and an explicit plus. At tabular-figure sizes a hyphen
 *  sits high and short and reads as a dash between two things. */
function signed(v: number | null, suffix: string, dp = 2): string {
  if (v == null) return DASH;
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${Math.abs(v).toFixed(dp)}${suffix}`;
}

// `--color-profit` / `--color-loss` are THIS app's tokens. The chart side of
// the codebase calls the same two colours `--up` / `--down`, and using those
// names here is not a near miss — an undefined custom property makes the whole
// declaration invalid, so the number silently renders in body text and the
// green/red disappears with no error anywhere.
const toneOf = (v: number | null): string =>
  v == null
    ? "var(--text-tertiary)"
    : v > 0
      ? "var(--color-profit)"
      : v < 0
        ? "var(--color-loss)"
        : "var(--text-secondary)";

function Signed({ v, suffix, dp = 2 }: { v: number | null; suffix: string; dp?: number }) {
  return <span style={{ color: toneOf(v) }}>{signed(v, suffix, dp)}</span>;
}

// ── selectable metrics (phone) ───────────────────────────────────────
// Groww's phone screener shows ONE value column at a time and lets you change
// which by tapping the header. That is how you read every metric on a narrow
// screen without a sideways-scrolling table. Each entry knows how to render
// its own value and whether that value is coloured by sign; `sortKey` (when
// present) is the server-orderable field, so picking a metric that the backend
// can order also re-sorts the whole universe — the ⇅ affordance, like Groww.

type MetricRender = { text: string; tone?: string };
type MobileMetric = {
  key: string;
  label: string;
  sortKey?: StockSortKey;
  render: (r: ScreenerStock) => MetricRender;
};

const pct = (v: number | null | undefined): MetricRender =>
  v == null ? { text: DASH } : { text: signed(v, "%"), tone: toneOf(v) };

export const MOBILE_METRICS: MobileMetric[] = [
  { key: "price", label: "Price", sortKey: "price", render: (r) => ({ text: fmtPrice(r.price) }) },
  { key: "change_pct", label: "Change %", sortKey: "change_pct", render: (r) => pct(r.change_pct) },
  { key: "market_cap_cr", label: "Mkt cap", sortKey: "market_cap_cr", render: (r) => ({ text: fmtCr(r.market_cap_cr) }) },
  { key: "pe", label: "P/E", sortKey: "pe", render: (r) => ({ text: r.pe == null ? DASH : r.pe.toFixed(1) }) },
  // ROE has no whole-universe server sort on this path, so it only changes the
  // displayed value — no sortKey, rather than a ⇅ that silently orders by
  // something else.
  { key: "roe", label: "ROE", render: (r) => ({ text: r.roe == null ? DASH : `${r.roe.toFixed(1)}%` }) },
  { key: "roce", label: "ROCE", render: (r) => ({ text: r.roce == null ? DASH : `${r.roce.toFixed(1)}%` }) },
  { key: "de", label: "D/E", render: (r) => ({ text: r.de == null ? DASH : r.de.toFixed(2) }) },
  { key: "one_year_pct", label: "1-Y return", sortKey: "one_year_pct", render: (r) => pct(r.one_year_pct) },
  { key: "volume", label: "Volume", render: (r) => ({ text: fmtVol(r.volume) }) },
  { key: "rsi14", label: "RSI 14", render: (r) => ({ text: r.rsi14 == null ? DASH : r.rsi14.toFixed(1) }) },
  { key: "sma200_rel", label: "vs 200D", render: (r) => pct(r.sma200_rel ?? null) },
  { key: "dist_52w_high", label: "From 52W high", render: (r) => pct(r.dist_52w_high ?? null) },
  { key: "day_open", label: "Open", render: (r) => ({ text: fmtPrice(r.day_open) }) },
  { key: "prev_close", label: "Prev close", render: (r) => ({ text: fmtPrice(r.prev_close) }) },
];

// ── the identity cell ────────────────────────────────────────────────
// The same construction as the chart's instrument search: a large mark, the
// ticker with its exchange beside it, the company under it. It is the row a
// person actually scans, so it gets the space.

function Identity({ row }: { row: ScreenerStock }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
      <CompanyLogo
        logoUrl={row.logo_url}
        name={row.name}
        symbol={row.symbol}
        size={40}
      />
      <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
        <span
          style={{
            fontSize: 15,
            fontWeight: 500,
            letterSpacing: "-0.012em",
            lineHeight: 1.15,
            whiteSpace: "nowrap",
            color: "var(--text-primary)",
          }}
        >
          {row.symbol}
        </span>
        <span
          style={{
            fontSize: 12,
            lineHeight: 1.25,
            color: "var(--text-tertiary)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            maxWidth: 260,
          }}
          title={row.name}
        >
          {row.name}
        </span>
      </div>
    </div>
  );
}

// ── columns ──────────────────────────────────────────────────────────

export type StockSortKey =
  | "symbol" | "name" | "market_cap_cr" | "price"
  | "change_pct" | "pe" | "roe" | "one_year_pct";

/** Backend-orderable keys. Everything else renders without a sort control. */
const SERVER_SORTABLE = new Set<string>([
  "symbol", "name", "market_cap_cr", "price", "change_pct", "pe", "roe", "one_year_pct",
]);

type Meta = { align: "left" | "right"; width: number; sortKey?: StockSortKey };

export function StockTable({
  rows,
  sort,
  onSort,
  offset = 0,
}: {
  rows: ScreenerStock[];
  /** Retained for caller compatibility; sector labels no longer appear in the
   * identity column because the company name is the useful scanning label. */
  sectorLabel?: (key: string) => string;
  sort: { by: string; dir: "asc" | "desc" };
  onSort: (key: StockSortKey) => void;
  /** Row number of the first row, so the rank column keeps counting across
   *  pages instead of restarting at 1 on every load-more. */
  offset?: number;
}): React.ReactElement {
  const router = useRouter();
  // The sparkline cache is module state (lib/sparklineStore), not component
  // state. The screener swaps this table out for a loading branch on every
  // metrics-warming poll, which unmounts it — so a cache held here was thrown
  // away and re-fetched on a cadence nobody asked for, and the charts
  // flickered back to empty each time. This component only subscribes.
  const [, rerender] = useReducer((n: number) => n + 1, 0);
  useEffect(() => subscribeSparklines(rerender), []);
  // The Kite-style quick-action bar target. It lives in its own trailing
  // column rather than covering a value: the old table hid MKT CAP behind it,
  // which meant the one number you were reaching for vanished as you reached.
  const [hoverSym, setHoverSym] = useState<string | null>(null);

  // Sparklines for the rows on screen, in one call, once per new set of
  // symbols. Symbols already fetched are never re-requested — a load-more asks
  // only for what it added.
  // Cheap to call on every render: the store drops anything cached, in
  // flight, or already queued, and coalesces what is left into one batch per
  // tick. Twelve symbols a request, so the first rows paint while the rest
  // are still in the air.
  useEffect(() => {
    requestSparklines(rows.map((r) => r.symbol));
  }, [rows]);

  const columns = useMemo<ColumnDef<ScreenerStock>[]>(() => [
    {
      id: "rank",
      header: "#",
      meta: { align: "right", width: 44 } satisfies Meta,
      cell: ({ row }) => (
        <span style={{ color: "var(--text-tertiary)", fontSize: 13 }}>{offset + row.index + 1}</span>
      ),
    },
    {
      id: "symbol",
      header: "Symbol",
      // Slightly tighter than before so the 1D sparkline sits closer to the
      // company name rather than across a wide gap.
      meta: { align: "left", width: 288, sortKey: "symbol" } satisfies Meta,
      cell: ({ row }) => <Identity row={row.original} />,
    },
    {
      id: "spark",
      header: "1D chart",
      meta: { align: "left", width: 96 } satisfies Meta,
      cell: ({ row }) => (
        <Sparkline points={getSparkline(row.original.symbol)} baseline={row.original.prev_close} />
      ),
    },
    {
      id: "price",
      header: "Price",
      meta: { align: "right", width: 116, sortKey: "price" } satisfies Meta,
      cell: ({ row }) => (
        <span>{fmtPrice(row.original.price)}</span>
      ),
    },
    {
      id: "change_abs",
      header: "Change",
      meta: { align: "right", width: 96 } satisfies Meta,
      cell: ({ row }) => <Signed v={row.original.change_abs} suffix="" />,
    },
    {
      id: "change_pct",
      header: "Change %",
      meta: { align: "right", width: 104, sortKey: "change_pct" } satisfies Meta,
      cell: ({ row }) => <Signed v={row.original.change_pct} suffix="%" />,
    },
    {
      id: "day_open",
      header: "Open",
      meta: { align: "right", width: 108 } satisfies Meta,
      // Where the session started. Read against Price it gives the intraday
      // move, and read against Prev close it gives the gap — two facts from
      // one column, both from the same quote as everything beside it.
      cell: ({ row }) => fmtPrice(row.original.day_open),
    },
    {
      id: "prev_close",
      header: "Prev close",
      meta: { align: "right", width: 112 } satisfies Meta,
      cell: ({ row }) => fmtPrice(row.original.prev_close),
    },
    {
      id: "volume",
      header: "Volume",
      meta: { align: "right", width: 104 } satisfies Meta,
      cell: ({ row }) => fmtVol(row.original.volume),
    },
    {
      id: "market_cap_cr",
      header: "Mkt cap",
      meta: { align: "right", width: 116, sortKey: "market_cap_cr" } satisfies Meta,
      cell: ({ row }) => fmtCr(row.original.market_cap_cr),
    },
    {
      id: "pe",
      header: "P/E",
      meta: { align: "right", width: 82, sortKey: "pe" } satisfies Meta,
      cell: ({ row }) => (row.original.pe == null ? DASH : row.original.pe.toFixed(1)),
    },
    {
      id: "roe",
      header: "ROE",
      meta: { align: "right", width: 88, sortKey: "roe" } satisfies Meta,
      cell: ({ row }) => (row.original.roe == null ? DASH : `${row.original.roe.toFixed(1)}%`),
    },
    {
      id: "roce",
      header: "ROCE",
      meta: { align: "right", width: 88 } satisfies Meta,
      cell: ({ row }) => (row.original.roce == null ? DASH : `${row.original.roce.toFixed(1)}%`),
    },
    {
      id: "de",
      header: "D/E",
      meta: { align: "right", width: 78 } satisfies Meta,
      cell: ({ row }) => (row.original.de == null ? DASH : row.original.de.toFixed(2)),
    },
    {
      id: "one_year_pct",
      header: "1-Y return",
      meta: { align: "right", width: 116, sortKey: "one_year_pct" } satisfies Meta,
      cell: ({ row }) => <Signed v={row.original.one_year_pct} suffix="%" />,
    },
    {
      id: "rsi14",
      header: "RSI 14",
      meta: { align: "right", width: 84 } satisfies Meta,
      cell: ({ row }) => row.original.rsi14 == null ? DASH : row.original.rsi14.toFixed(1),
    },
    {
      id: "sma200_rel",
      header: "vs 200D",
      meta: { align: "right", width: 96 } satisfies Meta,
      cell: ({ row }) => <Signed v={row.original.sma200_rel ?? null} suffix="%" />,
    },
    {
      id: "dist_52w_high",
      header: "From 52W high",
      meta: { align: "right", width: 126 } satisfies Meta,
      cell: ({ row }) => <Signed v={row.original.dist_52w_high ?? null} suffix="%" />,
    },
    {
      id: "actions",
      header: "",
      // Zero-width: the quick-action pill is an ABSOLUTE overlay pinned to the
      // row's right edge (see the cell below), not a reserved column. A fixed
      // trailing column left a permanent empty gutter to the right of "From
      // 52W high" — the excess right-hand whitespace — that showed even though
      // the pill only appears on hover.
      meta: { align: "right", width: 0 } satisfies Meta,
      cell: ({ row }) => (
        <span
          style={{
            // Take no layout width; float over the last data cells on hover so
            // nothing shifts and no empty column is reserved.
            position: "absolute",
            right: 14,
            top: "50%",
            transform: "translateY(-50%)",
            display: "inline-flex",
            justifyContent: "flex-end",
            visibility: hoverSym === row.original.symbol ? "visible" : "hidden",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <StockHoverActions
            symbol={row.original.symbol}
            name={row.original.name}
            logoUrl={row.original.logo_url}
          />
        </span>
      ),
    },
  ], [offset, hoverSym]);

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    // The server owns ordering; see the header comment.
    manualSorting: true,
  });

  return (
    // Scrolling belongs to `.screener-results`, the shared horizontal and
    // vertical viewport. A second overflow container here trapped sticky
    // headers inside a box that never moved vertically.
    <div style={{ width: "100%", overflow: "visible" }}>
      {/* Phone layout — a Groww-style list: name on the left, ONE selectable
          value column on the right whose metric you change from the header.
          Hidden on desktop by CSS; the table below is hidden on phone. Both
          read the same rows. */}
      <MobileStockList
        rows={rows}
        sort={sort}
        onSort={onSort}
        onOpen={(sym) => router.push(`/stock/${encodeURIComponent(sym)}`)}
      />
      <table
        className="screener-table-desktop"
        style={{
          width: "100%",
          minWidth: 1420,
          borderCollapse: "separate",
          borderSpacing: 0,
          fontFamily: "var(--font-ui)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => {
                const meta = h.column.columnDef.meta as Meta;
                const key = meta.sortKey;
                const active = key && sort.by === key;
                const stickyIdentity = h.column.id === "symbol";
                const isActions = h.column.id === "actions";
                return (
                  <th
                    key={h.id}
                    onClick={() => key && onSort(key)}
                    style={{
                      width: meta.width,
                      minWidth: meta.width,
                      textAlign: meta.align,
                      // The zero-width actions column carries no header padding
                      // so it adds no gutter to the table's right edge.
                      padding: isActions ? 0 : "9px 14px",
                      position: "sticky",
                      top: 0,
                      left: stickyIdentity ? 0 : undefined,
                      zIndex: stickyIdentity ? 4 : 2,
                      background: "var(--bg-primary)",
                      borderBottom: "1px solid var(--glass-border)",
                      fontSize: 12,
                      fontWeight: 500,
                      letterSpacing: "0.02em",
                      // Ink, not grey. A header is a label for the column
                      // under it and has to be readable at a glance; the
                      // ACTIVE sort is marked by its arrow, not by being the
                      // only header you can read.
                      color: "var(--text-primary)",
                      cursor: key ? "pointer" : "default",
                      whiteSpace: "nowrap",
                      userSelect: "none",
                    }}
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {active && (
                      <span aria-hidden style={{ marginLeft: 5 }}>
                        {sort.dir === "asc" ? "↑" : "↓"}
                      </span>
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((r) => (
            <tr
              key={r.original.symbol}
              onClick={() => router.push(`/stock/${encodeURIComponent(r.original.symbol)}`)}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--bg-secondary)";
                setHoverSym(r.original.symbol);
                // Warm the stock page (RSC payload + chart bundle) on hover so
                // the click lands on a rendered page, not a cold load.
                router.prefetch(`/stock/${encodeURIComponent(r.original.symbol)}`);
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                setHoverSym((v) => (v === r.original.symbol ? null : v));
              }}
              style={{
                cursor: "pointer",
                transition: "background-color 0.15s var(--ease-quartr)",
              }}
            >
              {r.getVisibleCells().map((cell) => {
                const meta = cell.column.columnDef.meta as Meta;
                const stickyIdentity = cell.column.id === "symbol";
                const isActions = cell.column.id === "actions";
                return (
                  <td
                    key={cell.id}
                    style={{
                      textAlign: meta.align,
                      // The zero-width actions cell carries no padding so it
                      // adds nothing to the row width; its pill is an absolute
                      // overlay anchored to this (right-edge) cell.
                      padding: isActions ? 0 : "7px 14px",
                      borderBottom: "1px solid var(--glass-border)",
                      fontSize: 14,
                      color: "var(--text-primary)",
                      whiteSpace: "nowrap",
                      position: stickyIdentity ? "sticky" : isActions ? "relative" : undefined,
                      left: stickyIdentity ? 0 : undefined,
                      zIndex: stickyIdentity ? 1 : undefined,
                      background: stickyIdentity
                        ? hoverSym === r.original.symbol
                          ? "var(--bg-secondary)"
                          : "var(--bg-base)"
                        : undefined,
                    }}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── mobile stock list ────────────────────────────────────────────────
// Groww's phone screener, faithfully: a flat white list — one tappable ROW
// per stock, hairline divider between them — with the company name on the LEFT
// and ONE value column on the RIGHT. Above the list, a header whose right side
// names the metric currently shown (e.g. "Market Price ⇅"); tapping it opens a
// menu of every column, and picking one both swaps the value on every row and,
// where the backend can order that field, re-sorts the whole universe. That is
// how you read all the data on a narrow screen — you change which column you're
// looking at, instead of scrolling a wide table sideways.

function MobileStockList({
  rows,
  sort,
  onSort,
  onOpen,
}: {
  rows: ScreenerStock[];
  sort: { by: string; dir: "asc" | "desc" };
  onSort: (key: StockSortKey) => void;
  onOpen: (symbol: string) => void;
}): React.ReactElement {
  // Which metric the value column shows. Follows the active server sort when it
  // maps to one of our metrics, so opening the screen already sorted by market
  // cap shows the market-cap column — the header and the order never disagree.
  const sortedMetric = MOBILE_METRICS.find((m) => m.sortKey === sort.by);
  const [metricKey, setMetricKey] = useState<string>(
    sortedMetric ? sortedMetric.key : "price",
  );
  useEffect(() => {
    if (sortedMetric) setMetricKey(sortedMetric.key);
  }, [sortedMetric]);
  const [menuOpen, setMenuOpen] = useState(false);

  const metric = MOBILE_METRICS.find((m) => m.key === metricKey) ?? MOBILE_METRICS[0]!;
  const sortedByThis = metric.sortKey != null && metric.sortKey === sort.by;

  const pick = (m: MobileMetric): void => {
    setMetricKey(m.key);
    setMenuOpen(false);
    // Selecting a server-orderable metric sorts the whole universe by it (the
    // ⇅). Non-orderable metrics just change what's displayed.
    if (m.sortKey && m.sortKey !== sort.by) onSort(m.sortKey);
  };

  return (
    <div
      className="screener-cards-mobile"
      style={{
        display: "none", // flipped to block under lg by globals.css
        fontFamily: "var(--font-ui)",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {/* Header: the value-column selector (Groww's "Market Price ⇅"). */}
      <div
        style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          padding: "8px 2px 10px",
          borderBottom: "1px solid var(--glass-border)",
        }}
      >
        <button
          type="button"
          onClick={() => setMenuOpen((o) => !o)}
          aria-expanded={menuOpen}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            padding: "4px 4px 4px 10px",
            background: "transparent",
            border: "none",
            color: "var(--text-primary)",
            fontFamily: "var(--font-ui)",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
            WebkitTapHighlightColor: "transparent",
          }}
        >
          {metric.label}
          <ChevronsUpDown size={14} strokeWidth={2.25} aria-hidden />
        </button>

        {menuOpen && (
          <>
            {/* Tap-away scrim */}
            <div
              onClick={() => setMenuOpen(false)}
              style={{ position: "fixed", inset: 0, zIndex: 40 }}
            />
            <div
              className="quartr-no-scrollbar"
              style={{
                position: "absolute",
                top: "calc(100% + 4px)",
                right: 0,
                zIndex: 41,
                minWidth: 180,
                maxHeight: 320,
                overflowY: "auto",
                background: "var(--bg-primary)",
                border: "1px solid var(--glass-border)",
                borderRadius: "var(--radius-md, 12px)",
                boxShadow: "0 1px 2px rgba(0,0,0,0.06), 0 14px 34px -10px rgba(0,0,0,0.24)",
                padding: 6,
              }}
            >
              {MOBILE_METRICS.map((m) => {
                const active = m.key === metric.key;
                return (
                  <button
                    key={m.key}
                    type="button"
                    onClick={() => pick(m)}
                    style={{
                      width: "100%",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 10,
                      padding: "9px 10px",
                      background: active ? "var(--bg-secondary)" : "transparent",
                      border: "none",
                      borderRadius: "var(--radius-sm, 8px)",
                      cursor: "pointer",
                      textAlign: "left",
                      fontFamily: "var(--font-ui)",
                      fontSize: 13,
                      fontWeight: active ? 600 : 400,
                      color: active ? "var(--text-primary)" : "var(--text-secondary)",
                    }}
                  >
                    <span>{m.label}</span>
                    {active && <Check size={14} strokeWidth={2.5} aria-hidden />}
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>

      {/* Rows: name left, the chosen metric right. */}
      {rows.map((row) => {
        const cell = metric.render(row);
        return (
          <button
            key={row.symbol}
            type="button"
            onClick={() => onOpen(row.symbol)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              width: "100%",
              padding: "13px 2px",
              textAlign: "left",
              background: "transparent",
              border: "none",
              borderBottom: "1px solid var(--glass-border)",
              cursor: "pointer",
              color: "var(--text-primary)",
              WebkitTapHighlightColor: "transparent",
            }}
          >
            <CompanyLogo logoUrl={row.logo_url} name={row.name} symbol={row.symbol} size={34} />
            <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, flex: 1 }}>
              <span
                style={{
                  fontSize: 14,
                  fontWeight: 500,
                  letterSpacing: "-0.01em",
                  lineHeight: 1.2,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
                title={row.name}
              >
                {row.name}
              </span>
              <span
                style={{
                  fontSize: 11.5,
                  lineHeight: 1.2,
                  color: "var(--text-tertiary)",
                  whiteSpace: "nowrap",
                }}
              >
                {row.symbol}
              </span>
            </div>

            {/* The one selected value, right-aligned. */}
            <span
              style={{
                flexShrink: 0,
                textAlign: "right",
                fontSize: 14,
                fontWeight: 500,
                color: cell.tone ?? "var(--text-primary)",
                whiteSpace: "nowrap",
              }}
            >
              {cell.text}
            </span>
          </button>
        );
      })}
      {/* When the value column IS the sort, note it — a quiet cue that the
          list order tracks the chosen metric, like Groww. */}
      {sortedByThis && (
        <div
          style={{
            padding: "8px 2px 2px",
            fontSize: 10.5,
            color: "var(--text-tertiary)",
            textAlign: "right",
          }}
        >
          sorted by {metric.label.toLowerCase()} ({sort.dir === "asc" ? "low → high" : "high → low"})
        </div>
      )}
    </div>
  );
}

export { SERVER_SORTABLE };
