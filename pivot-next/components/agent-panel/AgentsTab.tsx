"use client";

/**
 * AgentsTab — agent catalog grid.
 *
 * Card design mirrors the mini agent card in ActiveAgentsRail on the
 * dashboard: soft rounded-2xl surface with a category chip + status
 * pill in the header, the workflow name as the hero, the existing
 * deterministic sparkline preserved as the visual focal point, and
 * three muted KV rows beneath. The whole card is clickable.
 *
 * Single brand green (#4CAF50) is used for the Active status pill, in
 * lockstep with WorkflowDraftCard / InlineRunCard / ActiveAgentsRail.
 */

import { useEffect, useState } from "react";
import {
  AlertCircle,
  Bot,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { createWorkflow, getWorkflow, listWorkflows } from "@/lib/api";
import { isError } from "@/lib/types";
import type { Workflow, WorkflowStatus, WorkflowSummary } from "@/lib/types";
import { DEMO_WORKFLOW } from "@/components/agent-panel/demo-workflow";

const BRAND_GREEN = "#4CAF50";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type AgentsTabProps = {
  onOpenWorkflow: (workflow: Workflow) => void;
};

type Filter = "all" | WorkflowStatus;

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
  { value: "draft", label: "Draft" },
];

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: WorkflowSummary[] };

// ---------------------------------------------------------------------------
// Category derivation (from name/description). Matches the vocabulary used
// by ActiveAgentsRail so the same workflow reads as the same category on
// the dashboard and in the catalog grid.
// ---------------------------------------------------------------------------

type Category = "STRATEGY" | "INCOME" | "RISK" | "RESEARCH" | "CASH";

function deriveCategory(wf: WorkflowSummary): Category {
  const text = `${wf.name} ${wf.description ?? ""}`.toLowerCase();
  if (text.includes("cash") || text.includes("sweep") || text.includes("fund")) return "CASH";
  if (text.includes("research") || text.includes("report") || text.includes("analyse") || text.includes("analyze")) return "RESEARCH";
  if (text.includes("risk") || text.includes("hedge")) return "RISK";
  if (text.includes("income") || text.includes("dividend") || text.includes("yield")) return "INCOME";
  return "STRATEGY";
}

function categoryLabel(cat: Category): string {
  const MAP: Record<Category, string> = {
    STRATEGY: "Strategy",
    INCOME: "Income",
    RISK: "Risk",
    RESEARCH: "Research",
    CASH: "Fund Management",
  };
  return MAP[cat];
}

function categoryChipClass(cat: Category): string {
  switch (cat) {
    case "CASH":
      return "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300";
    case "RESEARCH":
      return "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300";
    case "RISK":
      return "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300";
    case "INCOME":
      return "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300";
    default:
      return "bg-muted text-muted-foreground";
  }
}

/** Derive UNIVERSE heuristic from name/description. Pulls the most
 *  plausible NSE ticker out of the workflow's free text. Tokens that
 *  are common English words (cadence/action verbs) are excluded so
 *  short tickers like TCS / RIL aren't shadowed by words like
 *  MONTHLY or WEEKLY. */
function deriveUniverse(wf: WorkflowSummary): string {
  const text = `${wf.name} ${wf.description ?? ""}`.toUpperCase();
  const nseMatch = text.match(/\b([A-Z]{2,12})\b/g);
  const knownIndices = ["NIFTY", "SENSEX", "BANKNIFTY"];
  // English-words / domain verbs that match the ticker regex but are
  // not real tickers. Add to this list rather than tightening the
  // length filter — TCS / SBI / RIL are 3-letter tickers we DO want.
  const NON_TICKER_WORDS = new Set([
    "EVERY", "WEEKDAY", "WEEKLY", "MONTHLY", "DAILY", "MONDAY", "TUESDAY",
    "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY",
    "MARKET", "BUYING", "POWER", "EMAIL", "PRICE", "FETCH", "NOTIFY",
    "MORNING", "AFTERNOON", "EVENING", "NIGHT",
    "AT", "ON", "IF", "THE", "AND", "FOR", "BUY", "SELL", "SIP",
    "LIMIT", "ORDER", "QUANTITY", "AMOUNT",
    "AM", "PM", "IST", "UTC",
  ]);
  for (const m of nseMatch ?? []) {
    if (knownIndices.includes(m)) return "NIFTY 50";
    if (m.length >= 3 && m.length <= 10 && !NON_TICKER_WORDS.has(m)) {
      return m;
    }
  }
  return "NSE 500";
}

