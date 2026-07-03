"use client";

/**
 * IpoListCard — inline chat card rendered when the chatbot's
 * `list_upcoming_ipos` tool returns `_render_hint: "ipo_list_card"`.
 *
 * Design mirrors WorkflowDraftCard: same max-w, padding, tile rows,
 * shadow, and entry animation.
 *
 * States:
 *  - unreachable: feed error, no rows.
 *  - empty: reachable but nothing open/upcoming.
 *  - populated: open → upcoming, closed filtered out.
 */

import { BellRing, CalendarX, WifiOff } from "lucide-react";
import { cn } from "@/lib/utils";
import type { IpoListPayload, IpoListItem } from "@/lib/types";

// ---------------------------------------------------------------------------
// Public props
// ---------------------------------------------------------------------------

export type IpoListCardProps = {
  payload: IpoListPayload;
  /** Triggers a new chat turn: "apply for the X IPO". */
  onSelectIpo: (symbol: string) => void;
  /** Triggers a new chat turn: "set up open-day reminders for the X IPO". */
  onRemindIpo: (symbol: string) => void;
  /** Opens the read-only details sidebar for the X IPO ("Know more"). */
  onKnowMore?: (symbol: string) => void;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_ORDER: Record<IpoListItem["status"], number> = {
  open: 0,
  upcoming: 1,
  closed: 2,
};

function sortIpos(ipos: IpoListItem[]): IpoListItem[] {
  return [...ipos]
    .filter((ipo) => ipo.status !== "closed")
    .sort((a, b) => {
      const statusDiff = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
      if (statusDiff !== 0) return statusDiff;
      if (!a.open_date && !b.open_date) return 0;
      if (!a.open_date) return 1;
      if (!b.open_date) return -1;
      return a.open_date.localeCompare(b.open_date);
    });
}

function formatDate(dateStr: string | null): string | null {
  if (!dateStr) return null;
  try {
    const d = new Date(dateStr.includes("T") ? dateStr : `${dateStr}T00:00:00`);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  } catch {
    return dateStr;
  }
}

function statusLabel(status: IpoListItem["status"]): string {
  return status === "open" ? "Open" : status === "upcoming" ? "Upcoming" : "Closed";
}

function typeLabel(type: IpoListItem["type"]): string {
  return type === "sme" ? "SME" : "Mainboard";
}

// ---------------------------------------------------------------------------
// Row — tile-style, matches DraftStepRow visual language
// ---------------------------------------------------------------------------

function IpoRow({
  ipo,
  onSelectIpo,
  onRemindIpo,
  onKnowMore,
  index,
}: {
  ipo: IpoListItem;
  onSelectIpo: (symbol: string) => void;
  onRemindIpo: (symbol: string) => void;
  onKnowMore?: (symbol: string) => void;
  index: number;
}): React.ReactElement {
  const openLabel = formatDate(ipo.open_date);
  const closeLabel = formatDate(ipo.close_date);
  const dateRange =
    openLabel && closeLabel
      ? `${openLabel} – ${closeLabel}`
      : openLabel ?? closeLabel ?? null;

  // Trendlyne-only IPOs carry no NSE symbol → can't register/automate. Route
  // chat actions by name so "Know more" still works; disable Apply/Remind.
  const registerable = ipo.registerable !== false && !!ipo.symbol;
  const actionRef = ipo.symbol || ipo.name;
  const subTotal = ipo.subscription?.total;

  return (
    <div
      className="flex flex-col gap-2 border-b border-border/40 py-3 last:border-0"
      style={{
        animation: `stepIn-quartr 320ms cubic-bezier(0.22, 1, 0.36, 1) both`,
        animationDelay: `${index * 50}ms`,
      }}
    >
      {/* Name + type · status */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5 min-w-0">
          <span className="text-[12.5px] font-semibold tracking-tight leading-tight truncate text-foreground">
            {ipo.name}
          </span>
          <span className="text-[10.5px] text-muted-foreground">{ipo.symbol}</span>
        </div>
        <span className="shrink-0 pt-0.5 text-[11px] font-medium text-foreground/60">
          {typeLabel(ipo.type)} · {statusLabel(ipo.status)}
        </span>
      </div>

      {/* Financial details */}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
        {ipo.price_band && (
          <span>
            <span className="font-medium text-foreground/80">Band</span>{" "}
            ₹{ipo.price_band}
          </span>
        )}
        {ipo.lot_size !== null && ipo.lot_size !== undefined && (
          <span>
            <span className="font-medium text-foreground/80">Lot</span>{" "}
            {String(ipo.lot_size)}
          </span>
        )}
        {ipo.issue_size && (
          <span>
            <span className="font-medium text-foreground/80">Issue</span>{" "}
            {ipo.issue_size}
          </span>
        )}
        {dateRange && (
          <span>
            <span className="font-medium text-foreground/80">Dates</span>{" "}
            {dateRange}
          </span>
        )}
        {subTotal != null && (
          <span>
            <span className="font-medium text-foreground/80">Subscribed</span>{" "}
            {subTotal.toFixed(1)}×
          </span>
        )}
      </div>

      {/* Subscription breakdown (Trendlyne) — retail / HNI / QIB */}
      {ipo.subscription &&
        (ipo.subscription.retail != null ||
          ipo.subscription.hni != null ||
          ipo.subscription.qib != null) && (
          <div className="flex flex-wrap gap-x-3 text-[10.5px] text-muted-foreground/80">
            {ipo.subscription.retail != null && (
              <span>Retail {ipo.subscription.retail.toFixed(1)}×</span>
            )}
            {ipo.subscription.hni != null && (
              <span>HNI {ipo.subscription.hni.toFixed(1)}×</span>
            )}
            {ipo.subscription.qib != null && (
              <span>QIB {ipo.subscription.qib.toFixed(1)}×</span>
            )}
          </div>
        )}

      {/* Actions */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onSelectIpo(actionRef)}
          disabled={!registerable}
          aria-label={`Apply for ${ipo.name} IPO`}
          title={registerable ? undefined : "Not on the NSE feed yet — registration unavailable"}
          className={cn(
            "inline-flex h-7 items-center rounded-full bg-primary px-3 text-[11.5px] font-medium text-primary-foreground",
            "transition-all hover:bg-primary/90 active:scale-[0.97]",
            !registerable && "cursor-not-allowed opacity-40 hover:bg-primary",
          )}
        >
          Apply
        </button>
        {onKnowMore && (
          <button
            type="button"
            onClick={() => onKnowMore(actionRef)}
            aria-label={`Know more about ${ipo.name} IPO`}
            className={cn(
              "inline-flex h-7 items-center rounded-full border border-border/60 px-3 text-[11px] font-medium text-foreground/75",
              "transition-colors hover:bg-muted hover:text-foreground",
            )}
          >
            Know more
          </button>
        )}
        <button
          type="button"
          onClick={() => onRemindIpo(actionRef)}
          disabled={!registerable}
          aria-label={`Set up reminders for ${ipo.name} IPO`}
          title={registerable ? undefined : "Not on the NSE feed yet — automation unavailable"}
          className={cn(
            "inline-flex h-7 items-center gap-1.5 rounded-full border border-border/60 px-2.5 text-[11px] font-medium text-foreground/75",
            "transition-colors hover:bg-muted hover:text-foreground",
            !registerable && "cursor-not-allowed opacity-40",
          )}
        >
          <BellRing className="h-3 w-3 shrink-0" aria-hidden="true" />
          Remind
        </button>
      </div>
    </div>
  );
}

/** Human label for the merged data source(s). */
function sourceLabel(source: string): string | null {
  if (!source || source === "unreachable") return null;
  const parts = source.split("+").map((s) => (s === "nse" ? "NSE" : s === "trendlyne" ? "Trendlyne" : s));
  return parts.join(" + ");
}

// ---------------------------------------------------------------------------
// Main card
// ---------------------------------------------------------------------------

export function IpoListCard({
  payload,
  onSelectIpo,
  onRemindIpo,
  onKnowMore,
}: IpoListCardProps): React.ReactElement {
  const isUnreachable = payload.source === "unreachable";
  const sorted = isUnreachable ? [] : sortIpos(payload.ipos);
  const isEmpty = !isUnreachable && sorted.length === 0;

  return (
    <div
      data-testid="ipo-list-card"
      role="region"
      aria-label="IPO list"
      className={cn(
        // Fixed 388px at sm+ so the sparse empty/unreachable states match the
        // populated card exactly (shrink-to-fit would otherwise narrow them);
        // full-width below sm for the mobile chat column.
        "mb-2 mt-1 w-full sm:w-[388px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
        "transition-all duration-500 ease-out",
      )}
      style={{
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      <div className="flex flex-col gap-3 px-5 pt-4 pb-4">
        {/* Header — IPOs chip + count, mirrors WorkflowDraftCard header row */}
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center rounded-md bg-violet-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
            IPOs
          </span>
          {isUnreachable ? (
            <span className="flex items-center gap-1 text-[10.5px] text-muted-foreground">
              <WifiOff className="h-3 w-3 shrink-0" aria-hidden="true" />
              Feed unreachable
            </span>
          ) : (
            <span className="text-[10.5px] font-medium text-muted-foreground">
              {sorted.length} {sorted.length === 1 ? "issue" : "issues"}
            </span>
          )}
        </div>

        {/* Unreachable state */}
        {isUnreachable && (
          <div role="status" className="flex flex-col items-center gap-2 py-6 text-center">
            <WifiOff className="h-8 w-8 text-muted-foreground/40" aria-hidden="true" />
            <p className="text-[12.5px] font-medium text-muted-foreground">
              Live IPO feed unreachable
            </p>
            {payload.note && (
              <p className="text-[11px] text-muted-foreground/70 max-w-[280px] leading-relaxed">
                {payload.note}
              </p>
            )}
          </div>
        )}

        {/* Empty state */}
        {isEmpty && (
          <div role="status" className="flex flex-col items-center gap-2 py-6 text-center">
            <CalendarX className="h-8 w-8 text-muted-foreground/40" aria-hidden="true" />
            <p className="text-[12.5px] font-medium text-muted-foreground">
              No IPOs open or upcoming right now
            </p>
            {payload.note && (
              <p className="text-[11px] text-muted-foreground/70 max-w-[280px] leading-relaxed">
                {payload.note}
              </p>
            )}
          </div>
        )}

        {/* Populated — tile list, same gap as WorkflowDraftCard step list */}
        {!isUnreachable && !isEmpty && (
          <div className="flex flex-col">
            {sorted.map((ipo, idx) => (
              <IpoRow
                key={ipo.symbol || ipo.name || idx}
                ipo={ipo}
                index={idx}
                onSelectIpo={onSelectIpo}
                onRemindIpo={onRemindIpo}
                onKnowMore={onKnowMore}
              />
            ))}
          </div>
        )}

        {/* Source attribution — honest provenance of the feed. */}
        {!isUnreachable && !isEmpty && sourceLabel(payload.source) && (
          <p className="pt-1 text-[10px] text-muted-foreground/60">
            Data: {sourceLabel(payload.source)}
          </p>
        )}
      </div>
    </div>
  );
}
