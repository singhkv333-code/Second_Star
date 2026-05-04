"use client";

/**
 * CalendarTab — Quartr-style calendar polish (Day 7 redesign, image 5).
 *
 * Views: Month (calendar grid, dot markers per day) and Agenda (chronological).
 * Data: GET /api/workflows/scheduled-runs?from=&to= — only trigger.schedule
 * workflows contribute. trigger.event is cut to v2.
 *
 * Header: serif big "Month YYYY" + Today button + nav arrows + Month/Agenda toggle.
 * Category filter chips: Earnings / Dividends / IPOs / Macro — honest v1 placeholders
 * with a tooltip explaining they'll wire to a real events scraper in v2.
 */

import { useEffect, useState } from "react";
import {
  addDays,
  addMonths,
  eachDayOfInterval,
  endOfMonth,
  format,
  isSameDay,
  isSameMonth,
  parseISO,
  startOfMonth,
  startOfWeek,
  endOfWeek,
  formatDistanceToNow,
} from "date-fns";
import {
  AlertCircle,
  Calendar,
  ChevronLeft,
  ChevronRight,
  List,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { getScheduledRuns, getCalendarEvents, type ScheduledRun } from "@/lib/api";
import { isError } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type CalendarTabProps = {
  onOpenWorkflow: (workflowId: string) => void;
};

type View = "month" | "agenda";

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: ScheduledRun[] };

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

// Category chips — all honest placeholders for v1
type CategoryChip = {
  key: string;
  label: string;
  color: string; // dot color class
};

const CATEGORY_CHIPS: CategoryChip[] = [
  { key: "earnings", label: "Earnings", color: "bg-blue-500" },
  { key: "dividends", label: "Dividends", color: "bg-emerald-500" },
  { key: "ipos", label: "IPOs", color: "bg-amber-500" },
  { key: "macro", label: "Macro", color: "bg-rose-500" },
];

const CATEGORY_TOOLTIP =
  "Category filtering lands when the events scraper ships in v2. For now, only your scheduled agent runs appear here.";

// ---------------------------------------------------------------------------
// CalendarTab
// ---------------------------------------------------------------------------

