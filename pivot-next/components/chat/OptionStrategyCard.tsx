"use client";

/**
 * OptionStrategyCard — compact F&O strategy widget (chat render hint
 * "option_strategy_card"). Mirrors the slim shape of the backtest / workflow
 * draft cards: it shows the *basic* read of the strategy and hands the full
 * interactive build off to a right-side sidebar (OptionStrategyPanel) where
 * the user can add/remove legs, change strikes/expiry/lots and watch the
 * payoff/Greeks/P&L recompute live, then register.
 *
 * Sections:
 *  - Header: F&O chip, exchange·segment, risk verdict chip
 *  - Title: template + underlying · expiry
 *  - Mini payoff sparkline (zero line + breakevens + forward)
 *  - Decision quad: max loss / max profit / POP / net premium
 *  - Legs summary line + "Open builder →" CTA
 *  - Disclosure footer
 *
 * Shared helpers (fmtInr, fmtExpiry, humanizeTemplate, RiskChip, PayoffChart)
 * are exported for OptionStrategyPanel to reuse.
 */

import { useState } from "react";
import { AlertCircle, ArrowUpRight, ShieldAlert, SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import type { OptionStrategyPayload } from "@/lib/types";
import { OptionStrategyPanel } from "@/components/chat/OptionStrategyPanel";
import { PayoffChart } from "@/components/chat/option-payoff-chart";

export { PayoffChart };

// ---------------------------------------------------------------------------
// Shared formatters / helpers (exported)
// ---------------------------------------------------------------------------

export function fmtInr(n: number, maximumFractionDigits = 0): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits,
  }).format(n);
}

/** Compact INR (₹3.9K / ₹1.2L) for tight stat cells. */
export function fmtInrCompact(n: number): string {
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `${sign}₹${(abs / 1e3).toFixed(1)}K`;
  return `${sign}₹${abs.toFixed(0)}`;
}

export function fmtExpiry(expiry: string): string {
  try {
    const d = new Date(expiry + "T00:00:00");
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" });
  } catch {
    return expiry;
  }
}

/** "bull_call_spread" → "Bull Call Spread" */
export function humanizeTemplate(t: string): string {
  return t
    .split("_")
    .map((w) => (w.length > 0 ? w[0]!.toUpperCase() + w.slice(1) : w))
    .join(" ");
}

