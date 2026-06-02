"use client";

/**
 * IpoListedCard — inline chat card rendered when the chatbot's
 * `get_ipo_listing` tool returns `_render_hint: "ipo_listed_card"`.
 *
 * Shows: name · symbol · type chip · "Listed" badge
 *        issue price → current price row
 *        listing gain % (green >0, red <0, neutral =0)
 *        listing date
 *
 * Honest pending states:
 *  - current_price null  → "Listing data pending — no live price yet"
 *  - issue_price null    → "Issue price unavailable"
 *  - both null           → both notes shown
 *
 * Never fabricates a number.
 */

import { TrendingDown, TrendingUp, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import type { IpoListedPayload } from "@/lib/types";

// ---------------------------------------------------------------------------
// Public props
// ---------------------------------------------------------------------------

export type IpoListedCardProps = {
  payload: IpoListedPayload;
};

// ---------------------------------------------------------------------------
// Helpers (mirror IpoApplicationCard formatters — no new imports needed)
// ---------------------------------------------------------------------------

function formatIndianCurrency(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  }).format(amount);
}

function formatListingDate(iso: string): string {
  try {
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// TypeChip — "MAINBOARD" | "SME"
// ---------------------------------------------------------------------------

function TypeChip({ type }: { type: IpoListedPayload["type"] }): React.ReactElement {
  return (
    <span className="inline-flex items-center rounded-md bg-violet-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
      {type.toUpperCase()}
    </span>
  );
}

// ---------------------------------------------------------------------------
// ListedBadge
// ---------------------------------------------------------------------------

function ListedBadge(): React.ReactElement {
  return (
    <span className="inline-flex items-center rounded-md bg-emerald-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">
      Listed
    </span>
  );
}

// ---------------------------------------------------------------------------
// GainDisplay — big signed gain % with directional color + icon
// ---------------------------------------------------------------------------

function GainDisplay({ pct }: { pct: number }): React.ReactElement {
  const isPositive = pct > 0;
  const isNegative = pct < 0;

  const colorCls = isPositive
    ? "text-emerald-600 dark:text-emerald-400"
    : isNegative
      ? "text-red-600 dark:text-red-400"
      : "text-foreground";

  const Icon = isPositive ? TrendingUp : isNegative ? TrendingDown : Minus;
  const sign = isPositive ? "+" : "";

  return (
    <div className={cn("flex items-center gap-1.5", colorCls)}>
      <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
      <span className="text-[22px] font-bold tabular-nums leading-none tracking-tight">
        {sign}{pct.toFixed(2)}%
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main card
// ---------------------------------------------------------------------------

export function IpoListedCard({ payload }: IpoListedCardProps): React.ReactElement {
  const {
    name,
    symbol,
    type,
    issue_price,
    listing_date,
    current_price,
    listing_gain_pct,
    note,
  } = payload;

  const hasGain = listing_gain_pct !== null && issue_price !== null && current_price !== null;
  const pendingPrice = current_price === null;
  const missingIssue = issue_price === null;

  return (
    <div
      data-testid="ipo-listed-card"
      role="region"
      aria-label={`IPO listing: ${name}`}
      className={cn(
        "my-2 w-full max-w-[440px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
      )}
      style={{
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      {/* Header */}
      <div className="flex flex-col gap-3 px-5 pt-4 pb-4">
        {/* Top row: type chip + Listed badge */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <TypeChip type={type} />
            <span className="text-[10.5px] font-medium text-muted-foreground uppercase tracking-widest">
              IPO
            </span>
          </div>
          <ListedBadge />
        </div>

        {/* Name + symbol */}
        <div>
          <h3 className="text-[15px] leading-[1.25] font-semibold tracking-tight text-foreground">
            {name}
          </h3>
          <p className="mt-0.5 text-[11.5px] text-muted-foreground">{symbol}</p>
        </div>

        {/* Price row */}
        <div className="rounded-xl bg-muted/40 px-4 py-3 flex flex-col gap-3">
          {/* Issue → Current */}
          <div className="flex items-center gap-3 text-[13px]">
            <div className="flex flex-col gap-0.5 min-w-0">
              <span className="text-[10px] uppercase tracking-widest text-muted-foreground/70">
                Issue price
              </span>
              {issue_price !== null ? (
                <span className="font-semibold tabular-nums text-foreground">
                  {formatIndianCurrency(issue_price)}
                </span>
              ) : (
                <span className="text-[11.5px] text-muted-foreground italic">
                  Unavailable
                </span>
              )}
            </div>

            <span className="text-muted-foreground/40 text-[16px] shrink-0" aria-hidden="true">
              →
            </span>

            <div className="flex flex-col gap-0.5 min-w-0">
              <span className="text-[10px] uppercase tracking-widest text-muted-foreground/70">
                Current price
              </span>
              {current_price !== null ? (
                <span className="font-semibold tabular-nums text-foreground">
                  {formatIndianCurrency(current_price)}
                </span>
              ) : (
                <span className="text-[11.5px] text-muted-foreground italic">
                  Pending
                </span>
              )}
            </div>
          </div>

          {/* Gain % */}
          {hasGain ? (
            <GainDisplay pct={listing_gain_pct} />
          ) : null}
        </div>

        {/* Listing date */}
        {listing_date && (
          <div className="flex items-center gap-2 text-[11.5px] text-muted-foreground">
            <span className="text-[10px] uppercase tracking-widest text-muted-foreground/70">
              Listed
            </span>
            <span>{formatListingDate(listing_date)}</span>
          </div>
        )}

        {/* Honest pending / unavailable notes */}
        {(pendingPrice || missingIssue) && (
          <div className="flex flex-col gap-1">
            {pendingPrice && !missingIssue && (
              <p className="text-[11px] text-muted-foreground/80 italic">
                Listing data pending — no live price yet
              </p>
            )}
            {missingIssue && (
              <p className="text-[11px] text-muted-foreground/80 italic">
                Issue price unavailable
              </p>
            )}
          </div>
        )}

        {/* Backend note (e.g. "this IPO has already listed — applications are closed") */}
        {note && (
          <p className="text-[11px] text-muted-foreground/80 italic">{note}</p>
        )}
      </div>
    </div>
  );
}
