"use client";

/**
 * SmartMarkdownTable — the chat's markdown tables, upgraded from a passive
 * grid into a small data surface:
 *
 *   • Clear column division (cell borders, numeric columns right-aligned,
 *     ink-black semibold header row).
 *   • Column hygiene: all-empty columns (e.g. a "Flag" column with no
 *     flags) are dropped, and a Symbol/Ticker column is FOLDED INTO the
 *     Name column — the name is what the user reads, the ticker is what
 *     the links/actions need — instead of burning width twice.
 *   • Click-to-sort on the columns where sorting means something — numeric
 *     columns and the name column — with an explicit direction indicator.
 *   • The name cell is bold, gets the widest column, links to the stock
 *     page, and hosts the Kite-style quick-action bar INLINE right next to
 *     the name while its row is hovered (never floating over other cells).
 *
 * Receives the raw hast <table> node from react-markdown and re-renders the
 * table itself (the markdown children are ignored) so all of the above
 * operates on plain cell text without fighting React reconciliation.
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

const NAME_HEADER_RE = /^(name|company|companies|stock|scrip)s?$/i;
const TICKER_HEADER_RE = /^(symbol|ticker)s?$/i;
const TICKER_RE = /^[A-Z0-9&.\-]{2,20}$/;

function isBlank(v: string): boolean {
  return v === "" || v === "—" || v === "-" || v === "–";
}

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
  const vals = rows.map((r) => r[col] ?? "").filter((v) => !isBlank(v));
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
  // Display-name → resolved ticker cache (only needed when the table has no
  // ticker column). Filled lazily on row hover; a state bump re-renders the
  // hovered row once the lookup lands.
  const resolvedRef = useRef<Map<string, { symbol: string; name: string } | null>>(
    new Map(),
  );
  const [, bumpResolved] = useState(0);

  // ── Column plan ────────────────────────────────────────────────────
  const plan = useMemo(() => {
    const numeric = header.map((_, i) => isNumericColumn(rows, i));
    let nameCol = header.findIndex((h) => NAME_HEADER_RE.test(h.trim()));
    const tickerCol = header.findIndex((h) => TICKER_HEADER_RE.test(h.trim()));
    // A ticker-only table: the symbol column IS the display column.
    if (nameCol === -1) nameCol = tickerCol;
    const hidden = new Set<number>();
    header.forEach((_, i) => {
      // Drop columns with no data at all (the empty "Flag" column class).
      if (rows.length > 0 && rows.every((r) => isBlank(r[i] ?? ""))) {
        hidden.add(i);
      }
    });
    // Fold a separate ticker column into the name column — the ticker
    // still powers links/actions, it just doesn't burn its own column.
    if (tickerCol !== -1 && tickerCol !== nameCol) hidden.add(tickerCol);
    const visible = header.map((_, i) => i).filter((i) => !hidden.has(i));
    return { numeric, nameCol, tickerCol, visible };
  }, [header, rows]);

  const sortable = header.map(
    (_, i) => plan.numeric[i] || i === plan.nameCol,
  );

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const { col, dir } = sort;
    const mul = dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = a[col] ?? "";
      const bv = b[col] ?? "";
      if (plan.numeric[col]) {
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
  }, [rows, sort, plan.numeric]);

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

  /** The ticker for a row: the (folded) symbol column when present, else a
   * ticker-looking name, else whatever the hover-resolution cached. */
  const tickerFor = (row: string[]): string | null => {
    if (plan.tickerCol !== -1) {
      const t = (row[plan.tickerCol] ?? "").trim();
      if (t) return t.toUpperCase();
    }
    const name = (row[plan.nameCol] ?? "").trim();
    if (TICKER_RE.test(name) && name === name.toUpperCase()) return name;
    return resolvedRef.current.get(name)?.symbol ?? null;
  };

  const resolveForHover = (row: string[]): void => {
    if (tickerFor(row)) return; // already resolvable
    const name = (row[plan.nameCol] ?? "").trim();
    if (!name || resolvedRef.current.has(name)) return;
    resolvedRef.current.set(name, null); // in flight
    void searchCompanies(name, 1).then((res) => {
      const hit = !isError(res) ? res.data.results[0] : undefined;
      resolvedRef.current.set(
        name,
        hit ? { symbol: hit.symbol, name: hit.name } : null,
      );
      bumpResolved((n) => n + 1);
    });
  };

  const openCompany = async (row: string[]): Promise<void> => {
    const ticker = tickerFor(row);
    if (ticker) {
      router.push(`/stock/${encodeURIComponent(ticker)}`);
      return;
    }
    const name = (row[plan.nameCol] ?? "").trim();
    if (!name) return;
    setResolving(name);
    try {
      const res = await searchCompanies(name, 1);
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
            {plan.visible.map((i, vi) => {
              const active = sort?.col === i;
              const isName = i === plan.nameCol;
              return (
                <th
                  key={i}
                  onClick={() => toggleSort(i)}
                  aria-sort={
                    active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined
                  }
                  style={isName ? { width: "44%", minWidth: 220 } : undefined}
                  className={[
                    "border-b-2 border-border bg-muted/60 px-3 py-2",
                    // Ink-black header — the row must read as the table's
                    // anchor, not another data row.
                    "text-[13px] font-semibold text-foreground",
                    vi < plan.visible.length - 1 ? "border-r border-border/50" : "",
                    plan.numeric[i] ? "text-right" : "text-left",
                    sortable[i] ? "cursor-pointer select-none hover:bg-muted" : "",
                  ].join(" ")}
                >
                  <span className="inline-flex items-center gap-1">
                    {header[i]}
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
                if (plan.nameCol >= 0) resolveForHover(row);
              }}
              onMouseLeave={() => setHoverRow(null)}
              className="hover:bg-muted/40"
            >
              {plan.visible.map((ci, vi) => {
                const cell = row[ci] ?? "";
                const isName = ci === plan.nameCol && cell.trim() !== "";
                const ticker = isName ? tickerFor(row) : null;
                return (
                  <td
                    key={ci}
                    className={[
                      "border-b border-border/50 px-3 py-2 align-middle",
                      vi < plan.visible.length - 1 ? "border-r border-border/40" : "",
                      plan.numeric[ci]
                        ? "text-right tabular-nums text-foreground"
                        : "text-foreground",
                      // Positioning context for the pinned quick-action bar.
                      isName ? "relative" : "",
                    ].join(" ")}
                  >
                    {isName ? (
                      <>
                        <button
                          type="button"
                          onClick={() => void openCompany(row)}
                          title={`Open ${cell}`}
                          className="inline-flex items-center gap-1.5 font-semibold text-foreground underline-offset-2 hover:text-primary hover:underline"
                          style={{
                            background: "none",
                            border: "none",
                            padding: 0,
                            cursor: "pointer",
                            font: "inherit",
                            fontWeight: 600,
                            whiteSpace: "nowrap",
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
                        {/* Quick actions — PINNED to the name column's right
                            edge (same X on every row, Kite-style) and
                            absolutely positioned so the row height never
                            changes when it appears. */}
                        {hoverRow === ri && ticker && (
                          <StockHoverActions
                            symbol={ticker}
                            name={cell.trim()}
                            className="absolute"
                            style={{
                              // Pinned to the name column's right edge —
                              // one constant axis for every row, never
                              // crossing into the next column's values.
                              right: 8,
                              top: "50%",
                              marginTop: -14,
                              padding: 2,
                              zIndex: 5,
                            }}
                          />
                        )}
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
