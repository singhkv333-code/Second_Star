"use client";

/**
 * SmartMarkdownTable — the chat's markdown tables, drawn in the screen card's
 * design (ScreenResultsCard's TABLE_CLS, CompanyCell, WrapText) so the chat
 * has one table, whoever wrote it:
 *
 *   • Column hygiene: all-empty columns are dropped, and a Symbol/Ticker
 *     column is FOLDED INTO the company column (logo, name, ticker beneath).
 *   • A company table gets the "#" rank column and a "Median of N" row;
 *     numeric columns are right-aligned and sortable by their header.
 *   • A column of sentences wraps inside a bounded width instead of widening
 *     the whole table.
 *   • The company cell links to the stock page and swaps in the quick-action
 *     bar on hover.
 *
 * Receives the raw hast <table> node from react-markdown and re-renders the
 * table itself (the markdown children are ignored) so all of the above
 * operates on plain cell text without fighting React reconciliation.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUp } from "lucide-react";
import { searchCompanies } from "@/lib/api";
import { isError } from "@/lib/types";
import { CompanyCell, TABLE_CLS, WrapText } from "@/components/chat/ScreenResultsCard";
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

// A company/name column, by header word. Kept broad because the model
// labels this column many ways ("Holding", "Instrument", "Constituent"…);
// whatever it misses, the CONTENT-based fallback in `plan` still catches.
const NAME_HEADER_RE =
  /^(names?|company|companies|stocks?|scrips?|holdings?|instruments?|securit(y|ies)|assets?|constituents?|positions?|funds?|etfs?)$/i;
// "NSE symbol", "BSE code", "Scrip" — the qualified forms a model reaches for
// when it is being helpful about WHICH symbol it means. Unqualified
// symbol/ticker was the only form the deterministic screen ever emitted, so
// the qualified ones fell through, the column was never folded into the name,
// and every row paid a searchCompanies round-trip on hover to recover a
// ticker that was sitting in the table already.
const TICKER_HEADER_RE =
  /^((nse|bse)\s*)?(symbol|ticker|code|scrip)s?$/i;
const RANK_HEADER_RE = /^(rank|#|sr\.?(\s*no\.?)?)$/i;
const TICKER_RE = /^[A-Z0-9&.\-]{2,20}$/;
// "Bank of Maharashtra (MAHABANK)" — the ticker the deterministic screen
// render appends in parens, resolvable statically (no search round-trip).
const PAREN_TICKER_RE = /\(([A-Z][A-Z0-9.&-]{1,19})\)\s*$/;
// "TVSMOTOR TVS Motor Company Limited" — some models PREPEND the ticker to the
// full legal name instead of appending "(TICKER)". Captures the leading token
// when it is followed by a mixed-case name (the trailing lowercase letter is
// the tell that the rest is a real name, not an all-caps phrase like
// "TATA MOTORS"). Strength is gated at the call site so short all-caps name
// prefixes (HDFC Bank, TCS, ITC) aren't mistaken for a ticker.
const LEAD_TICKER_RE = /^([A-Z][A-Z0-9&.-]{1,19})\s+.*[a-z]/;

function isBlank(v: string): boolean {
  return v === "" || v === "—" || v === "-" || v === "–";
}

/** Parse "₹1,234.56", "12.5%", "(3.2)", "0.42×", "24,945 cr" → number;
 * NaN when not numeric.
 *
 * The trailing-unit forms matter more than they look. A column of "0.42×"
 * multiples or "₹1,234 cr" figures parsed as NaN, which failed the 60%
 * threshold in isNumericColumn, which meant the column was treated as TEXT —
 * left-aligned and unsortable. The reading is silently worse for exactly the
 * columns a screen exists to rank on (D/E, EV/EBITDA, market cap). Units
 * belong in the header, and the emitter is told so, but a model that puts
 * them in the cell should not cost the user sorting.
 */
