"use client";

/**
 * SmartMarkdownTable — the chat's markdown tables, upgraded from a passive
 * grid into a small data surface:
 *
 *   • Clear column division (cell borders, numeric columns right-aligned,
 *     ink-black semibold header row).
 *   • Click-to-sort on the columns where sorting means something — numeric
 *     columns and the name/symbol column — with an explicit direction
 *     indicator. Other columns stay inert.
 *   • Company cells link to the stock page: ticker-looking text routes
 *     straight to /stock/SYMBOL; display names ("Rane Holdings") resolve
 *     through /api/companies/search on click.
 *   • Hovering a row surfaces the Kite-style quick-action bar (Buy / Sell /
 *     chart / option chain / ask Pivot) anchored to the row's right edge.
 *
 * Receives the raw hast <table> node from react-markdown and re-renders the
 * table itself (the markdown children are ignored) so sorting operates on
 * plain cell text without fighting React reconciliation.
 */

import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUp, ArrowUpDown, Loader2 } from "lucide-react";
import { searchCompanies } from "@/lib/api";
import { isError } from "@/lib/types";
import { StockHoverActions } from "@/components/StockHoverActions";

// ── hast extraction (minimal local typing — we only walk tag + children) ──

type HastNode = {
  type?: string;
  tagName?: string;
  value?: string;
  children?: HastNode[];
};

function textOf(node: HastNode | undefined): string {
  if (!node) return "";
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(textOf).join("");
}

function rowsOf(section: HastNode | undefined): string[][] {
  if (!section) return [];
  return (section.children ?? [])
    .filter((c) => c.tagName === "tr")
    .map((tr) =>
      (tr.children ?? [])
        .filter((c) => c.tagName === "th" || c.tagName === "td")
        .map((cell) => textOf(cell).trim()),
    );
}

function extractTable(node: HastNode): { header: string[]; rows: string[][] } {
  const kids = node.children ?? [];
  const thead = kids.find((c) => c.tagName === "thead");
  const tbody = kids.find((c) => c.tagName === "tbody");
  const headerRows = rowsOf(thead);
  return {
    header: headerRows[0] ?? [],
    rows: rowsOf(tbody),
  };
}

// ── Column semantics ─────────────────────────────────────────────────────

const NAME_HEADER_RE = /^(name|company|companies|symbol|ticker|stock|scrip)s?$/i;
const TICKER_RE = /^[A-Z0-9&.\-]{2,20}$/;

/** Parse "₹1,234.56", "12.5%", "(3.2)" → number; NaN when not numeric. */
function parseNum(s: string): number {
  const cleaned = s
    .replace(/[₹$,%]/g, "")
    .replace(/,/g, "")
    .replace(/^\((.*)\)$/, "-$1")
    .trim();
  if (!cleaned || /[^0-9+\-.eE]/.test(cleaned)) return NaN;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : NaN;
}

function isNumericColumn(rows: string[][], col: number): boolean {
  const vals = rows.map((r) => r[col] ?? "").filter((v) => v !== "" && v !== "—");
  if (vals.length === 0) return false;
  const numeric = vals.filter((v) => !Number.isNaN(parseNum(v))).length;
  return numeric / vals.length >= 0.6;
}

// ── The table ────────────────────────────────────────────────────────────

type SortState = { col: number; dir: "asc" | "desc" } | null;

