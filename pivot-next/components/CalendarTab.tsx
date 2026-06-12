"use client";

/**
 * CalendarTab — Quartr-design calendar.
 *
 * Visuals ported from frontend-quartr/src/components/calendar/CalendarPage.jsx:
 *   • Big serif "Month YYYY" + ‹ Today › nav row + Month/Agenda toggle
 *   • Filter chips (workflow runs vs events) with subdued opacity when off
 *   • Month view: 7-column grid, today badge, up-to-3 events per cell, +N overflow
 *   • Right Day Panel (320px) with sticky heading, event list with per-event
 *     dot + uppercase type label + meta grid
 *   • Agenda view: chronological list grouped by date
 *
 * Data path is unchanged — keeps using getScheduledRuns + getCalendarEvents
 * and combines them into a single ScheduledRun[] sorted by fire_time.
 */

import { useEffect, useMemo, useState } from "react";
import {
  addDays,
  addMonths,
  endOfMonth,
  format as formatDate,
  isSameDay,
  parseISO,
  startOfMonth,
} from "date-fns";
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getScheduledRuns,
  getWorkflow,
  listWorkflows,
  type ScheduledRun,
} from "@/lib/api";
// TODO(P2.1-calendar): import getIpoCalendar and IpoCalendarItem from @/lib/api
// and @/lib/types, then merge IPO open/close dates into the combined items list
// below. IpoCalendarItem has {ipo_symbol, open_date, close_date, status, type}
// whereas ScheduledRun has {workflow_id, workflow_name, fire_time, trigger_type}.
// Mapping: synthesise two ScheduledRun entries per IPO item — one for open_date
// (trigger_type "trigger.ipo_open") and one for close_date (trigger_type "trigger.event"),
// with fire_time=<date>T09:00:00+05:30 and workflow_name=`${name} IPO opens/closes`.
// The filter chips need a new "ipo" type key in EVENT_TYPES with a distinct color.
// Risk: the existing month/agenda view renders from a ScheduledRun[] and the
// DayPanelEvent/AgendaLine click handlers call onOpenWorkflow(workflow_id) — IPO
// entries have no workflow_id so that branch must be guarded. Deferred to P2.1.
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

// ---------------------------------------------------------------------------
// Event type styling — label only. Per-event color comes from
// `workflowColor()` so each agent's runs read with their own accent.
// ---------------------------------------------------------------------------

const EVENT_TYPES = {
  "trigger.schedule": { label: "Agent run" },
} as const;

type TypeKey = keyof typeof EVENT_TYPES;

// Accent palette — same six accents used in the donut/compare chart.
// Each workflow gets a deterministic slot via a djb2-style hash, so a
// given agent always picks up the same accent across navigations.
const AGENT_PALETTE = [
  "#1b7cc7", // cobalt blue
  "#fb8500", // vivid orange
  "#219ebc", // cyan teal
  "#ffb703", // golden yellow
  "#2c666e", // dark teal
  "#d00000", // red
];

function workflowColor(workflowId: string | null | undefined): string {
  if (!workflowId) return "var(--text-tertiary)";
  let hash = 0;
  for (let i = 0; i < workflowId.length; i++) {
    hash = ((hash << 5) - hash + workflowId.charCodeAt(i)) | 0;
  }
  return AGENT_PALETTE[Math.abs(hash) % AGENT_PALETTE.length]!;
}

// ---------------------------------------------------------------------------
// Helpers — date math + agenda grouping
// ---------------------------------------------------------------------------

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

type Cell = {
  date: Date;
  key: string;
  inMonth: boolean;
  isToday: boolean;
};

function buildMonthGrid(year: number, month: number): Cell[] {
  const first = new Date(year, month, 1);
  const lead = (first.getDay() + 6) % 7; // Monday-first
  const start = new Date(year, month, 1 - lead);
  const cells: Cell[] = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    cells.push({
      date: d,
      key: ymd(d),
      inMonth: d.getMonth() === month,
      isToday: ymd(d) === ymd(new Date()),
    });
  }
  // Drop the trailing week if every cell falls outside the current month
  const lastWeek = cells.slice(-7);
  if (lastWeek.every((c) => !c.inMonth)) cells.length = 35;
  return cells;
}

