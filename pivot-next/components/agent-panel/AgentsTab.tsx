"use client";

/**
 * AgentsTab — Quartr-style agent catalog card grid.
 *
 * Per docs/UI_TABS_V1.md §1 + Day 7 redesign (image 4).
 * Lists GET /api/workflows with filter chips. Clicking a card opens AgentPanel.
 *
 * Card design: file-folder style with:
 *   - Header bar: FILE NNN / QUANT|INCOME|etc + RISK pill
 *   - Serif title ending with a period
 *   - KEY:VALUE rows: METHOD / UNIVERSE / CADENCE / TURNOVER / MIN TICKET
 *   - Footer: VIEW AGENT link + CAGR placeholder
 */

import { useEffect, useState } from "react";
import { formatDistanceToNow, parseISO } from "date-fns";
import {
  AlertCircle,
  Bot,
  ExternalLink,
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
  { value: "archived", label: "Archived" },
];

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: WorkflowSummary[] };

// ---------------------------------------------------------------------------
// Category derivation (from name/description)
// ---------------------------------------------------------------------------

type Category = "QUANT" | "INCOME" | "TACTICAL" | "EVENT" | "PASSIVE";
type RiskLevel = "HIGH RISK" | "MEDIUM RISK" | "LOW RISK";

function deriveCategory(wf: WorkflowSummary): Category {
  const text = `${wf.name} ${wf.description ?? ""}`.toLowerCase();
  if (text.includes("dividend") || text.includes("income") || text.includes("yield")) return "INCOME";
  if (text.includes("event") || text.includes("earnings") || text.includes("ipo")) return "EVENT";
  if (text.includes("passive") || text.includes("index") || text.includes("etf")) return "PASSIVE";
  if (text.includes("tactical") || text.includes("swing") || text.includes("breakout")) return "TACTICAL";
  return "QUANT";
}

/** Derive risk: without full step data, use description heuristics. */
function deriveRisk(wf: WorkflowSummary): RiskLevel {
  const text = `${wf.name} ${wf.description ?? ""}`.toLowerCase();
  if (text.includes("approval") || text.includes("review")) return "MEDIUM RISK";
  if (text.includes("notify") || text.includes("fetch") || text.includes("monitor") || text.includes("watch")) return "LOW RISK";
  if (text.includes("buy") || text.includes("sell") || text.includes("order") || text.includes("trade")) return "HIGH RISK";
  return "MEDIUM RISK";
}

function riskColor(risk: RiskLevel): string {
  if (risk === "HIGH RISK") return "text-rose-700 dark:text-rose-400";
  if (risk === "MEDIUM RISK") return "text-amber-700 dark:text-amber-400";
  return "text-muted-foreground";
}

/** Derive METHOD from description (truncated) or name. */
function deriveMethod(wf: WorkflowSummary): string {
  if (wf.description) return wf.description.slice(0, 60) + (wf.description.length > 60 ? "…" : "");
  return wf.name;
}

/** Derive UNIVERSE heuristic from name/description. */
function deriveUniverse(wf: WorkflowSummary): string {
  const text = `${wf.name} ${wf.description ?? ""}`.toUpperCase();
  const nseMatch = text.match(/\b([A-Z]{2,12})\b/g);
  const knownIndices = ["NIFTY", "SENSEX", "BANKNIFTY"];
  for (const m of nseMatch ?? []) {
    if (knownIndices.includes(m)) return "NIFTY 50";
    if (m.length >= 4 && m.length <= 10 && !["EVERY", "WEEKDAY", "MARKET", "BUYING", "POWER", "EMAIL", "PRICE", "FETCH", "NOTIFY"].includes(m)) {
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
    <div className="flex flex-col gap-4" data-testid="agents-tab">
      {/* Filter chips */}
      <div className="flex items-center gap-2 flex-wrap" role="group" aria-label="Filter agents">
        {FILTERS.map((f) => (
          <Button
            key={f.value}
            variant={filter === f.value ? "default" : "outline"}
            size="sm"
            className="h-7 rounded-full px-3 text-xs"
            onClick={() => setFilter(f.value)}
            aria-pressed={filter === f.value}
            data-testid={`filter-${f.value}`}
          >
            {f.label}
          </Button>
        ))}
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
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
          data-testid="agents-list"
          role="list"
        >
          {state.items.map((wf, idx) => (
            <div key={wf.id} role="listitem">
              <AgentFileCard
                workflow={wf}
                seq={idx + 1}
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
// AgentFileCard — file-folder style
// ---------------------------------------------------------------------------

function AgentFileCard({
  workflow,
  seq,
  isOpening,
  onSelect,
}: {
  workflow: WorkflowSummary;
  seq: number;
  isOpening: boolean;
  onSelect: () => void;
}): React.ReactElement {
  const category = deriveCategory(workflow);
  const risk = deriveRisk(workflow);
  const method = deriveMethod(workflow);
  const universe = deriveUniverse(workflow);
  const cadence = deriveCadence(workflow);
  const lastRunAgo = workflow.last_run_at
    ? formatDistanceToNow(parseISO(workflow.last_run_at), { addSuffix: true })
    : null;

  return (
    <div
      className={cn(
        "flex flex-col rounded-xl border bg-card shadow-sm overflow-hidden",
        "hover:shadow-md transition-shadow",
        isOpening && "opacity-60",
      )}
      data-testid={`agent-card-${workflow.id}`}
    >
      {/* Header bar */}
      <div className="flex items-center justify-between border-b bg-muted/30 px-3.5 py-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          FILE {String(seq).padStart(3, "0")} / {category}
        </span>
        <span className={cn("text-[10px] font-semibold uppercase tracking-wide", riskColor(risk))}>
          {risk}
        </span>
      </div>

      {/* Body */}
      <div className="flex flex-col gap-3 p-4">
        {/* Serif title */}
        <h3 className="font-serif text-sm font-semibold leading-snug text-foreground">
          {workflow.name.endsWith(".") ? workflow.name : `${workflow.name}.`}
        </h3>

        {/* KV rows */}
        <div className="space-y-1">
          <CardKV label="METHOD" value={method} />
          <CardKV label="UNIVERSE" value={universe} />
          <CardKV label="CADENCE" value={cadence} />
          <CardKV label="TURNOVER" value="—" />
          <CardKV label="MIN TICKET" value="—" />
        </div>

        {/* Last run line */}
        {lastRunAgo && (
          <p className="text-[10px] text-muted-foreground">
            Last run {lastRunAgo}
          </p>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between border-t px-3.5 py-2.5">
        <button
          type="button"
          onClick={onSelect}
          disabled={isOpening}
          aria-label={`View agent: ${workflow.name}`}
          data-testid={`agent-row-${workflow.id}`}
          className={cn(
            "flex items-center gap-1 text-[11px] font-semibold text-primary",
            "hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            "disabled:opacity-50",
          )}
        >
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
          VIEW AGENT
        </button>
        <span className="text-[10px] text-muted-foreground">CAGR —</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card KV row
// ---------------------------------------------------------------------------

function CardKV({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div className="flex items-baseline gap-2">
      <span className="w-20 shrink-0 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="min-w-0 truncate text-[11px] text-foreground">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function AgentsGridSkeleton(): React.ReactElement {
  return (
    <div
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
      data-testid="agents-loading"
    >
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-52 w-full rounded-xl" />
      ))}
    </div>
  );
}
