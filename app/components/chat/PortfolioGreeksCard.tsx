"use client";

/**
 * PortfolioGreeksCard — chat widget for the "portfolio_greeks_card" render hint.
 *
 * Layout:
 *  - Header badge + position count.
 *  - 4 stat tiles: Net Δ, Θ ₹/day, Vega ₹/pt, FutEq ₹ notional.
 *  - Per-underlying table: underlying, positions, Δ, Θ, Vega, FutEq ₹.
 *  - Per-expiry strip.
 *  - Amber warning when unmarked symbols exist.
 *  - Empty state when position_count === 0 (renders `note`).
 *  - Footer small-print for `basis`.
 *
 * Units:
 *  delta   = units of underlying (signed)
 *  theta   = ₹/day (positive = earning decay)
 *  vega    = ₹ per vol point
 *  delta_notional = ₹ FutEq exposure
 *
 * Style: matches OptionChainCard (rounded-3xl card, shadcn tokens).
 */

import { cn } from "@/lib/utils";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import type { GreeksBucket, PortfolioGreeksPayload } from "@/lib/types";

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

/** Compact INR: ₹1.23Cr / ₹4.56L / ₹7.8K / ₹123 */
function fmtInrCompact(n: number): string {
  const MINUS = "−";
  const a = Math.abs(n);
  const sign = n < 0 ? MINUS : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)}Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)}L`;
  if (a >= 1e3) return `${sign}₹${(a / 1e3).toFixed(1)}K`;
  return `${sign}₹${a.toFixed(0)}`;
}

/** Signed float with 2dp and explicit +/- */
function fmtSigned(n: number, dp = 2): string {
  const MINUS = "−";
  const sign = n >= 0 ? "+" : MINUS;
  return `${sign}${Math.abs(n).toFixed(dp)}`;
}

/** Color for a greek value: green if positive, red if negative, muted if zero. */
function greekColor(n: number, posIsGood = false): string {
  if (n === 0) return "text-muted-foreground";
  if (posIsGood) return n > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400";
  return n > 0 ? "text-foreground" : "text-foreground";
}

// ---------------------------------------------------------------------------
// Stat tile
// ---------------------------------------------------------------------------

function StatTile({
  label,
  value,
  unit,
  colorClass,
}: {
  label: string;
  value: string;
  unit: string;
  colorClass?: string;
}): React.ReactElement {
  return (
    <div
      className="flex flex-col gap-0.5 rounded-xl bg-muted/30 px-3 py-2.5 min-w-0"
    >
      <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
        {label}
      </span>
      <span
        className={cn(
          "text-[15px] font-semibold tabular-nums leading-snug truncate",
          colorClass ?? "text-foreground",
        )}
      >
        {value}
      </span>
      <span className="text-[9.5px] text-muted-foreground/60">{unit}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-underlying table
// ---------------------------------------------------------------------------

type UnderlyingRow = {
  symbol: string;
} & GreeksBucket & {
  delta_notional: number;
  positions: number;
};

function UnderlyingTable({
  data,
}: {
  data: UnderlyingRow[];
}): React.ReactElement {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[420px] border-collapse text-[11px]">
        <thead>
          <tr className="border-b border-border/30 bg-muted/20">
            <th className="px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
              Underlying
            </th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
              Pos
            </th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
              Δ
            </th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
              Θ ₹/d
            </th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
              Vega ₹/pt
            </th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
              FutEq ₹
            </th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr
              key={row.symbol}
              className="border-b border-border/20 last:border-b-0 hover:bg-muted/20 transition-colors"
            >
              <td className="px-3 py-2 font-medium text-foreground text-[11.5px]">
                {row.symbol}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                {row.positions}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-foreground">
                {fmtSigned(row.delta)}
              </td>
              <td
                className={cn(
                  "px-3 py-2 text-right tabular-nums",
                  greekColor(row.theta, true),
                )}
              >
                {fmtSigned(row.theta, 0)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-foreground">
                {fmtSigned(row.vega, 0)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                {fmtInrCompact(row.delta_notional)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-expiry strip
// ---------------------------------------------------------------------------

type ExpiryEntry = {
  expiry: string;
  positions: number;
} & GreeksBucket;

function ExpiryStrip({ entries }: { entries: ExpiryEntry[] }): React.ReactElement {
  return (
    <div className="flex flex-wrap gap-2 px-4 py-2.5">
      {entries.map((e) => (
        <div
          key={e.expiry}
          className="flex items-center gap-2 rounded-lg bg-muted/40 px-3 py-1.5 text-[10.5px]"
        >
          <span className="font-medium text-foreground">{e.expiry}</span>
          <span className="text-muted-foreground/60">{e.positions}p</span>
          <span className="tabular-nums text-foreground/80">
            Δ{fmtSigned(e.delta, 1)}
          </span>
          <span
            className={cn(
              "tabular-nums",
              greekColor(e.theta, true),
            )}
          >
            Θ{fmtSigned(e.theta, 0)}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main card
// ---------------------------------------------------------------------------

export type PortfolioGreeksCardProps = {
  payload: PortfolioGreeksPayload;
};

export function PortfolioGreeksCard({
  payload,
}: PortfolioGreeksCardProps): React.ReactElement {
  // Empty state — no positions
  if (payload.position_count === 0) {
    return (
      <div
        data-testid="portfolio-greeks-card"
        className={cn(
          "my-2 w-full max-w-[560px] overflow-hidden rounded-3xl border border-border/50 bg-card",
          "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
        )}
      >
        <div className="px-5 pt-4 pb-3">
          <span className="inline-flex items-center rounded-md bg-sky-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
            Portfolio Greeks
          </span>
        </div>
        <div className="flex flex-col items-center justify-center gap-2 px-5 py-10 text-center">
          <p className="text-sm text-muted-foreground">
            {payload.note ?? "No F&O positions with Greek data."}
          </p>
        </div>
        {payload.basis && (
          <div className="flex items-start gap-1.5 border-t border-border/40 bg-muted/20 px-5 py-2">
            <ShieldAlert className="mt-px h-3 w-3 shrink-0 text-muted-foreground/50" aria-hidden />
            <p className="text-[9.5px] text-muted-foreground/60">{payload.basis}</p>
          </div>
        )}
      </div>
    );
  }

  // Build sorted rows for the underlying table
  const underlyingRows: UnderlyingRow[] = Object.entries(payload.by_underlying)
    .map(([symbol, v]) => ({ symbol, ...v }))
    .sort((a, b) => Math.abs(b.delta_notional) - Math.abs(a.delta_notional));

  // Build sorted entries for the expiry strip
  const expiryEntries: ExpiryEntry[] = Object.entries(payload.by_expiry)
    .map(([expiry, v]) => ({ expiry, ...v }))
    .sort((a, b) => a.expiry.localeCompare(b.expiry));

  return (
    <div
      data-testid="portfolio-greeks-card"
      role="region"
      aria-label="Portfolio Greeks"
      className={cn(
        "my-2 w-full max-w-[560px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
      )}
    >
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-5 pt-4 pb-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="inline-flex items-center rounded-md bg-sky-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
            Portfolio Greeks
          </span>
          <span className="text-[10.5px] text-muted-foreground">
            {payload.position_count} position{payload.position_count !== 1 ? "s" : ""}
          </span>
        </div>
      </div>

      {/* 4-tile grid */}
      <div className="grid grid-cols-2 gap-2 px-4 pb-3 sm:grid-cols-4">
        <StatTile
          label="Net Delta"
          value={fmtSigned(payload.net.delta)}
          unit="units of underlying"
        />
        <StatTile
          label="Theta"
          value={fmtSigned(payload.net.theta, 0)}
          unit="₹/day"
          colorClass={
            payload.net.theta > 0
              ? "text-emerald-600 dark:text-emerald-400"
              : payload.net.theta < 0
                ? "text-rose-600 dark:text-rose-400"
                : undefined
          }
        />
        <StatTile
          label="Vega"
          value={fmtSigned(payload.net.vega, 0)}
          unit="₹ per vol pt"
        />
        <StatTile
          label="FutEq Notional"
          value={fmtInrCompact(payload.delta_notional)}
          unit="₹ FutEq"
          colorClass={
            payload.delta_notional !== 0
              ? (payload.delta_notional > 0
                ? "text-foreground"
                : "text-foreground")
              : undefined
          }
        />
      </div>

      {/* Per-underlying table */}
      {underlyingRows.length > 0 && (
        <div className="border-t border-border/40">
          <UnderlyingTable data={underlyingRows} />
        </div>
      )}

      {/* Per-expiry strip */}
      {expiryEntries.length > 0 && (
        <div className="border-t border-border/30">
          <ExpiryStrip entries={expiryEntries} />
        </div>
      )}

      {/* Unmarked warning */}
      {payload.unmarked.length > 0 && (
        <div className="mx-4 mb-3 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>
            Greeks unavailable for:{" "}
            {payload.unmarked.join(", ")}
          </span>
        </div>
      )}

      {/* Footer */}
      {payload.basis && (
        <div className="flex items-start gap-1.5 border-t border-border/40 bg-muted/20 px-5 py-2">
          <ShieldAlert
            className="mt-px h-3 w-3 shrink-0 text-muted-foreground/50"
            aria-hidden
          />
          <p className="text-[9.5px] leading-snug text-muted-foreground/60">
            {payload.basis}
          </p>
        </div>
      )}
    </div>
  );
}
