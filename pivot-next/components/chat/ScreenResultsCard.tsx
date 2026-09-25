"use client";

/**
 * ScreenResultsCard — the rows of a `screen_fundamentals` or `scan_technicals`
 * call, rendered
 * under the model's reply when the tool returns
 * `_render_hint: "screen_results_card"`.
 *
 * The model writes the reading of the screen; this card carries the rows.
 * A screen can return up to 100 names, so the first PAGE are shown and the
 * rest sit behind "Show all". Values are the tool's rows, formatted by the
 * unit the backend attaches to each column, never recomputed.
 */

import React, { useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ChevronDown, ListFilter } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CompanyLogo } from "@/components/CompanyLogo";
import { StockHoverActions } from "@/components/StockHoverActions";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";
import { putPendingScreen } from "@/lib/screensApi";

export type ScreenColumn = {
  key: string;
  label: string;
  unit: "cr" | "pct" | "pct_signed" | "num" | "inr" | "int" | "text";
};

export type ScreenRow = {
  symbol: string;
  name?: string | null;
  sector?: string | null;
} & Record<string, unknown>;

export type ScreenResultsPayload = {
  title?: string;
  count?: number;
  total_matched?: number;
  results: ScreenRow[];
  columns?: ScreenColumn[];
  applied_filters?: { field: string; op: string; value?: number; value_field?: string }[];
  /** Every name that passed (a scan can match more than it returns). */
  symbols?: string[];
  as_of?: string;
};

const PAGE = 10;

/** The middle of the returned rows, for reading any one row against the set.
 *  Presentation arithmetic over what the card already shows; null when
 *  fewer than three rows carry a number. */
export function median(rows: ScreenRow[], key: string): number | null {
  const xs = rows
    .map((r) => r[key])
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v))
    .sort((a, b) => a - b);
  if (xs.length < 3) return null;
  const m = xs.length >> 1;
  return xs.length % 2 ? xs[m]! : (xs[m - 1]! + xs[m]!) / 2;
}

function fmt(v: unknown, unit: ScreenColumn["unit"]): string {
  if (unit === "text") return typeof v === "string" && v ? v : "—";
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  switch (unit) {
    case "inr":
      return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
    case "int":
      return Math.round(v).toLocaleString("en-IN");
    case "cr":
      return `₹${Math.round(v).toLocaleString("en-IN")} Cr`;
    case "pct_signed":
      return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
    case "pct":
      return `${v.toFixed(2)}%`;
    default:
      return v.toFixed(2);
  }
}

function tone(v: unknown, unit: ScreenColumn["unit"]): string | undefined {
  if (unit !== "pct_signed" || typeof v !== "number" || v === 0) return undefined;
  return v > 0 ? "var(--color-profit)" : "var(--color-loss)";
}

function filterLabel(
  f: NonNullable<ScreenResultsPayload["applied_filters"]>[number],
  labels: Record<string, string>,
  units: Record<string, ScreenColumn["unit"]>,
): string {
  const field = labels[f.field] ?? f.field.replace(/_/g, " ");
  // A threshold carries the unit of the column it filters ("> 15%").
  const unit = f.field === "market_cap" ? "cr" : units[f.field];
  const rhs = f.value_field
    ? labels[f.value_field] ?? f.value_field
    : unit === "cr"
      ? `₹${(f.value ?? 0).toLocaleString("en-IN")} Cr`
      : unit === "inr"
      ? `₹${(f.value ?? 0).toLocaleString("en-IN")}`
      : unit === "pct" || unit === "pct_signed"
        ? `${f.value}%`
        : String(f.value);
  return `${field} ${f.op} ${rhs}`;
}

