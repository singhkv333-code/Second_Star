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

import { useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowUpRight, Loader2, Minus, Plus, ShieldAlert, SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { useExclusiveSidePanel } from "@/lib/sidePanels";
import type { OptionStrategyPayload } from "@/lib/types";
import { isError } from "@/lib/types";
import { computeOptionStrategy } from "@/lib/api";
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
  const [panelOpen, setPanelOpen] = useState(false);
  useExclusiveSidePanel("option-strategy", panelOpen, () => setPanelOpen(false));

  // Live, editable payload. The inline card lets the user change the lot count
  // directly (the most common edit); strikes/legs/expiry still hand off to the
  // full builder panel. Recompute hits the same /option-strategies/compute
  // endpoint the panel uses, so the displayed payoff/stats stay truthful.
  const [current, setCurrent] = useState<OptionStrategyPayload>(payload);
  const [qtyLots, setQtyLots] = useState<number>(payload.editable.qty_lots);
  const [computing, setComputing] = useState(false);
  const [computeError, setComputeError] = useState<string | null>(null);
  const baselineLots = useRef<number>(payload.editable.qty_lots);

  // Keep local state in sync if a new payload streams in (e.g. chat amendment).
  useEffect(() => {
    setCurrent(payload);
    setQtyLots(payload.editable.qty_lots);
    baselineLots.current = payload.editable.qty_lots;
  }, [payload]);

  // Debounced live recompute when the lot count changes. Legs/strikes are kept
  // from the current payload (only qty_lots changes here), so no chain fetch is
  // needed — the backend re-evaluates payoff/Greeks/premium for the new size.
  useEffect(() => {
    if (qtyLots === baselineLots.current) return;
    if (!Number.isInteger(qtyLots) || qtyLots < 1) return;
    let cancelled = false;
    setComputing(true);
    const t = setTimeout(async () => {
      const res = await computeOptionStrategy({
        underlying: current.locked.underlying,
        expiry: current.locked.expiry,
        template: current.editable.template,
        qty_lots: qtyLots,
        legs: current.editable.legs.map((l) => ({
          option_type: l.option_type,
          side: l.side,
          strike: l.strike,
        })),
      });
      if (cancelled) return;
      setComputing(false);
      if (isError(res)) {
        setComputeError(res.error.message ?? "Recompute failed — try again.");
        return;
      }
      if (!res.data.success || !res.data.payload) {
        setComputeError(res.data.error ?? "Couldn't recompute for this lot size.");
        return;
      }
      setComputeError(null);
      baselineLots.current = qtyLots;
      setCurrent(res.data.payload);
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [qtyLots, current.locked.underlying, current.locked.expiry, current.editable.template, current.editable.legs]);

  const { locked, computed, editable, critique } = current;
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

        {/* Legs summary + inline lots editor + CTA */}
        <div className="flex flex-col gap-2.5 border-t border-border/30 px-5 py-3">
          <div className="flex items-center justify-between gap-2">
            <p className="min-w-0 truncate text-[11px] text-muted-foreground">
              {legCount} {legCount === 1 ? "leg" : "legs"} ·{" "}
              {editable.legs
                .map((l) => `${l.side === "BUY" ? "+" : "-"}${l.strike}${l.option_type}`)
                .join("  ")}
            </p>
            {/* Lots stepper — edits the whole-strategy size; recomputes live. */}
            <div className="flex shrink-0 items-center gap-1">
              <span className="mr-0.5 text-[10px] uppercase tracking-wider text-muted-foreground/70">
                Lots
              </span>
              <button
                type="button"
                aria-label="Decrease lots"
                disabled={qtyLots <= 1}
                onClick={() => setQtyLots((n) => Math.max(1, n - 1))}
                className={cn(
                  "inline-flex h-6 w-6 items-center justify-center rounded-full border border-border/60 text-foreground/70",
                  "transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40",
                )}
              >
                <Minus className="h-3 w-3" aria-hidden="true" />
              </button>
              <input
                type="number"
                min={1}
                inputMode="numeric"
                aria-label="Number of lots"
                data-testid="option-strategy-lots-input"
                value={qtyLots}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  if (!Number.isNaN(v)) setQtyLots(Math.max(1, v));
                }}
                className="h-6 w-10 rounded-md border border-border/60 bg-background text-center text-[12px] font-semibold tabular-nums text-foreground [appearance:textfield] focus:outline-none focus:ring-1 focus:ring-primary/40 [&::-webkit-inner-spin-button]:appearance-none"
              />
              <button
                type="button"
                aria-label="Increase lots"
                onClick={() => setQtyLots((n) => n + 1)}
                className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-border/60 text-foreground/70 transition-colors hover:bg-muted"
              >
                <Plus className="h-3 w-3" aria-hidden="true" />
              </button>
            </div>
          </div>

          {/* Recompute status — honest "computing" + error, never a stale number silently */}
          {(computing || computeError) && (
            <p
              className={cn(
                "flex items-center gap-1 text-[10.5px]",
                computeError ? "text-rose-600 dark:text-rose-400" : "text-muted-foreground",
              )}
            >
              {computing && <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />}
              {computeError ?? "Recomputing payoff for new lot size…"}
            </p>
          )}

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

      {/* Hand the live (lot-edited) payload to the full builder so it opens
          from the same size the user set inline. */}
      <OptionStrategyPanel open={panelOpen} onOpenChange={setPanelOpen} payload={current} />
    </>
  );
}
