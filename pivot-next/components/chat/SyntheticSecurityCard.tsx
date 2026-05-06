"use client";

/**
 * SyntheticSecurityCard — inline chat card for `build_product` results.
 *
 * Renders SafeGrow / StormShield / Barbell builds: headline, leg breakdown,
 * payoff table (when present), rebalance triggers (Barbell), and a
 * "Confirm & activate" CTA. The CTA is currently a no-op stub — wiring
 * it to a /products/activate endpoint is a follow-up; the card needs to
 * exist first so the user sees structured output instead of prose.
 */

import { useState } from "react";
import { Check, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

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
  // Indian comma format: ₹1,00,000
  const rounded = Math.round(amount);
  const s = String(Math.abs(rounded));
  const last3 = s.slice(-3);
  const rest = s.slice(0, -3);
  const restWithCommas = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  const formatted = rest ? `${restWithCommas},${last3}` : last3;
  return `${rounded < 0 ? "-" : ""}₹${formatted}`;
}

const PRODUCT_BADGE: Record<string, string> = {
  safegrow: "Capital Protection",
  stormshield: "Bear Hedge",
  barbell: "Gold + Equity",
};

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
  const badgeLabel = PRODUCT_BADGE[productKey] ?? "Synthetic Security";

  return (
    <div
      className="w-full max-w-xl rounded-xl border border-border bg-card p-4 shadow-sm"
      data-testid="synthetic-security-card"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3 border-b border-border/60 pb-3">
        <div className="flex flex-col gap-1">
          <Badge variant="secondary" className="self-start text-[10px] uppercase tracking-wide">
            {badgeLabel}
          </Badge>
          <h3 className="text-sm font-semibold leading-tight">
            {payload.display_name}
          </h3>
          <div className="text-xs text-muted-foreground">
            Capital: {formatINR(payload.capital)}
            {payload.horizon_months ? ` · ${payload.horizon_months} months` : ""}
            {payload.arb_yield_pct
              ? ` · Arb yield ${payload.arb_yield_pct.toFixed(2)}%`
              : ""}
          </div>
        </div>
      </div>

      {/* Risk warning (StormShield) */}
      {payload.risk_warning && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden={true} />
          <span>{payload.risk_warning}</span>
        </div>
      )}

      {/* Legs */}
      <div className="mt-3 flex flex-col gap-2">
        {payload.legs.map((leg, i) => (
          <div
            key={i}
            className="flex items-start justify-between gap-3 rounded-md border border-border/60 bg-muted/30 px-3 py-2"
          >
            <div className="flex flex-col gap-0.5">
              <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {leg.label}
                {leg.weight_pct !== undefined && (
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] normal-case text-foreground">
                    {leg.weight_pct.toFixed(1)}%
                  </span>
                )}
              </div>
              <div className="text-xs text-foreground">{leg.instrument}</div>
              {leg.expected_return && (
                <div className="text-[11px] text-muted-foreground">
                  {leg.expected_return}
                </div>
              )}
            </div>
            <div className="text-right text-sm font-semibold tabular-nums">
              {formatINR(leg.amount)}
            </div>
          </div>
        ))}
      </div>

      {/* Cash float */}
      {payload.cash_float !== undefined && payload.cash_float > 0 && (
        <div className="mt-2 text-[11px] text-muted-foreground">
          Cash float (post-rounding): {formatINR(payload.cash_float)}
        </div>
      )}

      {/* Payoff table — SafeGrow / StormShield */}
      {payload.payoff_table && payload.payoff_table.length > 0 && (
        <div className="mt-4">
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Payoff scenarios at maturity
          </div>
          <div className="overflow-hidden rounded-md border border-border/60">
            <table className="w-full text-xs">
              <thead className="bg-muted/40 text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-2 py-1.5 text-left font-medium">Scenario</th>
                  <th className="px-2 py-1.5 text-right font-medium">Nifty</th>
                  <th className="px-2 py-1.5 text-right font-medium">Portfolio</th>
                  <th className="px-2 py-1.5 text-right font-medium">Return</th>
                </tr>
              </thead>
              <tbody>
                {payload.payoff_table.map((row, i) => (
                  <tr key={i} className="border-t border-border/40">
                    <td className="px-2 py-1.5">{row.scenario}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      {row.nifty_level.toLocaleString("en-IN")}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      {formatINR(row.portfolio_value)}
                    </td>
                    <td
                      className={`px-2 py-1.5 text-right tabular-nums ${
                        row.return_pct > 0
                          ? "text-emerald-600 dark:text-emerald-400"
                          : row.return_pct < 0
                          ? "text-red-600 dark:text-red-400"
                          : ""
                      }`}
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

      {/* Rebalance triggers — Barbell */}
      {payload.rebalance && (
        <div className="mt-4">
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Rebalance triggers ({payload.rebalance.threshold_pct.toFixed(0)}% threshold)
          </div>
          <div className="rounded-md border border-border/60 px-3 py-2 text-xs">
            <div className="text-muted-foreground">{payload.rebalance.rule}</div>
            <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] tabular-nums">
              <span className="text-muted-foreground">GOLDBEES ↑ to</span>
              <span className="text-right">{formatINR(payload.rebalance.gold_triggers.up)}</span>
              <span className="text-muted-foreground">GOLDBEES ↓ to</span>
              <span className="text-right">{formatINR(payload.rebalance.gold_triggers.down)}</span>
              <span className="text-muted-foreground">NIFTYBEES ↑ to</span>
              <span className="text-right">{formatINR(payload.rebalance.equity_triggers.up)}</span>
              <span className="text-muted-foreground">NIFTYBEES ↓ to</span>
              <span className="text-right">{formatINR(payload.rebalance.equity_triggers.down)}</span>
            </div>
          </div>
          {payload.rebalancing_calendar && (
            <div className="mt-2 text-[11px] text-muted-foreground">
              Projected:{" "}
              {payload.rebalancing_calendar.avg_rebalances_per_year !== null
                ? `~${payload.rebalancing_calendar.avg_rebalances_per_year.toFixed(2)} rebalances/yr (${
                    payload.rebalancing_calendar.lookback_years ?? "?"
                  }y lookback) · `
                : ""}
              {payload.rebalancing_calendar.next_window_estimate}
            </div>
          )}
        </div>
      )}

      {/* Tax note (Barbell) */}
      {payload.tax_note && (
        <div className="mt-3 rounded-md border border-blue-500/30 bg-blue-500/5 px-3 py-2 text-[11px] text-blue-700 dark:text-blue-300">
          {payload.tax_note}
        </div>
      )}

      {/* Explanation */}
      {payload.explanation && (
        <p className="mt-3 text-xs text-muted-foreground">{payload.explanation}</p>
      )}

      {/* CTA */}
      <div className="mt-4 flex items-center justify-between border-t border-border/60 pt-3">
        <span className="text-[10px] text-muted-foreground">
          {payload.disclaimer}
        </span>
        <Button
          size="sm"
          variant={confirmed ? "secondary" : "default"}
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
