"use client";

/**
 * IpoListCard — inline chat card rendered when the chatbot's
 * `list_upcoming_ipos` tool returns `_render_hint: "ipo_list_card"`.
 *
 * States:
 *  - unreachable: shows the note honestly, no rows.
 *  - empty (source reachable, count 0): empty state with the note.
 *  - populated: sorted list of rows (open → upcoming → closed, then by open_date).
 *
 * Per-row actions:
 *  - "Apply" button → onSelectIpo(symbol)  [open/upcoming only]
 *  - "Remind" link  → onRemindIpo(symbol)  [open/upcoming only]
 *  - Closed rows: muted, no Apply.
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
};

// ---------------------------------------------------------------------------
// Sorting
// ---------------------------------------------------------------------------

const STATUS_ORDER: Record<IpoListItem["status"], number> = {
  open: 0,
  upcoming: 1,
  closed: 2,
};

function sortIpos(ipos: IpoListItem[]): IpoListItem[] {
  return [...ipos].sort((a, b) => {
    const statusDiff = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
    if (statusDiff !== 0) return statusDiff;
    // Within same status: sort by open_date ascending (nulls last)
    if (!a.open_date && !b.open_date) return 0;
    if (!a.open_date) return 1;
    if (!b.open_date) return -1;
    return a.open_date.localeCompare(b.open_date);
  });
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function formatDate(dateStr: string | null): string | null {
  if (!dateStr) return null;
  try {
    // dateStr may be ISO or a raw NSE date string; parse best-effort
    const d = new Date(dateStr.includes("T") ? dateStr : `${dateStr}T00:00:00`);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  } catch {
    return dateStr;
  }
}

function StatusBadge({ status }: { status: IpoListItem["status"] }): React.ReactElement {
  const map = {
    open: { label: "Open", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300" },
    upcoming: { label: "Upcoming", cls: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300" },
    closed: { label: "Closed", cls: "bg-slate-100 text-slate-500 dark:bg-slate-500/15 dark:text-slate-400" },
  };
  const { label, cls } = map[status];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium tracking-tight",
        cls,
      )}
    >
      {label}
    </span>
  );
}

function TypeChip({ type }: { type: IpoListItem["type"] }): React.ReactElement {
  const map = {
    mainboard: { label: "Mainboard", cls: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300" },
    sme: { label: "SME", cls: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300" },
  };
  const { label, cls } = map[type];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium tracking-tight",
        cls,
      )}
    >
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function IpoRow({
  ipo,
  onSelectIpo,
  onRemindIpo,
}: {
  ipo: IpoListItem;
  onSelectIpo: (symbol: string) => void;
  onRemindIpo: (symbol: string) => void;
}): React.ReactElement {
  const isClosed = ipo.status === "closed";
  const openLabel = formatDate(ipo.open_date);
  const closeLabel = formatDate(ipo.close_date);
  const dateRange =
    openLabel && closeLabel
      ? `${openLabel} – ${closeLabel}`
      : openLabel ?? closeLabel ?? null;

  return (
    <div
      className={cn(
        "flex flex-col gap-2 px-4 py-3 border-b border-border/40 last:border-0",
        isClosed && "opacity-60",
      )}
    >
      {/* Top row: name + chips */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5 min-w-0">
          <span
            className={cn(
              "text-[12.5px] font-semibold tracking-tight leading-tight truncate",
              isClosed ? "text-muted-foreground" : "text-foreground",
            )}
          >
            {ipo.name}
          </span>
          <span className="text-[10.5px] text-muted-foreground">{ipo.symbol}</span>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <TypeChip type={ipo.type} />
          <StatusBadge status={ipo.status} />
        </div>
      </div>

      {/* Details row */}
      <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-muted-foreground">
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
      </div>

      {/* Actions row */}
      {!isClosed && (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onSelectIpo(ipo.symbol)}
            aria-label={`Apply for ${ipo.name} IPO`}
            className={cn(
              "inline-flex h-7 items-center gap-1 rounded-full bg-primary px-3 text-[11.5px] font-medium text-primary-foreground",
              "transition-all hover:bg-primary/90 active:scale-[0.97]",
            )}
          >
            Apply
          </button>
          <button
            type="button"
            onClick={() => onRemindIpo(ipo.symbol)}
            aria-label={`Set up reminders for ${ipo.name} IPO`}
            className={cn(
              "inline-flex h-7 items-center gap-1 rounded-md border border-border/60 px-2.5 text-[11px] text-muted-foreground",
              "transition-colors hover:bg-muted hover:text-foreground",
            )}
          >
            <BellRing className="h-3 w-3 shrink-0" aria-hidden="true" />
            Remind
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main card
// ---------------------------------------------------------------------------

export function IpoListCard({
  payload,
  onSelectIpo,
  onRemindIpo,
}: IpoListCardProps): React.ReactElement {
  const isUnreachable = payload.source === "unreachable";
  const isEmpty = !isUnreachable && payload.count === 0;
  const sorted = isUnreachable || isEmpty ? [] : sortIpos(payload.ipos);

  return (
    <div
      data-testid="ipo-list-card"
      role="region"
      aria-label="IPO list"
      className={cn(
        "my-2 w-full max-w-[520px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
      )}
      style={{
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b border-border/40 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center rounded-md bg-violet-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
            IPOs
          </span>
          {!isUnreachable && (
            <span className="text-[11.5px] font-medium text-foreground">
              {payload.count} {payload.count === 1 ? "issue" : "issues"}
            </span>
          )}
        </div>
        {isUnreachable && (
          <span className="flex items-center gap-1 text-[10.5px] text-muted-foreground">
            <WifiOff className="h-3 w-3 shrink-0" aria-hidden="true" />
            Feed unreachable
          </span>
        )}
      </div>

      {/* Unreachable state */}
      {isUnreachable && (
        <div
          role="status"
          className="flex flex-col items-center gap-2 px-6 py-8 text-center"
        >
          <WifiOff
            className="h-8 w-8 text-muted-foreground/40"
            aria-hidden="true"
          />
          <p className="text-[12.5px] font-medium text-muted-foreground">
            Live IPO feed unreachable
          </p>
          {payload.note && (
            <p className="text-[11px] text-muted-foreground/70 max-w-[300px] leading-relaxed">
              {payload.note}
            </p>
          )}
        </div>
      )}

      {/* Empty-but-reachable state */}
      {isEmpty && (
        <div
          role="status"
          className="flex flex-col items-center gap-2 px-6 py-8 text-center"
        >
          <CalendarX
            className="h-8 w-8 text-muted-foreground/40"
            aria-hidden="true"
          />
          <p className="text-[12.5px] font-medium text-muted-foreground">
            No IPOs open or upcoming right now
          </p>
          {payload.note && (
            <p className="text-[11px] text-muted-foreground/70 max-w-[300px] leading-relaxed">
              {payload.note}
            </p>
          )}
        </div>
      )}

      {/* Populated list */}
      {!isUnreachable && !isEmpty && (
        <div>
          {sorted.map((ipo) => (
            <IpoRow
              key={ipo.symbol}
              ipo={ipo}
              onSelectIpo={onSelectIpo}
              onRemindIpo={onRemindIpo}
            />
          ))}
        </div>
      )}
    </div>
  );
}