export function RiskChip({
  verdict,
}: {
  verdict: "ok" | "caution" | "risky";
}): React.ReactElement {
  const map = {
    ok: { label: "Balanced", cls: "text-emerald-600 dark:text-emerald-300" },
    caution: { label: "Caution", cls: "text-amber-600 dark:text-amber-300" },
    risky: { label: "Risky", cls: "text-rose-600 dark:text-rose-300" },
  };
  const { label, cls } = map[verdict];
  return (
    <span className={cn("inline-flex items-center text-[10.5px] font-semibold", cls)}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Compact decision stat
// ---------------------------------------------------------------------------

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "loss" | "profit" | "neutral";
}): React.ReactElement {
  return (
    <div className="min-w-0 text-center">
      <p className="text-[9.5px] uppercase tracking-wider text-muted-foreground/70">{label}</p>
      <p
        className={cn(
          "mt-0.5 truncate text-[13.5px] font-semibold tabular-nums",
          tone === "loss" && "text-rose-600 dark:text-rose-400",
          tone === "profit" && "text-emerald-600 dark:text-emerald-400",
          (!tone || tone === "neutral") && "text-foreground",
        )}
      >
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

export type OptionStrategyCardProps = {
  payload: OptionStrategyPayload;
  onSelectCandidate?: (template: string) => void;
};

export function OptionStrategyCard({ payload }: OptionStrategyCardProps): React.ReactElement {
  const { locked, computed, editable, critique } = payload;
  const [panelOpen, setPanelOpen] = useState(false);

  const legCount = editable.legs.length;

  return (
    <>
      <div
        data-testid="option-strategy-card"
        role="region"
        aria-label={`Option strategy: ${humanizeTemplate(editable.template)} on ${locked.underlying}`}
        className={cn(
          "my-2 w-full max-w-[388px] overflow-hidden rounded-3xl border border-border/50 bg-card",
          "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
        )}
      >
        {/* Header */}
        <div className="flex flex-col gap-2 px-5 pt-4 pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="inline-flex items-center rounded-md bg-orange-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-orange-700 dark:bg-orange-500/15 dark:text-orange-300">
                F&amp;O Strategy
              </span>
              <span className="text-[10.5px] font-medium uppercase tracking-widest text-muted-foreground">
                {locked.exchange} · {locked.segment}
              </span>
            </div>
            <RiskChip verdict={critique.verdict} />
          </div>
          <div className="flex flex-wrap items-baseline gap-2">
            <h3 className="text-[16px] font-semibold tracking-tight text-foreground">
              {humanizeTemplate(editable.template)}
            </h3>
            <span className="text-[12px] text-muted-foreground">
              {locked.underlying} · {fmtExpiry(locked.expiry)} ({locked.expiry_kind})
            </span>
          </div>
          {locked.research_only && (
            <span className="inline-flex items-center gap-1 self-start rounded-md bg-amber-100 px-2 py-0.5 text-[10.5px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
              <AlertCircle className="h-3 w-3 shrink-0" aria-hidden="true" />
              MCX — research only, no execution
            </span>
          )}
        </div>

        {/* Mini payoff */}
        <div className="border-t border-border/30 px-3 py-2">
          <PayoffChart
            data={computed.payoff}
            now={computed.payoff_now}
            breakevens={computed.breakevens}
            forward={locked.forward}
            height={92}
            compact
          />
        </div>

        {/* Decision quad */}
        <div className="grid grid-cols-4 gap-2 border-t border-border/30 px-5 py-3">
          <Stat
            label="Max loss"
            tone="loss"
            value={computed.max_loss === null ? "Unlimited" : fmtInrCompact(computed.max_loss)}
          />
          <Stat
            label="Max profit"
            tone="profit"
            value={computed.max_profit === null ? "Unlimited" : fmtInrCompact(computed.max_profit)}
          />
          <Stat label="POP" value={computed.pop === null ? "—" : `${(computed.pop * 100).toFixed(0)}%`} />
          <Stat
            label={computed.net_premium <= 0 ? "Net debit" : "Net credit"}
            value={fmtInrCompact(Math.abs(computed.net_premium))}
          />
        </div>

        {/* Legs summary + CTA */}
        <div className="flex flex-col gap-2.5 border-t border-border/30 px-5 py-3">
          <p className="text-[11px] text-muted-foreground">
            {legCount} {legCount === 1 ? "leg" : "legs"} · {editable.qty_lots}{" "}
            {editable.qty_lots === 1 ? "lot" : "lots"} ·{" "}
            {editable.legs
              .map((l) => `${l.side === "BUY" ? "+" : "-"}${l.strike}${l.option_type}`)
              .join("  ")}
          </p>
          <button
            type="button"
            onClick={() => setPanelOpen(true)}
            data-testid="option-strategy-open-builder"
            className={cn(
              "inline-flex h-8 w-full items-center justify-center gap-2 rounded-full bg-primary text-[12px] font-medium tracking-tight text-primary-foreground transition-all",
              "hover:bg-primary/90 active:scale-[0.98]",
            )}
          >
            <SlidersHorizontal className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>Open builder</span>
            <ArrowUpRight className="h-4 w-4 shrink-0" aria-hidden="true" />
          </button>
        </div>

        {/* Disclosure footer */}
        <div className="flex items-start gap-1.5 border-t border-border/40 bg-amber-50/40 px-5 py-2 dark:bg-amber-500/[0.04]">
          <ShieldAlert
            className="mt-px h-3 w-3 shrink-0 text-amber-600/80 dark:text-amber-400/80"
            aria-hidden="true"
          />
          <p className="text-[10.5px] leading-snug text-amber-700/90 dark:text-amber-300/90">
            {locked.disclosure}
          </p>
        </div>
      </div>

      <OptionStrategyPanel open={panelOpen} onOpenChange={setPanelOpen} payload={payload} />
    </>
  );
}