function safeParse(iso: string): Date | null {
  if (!iso) return null;
  const d = parseISO(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

function indexEventsByDate(items: ScheduledRun[]): Map<string, ScheduledRun[]> {
  const map = new Map<string, ScheduledRun[]>();
  for (const it of items) {
    // fire_time is the canonical ISO timestamp; fire_time_local is a
    // pre-formatted human label like "3:55 PM IST" and is NOT parseable.
    // Always group by fire_time.
    const parsed = safeParse(it.fire_time);
    if (!parsed) continue;
    const key = ymd(parsed);
    const list = map.get(key) ?? [];
    list.push(it);
    map.set(key, list);
  }
  for (const [k, list] of map) {
    list.sort(
      (a, b) =>
        (safeParse(a.fire_time)?.getTime() ?? 0) -
        (safeParse(b.fire_time)?.getTime() ?? 0),
    );
    map.set(k, list);
  }
  return map;
}

function eventTypeKey(_item: ScheduledRun): TypeKey {
  return "trigger.schedule";
}

function eventTime(item: ScheduledRun): string {
  // Prefer the backend's pre-formatted local time when present (e.g.
  // "3:55 PM IST"); fall back to formatting the ISO fire_time. The
  // local string is opaque to date-fns so we never try to parse it.
  if (item.fire_time_local && item.fire_time_local.trim().length > 0) {
    return item.fire_time_local;
  }
  const d = safeParse(item.fire_time);
  return d ? formatDate(d, "HH:mm") : "";
}

// ---------------------------------------------------------------------------
// Client-side fallback: derive scheduled runs from active workflows when
// the backend's /workflows/scheduled-runs is empty. We expand each active
// workflow's first schedule trigger into the [from, to] window.
// ---------------------------------------------------------------------------

/**
 * Expand a small subset of cron expressions plus an explicit cadence
 * field into concrete fire times in [from, to]. Only the patterns the
 * seeded demo agents emit are handled; anything else returns [].
 *
 *   "55 15 * * 1-5"  → 3:55 PM, Mon–Fri
 *   "0 9 * * 1"      → 9:00 AM, Mondays
 *   "0 9 1 * *"      → 9:00 AM, 1st of each month
 *
 * Free-form `cadence` strings ("weekly", "weekday", "monthly") are
 * also accepted as a fallback when no cron is present.
 */
function expandSchedule(
  cron: string | null,
  cadence: string | null,
  from: Date,
  to: Date,
  hour: number,
  minute: number,
): Date[] {
  const out: Date[] = [];

  // cron parser: only minute/hour + simple dow / day-of-month patterns.
  let dowSet: Set<number> | null = null;
  let dayOfMonth: number | null = null;
  let h = hour;
  let m = minute;

  if (cron) {
    const parts = cron.trim().split(/\s+/);
    if (parts.length === 5) {
      const [pm, ph, pdom, _pmonth, pdow] = parts as [string, string, string, string, string];
      if (/^\d+$/.test(pm)) m = parseInt(pm, 10);
      if (/^\d+$/.test(ph)) h = parseInt(ph, 10);
      if (/^\d+$/.test(pdom)) dayOfMonth = parseInt(pdom, 10);
      if (pdow !== "*") {
        dowSet = new Set();
        pdow.split(",").forEach((token) => {
          const range = token.split("-").map((n) => parseInt(n, 10));
          if (range.length === 2 && !isNaN(range[0]!) && !isNaN(range[1]!)) {
            for (let i = range[0]!; i <= range[1]!; i++) dowSet!.add(i);
          } else if (!isNaN(range[0]!)) {
            dowSet!.add(range[0]!);
          }
        });
      }
    }
  }

  // cadence-based fallback when no cron is present.
  if (!cron && cadence) {
    const c = cadence.toLowerCase();
    if (c === "weekday") dowSet = new Set([1, 2, 3, 4, 5]);
    else if (c === "weekly") dowSet = new Set([1]); // Mondays
    else if (c === "monthly") dayOfMonth = 1;
    else if (c === "daily") dowSet = new Set([0, 1, 2, 3, 4, 5, 6]);
  }

  // Walk every day in the window and emit a fire-time when it matches.
  let cursor = new Date(from.getFullYear(), from.getMonth(), from.getDate(), h, m, 0, 0);
  const end = new Date(to.getFullYear(), to.getMonth(), to.getDate(), 23, 59, 59, 999);
  while (cursor <= end) {
    const dow = cursor.getDay();
    const dom = cursor.getDate();
    let match = true;
    if (dowSet && !dowSet.has(dow)) match = false;
    if (dayOfMonth !== null && dom !== dayOfMonth) match = false;
    if (match) out.push(new Date(cursor));
    cursor = addDays(cursor, 1);
  }
  return out;
}

async function deriveRunsFromActiveWorkflows(
  from: Date,
  to: Date,
): Promise<ScheduledRun[]> {
  // 1. List active + paused workflows.
  const wfList = await listWorkflows({ limit: 50 }).catch(() => null);
  if (!wfList || isError(wfList)) return [];

  const summaries = wfList.data.items.filter(
    (w) => w.status === "active" || w.status === "paused",
  );

  // 2. Fetch full workflow for each so we can read the schedule trigger
  //    config. Skip on failure.
  const fulls = await Promise.all(
    summaries.map((s) =>
      getWorkflow(s.id)
        .then((r) => (isError(r) ? null : r.data))
        .catch(() => null),
    ),
  );

  const out: ScheduledRun[] = [];
  fulls.forEach((wf, idx) => {
    if (!wf) return;
    const trigger = wf.steps.find(
      (st) => st.step_type === "trigger.schedule",
    );
    const cfg = (trigger?.config ?? {}) as {
      cron?: string | null;
      cadence?: string | null;
      hour?: number;
      minute?: number;
      timezone?: string;
    };
    const fireTimes = expandSchedule(
      cfg.cron ?? null,
      cfg.cadence ?? null,
      from,
      to,
      typeof cfg.hour === "number" ? cfg.hour : 9,
      typeof cfg.minute === "number" ? cfg.minute : 0,
    );
    fireTimes.forEach((d) => {
      out.push({
        workflow_id: wf.id,
        workflow_name: summaries[idx]?.name ?? wf.name,
        trigger_type: "trigger.schedule",
        fire_time: d.toISOString(),
        fire_time_local: formatDate(d, "h:mm a"),
      });
    });
  });
  return out;
}

// ---------------------------------------------------------------------------
// CalendarTab
// ---------------------------------------------------------------------------

export function CalendarTab({ onOpenWorkflow }: CalendarTabProps): React.ReactElement {
  const today = useMemo(() => new Date(), []);
  const [cursor, setCursor] = useState<{ year: number; month: number }>({
    year: today.getFullYear(),
    month: today.getMonth(),
  });
  const [view, setView] = useState<View>("month");
  const [selectedDate, setSelectedDate] = useState<string>(ymd(today));
  const [state, setState] = useState<FetchState>({ kind: "loading" });

  // Data fetch — primary path is the backend's getScheduledRuns. When
  // that comes back empty (the demo backend doesn't compute upcoming
  // fire times for the seeded agents), we derive runs client-side by
  // expanding each active workflow's schedule trigger into the [from, to]
  // window. That way the calendar reflects what /agents shows.
  const fetchRuns = (year: number, month: number): void => {
    setState({ kind: "loading" });
    const anchor = new Date(year, month, 1);
    const fromDate = startOfMonth(anchor);
    const toDate = endOfMonth(anchor);
    const from = fromDate.toISOString();
    const to = toDate.toISOString();

    getScheduledRuns({ from, to })
      .then(async (runsResult) => {
        if (isError(runsResult)) {
          setState({ kind: "error", message: runsResult.error.message });
          return;
        }
        // Market events are no longer surfaced — drop any items the
        // backend tags as `trigger.event` before they reach the grid.
        let runs = runsResult.data.items.filter(
          (it) => it.trigger_type !== "trigger.event",
        );

        // Fallback: backend returned no scheduled runs for this month —
        // derive from active workflows' schedule triggers so the
        // calendar isn't empty just because /workflows/scheduled-runs
        // hasn't been wired in this environment.
        if (runs.length === 0) {
          runs = await deriveRunsFromActiveWorkflows(fromDate, toDate);
        }

        const combined = runs.slice().sort(
          (a, b) => new Date(a.fire_time).getTime() - new Date(b.fire_time).getTime(),
        );
        setState({ kind: "ok", items: combined });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  };

  useEffect(() => {
    fetchRuns(cursor.year, cursor.month);
  }, [cursor]);

  const filteredItems = useMemo(
    () => (state.kind === "ok" ? state.items : []),
    [state],
  );
  const eventsByDate = useMemo(() => indexEventsByDate(filteredItems), [filteredItems]);
  const cells = useMemo(() => buildMonthGrid(cursor.year, cursor.month), [cursor]);

  const goPrev = (): void =>
    setCursor((c) => {
      const m = c.month - 1;
      return m < 0 ? { year: c.year - 1, month: 11 } : { year: c.year, month: m };
    });
  const goNext = (): void =>
    setCursor((c) => {
      const m = c.month + 1;
      return m > 11 ? { year: c.year + 1, month: 0 } : { year: c.year, month: m };
    });
  const goToday = (): void => {
    const t = new Date();
    setCursor({ year: t.getFullYear(), month: t.getMonth() });
    setSelectedDate(ymd(t));
  };

  return (
    <div
      className="flex flex-col"
      style={{ background: "var(--bg-base)", height: "100%", minHeight: 0 }}
      data-testid="calendar-tab"
    >
      {/* Top bar — title, nav, view toggle. On phone the title takes its
          own row and the controls (nav + view toggle) drop to row 2,
          handled by .calendar-toolbar in globals.css. */}
      <div
        className="calendar-toolbar flex shrink-0 items-center"
        style={{ gap: 16, padding: "0 0 16px" }}
      >
        <h1
          className="q-serif calendar-toolbar-title"
          style={{
            fontSize: 22,
            letterSpacing: "-0.025em",
            color: "var(--text-primary)",
            margin: 0,
            whiteSpace: "nowrap",
          }}
        >
          {MONTH_NAMES[cursor.month]} {cursor.year}
        </h1>

        <div className="calendar-toolbar-spacer" style={{ flex: 1 }} />

        <div className="inline-flex items-center" style={{ gap: 4 }}>
          <NavArrow direction="prev" onClick={goPrev} />
          <button type="button" onClick={goToday} style={ghostBtn}>
            Today
          </button>
          <NavArrow direction="next" onClick={goNext} />
        </div>

        <div
          className="inline-flex"
          style={{
            gap: 2,
            padding: 3,
            background: "var(--bg-base)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-pill)",
          }}
        >
          {(["month", "agenda"] as const).map((v) => {
            const active = view === v;
            return (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                aria-pressed={active}
                data-testid={`view-${v}`}
                style={{
                  padding: "6px 14px",
                  border: "none",
                  cursor: "pointer",
                  borderRadius: "var(--radius-pill)",
                  fontSize: 12,
                  fontFamily: "var(--font-ui)",
                  fontWeight: 500,
                  background: active ? "var(--text-primary)" : "transparent",
                  color: active ? "var(--bg-primary)" : "var(--text-secondary)",
                  transition:
                    "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
                }}
              >
                {v === "month" ? "Month" : "Agenda"}
              </button>
            );
          })}
        </div>
      </div>

      {/* Body */}
      {state.kind === "loading" && (
        <div className="flex" style={{ flex: 1, minHeight: 0, gap: 24 }}>
          <Skeleton style={{ flex: 1, minHeight: 320 }} />
          <Skeleton style={{ width: 320, height: 320 }} />
        </div>
      )}

      {state.kind === "error" && (
        <div
          role="alert"
          className="flex flex-col items-center justify-center"
          style={{ padding: 48, color: "var(--text-tertiary)" }}
          data-testid="calendar-error"
        >
          <AlertCircle
            className="mb-3"
            size={24}
            style={{ color: "var(--color-loss)" }}
            aria-hidden="true"
          />
          <p style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>
            Couldn&apos;t load schedule
          </p>
          <p style={{ fontSize: 12, marginTop: 4 }}>{state.message}</p>
          <button
            type="button"
            onClick={() => fetchRuns(cursor.year, cursor.month)}
            className="inline-flex items-center"
            style={{
              marginTop: 16,
              gap: 8,
              padding: "6px 12px",
              background: "transparent",
              border: "1px solid var(--glass-border-hover)",
              borderRadius: "var(--radius-sm)",
              color: "var(--text-primary)",
              fontSize: 12,
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            <RefreshCw size={13} aria-hidden="true" />
            Retry
          </button>
        </div>
      )}

      {state.kind === "ok" && (
        <div className="calendar-body flex" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          {view === "month" ? (
            <>
              <MonthView
                cells={cells}
                eventsByDate={eventsByDate}
                selectedDate={selectedDate}
                onSelect={setSelectedDate}
              />
              <DayPanel
                date={selectedDate}
                events={(eventsByDate.get(selectedDate) ?? []).slice()}
                onOpenWorkflow={onOpenWorkflow}
              />
            </>
          ) : (
            <AgendaView events={filteredItems} onOpenWorkflow={onOpenWorkflow} />
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MonthView
// ---------------------------------------------------------------------------

function MonthView({
  cells,
  eventsByDate,
  selectedDate,
  onSelect,
}: {
  cells: Cell[];
  eventsByDate: Map<string, ScheduledRun[]>;
  selectedDate: string;
  onSelect: (key: string) => void;
}): React.ReactElement {
  const rowCount = Math.ceil(cells.length / 7);
  return (
    <div
      className="calendar-month-view flex flex-col"
      style={{ flex: 1, minWidth: 0, minHeight: 0, padding: "0 24px 24px 0" }}
    >
      {/* DOW header */}
      <div
        className="grid shrink-0"
        style={{ gridTemplateColumns: "repeat(7, minmax(0, 1fr))" }}
      >
        {DOW_LABELS.map((d) => (
          <div
            key={d}
            style={{
              fontSize: 10,
              color: "var(--text-tertiary)",
              fontWeight: 500,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              padding: "0 4px 8px",
            }}
          >
            {d}
          </div>
        ))}
      </div>

      {/* Date grid */}
      <div
        className="grid"
        style={{
          flex: 1,
          minHeight: 0,
          gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
          gridTemplateRows: `repeat(${rowCount}, minmax(0, 1fr))`,
          borderTop: "1px solid var(--glass-border)",
        }}
      >
        {cells.map((c, i) => {
          const events = (eventsByDate.get(c.key) ?? []).slice();
          const row = Math.floor(i / 7);
          const isSelected = c.key === selectedDate;
          return (
            <button
              key={c.key}
              type="button"
              onClick={() => onSelect(c.key)}
              className="flex flex-col"
              style={{
                textAlign: "left",
                padding: "8px 6px 6px",
                background: isSelected ? "var(--surface-active)" : "transparent",
                border: "none",
                borderBottom: row < rowCount - 1 ? "1px solid var(--glass-border)" : "none",
                cursor: "pointer",
                overflow: "hidden",
                minHeight: 0,
                transition: "background-color 0.2s var(--ease-quartr)",
                fontFamily: "inherit",
              }}
              onMouseEnter={(e) => {
                if (!isSelected) e.currentTarget.style.background = "var(--surface-hover)";
              }}
              onMouseLeave={(e) => {
                if (!isSelected) e.currentTarget.style.background = "transparent";
              }}
            >
              <div
                className="flex items-center"
                style={{ marginBottom: 6, padding: "0 2px" }}
              >
                <span
                  className="inline-flex items-center justify-center"
                  style={{
                    minWidth: 22,
                    height: 22,
                    borderRadius: c.isToday ? "50%" : 0,
                    background: c.isToday ? "var(--text-primary)" : "transparent",
                    fontFamily: "var(--font-display)",
                    fontWeight: 550,
                    fontSize: 13,
                    letterSpacing: "-0.01em",
                    color: c.isToday
                      ? "var(--bg-primary)"
                      : c.inMonth
                        ? "var(--text-primary)"
                        : "var(--text-disabled)",
                  }}
                >
                  {c.date.getDate()}
                </span>
              </div>
              <div
                className="calendar-cell-events flex flex-col"
                style={{ gap: 1, minWidth: 0, overflow: "hidden" }}
              >
                {events.slice(0, 3).map((ev, idx) => (
                  <EventRow key={idx} event={ev} muted={!c.inMonth} />
                ))}
                {events.length > 3 && (
                  <span
                    className="calendar-cell-overflow"
                    style={{
                      fontSize: 10.5,
                      color: "var(--text-tertiary)",
                      padding: "1px 4px",
                    }}
                  >
                    +{events.length - 3}
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function EventRow({
  event,
  muted,
}: {
  event: ScheduledRun;
  muted: boolean;
}): React.ReactElement {
  const label = event.workflow_name;
  const color = workflowColor(event.workflow_id);
  return (
    <div
      title={label}
      className="calendar-cell-event flex items-center"
      style={{
        gap: 6,
        padding: "1px 4px",
        fontSize: 11,
        fontFamily: "var(--font-ui)",
        fontWeight: 500,
        opacity: muted ? 0.4 : 1,
        cursor: "default",
        lineHeight: 1.4,
      }}
    >
      <span
        className="calendar-cell-event-dot"
        style={{
          width: 5,
          height: 5,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
        }}
      />
      <span
        className="calendar-cell-event-label"
        style={{
          color: "var(--text-primary)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          minWidth: 0,
        }}
      >
        {label}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DayPanel — right rail
// ---------------------------------------------------------------------------

function DayPanel({
  date,
  events,
  onOpenWorkflow,
}: {
  date: string;
  events: ScheduledRun[];
  onOpenWorkflow: (workflowId: string) => void;
}): React.ReactElement {
  const d = new Date(date + "T00:00:00");
  const today = new Date();
  const tomorrow = addDays(today, 1);
  const heading =
    isSameDay(d, today)
      ? "Today"
      : isSameDay(d, tomorrow)
        ? "Tomorrow"
        : d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });

  return (
    <aside
      className="calendar-day-panel flex flex-col"
      style={{
        width: 320,
        flexShrink: 0,
        borderLeft: "1px solid var(--glass-border)",
        padding: "0 24px 24px",
        overflowY: "auto",
        minHeight: 0,
      }}
    >
      <div
        style={{
          position: "sticky",
          top: 0,
          background: "var(--bg-base)",
          paddingTop: 4,
          paddingBottom: 14,
          marginBottom: 4,
        }}
      >
        <div
          className="q-display"
          style={{ fontSize: 16, color: "var(--text-primary)", lineHeight: 1.2 }}
        >
          {heading}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-tertiary)", marginTop: 4 }}>
          {events.length === 0
            ? "No events"
            : `${events.length} event${events.length === 1 ? "" : "s"}`}
        </div>
      </div>

      {events.length === 0 ? (
        <div style={{ fontSize: 12.5, color: "var(--text-secondary)", padding: "16px 0" }}>
          Nothing scheduled.
        </div>
      ) : (
        <div className="flex flex-col" style={{ gap: 12 }}>
          {events.map((ev, i) => (
            <DayPanelEvent key={i} event={ev} onOpenWorkflow={onOpenWorkflow} />
          ))}
        </div>
      )}
    </aside>
  );
}

function DayPanelEvent({
  event,
  onOpenWorkflow,
}: {
  event: ScheduledRun;
  onOpenWorkflow: (workflowId: string) => void;
}): React.ReactElement {
  const tk = eventTypeKey(event);
  const t = EVENT_TYPES[tk];
  const isWorkflow = tk === "trigger.schedule" && event.workflow_id;
  const time = eventTime(event);
  const color = workflowColor(event.workflow_id);

  return (
    <button
      type="button"
      onClick={() => isWorkflow && onOpenWorkflow(event.workflow_id)}
      style={{
        display: "block",
        textAlign: "left",
        background: "transparent",
        border: "none",
        padding: 0,
        cursor: isWorkflow ? "pointer" : "default",
        width: "100%",
      }}
    >
      <div className="flex items-center" style={{ gap: 8, marginBottom: 4 }}>
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: color,
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontSize: 10,
            color: "var(--text-secondary)",
            fontWeight: 500,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
          }}
        >
          {t.label}
        </span>
        {time && (
          <span
            style={{
              marginLeft: "auto",
              fontSize: 10.5,
              color: "var(--text-tertiary)",
              fontFamily: "var(--font-mono)",
            }}
          >
            {time}
          </span>
        )}
      </div>
      <div
        style={{
          fontFamily: "var(--font-ui)",
          fontWeight: 550,
          fontSize: 13,
          color: "var(--text-primary)",
          lineHeight: 1.4,
          letterSpacing: "-0.005em",
        }}
      >
        {event.workflow_name}
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// AgendaView
// ---------------------------------------------------------------------------

function AgendaView({
  events,
  onOpenWorkflow,
}: {
  events: ScheduledRun[];
  onOpenWorkflow: (workflowId: string) => void;
}): React.ReactElement {
  const groups = useMemo(() => {
    const sorted = events.slice().sort(
      (a, b) =>
        (safeParse(a.fire_time)?.getTime() ?? 0) -
        (safeParse(b.fire_time)?.getTime() ?? 0),
    );
    const map = new Map<string, ScheduledRun[]>();
    for (const ev of sorted) {
      const parsed = safeParse(ev.fire_time);
      if (!parsed) continue;
      const key = ymd(parsed);
      const list = map.get(key) ?? [];
      list.push(ev);
      map.set(key, list);
    }
    return Array.from(map.entries());
  }, [events]);

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        overflowY: "auto",
        padding: "0 0 32px",
      }}
    >
      <div
        className="flex flex-col"
        style={{ maxWidth: 760, margin: "0 auto" }}
      >
        {groups.length === 0 && (
          <div
            style={{
              padding: 48,
              textAlign: "center",
              color: "var(--text-secondary)",
              fontSize: 13,
            }}
          >
            No events match the active filters.
          </div>
        )}

        {groups.map(([date, evs]) => {
          const d = new Date(date + "T00:00:00");
          return (
            <div
              key={date}
              className="calendar-agenda-row"
              style={{
                display: "grid",
                gridTemplateColumns: "88px minmax(0, 1fr)",
                gap: 20,
                padding: "14px 0",
                borderBottom: "1px solid var(--glass-border)",
              }}
            >
              <div>
                <div
                  className="q-display"
                  style={{ fontSize: 18, color: "var(--text-primary)", lineHeight: 1 }}
                >
                  {d.getDate()}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
                  {d.toLocaleDateString("en-IN", { weekday: "short", month: "short" })}
                </div>
              </div>
              <div className="flex flex-col" style={{ gap: 8 }}>
                {evs.map((ev, i) => (
                  <AgendaLine key={i} event={ev} onOpenWorkflow={onOpenWorkflow} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AgendaLine({
  event,
  onOpenWorkflow,
}: {
  event: ScheduledRun;
  onOpenWorkflow: (workflowId: string) => void;
}): React.ReactElement {
  const tk = eventTypeKey(event);
  const isWorkflow = tk === "trigger.schedule" && event.workflow_id;
  const time = eventTime(event);
  const color = workflowColor(event.workflow_id);
  return (
    <button
      type="button"
      onClick={() => isWorkflow && onOpenWorkflow(event.workflow_id)}
      className="flex items-center"
      style={{
        gap: 10,
        padding: "6px 0",
        background: "transparent",
        border: "none",
        cursor: isWorkflow ? "pointer" : "default",
        textAlign: "left",
        width: "100%",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
        }}
      />
      <div
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 13.5,
          color: "var(--text-primary)",
          lineHeight: 1.4,
          minWidth: 0,
          flex: 1,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {event.workflow_name}
      </div>
      {time && (
        <span
          style={{
            fontSize: 11.5,
            color: "var(--text-tertiary)",
            fontFamily: "var(--font-mono)",
            flexShrink: 0,
          }}
        >
          {time}
        </span>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Small chrome bits
// ---------------------------------------------------------------------------

const ghostBtn: React.CSSProperties = {
  height: 28,
  padding: "0 12px",
  background: "transparent",
  border: "1px solid var(--glass-border)",
  borderRadius: "var(--radius-sm)",
  color: "var(--text-secondary)",
  fontFamily: "var(--font-ui)",
  fontSize: 12,
  fontWeight: 500,
  cursor: "pointer",
  transition: "color 0.2s var(--ease-quartr), border-color 0.2s var(--ease-quartr)",
};

function NavArrow({
  direction,
  onClick,
}: {
  direction: "prev" | "next";
  onClick: () => void;
}): React.ReactElement {
  const Icon = direction === "prev" ? ChevronLeft : ChevronRight;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={direction === "prev" ? "Previous month" : "Next month"}
      data-testid={direction === "prev" ? "prev-month" : "next-month"}
      className="inline-flex items-center justify-center"
      style={{
        width: 28,
        height: 28,
        background: "transparent",
        border: "none",
        borderRadius: "var(--radius-sm)",
        color: "var(--text-tertiary)",
        cursor: "pointer",
        transition: "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = "var(--text-primary)";
        e.currentTarget.style.background = "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = "var(--text-tertiary)";
        e.currentTarget.style.background = "transparent";
      }}
    >
      <Icon size={13} strokeWidth={2} aria-hidden="true" />
    </button>
  );
}