/** Derive CADENCE from next_run_at / last_run_at availability. */
function deriveCadence(wf: WorkflowSummary): string {
  if (wf.next_run_at) return "scheduled";
  if (wf.last_run_at) return "manual";
  const text = `${wf.name} ${wf.description ?? ""}`.toLowerCase();
  if (text.includes("weekday") || text.includes("daily")) return "weekday";
  if (text.includes("weekly") || text.includes("monday")) return "weekly";
  if (text.includes("monthly")) return "monthly";
  if (text.includes("real-time") || text.includes("price") || text.includes("indicator")) return "real-time";
  return "manual";
}

// ---------------------------------------------------------------------------
// AgentsTab
// ---------------------------------------------------------------------------

export function AgentsTab({ onOpenWorkflow }: AgentsTabProps): React.ReactElement {
  const [filter, setFilter] = useState<Filter>("all");
  const [state, setState] = useState<FetchState>({ kind: "loading" });
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);

  const load = (f: Filter): void => {
    setState({ kind: "loading" });
    const statusParam = f === "all" ? ["active", "paused", "draft"] : [f];
    listWorkflows({ status: statusParam as WorkflowStatus[], limit: 50 })
      .then((result) => {
        if (isError(result)) {
          setState({ kind: "error", message: result.error.message });
          return;
        }
        setState({ kind: "ok", items: result.data.items });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  };

  useEffect(() => {
    load(filter);
  }, [filter]);

  const seedDemoAgent = (): void => {
    setSeeding(true);
    setSeedError(null);
    createWorkflow({
      name: DEMO_WORKFLOW.name,
      description: DEMO_WORKFLOW.description ?? undefined,
      single_instance: DEMO_WORKFLOW.single_instance,
      steps: DEMO_WORKFLOW.steps.map((s) => ({
        step_type: s.step_type,
        label: s.label,
        config: s.config,
      })),
    })
      .then((result) => {
        if (isError(result)) {
          setSeedError(result.error.message);
          return;
        }
        load(filter);
      })
      .catch((err: unknown) => {
        setSeedError(err instanceof Error ? err.message : "Network error");
      })
      .finally(() => setSeeding(false));
  };

  const handleSelect = (id: string): void => {
    setOpeningId(id);
    getWorkflow(id)
      .then((result) => {
        if (isError(result)) return;
        onOpenWorkflow(result.data);
      })
      .catch(() => {})
      .finally(() => setOpeningId(null));
  };

  return (
    <div className="flex flex-col" style={{ gap: 18 }} data-testid="agents-tab">
      {/* Page heading — Quartr serif */}
      <h1
        className="q-serif"
        style={{
          fontSize: 22,
          letterSpacing: "-0.025em",
          color: "var(--text-primary)",
          margin: 0,
        }}
      >
        Active Agents
      </h1>

      {/* Filter chips — Quartr pills */}
      <div
        className="flex flex-wrap items-center"
        style={{ gap: 6 }}
        role="group"
        aria-label="Filter agents"
      >
        {FILTERS.map((f) => {
          const active = filter === f.value;
          return (
            <button
              key={f.value}
              type="button"
              onClick={() => setFilter(f.value)}
              aria-pressed={active}
              data-testid={`filter-${f.value}`}
              style={{
                padding: "6px 12px",
                background: active ? "var(--text-primary)" : "transparent",
                border: `1px solid ${active ? "var(--text-primary)" : "var(--glass-border)"}`,
                borderRadius: "var(--radius-pill)",
                color: active ? "var(--bg-primary)" : "var(--text-secondary)",
                fontFamily: "var(--font-ui)",
                fontSize: 12,
                fontWeight: 500,
                cursor: "pointer",
                transition: "all 0.2s var(--ease-quartr)",
              }}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      {/* States */}
      {state.kind === "loading" && <AgentsGridSkeleton />}

      {state.kind === "error" && (
        <div
          role="alert"
          className="flex flex-col items-center justify-center py-12 text-center"
          data-testid="agents-error"
        >
          <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
          <p className="text-sm font-medium">Couldn&apos;t load agents</p>
          <p className="mt-1 text-xs text-muted-foreground">{state.message}</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-4"
            onClick={() => load(filter)}
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}

      {state.kind === "ok" && state.items.length === 0 && (
        <div
          className="flex flex-col items-center justify-center py-12 text-center"
          data-testid="agents-empty"
        >
          <Bot className="mb-3 h-8 w-8 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm font-medium">No agents yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Start a chat to propose one, or try the example below.
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-4 gap-1.5"
            onClick={seedDemoAgent}
            disabled={seeding}
            data-testid="create-example-agent-btn"
          >
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            {seeding ? "Creating..." : "Create example agent"}
          </Button>
          {seedError && (
            <p
              className="mt-2 text-xs text-destructive"
              role="alert"
              data-testid="seed-error"
            >
              {seedError}
            </p>
          )}
        </div>
      )}

      {state.kind === "ok" && state.items.length > 0 && (
        <div
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
          data-testid="agents-list"
          role="list"
        >
          {state.items.map((wf) => (
            <div key={wf.id} role="listitem" className="h-full">
              <AgentMiniCard
                workflow={wf}
                isOpening={openingId === wf.id}
                onSelect={() => handleSelect(wf.id)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentMiniCard — same shape as ActiveAgentsRail.AgentCardItem.
//   - Soft rounded-2xl surface, hairline border, low-key drop shadow
//   - Category chip + status pill row at the top
//   - Workflow name as the hero (line-clamp-2)
//   - Existing deterministic sparkline preserved as the visual focal point
//   - Three muted KV rows beneath: Method / Universe / Cadence
//   - Whole card is clickable; agent-row-{id} testid points at the
//     clickable root so the existing test still works
// ---------------------------------------------------------------------------

function AgentMiniCard({
  workflow,
  isOpening,
  onSelect,
}: {
  workflow: WorkflowSummary;
  isOpening: boolean;
  onSelect: () => void;
}): React.ReactElement {
  const category = deriveCategory(workflow);
  const universe = deriveUniverse(workflow);
  const cadence = deriveCadence(workflow);
  const description = workflow.description ?? null;

  const handleKey = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect();
    }
  };

  return (
    <div
      data-testid={`agent-card-${workflow.id}`}
      role="button"
      tabIndex={0}
      aria-label={`Open agent: ${workflow.name}`}
      onClick={onSelect}
      onKeyDown={handleKey}
      className={cn(
        "group flex h-full cursor-pointer flex-col gap-4 rounded-2xl border border-border/50 bg-card px-5 py-5",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_8px_20px_-12px_rgba(15,23,42,0.08)]",
        "transition-colors hover:border-border focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        isOpening && "opacity-70",
      )}
    >
      {/* Header: category chip + status pill */}
      <div className="flex items-center justify-between gap-3">
        <span
          className={cn(
            "inline-flex items-center rounded-md px-2.5 py-0.5 text-[11px] font-medium tracking-tight",
            categoryChipClass(category),
          )}
        >
          {categoryLabel(category)}
        </span>
        <WorkflowStatusPill status={workflow.status} />
      </div>

      {/* Title */}
      <h3 className="m-0 line-clamp-2 text-[20px] leading-[1.2] font-semibold tracking-tight text-foreground">
        {workflow.name}
      </h3>

      {/* Sparkline — preserved verbatim. */}
      <Sparkline seed={workflow.id} positive={true} />

      {/* KV rows — same muted rhythm as the checklist in
          ActiveAgentsRail.AgentCardItem. */}
      <div className="mt-auto flex flex-col gap-1.5 border-t border-border/40 pt-3 text-[12px]">
        <KvRow label="Method" value={description ?? "Manual workflow"} />
        <KvRow label="Universe" value={universe} />
        <KvRow label="Cadence" value={cadence} />
      </div>

      {/* Hidden interaction sentinel — preserves the existing
          `agent-row-{id}` testid contract from the previous file-card
          design while keeping the whole card clickable. */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        disabled={isOpening}
        aria-label={`View agent: ${workflow.name}`}
        data-testid={`agent-row-${workflow.id}`}
        className="sr-only"
      >
        View agent
      </button>
    </div>
  );
}

function KvRow({
  label,
  value,
}: {
  label: string;
  value: string;
}): React.ReactElement {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="shrink-0 text-muted-foreground/70">{label}</span>
      <span className="min-w-0 truncate text-right text-foreground/85">
        {value}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// WorkflowStatusPill — single brand-green Active pill, muted Paused / Draft.
// Same shape used everywhere else in the agent widget family.
// ---------------------------------------------------------------------------

function WorkflowStatusPill({
  status,
}: {
  status: WorkflowStatus;
}): React.ReactElement {
  if (status === "active") {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full border bg-transparent px-2.5 py-1 text-[11px] font-medium"
        style={{ borderColor: BRAND_GREEN, color: BRAND_GREEN }}
      >
        <span
          aria-hidden={true}
          className="h-1.5 w-1.5 rounded-full"
          style={{
            background: BRAND_GREEN,
            animation: "pulse-quartr 1.6s ease-in-out infinite",
          }}
        />
        Active
      </span>
    );
  }
  const palette: Record<
    Exclude<WorkflowStatus, "active">,
    { dot: string; label: string; bg: string; text: string }
  > = {
    paused: {
      dot: "bg-amber-500",
      label: "Paused",
      bg: "bg-amber-50 dark:bg-amber-500/10",
      text: "text-amber-700 dark:text-amber-300",
    },
    draft: {
      dot: "bg-muted-foreground/60",
      label: "Draft",
      bg: "bg-muted",
      text: "text-muted-foreground",
    },
    archived: {
      dot: "bg-muted-foreground/40",
      label: "Archived",
      bg: "bg-muted",
      text: "text-muted-foreground/80",
    },
  };
  const p = palette[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium",
        p.bg,
        p.text,
      )}
    >
      <span
        aria-hidden={true}
        className={cn("h-1.5 w-1.5 rounded-full", p.dot)}
      />
      {p.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Sparkline — deterministic seeded series, mirrors FQ's StrategyList::Sparkline
// ---------------------------------------------------------------------------

function Sparkline({ seed, positive }: { seed: string; positive: boolean }): React.ReactElement {
  const POINTS = 40;
  const W = 280;
  const H = 56;
  const numericSeed = String(seed || "x")
    .split("")
    .reduce((a, c) => a + c.charCodeAt(0), 0);

  let v = 50;
  const series: number[] = [];
  for (let i = 0; i < POINTS; i++) {
    const r = Math.sin((i + numericSeed) * 0.41) + Math.cos((i + numericSeed) * 0.19);
    v += (positive ? 0.45 : -0.4) + r * 1.4;
    series.push(v);
  }

  const min = Math.min(...series);
  const max = Math.max(...series);
  const span = Math.max(1, max - min);

  const xAt = (i: number): number => (i / (POINTS - 1)) * W;
  const yAt = (val: number): number => H - ((val - min) / span) * (H - 6) - 3;

  const linePath = series
    .map((val, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(val).toFixed(1)}`)
    .join(" ");
  const areaPath = `${linePath} L ${W} ${H} L 0 ${H} Z`;
  const gradId = `spark-${numericSeed}`;
  const stroke = positive ? "var(--color-profit)" : "var(--color-loss)";
  const fillTop = positive ? "rgba(16, 185, 129, 0.22)" : "rgba(239, 68, 68, 0.22)";
  const fillBot = positive ? "rgba(16, 185, 129, 0)" : "rgba(239, 68, 68, 0)";

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      preserveAspectRatio="none"
      style={{ display: "block", marginTop: 14 }}
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={fillTop} />
          <stop offset="100%" stopColor={fillBot} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path
        d={linePath}
        fill="none"
        stroke={stroke}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function AgentsGridSkeleton(): React.ReactElement {
  return (
    <div
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
      data-testid="agents-loading"
    >
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-64 w-full rounded-2xl" />
      ))}
    </div>
  );
}