export function CalendarTab({ onOpenWorkflow }: CalendarTabProps): React.ReactElement {
  const [view, setView] = useState<View>("month");
  const [monthAnchor, setMonthAnchor] = useState<Date>(() => startOfMonth(new Date()));
  const [state, setState] = useState<FetchState>({ kind: "loading" });
  const [selectedDay, setSelectedDay] = useState<Date | null>(null);

  const fetchRuns = (anchor: Date, v: View): void => {
    setState({ kind: "loading" });
    const from =
      v === "month"
        ? startOfMonth(anchor).toISOString()
        : new Date().toISOString();
    const to =
      v === "month"
        ? endOfMonth(anchor).toISOString()
        : addDays(new Date(), 30).toISOString();

    // Union scheduled workflow runs with real calendar events (earnings, macro, etc.)
    // Sort combined list by fire_time asc.
    Promise.all([
      getScheduledRuns({ from, to }),
      getCalendarEvents({ from, to }).catch(() => null), // silently skip if events endpoint fails
    ])
      .then(([runsResult, eventsResult]) => {
        if (isError(runsResult)) {
          setState({ kind: "error", message: runsResult.error.message });
          return;
        }
        const runs = runsResult.data.items;
        // Map calendar events to ScheduledRun shape for unified rendering
        const eventRuns: ScheduledRun[] =
          eventsResult && !isError(eventsResult)
            ? eventsResult.data.items.map((ev) => ({
                workflow_id: ev.workflow_id ?? "",
                workflow_name: ev.title,
                fire_time: ev.fire_time,
                fire_time_local: ev.fire_time,
                trigger_type: "trigger.event" as const,
              }))
            : [];
        const combined = [...runs, ...eventRuns].sort(
          (a, b) =>
            new Date(a.fire_time).getTime() - new Date(b.fire_time).getTime(),
        );
        setState({ kind: "ok", items: combined });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  };

  useEffect(() => {
    fetchRuns(monthAnchor, view);
    setSelectedDay(null);
  }, [monthAnchor, view]);

  const goToday = (): void => {
    setMonthAnchor(startOfMonth(new Date()));
    setSelectedDay(new Date());
  };

  const items = state.kind === "ok" ? state.items : [];

  return (
    <TooltipProvider>
      <div className="flex flex-col gap-5" data-testid="calendar-tab">
        {/* Big serif heading + nav */}
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="font-serif text-2xl font-semibold tracking-tight text-foreground">
              {format(monthAnchor, "MMMM yyyy")}
            </h1>
            <div className="mt-1 flex items-center gap-1">
              <Button
                variant="outline"
                size="sm"
                className="h-6 rounded-full px-2.5 text-xs"
                onClick={goToday}
                data-testid="today-btn"
              >
                Today
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                onClick={() => setMonthAnchor((d) => startOfMonth(addMonths(d, -1)))}
                aria-label="Previous month"
                data-testid="prev-month"
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                onClick={() => setMonthAnchor((d) => startOfMonth(addMonths(d, 1)))}
                aria-label="Next month"
                data-testid="next-month"
              >
                <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </div>
          </div>

          {/* View toggle */}
          <div
            className="flex items-center rounded-lg border p-0.5 gap-0.5"
            role="group"
            aria-label="Calendar view"
          >
            <Button
              variant={view === "month" ? "default" : "ghost"}
              size="sm"
              className="h-7 px-2.5 text-xs"
              onClick={() => setView("month")}
              aria-pressed={view === "month"}
              data-testid="view-month"
            >
              <Calendar className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              Month
            </Button>
            <Button
              variant={view === "agenda" ? "default" : "ghost"}
              size="sm"
              className="h-7 px-2.5 text-xs"
              onClick={() => setView("agenda")}
              aria-pressed={view === "agenda"}
              data-testid="view-agenda"
            >
              <List className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              Agenda
            </Button>
          </div>
        </div>

        {/* Category filter chips (placeholder) */}
        <div
          className="flex items-center gap-2 flex-wrap"
          role="group"
          aria-label="Event category filters (coming in v2)"
        >
          {CATEGORY_CHIPS.map((chip) => (
            <Tooltip key={chip.key}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  disabled
                  className={cn(
                    "flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs",
                    "cursor-not-allowed opacity-50",
                    "bg-card text-muted-foreground",
                  )}
                  aria-label={`${chip.label} — coming in v2`}
                  data-testid={`chip-${chip.key}`}
                >
                  <span
                    className={cn("h-2 w-2 rounded-full", chip.color)}
                    aria-hidden={true}
                  />
                  {chip.label}
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-xs text-center">
                {CATEGORY_TOOLTIP}
              </TooltipContent>
            </Tooltip>
          ))}
        </div>

        {/* Content */}
        {state.kind === "loading" && <CalendarSkeleton view={view} />}

        {state.kind === "error" && (
          <div
            role="alert"
            className="flex flex-col items-center justify-center py-12 text-center"
            data-testid="calendar-error"
          >
            <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
            <p className="text-sm font-medium">Couldn&apos;t load schedule</p>
            <p className="mt-1 text-xs text-muted-foreground">{state.message}</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-4"
              onClick={() => fetchRuns(monthAnchor, view)}
            >
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              Retry
            </Button>
          </div>
        )}

        {state.kind === "ok" && view === "month" && (
          <MonthView
            anchor={monthAnchor}
            items={items}
            selectedDay={selectedDay}
            onSelectDay={setSelectedDay}
            onOpenWorkflow={onOpenWorkflow}
          />
        )}

        {state.kind === "ok" && view === "agenda" && (
          <AgendaView items={items} onOpenWorkflow={onOpenWorkflow} />
        )}
      </div>
    </TooltipProvider>
  );
}

// ---------------------------------------------------------------------------
// Month view
// ---------------------------------------------------------------------------

function MonthView({
  anchor,
  items,
  selectedDay,
  onSelectDay,
  onOpenWorkflow,
}: {
  anchor: Date;
  items: ScheduledRun[];
  selectedDay: Date | null;
  onSelectDay: (d: Date) => void;
  onOpenWorkflow: (id: string) => void;
}): React.ReactElement {
  const monthStart = startOfMonth(anchor);
  const monthEnd = endOfMonth(anchor);
  const gridStart = startOfWeek(monthStart);
  const gridEnd = endOfWeek(monthEnd);
  const days = eachDayOfInterval({ start: gridStart, end: gridEnd });

  // Group items by day
  const byDay = new Map<string, ScheduledRun[]>();
  for (const item of items) {
    const key = format(parseISO(item.fire_time), "yyyy-MM-dd");
    const existing = byDay.get(key) ?? [];
    existing.push(item);
    byDay.set(key, existing);
  }

  const today = new Date();
  const todayKey = format(today, "yyyy-MM-dd");
  const selectedKey = selectedDay ? format(selectedDay, "yyyy-MM-dd") : null;
  const selectedItems = selectedKey ? (byDay.get(selectedKey) ?? []) : [];
  const isEmpty = items.length === 0;

  return (
    <div className="flex flex-col gap-3" data-testid="month-view">
      {/* Day name headers */}
      <div className="grid grid-cols-7 text-center">
        {DAY_NAMES.map((d) => (
          <span
            key={d}
            className="py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"
          >
            {d}
          </span>
        ))}
      </div>

      {/* Day cells */}
      <div className="grid grid-cols-7 gap-px rounded-xl border bg-border overflow-hidden">
        {days.map((day) => {
          const key = format(day, "yyyy-MM-dd");
          const dayItems = byDay.get(key) ?? [];
          const isToday = key === todayKey;
          const isSelected = key === selectedKey;
          const isCurrentMonth = isSameMonth(day, anchor);

          return (
            <button
              key={key}
              type="button"
              className={cn(
                "flex flex-col items-start p-1.5 min-h-[52px] text-left bg-background",
                "hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                "transition-colors",
                !isCurrentMonth && "opacity-40",
                isSelected && "bg-primary/5",
              )}
              onClick={() => onSelectDay(day)}
              aria-label={`${format(day, "MMMM d")}, ${dayItems.length} event${dayItems.length !== 1 ? "s" : ""}`}
              aria-pressed={isSelected}
              data-testid={`day-cell-${key}`}
            >
              <span
                className={cn(
                  "flex h-5 w-5 items-center justify-center rounded-full text-[11px]",
                  isToday && "bg-primary text-primary-foreground font-semibold",
                  !isToday && isSelected && "bg-muted font-medium",
                )}
              >
                {format(day, "d")}
              </span>
              {dayItems.length > 0 && (
                <div className="mt-0.5 flex flex-wrap gap-0.5">
                  {dayItems.slice(0, 3).map((_, i) => (
                    <span
                      key={i}
                      aria-hidden="true"
                      className="h-1.5 w-1.5 rounded-full bg-primary"
                    />
                  ))}
                  {dayItems.length > 3 && (
                    <span className="text-[9px] text-muted-foreground">
                      +{dayItems.length - 3}
                    </span>
                  )}
                </div>
              )}
            </button>
          );
        })}
      </div>

      {/* Selected day panel */}
      {selectedDay && (
        <div
          className="rounded-xl border bg-card p-4"
          data-testid="day-detail"
        >
          <h3 className="mb-3 font-serif text-sm font-semibold">
            {format(selectedDay, "EEEE, MMMM d")}
            {isSameDay(selectedDay, today) && (
              <span className="ml-2 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                Today
              </span>
            )}
          </h3>
          {selectedItems.length === 0 ? (
            <p className="text-xs text-muted-foreground">No runs scheduled.</p>
          ) : (
            <>
              <p className="mb-2 text-[11px] text-muted-foreground">
                {selectedItems.length} event{selectedItems.length !== 1 ? "s" : ""}
              </p>
              <ul className="space-y-2">
                {selectedItems.map((item, i) => (
                  <li key={i}>
                    <ScheduledRunRow item={item} onOpenWorkflow={onOpenWorkflow} />
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {isEmpty && !selectedDay && (
        <div
          className="flex flex-col items-center justify-center py-8 text-center"
          data-testid="calendar-empty"
        >
          <Calendar className="mb-3 h-6 w-6 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm font-medium">No scheduled runs this month</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Activate an agent with a schedule trigger to see it here.
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agenda view
// ---------------------------------------------------------------------------

function AgendaView({
  items,
  onOpenWorkflow,
}: {
  items: ScheduledRun[];
  onOpenWorkflow: (id: string) => void;
}): React.ReactElement {
  if (items.length === 0) {
    return (
      <div
        className="flex flex-col items-center justify-center py-12 text-center"
        data-testid="calendar-empty"
      >
        <Calendar className="mb-3 h-6 w-6 text-muted-foreground" aria-hidden="true" />
        <p className="text-sm font-medium">No scheduled runs in the next 30 days</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Activate an agent with a schedule trigger to see it here.
        </p>
      </div>
    );
  }

  // Group by date
  const groups = new Map<string, ScheduledRun[]>();
  for (const item of items) {
    const key = format(parseISO(item.fire_time), "yyyy-MM-dd");
    const existing = groups.get(key) ?? [];
    existing.push(item);
    groups.set(key, existing);
  }

  return (
    <div className="space-y-4" data-testid="agenda-view">
      {[...groups.entries()].map(([dateKey, dayItems]) => {
        const dateObj = parseISO(dateKey);
        const isToday = format(new Date(), "yyyy-MM-dd") === dateKey;
        return (
          <div key={dateKey}>
            <div className="mb-2 flex items-center gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {format(dateObj, "EEEE, MMMM d")}
              </h3>
              {isToday && (
                <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  Today
                </span>
              )}
            </div>
            <ul className="space-y-1.5">
              {dayItems.map((item, i) => (
                <li key={i}>
                  <ScheduledRunRow item={item} onOpenWorkflow={onOpenWorkflow} />
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared row component
// ---------------------------------------------------------------------------

function ScheduledRunRow({
  item,
  onOpenWorkflow,
}: {
  item: ScheduledRun;
  onOpenWorkflow: (id: string) => void;
}): React.ReactElement {
  const relTime = formatDistanceToNow(parseISO(item.fire_time), { addSuffix: true });

  return (
    <button
      type="button"
      className={cn(
        "flex w-full items-center gap-3 rounded-lg border bg-card px-3 py-2.5 text-left",
        "hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "transition-colors",
      )}
      onClick={() => onOpenWorkflow(item.workflow_id)}
      data-testid={`scheduled-run-${item.workflow_id}`}
    >
      <span
        className="h-2 w-2 shrink-0 rounded-full bg-primary"
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <span className="truncate text-xs font-medium text-foreground">
          {item.workflow_name}
        </span>
      </div>
      <div className="shrink-0 text-right">
        <span
          className="block text-xs font-medium text-foreground"
          title={`UTC: ${item.fire_time}`}
        >
          {item.fire_time_local}
        </span>
        <span className="text-[10px] text-muted-foreground">{relTime}</span>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function CalendarSkeleton({ view }: { view: View }): React.ReactElement {
  if (view === "agenda") {
    return (
      <div className="space-y-3" data-testid="calendar-loading">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full rounded-lg" />
        ))}
      </div>
    );
  }
  return (
    <div className="space-y-2" data-testid="calendar-loading">
      <Skeleton className="h-6 w-full" />
      <Skeleton className="h-[300px] w-full rounded-xl" />
    </div>
  );
}