function parseNum(s: string): number {
  const cleaned = s
    .replace(/[₹$,%]/g, "")
    .replace(/,/g, "")
    .replace(/\s*(?:x|×|bps|cr|crore|crores|lakhs?|mn|bn|days?|yrs?|years?)\s*$/i, "")
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

/** A value written the way its column writes its own: the same currency
 * prefix, unit suffix, decimals, grouping and sign convention. */
function formatLike(samples: string[], v: number): string {
  const first = (samples[0] ?? "").trim().replace(/^\((.*)\)$/, "$1").replace(/^[+\-−]/, "");
  const prefix = /^[^\d.]*/.exec(first)?.[0] ?? "";
  const suffix = /[^\d.]*$/.exec(first)?.[0] ?? "";
  const decimals = Math.max(0, ...samples.map((x) => /\.(\d+)/.exec(x)?.[1]?.length ?? 0));
  const abs = Math.abs(v).toLocaleString("en-IN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    useGrouping: samples.some((x) => x.includes(",")),
  });
  const core = `${prefix}${abs}${suffix}`;
  if (v < 0) return samples.some((x) => /^\(.*\)$/.test(x.trim())) ? `(${core})` : `-${core}`;
  return v > 0 && samples.some((x) => x.trim().startsWith("+")) ? `+${core}` : core;
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
  const [resolvedTick, bumpResolved] = useState(0);

  // ── Column plan ────────────────────────────────────────────────────
  const plan = useMemo(() => {
    const numeric = header.map((_, i) => isNumericColumn(rows, i));
    let nameCol = header.findIndex((h) => NAME_HEADER_RE.test(h.trim()));
    const tickerCol = header.findIndex((h) => TICKER_HEADER_RE.test(h.trim()));
    // A ticker-only table: the symbol column IS the display column.
    if (nameCol === -1) nameCol = tickerCol;
    // CONTENT-based fallback — the uniform funnel. When no header matched but
    // a text column's cells are shaped "Name (TICKER)" (the ticker-in-parens
    // form the model emits), that column IS the company column regardless of
    // its header word. Picks the highest-signal such column; the paren form
    // is unambiguous (sectors/metrics never carry a trailing "(TICKER)"), so
    // this never misfires on a Sector/Weight column.
    if (nameCol === -1) {
      let best = -1;
      let bestScore = 0.5; // require a majority to claim the column
      header.forEach((_, i) => {
        if (numeric[i]) return;
        const cells = rows
          .map((r) => (r[i] ?? "").trim())
          .filter((v) => !isBlank(v));
        if (cells.length === 0) return;
        const score =
          cells.filter((v) => PAREN_TICKER_RE.test(v)).length / cells.length;
        if (score >= bestScore) {
          best = i;
          bestScore = score;
        }
      });
      if (best !== -1) nameCol = best;
    }
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
    // Leading-ticker form: "TVSMOTOR TVS Motor Company Limited". Trust the lead
    // token as the symbol only on a STRONG ticker signal — 5+ chars, or a
    // digit/&/hyphen — so a short all-caps name prefix ("HDFC Bank", "ITC")
    // isn't mislinked. Weaker cases fall through to hover resolution.
    const lead = LEAD_TICKER_RE.exec(name);
    if (lead?.[1]) {
      const t = lead[1];
      if ((t.length >= 5 || /[0-9&-]/.test(t)) && TICKER_RE.test(t)) return t;
    }
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
        : rows.map((r) => tickerFor(r)).filter((t): t is string => !!t),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, plan.nameCol, plan.tickerCol, resolvedTick],
  );
  const logos = useCompanyLogos(logoSymbols);

  // Rows named but not tickered ("Infosys", "Bajaj Auto") are resolved when
  // the table appears, all at once, not on hover: resolved lazily, they drew a
  // letter until someone happened to point at them.
  useEffect(() => {
    if (plan.nameCol === -1) return;
    const names = Array.from(new Set(
      rows
        .filter((r) => !staticTickerFor(r))
        .map((r) => (r[plan.nameCol] ?? "").trim())
        .filter((n) => n && !resolvedRef.current.has(n) && !inFlightRef.current.has(n)),
    )).slice(0, 40);
    if (names.length === 0) return;
    names.forEach((n) => inFlightRef.current.add(n));
    void Promise.all(
      names.map((name) =>
        searchCompanies(name, 1)
          .then((res) => {
            const hit = !isError(res) ? res.data.results[0] : undefined;
            if (hit) resolvedRef.current.set(name, { symbol: hit.symbol, name: hit.name });
          })
          .catch(() => {})
          .finally(() => inFlightRef.current.delete(name)),
      ),
    ).then(() => bumpResolved((n) => n + 1));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, plan.nameCol, plan.tickerCol]);

  // A rank column the model wrote leads as the card's "#"; a company table
  // without one gets it, as a screen does.
  const firstVis = plan.visible[0];
  const rankCol =
    firstVis !== undefined && RANK_HEADER_RE.test((header[firstVis] ?? "").trim())
      ? firstVis
      : -1;
  const addRank = rankCol === -1 && plan.nameCol !== -1;
  const dataCols = plan.visible.filter((i) => i !== rankCol);
  // Columns of sentences wrap inside a bounded width (see WrapText).
  const longText = header.map(
    (_, i) =>
      !plan.numeric[i] &&
      i !== plan.nameCol &&
      rows.some((r) => (r[i] ?? "").length > 28),
  );
  // The screen card's median row, for a company table with enough rows.
  const medians = useMemo(
    () =>
      header.map((_, i) => {
        if (plan.nameCol === -1 || !plan.numeric[i] || i === rankCol) return null;
        const cells = rows.map((r) => r[i] ?? "").filter((v) => !Number.isNaN(parseNum(v)));
        const xs = cells.map(parseNum).sort((a, b) => a - b);
        if (xs.length < 3) return null;
        const m = xs.length >> 1;
        return formatLike(cells, xs.length % 2 ? xs[m]! : (xs[m - 1]! + xs[m]!) / 2);
      }),
    [header, rows, plan, rankCol],
  );

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
    <div className={TABLE_CLS.shell}>
      <div className="overflow-x-auto">
        <table className={TABLE_CLS.table}>
          <thead>
            <tr className={TABLE_CLS.headRow}>
              {(addRank || rankCol !== -1) && (
                <th className={`w-10 text-right ${TABLE_CLS.head}`}>#</th>
              )}
              {dataCols.map((i) => {
                const active = sort?.col === i;
                return (
                  <th
                    key={i}
                    onClick={() => toggleSort(i)}
                    aria-sort={
                      active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined
                    }
                    className={[
                      TABLE_CLS.head,
                      plan.numeric[i] ? "text-right" : "text-left",
                      sortable[i] ? "cursor-pointer select-none hover:text-foreground" : "",
                      active ? "text-foreground" : "",
                    ].join(" ")}
                  >
                    <span className="inline-flex items-center gap-1">
                      {header[i]}
                      {active &&
                        (sort!.dir === "asc" ? (
                          <ArrowUp size={11} strokeWidth={2.2} aria-hidden="true" />
                        ) : (
                          <ArrowDown size={11} strokeWidth={2.2} aria-hidden="true" />
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
                className={TABLE_CLS.row}
              >
                {addRank && <td className={TABLE_CLS.rank}>{ri + 1}</td>}
                {rankCol !== -1 && <td className={TABLE_CLS.rank}>{row[rankCol]}</td>}
                {dataCols.map((ci) => {
                  const cell = row[ci] ?? "";
                  if (ci === plan.nameCol && cell.trim() !== "") {
                    const ticker = tickerFor(row);
                    return (
                      <td key={ci} className="px-3 py-2">
                        <CompanyCell
                          symbol={ticker}
                          name={cell.replace(PAREN_TICKER_RE, "").trim() || cell}
                          logoUrl={ticker ? logos[ticker] : null}
                          hovered={hoverRow === ri}
                          busy={resolving === cell.trim()}
                          onOpen={() => void openCompany(row)}
                        />
                      </td>
                    );
                  }
                  if (plan.numeric[ci]) {
                    return (
                      <td key={ci} className={TABLE_CLS.num}>
                        {colorizeGainLoss(cell, `cell-${ri}-${ci}`)}
                      </td>
                    );
                  }
                  return (
                    <td
                      key={ci}
                      className={`${TABLE_CLS.cell} ${longText[ci] ? "" : "whitespace-nowrap"}`}
                    >
                      {longText[ci] ? <WrapText>{cell}</WrapText> : cell}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
          {medians.some((m) => m !== null) && (
            <tfoot>
              <tr className={TABLE_CLS.footRow}>
                {(addRank || rankCol !== -1) && <td className="px-3 py-2" />}
                {dataCols.map((ci) => (
                  <td
                    key={ci}
                    className={
                      ci === plan.nameCol
                        ? "whitespace-nowrap px-3 py-2 font-medium"
                        : "whitespace-nowrap px-3 py-2 text-right tabular-nums"
                    }
                  >
                    {ci === plan.nameCol ? `Median of ${rows.length}` : (medians[ci] ?? "")}
                  </td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  );
}
