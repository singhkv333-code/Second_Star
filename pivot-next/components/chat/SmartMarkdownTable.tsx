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
import { CompanyLogo } from "@/components/CompanyLogo";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";
import { colorizeGainLoss } from "@/components/chat/AssistantMessage";

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
const RANK_HEADER_RE = /^(rank|#|sr\.?(\s*no\.?)?)$/i;
const TICKER_RE = /^[A-Z0-9&.\-]{2,20}$/;
// "Bank of Maharashtra (MAHABANK)" — the ticker the deterministic screen
// render appends in parens, resolvable statically (no search round-trip).
const PAREN_TICKER_RE = /\(([A-Z][A-Z0-9.&-]{1,19})\)\s*$/;

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
  const inFlightRef = useRef<Set<string>>(new Set());
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

  /** Ticker resolvable WITHOUT any lookup: the (folded) symbol column, the
   * "(SYMBOL)" the screen render appends to the name, or a ticker-looking
   * bare name. Null when only a hover-time search could resolve it. */
  const staticTickerFor = (row: string[]): string | null => {
    if (plan.tickerCol !== -1) {
      const t = (row[plan.tickerCol] ?? "").trim();
      if (t) return t.toUpperCase();
    }
    const name = (row[plan.nameCol] ?? "").trim();
    const paren = PAREN_TICKER_RE.exec(name);
    if (paren?.[1]) return paren[1];
    if (TICKER_RE.test(name) && name === name.toUpperCase()) return name;
    return null;
  };

  /** The ticker for a row: static when possible, else whatever the
   * hover-resolution cached. */
  const tickerFor = (row: string[]): string | null => {
    const t = staticTickerFor(row);
    if (t) return t;
    const name = (row[plan.nameCol] ?? "").trim();
    return resolvedRef.current.get(name)?.symbol ?? null;
  };

  // Company logos for every statically-resolvable row, one batched request
  // through the module-level cache (same source as the Screener tab).
  const logoSymbols = useMemo(
    () =>
      plan.nameCol === -1
        ? []
        : rows.map((r) => staticTickerFor(r)).filter((t): t is string => !!t),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, plan.nameCol, plan.tickerCol],
  );
  const logos = useCompanyLogos(logoSymbols);

  // Frozen lead columns: Rank (when it leads) + the name column stay pinned
  // while the metric columns scroll horizontally underneath.
  const firstVis = plan.visible[0];
  const rankLeads =
    firstVis !== undefined && RANK_HEADER_RE.test((header[firstVis] ?? "").trim());
  const nameVisIdx = plan.visible.indexOf(plan.nameCol);
  const stickyCount =
    plan.nameCol !== -1 && (nameVisIdx === 0 || (rankLeads && nameVisIdx === 1))
      ? nameVisIdx + 1
      : rankLeads
        ? 1
        : 0;
  const RANK_W = 52; // px — fixed so the name column's sticky offset is exact
  const stickyStyle = (vi: number): React.CSSProperties | undefined =>
    vi < stickyCount
      ? {
          position: "sticky",
          left: vi === 0 ? 0 : rankLeads ? RANK_W : 0,
          zIndex: 2,
          background: "hsl(var(--background))",
          ...(vi === 0 && rankLeads ? { width: RANK_W, minWidth: RANK_W } : {}),
        }
      : undefined;

  const resolveForHover = (row: string[]): void => {
    if (tickerFor(row)) return; // already resolvable
    const name = (row[plan.nameCol] ?? "").trim();
    // In-flight is tracked separately from the result cache: a failed lookup
    // used to cache `null`, which the `has(name)` guard then read as "already
    // answered" — so one network blip hid that row's action bar for good.
    // Misses stay uncached, so the next hover retries.
    if (!name || inFlightRef.current.has(name)) return;
    inFlightRef.current.add(name);
    void searchCompanies(name, 1).then((res) => {
      const hit = !isError(res) ? res.data.results[0] : undefined;
      inFlightRef.current.delete(name);
      if (hit) resolvedRef.current.set(name, { symbol: hit.symbol, name: hit.name });
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
      {/* border-separate (not collapse): collapsed borders detach from
          position:sticky cells and smear while the rest scrolls. */}
      <table className="w-full border-separate border-spacing-0 text-sm">
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
                  style={{
                    ...(isName ? { width: "44%", minWidth: 220 } : {}),
                    ...(stickyStyle(vi) ?? {}),
                    // Sticky header cells need an OPAQUE fill (muted/60 lets
                    // scrolled columns bleed through underneath).
                    ...(vi < stickyCount ? { background: "hsl(var(--muted))" } : {}),
                  }}
                  className={[
                    "border-b-2 border-border px-3 py-2",
                    vi < stickyCount ? "" : "bg-muted/60",
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
                const showActions = isName && hoverRow === ri && !!ticker;
                return (
                  <td
                    key={ci}
                    style={stickyStyle(vi)}
                    className={[
                      "border-b border-border/50 px-3 py-2 align-middle",
                      vi < plan.visible.length - 1 ? "border-r border-border/40" : "",
                      plan.numeric[ci]
                        ? "text-right tabular-nums text-foreground"
                        : "text-foreground",
                    ].join(" ")}
                  >
                    {isName ? (
                      <>
                        {ticker && (
                          <span className="mr-2 inline-block align-middle">
                            <CompanyLogo
                              logoUrl={logos[ticker] ?? null}
                              name={cell.replace(PAREN_TICKER_RE, "").trim() || ticker}
                              symbol={ticker}
                              size={22}
                            />
                          </span>
                        )}
                        {/* The name and the quick-action bar share one box:
                            hovering FLIPS the name out and the bar in, so the
                            bar lands exactly where the name was instead of
                            floating over it. A right-pinned bar covered long
                            names (129px bar vs 57px of free space) while
                            clearing short ones — the same row reading as
                            broken or fine purely by name length. `visibility`
                            (not `display`) keeps the name's width reserved, so
                            the column never reflows on hover. */}
                        <span className="relative inline-flex items-center align-middle">
                          <button
                            type="button"
                            onClick={() => void openCompany(row)}
                            title={`Open ${cell}`}
                            aria-hidden={showActions}
                            tabIndex={showActions ? -1 : undefined}
                            className="inline-flex items-center gap-1.5 font-semibold text-foreground underline-offset-2 hover:text-primary hover:underline"
                            style={{
                              background: "none",
                              border: "none",
                              padding: 0,
                              cursor: "pointer",
                              font: "inherit",
                              fontWeight: 600,
                              whiteSpace: "nowrap",
                              visibility: showActions ? "hidden" : "visible",
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
                          {showActions && ticker && (
                            <StockHoverActions
                              symbol={ticker}
                              name={cell.trim()}
                              className="absolute"
                              style={{
                                // Anchored to the name's own left edge — the
                                // bar occupies the name's slot exactly.
                                left: 0,
                                top: "50%",
                                marginTop: -14,
                                padding: 2,
                                zIndex: 5,
                              }}
                            />
                          )}
                        </span>
                      </>
                    ) : plan.numeric[ci] ? (
                      colorizeGainLoss(cell, `cell-${ri}-${ci}`)
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
