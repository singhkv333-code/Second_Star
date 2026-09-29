"use client";

import { useEffect, useMemo, useReducer, useState } from "react";
import { useRouter } from "next/navigation";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";

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
      {/* Phone layout — a readable card per stock instead of a wide table you
          have to swipe sideways through. Hidden on desktop by CSS; the table
          below is hidden on phone. Both read the same rows, so sort/scroll
          behaviour is identical. */}
      <MobileStockList
        rows={rows}
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
// The phone-width alternative to the wide table, modelled on how Groww and
// INDmoney show a scan list: a flat, dense list — one tappable ROW per stock,
// hairline divider between them — not a stack of bordered cards. Each row is
// [logo · ticker/name] ……… [day sparkline] [price / %change]. The scan list
// stays minimal on purpose; mkt-cap / P/E / 1-Y live on the stock page, and
// crowding them into every row is exactly what made the first pass read as
// noise. Whole row opens the stock page.

function MobileStockList({
  rows,
  onOpen,
}: {
  rows: ScreenerStock[];
  onOpen: (symbol: string) => void;
}): React.ReactElement {
  return (
    <div
      className="screener-cards-mobile"
      style={{
        display: "none", // flipped to block under lg by globals.css
        fontFamily: "var(--font-ui)",
        fontVariantNumeric: "tabular-nums",
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md, 12px)",
        overflow: "hidden",
      }}
    >
      {rows.map((row, i) => {
        const pctTone = toneOf(row.change_pct);
        const up = (row.change_pct ?? 0) > 0;
        const down = (row.change_pct ?? 0) < 0;
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
              padding: "12px 14px",
              textAlign: "left",
              background: "transparent",
              border: "none",
              borderTop: i === 0 ? "none" : "1px solid var(--glass-border)",
              cursor: "pointer",
              color: "var(--text-primary)",
              WebkitTapHighlightColor: "transparent",
            }}
          >
            {/* Identity */}
            <CompanyLogo logoUrl={row.logo_url} name={row.name} symbol={row.symbol} size={36} />
            <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, flex: 1 }}>
              <span
                style={{
                  fontSize: 14,
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                  lineHeight: 1.15,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {row.symbol}
              </span>
              <span
                style={{
                  fontSize: 11.5,
                  lineHeight: 1.2,
                  color: "var(--text-tertiary)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
                title={row.name}
              >
                {row.name}
              </span>
            </div>

            {/* Day shape — small, sits between name and price like Groww's
                trend graph. Hidden when there's nothing to draw so the row
                doesn't reserve an empty gap. */}
            <div style={{ flexShrink: 0, opacity: 0.9 }}>
              <Sparkline points={getSparkline(row.symbol)} baseline={row.prev_close} width={52} height={26} />
            </div>

            {/* Price + day move */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "flex-end",
                gap: 2,
                flexShrink: 0,
                minWidth: 76,
              }}
            >
              <span style={{ fontSize: 14, fontWeight: 600 }}>{fmtPrice(row.price)}</span>
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 2,
                  fontSize: 12,
                  fontWeight: 500,
                  color: pctTone,
                }}
              >
                {row.change_pct != null && (up || down) && (
                  <span aria-hidden style={{ fontSize: 9, lineHeight: 1 }}>
                    {up ? "▲" : "▼"}
                  </span>
                )}
                {row.change_pct == null ? DASH : `${Math.abs(row.change_pct).toFixed(2)}%`}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

export { SERVER_SORTABLE };