export function ScreenResultsCard({
  payload,
}: {
  payload: ScreenResultsPayload;
}): React.ReactElement {
  const router = useRouter();
  const pathname = usePathname();
  const [expanded, setExpanded] = useState(false);
  const [hover, setHover] = useState<number | null>(null);

  const rows = useMemo(() => payload.results ?? [], [payload.results]);
  const cols = useMemo(() => payload.columns ?? [], [payload.columns]);
  const shown = expanded ? rows : rows.slice(0, PAGE);
  const logos = useCompanyLogos(useMemo(() => rows.map((r) => r.symbol), [rows]));

  const labels = useMemo(() => {
    const m: Record<string, string> = { market_cap: "Market Cap" };
    for (const c of cols) m[c.key] = c.label;
    return m;
  }, [cols]);
  const medians = useMemo(
    () => cols.map((c) => (c.unit === "text" ? null : median(rows, c.key))),
    [cols, rows],
  );
  const units = useMemo(
    () => Object.fromEntries(cols.map((c) => [c.key, c.unit])) as Record<string, ScreenColumn["unit"]>,
    [cols],
  );

  // Hands the screen to the Screener tab through the same handover the chart
  // uses: these exact names, frozen, with the conditions. Not re-expressed as
  // Screener filters: the Screener can't filter on growth, so a translation
  // would quietly drop a condition. `matched` is the list's size (the chip
  // reads it as "N names"); how many passed in total goes in the criteria.
  const openInScreener = (): void => {
    const total = payload.total_matched ?? rows.length;
    const conds = (payload.applied_filters ?? []).map((f) => filterLabel(f, labels, units));
    // A scan hands over every name that passed; a fundamentals screen
    // hands over the rows it returned.
    const symbols = payload.symbols?.length ? payload.symbols : rows.map((r) => r.symbol);
    if (total > symbols.length) {
      conds.push(`top ${symbols.length} of ${total.toLocaleString("en-IN")} matches`);
    }
    putPendingScreen({
      symbols,
      criteria: conds.join(" · "),
      as_of: payload.as_of || new Date().toISOString().slice(0, 10),
      matched: symbols.length,
      universe: 0,
    });
    if (pathname === "/") window.location.hash = "#screener";
    else router.push("/#screener");
  };

  return (
    <div
      className="w-full overflow-hidden rounded-xl border border-border bg-card"
      data-testid="screen-results-card"
    >
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 pt-3.5 pb-2">
        <h3 className="text-[15px] font-semibold text-foreground">
          {payload.title || "Screen results"}
        </h3>
        <Button size="sm" variant="outline" onClick={openInScreener}>
          <ListFilter className="h-4 w-4" aria-hidden />
          Open in screener
        </Button>
      </div>
      {!!payload.applied_filters?.length && (
        <div className="flex flex-wrap gap-1.5 px-4 pb-3">
          {payload.applied_filters.map((f, i) => (
            <span
              key={i}
              className="rounded-md bg-muted px-2 py-0.5 text-[11.5px] text-muted-foreground"
            >
              {filterLabel(f, labels, units)}
            </span>
          ))}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-y border-border/60 text-[11.5px] text-muted-foreground">
              <th className="w-10 px-3 py-2 text-right font-medium">#</th>
              <th className="px-3 py-2 text-left font-medium">Company</th>
              {cols.map((c) => (
                <th key={c.key} className="whitespace-nowrap px-3 py-2 text-right font-medium">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr
                key={r.symbol}
                className="border-b border-border/40 last:border-b-0 hover:bg-muted/40"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              >
                <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                  {i + 1}
                </td>
                <td className="px-3 py-2">
                  <div className="relative flex min-w-[180px] items-center gap-2.5">
                    <CompanyLogo
                      logoUrl={logos[r.symbol.toUpperCase()] ?? null}
                      name={r.name || r.symbol}
                      symbol={r.symbol}
                      size={28}
                    />
                    <button
                      type="button"
                      onClick={() => router.push(`/stock/${encodeURIComponent(r.symbol)}`)}
                      className="min-w-0 text-left"
                      style={{ visibility: hover === i ? "hidden" : "visible" }}
                      title={`Open ${r.name || r.symbol}`}
                    >
                      <div className="truncate font-semibold text-foreground">
                        {r.name || r.symbol}
                      </div>
                      <div className="text-[11.5px] text-muted-foreground">{r.symbol}</div>
                    </button>
                    {hover === i && (
                      <StockHoverActions
                        symbol={r.symbol}
                        name={r.name || r.symbol}
                        className="absolute"
                        style={{ left: 38, top: "50%", marginTop: -14, padding: 2, zIndex: 5 }}
                      />
                    )}
                  </div>
                </td>
                {cols.map((c) => (
                  <td
                    key={c.key}
                    className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-foreground"
                    style={{ color: tone(r[c.key], c.unit) }}
                  >
                    {fmt(r[c.key], c.unit)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          {medians.some((m) => m !== null) && (
            <tfoot>
              <tr
                className="border-t border-border/60 bg-muted/30 text-[12.5px] text-muted-foreground"
                data-testid="screen-median-row"
              >
                <td className="px-3 py-2" />
                <td className="px-3 py-2 font-medium">Median of {rows.length}</td>
                {cols.map((c, i) => (
                  <td key={c.key} className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                    {medians[i] === null ? "" : fmt(medians[i], c.unit)}
                  </td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      {rows.length > PAGE && (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="flex w-full items-center justify-center gap-1.5 border-t border-border/60 py-2.5 text-[12.5px] font-medium text-muted-foreground hover:bg-muted/40 hover:text-foreground"
        >
          {expanded ? "Show fewer" : `Show all ${rows.length}`}
          <ChevronDown
            size={14}
            className={expanded ? "rotate-180 transition-transform" : "transition-transform"}
          />
        </button>
      )}
    </div>
  );
}
