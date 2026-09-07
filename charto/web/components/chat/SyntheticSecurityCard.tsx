"use client";

/**
 * SyntheticSecurityCard — inline chat card for `build_product` results.
 *
 * Renders SafeGrow / StormShield / Barbell builds: headline, leg breakdown,
 * payoff table (when present), rebalance triggers (Barbell), and a
 * "Confirm & activate" CTA.
 */

import { useState } from "react";
import { Check, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types — shape mirrors agents/structured_builder.py builders.
// ---------------------------------------------------------------------------

export type SyntheticLeg = {
  label: string;
  type: string;
  instrument: string;
  instrument_type?: string;
  amount: number;
  units?: number;
  lots?: number;
  weight_pct?: number;
  expected_return?: string;
  buffer_to_liquid_fund?: number;
};

export type PayoffRow = {
  scenario: string;
  nifty_level: number;
  portfolio_value: number;
  return_pct: number;
};

export type RebalanceTriggers = {
  threshold_pct: number;
  rule: string;
  gold_triggers: { up: number; down: number };
  equity_triggers: { up: number; down: number };
};

export type RebalancingCalendar = {
  avg_rebalances_per_year: number | null;
  next_window_estimate: string;
  lookback_years?: number;
  rebalances_observed?: number;
};

export type SyntheticSecurityPayload = {
  product_type: "safegrow" | "stormshield" | "barbell" | string;
  display_name: string;
  capital: number;
  horizon_months?: number;
  arb_yield_pct?: number;
  legs: SyntheticLeg[];
  payoff_table?: PayoffRow[];
  rebalance?: RebalanceTriggers;
  rebalancing_calendar?: RebalancingCalendar;
  tax_note?: string;
  cash_float?: number;
  nifty_reference_level?: number;
  etf_prices?: Record<string, number>;
  explanation?: string;
  risk_warning?: string;
  disclaimer: string;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatINR(amount: number): string {
  const rounded = Math.round(amount);
  const s = String(Math.abs(rounded));
  const last3 = s.slice(-3);
  const rest = s.slice(0, -3);
  const restWithCommas = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  const formatted = rest ? `${restWithCommas},${last3}` : last3;
  return `${rounded < 0 ? "-" : ""}₹${formatted}`;
}

const PRODUCT_BADGE: Record<string, string> = {
  safegrow: "Capital protection",
  stormshield: "Bear hedge",
  barbell: "Gold + equity",
};

const SANS_FONT =
  "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Inter, Roboto, sans-serif";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SyntheticSecurityCard({
  payload,
}: {
  payload: SyntheticSecurityPayload;
}): React.ReactElement {
  const [confirmed, setConfirmed] = useState(false);

  const productKey = (payload.product_type || "").toLowerCase();
  const badgeLabel = PRODUCT_BADGE[productKey] ?? "Synthetic security";

  return (
    <div
      className="w-full max-w-xl overflow-hidden rounded-xl border border-border/60 bg-card shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
      style={{ fontFamily: SANS_FONT }}
      data-testid="synthetic-security-card"
    >
      {/* Header */}
      <div className="px-5 pt-5 pb-4">
        <div className="flex items-center gap-1.5">
          <span className="inline-flex items-center rounded-md bg-foreground/[0.04] px-1.5 py-0.5 text-[10px] font-medium tracking-tight text-muted-foreground">
            {badgeLabel}
          </span>
        </div>
        <h3 className="mt-2 text-[18px] leading-[1.2] font-semibold tracking-[-0.01em] text-foreground">
          {payload.display_name}
        </h3>
        <p className="mt-1 text-[12px] text-muted-foreground tabular-nums">
          {formatINR(payload.capital)}
          {payload.horizon_months ? ` · ${payload.horizon_months}-month horizon` : ""}
          {payload.arb_yield_pct
            ? ` · ${payload.arb_yield_pct.toFixed(2)}% arb yield`
            : ""}
        </p>
      </div>

      {/* Risk warning */}
      {payload.risk_warning && (
        <div className="mx-5 mb-4 flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-50/60 dark:bg-amber-500/[0.06] px-3 py-2 text-[11.5px] text-amber-900 dark:text-amber-300">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden={true} />
          <span className="leading-relaxed">{payload.risk_warning}</span>
        </div>
      )}

      {/* Legs */}
      <div className="border-t border-border/50">
        <div className="px-5 pt-3.5 pb-2 text-[10px] font-medium tracking-[0.06em] uppercase text-muted-foreground/80">
          Allocation
        </div>
        <div className="px-5 pb-2">
          {payload.legs.map((leg, i) => (
            <div
              key={i}
              className={cn(
                "flex items-start justify-between gap-3 py-2.5",
                i !== payload.legs.length - 1 && "border-b border-border/40",
              )}
            >
              <div className="flex flex-col gap-0.5 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-[10.5px] font-medium tracking-tight text-muted-foreground">
                    {leg.label}
                  </span>
                  {leg.weight_pct !== undefined && (
                    <span className="text-[10.5px] font-medium tabular-nums text-foreground/60">
                      {leg.weight_pct.toFixed(1)}%
                    </span>
                  )}
                </div>
                <div className="text-[13px] font-medium text-foreground truncate">
                  {leg.instrument}
                </div>
                {leg.expected_return && (
                  <div className="text-[11px] text-muted-foreground/90">
                    {leg.expected_return}
                  </div>
                )}
              </div>
              <div className="text-right text-[14px] font-semibold tabular-nums text-foreground tracking-tight">
                {formatINR(leg.amount)}
              </div>
            </div>
          ))}
        </div>
        {payload.cash_float !== undefined && payload.cash_float > 0 && (
          <div className="px-5 pb-3 text-[11px] text-muted-foreground tabular-nums">
            Cash float · {formatINR(payload.cash_float)}
          </div>
        )}
      </div>

      {/* Payoff table */}
      {payload.payoff_table && payload.payoff_table.length > 0 && (
        <div className="border-t border-border/50">
          <div className="px-5 pt-3.5 pb-2 text-[10px] font-medium tracking-[0.06em] uppercase text-muted-foreground/80">
            Payoff at maturity
          </div>
          <div className="overflow-hidden">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="border-y border-border/50 bg-muted/20">
                  <th className="px-5 py-2 text-left text-[10px] font-medium tracking-tight text-muted-foreground">
                    Scenario
                  </th>
                  <th className="px-3 py-2 text-right text-[10px] font-medium tracking-tight text-muted-foreground">
                    Nifty
                  </th>
                  <th className="px-3 py-2 text-right text-[10px] font-medium tracking-tight text-muted-foreground">
                    Portfolio
                  </th>
                  <th className="px-5 py-2 text-right text-[10px] font-medium tracking-tight text-muted-foreground">
                    Return
                  </th>
                </tr>
              </thead>
              <tbody>
                {payload.payoff_table.map((row, i) => (
                  <tr
                    key={i}
                    className={cn(
                      i !== (payload.payoff_table?.length ?? 0) - 1 &&
                        "border-b border-border/40",
                    )}
                  >
                    <td className="px-5 py-2.5 text-foreground">{row.scenario}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-foreground">
                      {row.nifty_level.toLocaleString("en-IN")}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-foreground">
                      {formatINR(row.portfolio_value)}
                    </td>
                    <td
                      className={cn(
                        "px-5 py-2.5 text-right tabular-nums font-medium",
                        row.return_pct > 0 && "text-emerald-600 dark:text-emerald-400",
                        row.return_pct < 0 && "text-rose-600 dark:text-rose-400",
                      )}
                    >
                      {row.return_pct > 0 ? "+" : ""}
                      {row.return_pct.toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Rebalance triggers */}
      {payload.rebalance && (
        <div className="border-t border-border/50 px-5 py-4">
          <div className="flex items-baseline justify-between">
            <span className="text-[10px] font-medium tracking-[0.06em] uppercase text-muted-foreground/80">
              Rebalance triggers
            </span>
            <span className="text-[11px] font-medium tabular-nums text-foreground/70">
              {payload.rebalance.threshold_pct.toFixed(0)}% threshold
            </span>
          </div>
          <p className="mt-1.5 text-[11.5px] leading-relaxed text-muted-foreground">
            {payload.rebalance.rule}
          </p>
          <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-[12px] tabular-nums">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">GOLDBEES ↑</span>
              <span className="text-foreground font-medium">
                {formatINR(payload.rebalance.gold_triggers.up)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">NIFTYBEES ↑</span>
              <span className="text-foreground font-medium">
                {formatINR(payload.rebalance.equity_triggers.up)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">GOLDBEES ↓</span>
              <span className="text-foreground font-medium">
                {formatINR(payload.rebalance.gold_triggers.down)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">NIFTYBEES ↓</span>
              <span className="text-foreground font-medium">
                {formatINR(payload.rebalance.equity_triggers.down)}
              </span>
            </div>
          </div>
          {payload.rebalancing_calendar && (
            <p className="mt-3 text-[11px] text-muted-foreground">
              {payload.rebalancing_calendar.avg_rebalances_per_year !== null
                ? `~${payload.rebalancing_calendar.avg_rebalances_per_year.toFixed(2)} rebalances/yr (${
                    payload.rebalancing_calendar.lookback_years ?? "?"
                  }y lookback) · `
                : ""}
              {payload.rebalancing_calendar.next_window_estimate}
            </p>
          )}
        </div>
      )}

      {/* Tax note */}
      {payload.tax_note && (
        <div className="border-t border-border/50 px-5 py-2.5 text-[11.5px] text-muted-foreground">
          {payload.tax_note}
        </div>
      )}

      {/* Explanation */}
      {payload.explanation && (
        <div className="border-t border-border/50 px-5 py-3">
          <p className="text-[11.5px] leading-relaxed text-muted-foreground">
            {payload.explanation}
          </p>
        </div>
      )}

      {/* CTA */}
      <div className="flex items-center justify-between gap-3 border-t border-border/50 bg-muted/[0.25] px-5 py-3">
        <span className="text-[10.5px] leading-relaxed text-muted-foreground/80 line-clamp-2 flex-1">
          {payload.disclaimer}
        </span>
        <Button
          size="sm"
          className={cn(
            "h-8 shrink-0 rounded-lg text-[12px] font-medium px-4 tracking-tight",
            confirmed
              ? "bg-emerald-500/10 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-400 shadow-none"
              : "bg-foreground text-background hover:bg-foreground/90 shadow-none",
          )}
          onClick={() => setConfirmed(true)}
          disabled={confirmed}
          data-testid="synthetic-confirm-btn"
        >
          {confirmed ? (
            <>
              <Check className="mr-1 h-3.5 w-3.5" /> Activated
            </>
          ) : (
            "Confirm & activate"
          )}
        </Button>
      </div>
    </div>
  );
}
