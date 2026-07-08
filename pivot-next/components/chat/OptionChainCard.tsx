"use client";

/**
 * OptionChainCard — renders OptionChainPayload as an ATM-centered option
 * chain table. CE on left, strike in the middle, PE on right. Greeks toggle
 * (default off). Expiry selector. Expected move band. Source badge.
 *
 * Structural precedent: IpoApplicationCard.tsx.
 */

import { useState } from "react";
import { AlertCircle, ChevronDown, Info, ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  IvStatus,
  OptionChainPayload,
  OptionChainRow,
  OptionSideQuote,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type OptionChainCardProps = {
  payload: OptionChainPayload;
  /** Called when user selects a different expiry. Parent may ignore. */
  onExpiryChange?: (expiry: string) => void;
};

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function fmtInr(n: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(n);
}

function fmtOi(oi: number): string {
  if (oi >= 1_00_00_000) return `${(oi / 1_00_00_000).toFixed(1)}Cr`;
  if (oi >= 1_00_000) return `${(oi / 1_00_000).toFixed(1)}L`;
  if (oi >= 1_000) return `${(oi / 1_000).toFixed(1)}K`;
  return String(oi);
}

function fmtExpiry(expiry: string): string {
  try {
    const d = new Date(expiry + "T00:00:00");
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" });
  } catch {
    return expiry;
  }
}

function fmtAsof(asof: string): string {
  try {
    const d = new Date(asof);
    return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return asof;
  }
}

// ---------------------------------------------------------------------------
// IV status helpers
// ---------------------------------------------------------------------------

const IV_STATUS_LABELS: Record<IvStatus, string> = {
  ok: "IV computed",
  no_arb: "No-arb bound only",
  no_solution: "No IV solution",
  wide_spread: "Spread too wide for IV",
  illiquid: "Illiquid — IV unreliable",
  stale: "Stale quote",
};

// ---------------------------------------------------------------------------
// Side quote cell
// ---------------------------------------------------------------------------

function SideCell({
  quote,
  showGreeks,
  isAtm,
  align,
}: {
  quote: OptionSideQuote | null;
  showGreeks: boolean;
  isAtm: boolean;
  align: "left" | "right";
}): React.ReactElement {
  if (!quote) {
    return (
      <td
        className={cn(
          "px-2 py-1.5 text-[11px] text-muted-foreground/40",
          align === "right" ? "text-right" : "text-left",
          isAtm && "bg-amber-50/40 dark:bg-amber-500/[0.04]",
        )}
      >
        —
      </td>
    );
  }

  const ivOk = quote.iv_status === "ok";
  const ivText = quote.iv !== null ? `${quote.iv.toFixed(1)}%` : "—";

  return (
    <td
      className={cn(
        "px-2 py-1.5 align-top",
        isAtm && "bg-amber-50/40 dark:bg-amber-500/[0.04]",
      )}
    >
      <div
        className={cn(
          "flex flex-col gap-0.5",
          align === "right" ? "items-end" : "items-start",
        )}
      >
        {/* LTP / mid */}
        <span className="text-[11.5px] font-medium tabular-nums text-foreground">
          {fmtInr(quote.mid)}
        </span>
        {/* OI */}
        <span className="text-[10px] tabular-nums text-muted-foreground">
          OI {fmtOi(quote.oi)}
        </span>
        {/* IV */}
        <span
          className={cn(
            "text-[10px] tabular-nums",
            ivOk ? "text-sky-600 dark:text-sky-400" : "text-muted-foreground/40",
          )}
          title={IV_STATUS_LABELS[quote.iv_status]}
        >
          {ivOk ? ivText : <span title={IV_STATUS_LABELS[quote.iv_status]}>{ivText}</span>}
        </span>
        {/* Greeks */}
        {showGreeks && (
          <div
            className={cn(
              "mt-0.5 flex gap-2 text-[9.5px] tabular-nums text-muted-foreground/70",
              align === "right" ? "flex-row-reverse" : "flex-row",
            )}
          >
            <span>δ {quote.delta !== null ? quote.delta.toFixed(2) : "—"}</span>
            <span>θ {quote.theta !== null ? quote.theta.toFixed(2) : "—"}</span>
            <span>ν {quote.vega !== null ? quote.vega.toFixed(2) : "—"}</span>
          </div>
        )}
      </div>
    </td>
  );
}

// ---------------------------------------------------------------------------
// Strike cell
// ---------------------------------------------------------------------------

function StrikeCell({
  strike,
  isAtm,
}: {
  strike: number;
  isAtm: boolean;
}): React.ReactElement {
  return (
    <td
      className={cn(
        "px-2 py-1.5 text-center text-[11px] font-medium tabular-nums",
        isAtm
          ? "bg-amber-100/60 text-amber-800 font-semibold dark:bg-amber-500/15 dark:text-amber-300"
          : "text-muted-foreground",
      )}
    >
      {strike.toLocaleString("en-IN")}
      {isAtm && (
        <span className="ml-1 text-[8.5px] font-semibold uppercase tracking-widest text-amber-600 dark:text-amber-400">
          ATM
        </span>
      )}
    </td>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function ChainRow({
  row,
  atmStrike,
  showGreeks,
}: {
  row: OptionChainRow;
  atmStrike: number;
  showGreeks: boolean;
}): React.ReactElement {
  const isAtm = row.strike === atmStrike;
  return (
    <tr
      className={cn(
        "border-b border-border/30 last:border-b-0",
        isAtm && "ring-1 ring-inset ring-amber-300/50 dark:ring-amber-500/30",
      )}
    >
      {/* CE — left side */}
      <SideCell quote={row.ce} showGreeks={showGreeks} isAtm={isAtm} align="right" />
      {/* Strike — centre */}
      <StrikeCell strike={row.strike} isAtm={isAtm} />
      {/* PE — right side */}
      <SideCell quote={row.pe} showGreeks={showGreeks} isAtm={isAtm} align="left" />
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main card
// ---------------------------------------------------------------------------

export function OptionChainCard({
  payload,
  onExpiryChange,
}: OptionChainCardProps): React.ReactElement {
  const [showGreeks, setShowGreeks] = useState(false);
  const [selectedExpiry, setSelectedExpiry] = useState(payload.expiry);

  function handleExpiryChange(e: React.ChangeEvent<HTMLSelectElement>): void {
    setSelectedExpiry(e.target.value);
    onExpiryChange?.(e.target.value);
  }

  const forwardSourceLabel: Record<OptionChainPayload["forward_source"], string> = {
    future: "Futures",
    synthetic: "Synthetic",
    spot: "Spot",
    strike_median: "Strike median",
  };

  return (
    <div
      data-testid="option-chain-card"
      role="region"
      aria-label={`Option chain: ${payload.underlying}`}
      className={cn(
        "my-2 w-full max-w-[560px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
      )}
    >
      {/* Header */}
      <div className="flex flex-col gap-2 px-5 pt-4 pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center rounded-md bg-sky-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
              Option Chain
            </span>
            <span className="text-[10.5px] font-medium text-muted-foreground uppercase tracking-widest">
              {payload.exchange} · {payload.segment}
            </span>
            {payload.research_only && (
              <span className="inline-flex items-center gap-1 rounded-md bg-amber-100 px-2 py-0.5 text-[10.5px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
                <AlertCircle className="h-3 w-3 shrink-0" aria-hidden="true" />
                MCX — research only, no execution
              </span>
            )}
            {payload.source === "mock" && (
              <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500 dark:bg-slate-500/15 dark:text-slate-400">
                mock
              </span>
            )}
          </div>
          {/* Expiry selector */}
          <div className="relative flex items-center">
            <select
              value={selectedExpiry}
              onChange={handleExpiryChange}
              aria-label="Select expiry"
              className={cn(
                "appearance-none rounded-lg border border-border/60 bg-background pl-2.5 pr-6 py-1 text-[11.5px] text-foreground",
                "focus:outline-none focus:ring-1 focus:ring-ring",
              )}
            >
              {payload.expiries.map((e) => (
                <option key={e.expiry} value={e.expiry}>
                  {fmtExpiry(e.expiry)} ({e.kind})
                </option>
              ))}
            </select>
            <ChevronDown
              className="pointer-events-none absolute right-1.5 h-3 w-3 text-muted-foreground"
              aria-hidden="true"
            />
          </div>
        </div>

        {/* Underlying + forward */}
        <div className="flex flex-wrap items-baseline gap-3">
          <h3 className="text-[17px] font-semibold tracking-tight text-foreground">
            {payload.underlying}
          </h3>
          <span className="text-[12px] tabular-nums text-muted-foreground">
            Fwd {fmtInr(payload.forward)}
            <span className="ml-1.5 text-[10px] text-muted-foreground/60">
              ({forwardSourceLabel[payload.forward_source]})
            </span>
          </span>
          {payload.spot !== null && (
            <span className="text-[11.5px] tabular-nums text-muted-foreground/70">
              Spot {fmtInr(payload.spot)}
            </span>
          )}
        </div>

        {/* Info row: lot size + expected move + max pain + PCR */}
        <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
          {payload.lot_size !== null && (
            <span>Lot {payload.lot_size}</span>
          )}
          {payload.expected_move !== null && (
            <span className="inline-flex items-center gap-1 rounded-md bg-muted/50 px-2 py-0.5">
              <Info className="h-3 w-3 shrink-0 text-muted-foreground/60" aria-hidden="true" />
              Expected move ±{payload.expected_move.abs.toLocaleString("en-IN")}
              {" "}({payload.expected_move.pct.toFixed(1)}%)
            </span>
          )}
          {payload.max_pain != null && (
            <span className="inline-flex items-center gap-1 rounded-md bg-muted/50 px-2 py-0.5">
              Max pain {payload.max_pain.toLocaleString("en-IN")}
            </span>
          )}
          {payload.pcr_oi != null && (
            <span className="inline-flex items-center gap-1 rounded-md bg-muted/50 px-2 py-0.5">
              PCR(OI) {payload.pcr_oi.toFixed(2)}
            </span>
          )}
        </div>
      </div>

      {/* Greeks toggle */}
      <div className="flex items-center justify-between border-b border-border/40 px-5 py-1.5">
        <div className="flex gap-4 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <span className="text-[11px] font-semibold text-foreground">CE</span>
          <span className="mx-auto text-[10px] text-muted-foreground/70">Strike</span>
          <span className="text-[11px] font-semibold text-foreground">PE</span>
        </div>
        <button
          type="button"
          onClick={() => setShowGreeks((v) => !v)}
          className={cn(
            "rounded-md px-2.5 py-1 text-[10.5px] font-medium border transition-colors",
            showGreeks
              ? "border-primary/50 bg-primary/10 text-primary"
              : "border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
          aria-pressed={showGreeks}
        >
          Greeks {showGreeks ? "on" : "off"}
        </button>
      </div>

      {/* Column headers */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[360px] border-collapse text-[11px]">
          <thead>
            <tr className="border-b border-border/30 bg-muted/30">
              <th className="px-2 py-1.5 text-right text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70 w-[38%]">
                Mid · OI · IV
                {showGreeks && <span className="block text-[9px] normal-case font-normal">δ θ ν</span>}
              </th>
              <th className="px-2 py-1.5 text-center text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70 w-[24%]">
                Strike
              </th>
              <th className="px-2 py-1.5 text-left text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70 w-[38%]">
                Mid · OI · IV
                {showGreeks && <span className="block text-[9px] normal-case font-normal">δ θ ν</span>}
              </th>
            </tr>
          </thead>
          <tbody>
            {payload.rows.length === 0 ? (
              <tr>
                <td colSpan={3} className="px-4 py-6 text-center text-[11.5px] text-muted-foreground">
                  No strikes available for this expiry.
                </td>
              </tr>
            ) : (
              payload.rows.map((row) => (
                <ChainRow
                  key={row.strike}
                  row={row}
                  atmStrike={payload.atm_strike}
                  showGreeks={showGreeks}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="flex items-start gap-1.5 border-t border-border/40 bg-muted/20 px-5 py-2">
        <ShieldAlert
          className="mt-px h-3 w-3 shrink-0 text-muted-foreground/50"
          aria-hidden="true"
        />
        <div className="min-w-0">
          <p className="text-[10px] leading-snug text-muted-foreground/70">
            {payload.disclosure}
          </p>
          <p className="mt-0.5 text-[9.5px] tabular-nums text-muted-foreground/50">
            as of {fmtAsof(payload.asof)} · source: {payload.source}
          </p>
        </div>
      </div>
    </div>
  );
}