export function SmartMarkdownTable({ node }: { node: unknown }): React.ReactElement {
  const router = useRouter();
  const { header, rows } = useMemo(
    () => extractTable((node ?? {}) as HastNode),
    [node],
  );
  const [sort, setSort] = useState<SortState>(null);
  const [hoverRow, setHoverRow] = useState<number | null>(null);
  const [resolving, setResolving] = useState<string | null>(null);
  // Display-name → resolved {symbol, name} cache. Filled lazily on row
  // hover so the quick-action bar always carries a REAL ticker (a bar on
  // "Rane Holdings" must not deep-link /stock/Rane%20Holdings). The state
  // bump re-renders the hovered row once the lookup lands.
  const resolvedRef = useRef<Map<string, { symbol: string; name: string } | null>>(
    new Map(),
  );
  const [, bumpResolved] = useState(0);

  const resolveForHover = (cellText: string): void => {
    const t = cellText.trim();
    if (!t || resolvedRef.current.has(t)) return;
    if (TICKER_RE.test(t) && t === t.toUpperCase()) {
      resolvedRef.current.set(t, { symbol: t, name: t });
      return;
    }
    resolvedRef.current.set(t, null); // in flight — render nothing yet
    void searchCompanies(t, 1).then((res) => {
      const hit = !isError(res) ? res.data.results[0] : undefined;
      resolvedRef.current.set(
        t,
        hit ? { symbol: hit.symbol, name: hit.name } : null,
      );
      bumpResolved((n) => n + 1);
    });
  };

  const numericCols = useMemo(
    () => header.map((_, i) => isNumericColumn(rows, i)),
    [header, rows],
  );
  const nameCol = useMemo(
    () => header.findIndex((h) => NAME_HEADER_RE.test(h.trim())),
    [header],
  );
  const sortable = header.map(
    (h, i) => numericCols[i] || i === nameCol,
  );

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const { col, dir } = sort;
    const mul = dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = a[col] ?? "";
      const bv = b[col] ?? "";
      if (numericCols[col]) {
        const an = parseNum(av);
        const bn = parseNum(bv);
        // Blanks/dashes sink to the bottom in either direction.
        if (Number.isNaN(an) && Number.isNaN(bn)) return 0;
        if (Number.isNaN(an)) return 1;
        if (Number.isNaN(bn)) return -1;
        return (an - bn) * mul;
      }
      return av.localeCompare(bv) * mul;
    });
  }, [rows, sort, numericCols]);

  const toggleSort = (col: number): void => {
    if (!sortable[col]) return;
    setSort((prev) =>
      prev?.col === col
        ? prev.dir === "asc"
          ? { col, dir: "desc" }
          : null // third click clears back to the model's order
        : { col, dir: "asc" },
    );
  };

  /** Company cell → stock page. Tickers route directly; display names
   * resolve through the company search (first hit wins). */
  const openCompany = async (cellText: string): Promise<void> => {
    const t = cellText.trim();
    if (!t) return;
    if (TICKER_RE.test(t) && t === t.toUpperCase()) {
      router.push(`/stock/${encodeURIComponent(t)}`);
      return;
    }
    setResolving(t);
    try {
      const res = await searchCompanies(t, 1);
      if (!isError(res) && res.data.results[0]) {
        router.push(`/stock/${encodeURIComponent(res.data.results[0].symbol)}`);
      }
    } finally {
      setResolving(null);
    }
  };

  // Fallback: a malformed/headerless table renders nothing smart — just
  // an empty fragment guard so we never crash the message.
  if (header.length === 0) {
    return <div />;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {header.map((h, i) => {
              const active = sort?.col === i;
              return (
                <th
                  key={i}
                  onClick={() => toggleSort(i)}
                  aria-sort={
                    active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined
                  }
                  className={[
                    "border-b-2 border-border bg-muted/60 px-3 py-2",
                    // Ink-black header — the row must read as the table's
                    // anchor, not another data row.
                    "text-[13px] font-semibold text-foreground",
                    i < header.length - 1 ? "border-r border-border/50" : "",
                    numericCols[i] ? "text-right" : "text-left",
                    sortable[i] ? "cursor-pointer select-none hover:bg-muted" : "",
                  ].join(" ")}
                >
                  <span className="inline-flex items-center gap-1">
                    {h}
                    {sortable[i] &&
                      (active ? (
                        sort!.dir === "asc" ? (
                          <ArrowUp size={12} strokeWidth={2.2} aria-hidden="true" />
                        ) : (
                          <ArrowDown size={12} strokeWidth={2.2} aria-hidden="true" />
                        )
                      ) : (
                        <ArrowUpDown
                          size={11}
                          strokeWidth={2}
                          aria-hidden="true"
                          className="opacity-35"
                        />
                      ))}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, ri) => (
            <tr
              key={ri}
              onMouseEnter={() => {
                setHoverRow(ri);
                if (nameCol >= 0) resolveForHover(row[nameCol] ?? "");
              }}
              onMouseLeave={() => setHoverRow(null)}
              className="relative hover:bg-muted/40"
            >
              {row.map((cell, ci) => {
                const isName = ci === nameCol && cell.trim() !== "";
                return (
                  <td
                    key={ci}
                    className={[
                      "border-b border-border/50 px-3 py-2 align-top",
                      ci < row.length - 1 ? "border-r border-border/40" : "",
                      numericCols[ci]
                        ? "text-right tabular-nums text-foreground"
                        : "text-foreground",
                      // The name cell hosts the hover bar — needs a
                      // positioning context wider than the cell.
                      isName ? "relative" : "",
                    ].join(" ")}
                  >
                    {isName ? (
                      <>
                        <button
                          type="button"
                          onClick={() => void openCompany(cell)}
                          title={`Open ${cell}`}
                          className="inline-flex items-center gap-1.5 font-medium text-foreground underline-offset-2 hover:text-primary hover:underline"
                          style={{
                            background: "none",
                            border: "none",
                            padding: 0,
                            cursor: "pointer",
                            font: "inherit",
                          }}
                        >
                          {cell}
                          {resolving === cell.trim() && (
                            <Loader2
                              size={11}
                              className="animate-spin"
                              aria-hidden="true"
                            />
                          )}
                        </button>
                        {/* Kite-style quick actions — appear on row hover
                            once the cell resolves to a real ticker,
                            floating over the columns to the right. */}
                        {hoverRow === ri &&
                          (() => {
                            const hit = resolvedRef.current.get(cell.trim());
                            if (!hit) return null;
                            return (
                              <StockHoverActions
                                symbol={hit.symbol}
                                name={hit.name}
                                className="absolute z-20"
                                style={{
                                  top: "50%",
                                  transform: "translateY(-50%)",
                                  left: "100%",
                                  marginLeft: 8,
                                }}
                              />
                            );
                          })()}
                      </>
                    ) : (
                      cell
                    )}
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
