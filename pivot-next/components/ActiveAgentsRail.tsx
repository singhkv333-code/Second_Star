"use client";

/**
 * ActiveAgentsRail — right-side "Active Agents" panel shown on the dashboard.
 *
 * Fetches GET /api/workflows (active + paused) and for each fetches
 * GET /api/workflows/{id}/runs?limit=1 to derive status pill.
 *
 * Status derivation:
 *   RUNNING  — workflow active + last run status is "running" or "awaiting_approval"
 *   BLOCKED  — workflow active + last run status is "failed"
 *   IDLE     — workflow active, no in-flight run
 */

import { useEffect, useState } from "react";
import { formatDistanceToNow, parseISO } from "date-fns";
import { Bot, ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { getWorkflow, listRuns, listWorkflows } from "@/lib/api";
import { isError } from "@/lib/types";
import type { Workflow, WorkflowSummary } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type AgentStatus = "RUNNING" | "BLOCKED" | "IDLE";

type AgentCard = {
  workflow: WorkflowSummary;
  agentStatus: AgentStatus;
  seq: number;
  category: string;
};

type RailState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; cards: AgentCard[] };

type ActiveAgentsRailProps = {
  onOpenWorkflow: (workflow: Workflow) => void;
};

// ---------------------------------------------------------------------------
// Category derivation (from step_type prefix patterns)
// ---------------------------------------------------------------------------

/** Derive a display category from a workflow summary (no steps available).
 *  Falls back to "AGENT" since WorkflowSummary doesn't include steps. */
function deriveCategory(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("cash") || n.includes("sweep") || n.includes("fund")) return "CASH";
  if (n.includes("research") || n.includes("report") || n.includes("analyse") || n.includes("analyze")) return "RESEARCH";
  if (n.includes("risk") || n.includes("hedge")) return "RISK";
  if (n.includes("income") || n.includes("dividend")) return "INCOME";
  return "AGENT";
}

/** Derive a human-readable category pill label. */
function categoryLabel(cat: string): string {
  const MAP: Record<string, string> = {
    CASH: "Fund Management",
    RESEARCH: "Research",
    RISK: "Risk",
    INCOME: "Income",
    AGENT: "Strategy",
  };
  return MAP[cat] ?? "Strategy";
}

/** Category pill color classes. */
function categoryColor(cat: string): string {
  const MAP: Record<string, string> = {
    CASH: "bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-400",
    RESEARCH: "bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-400",
    RISK: "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400",
    INCOME: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400",
    AGENT: "bg-muted text-muted-foreground",
  };
  return MAP[cat] ?? "bg-muted text-muted-foreground";
}

// ---------------------------------------------------------------------------
// ActiveAgentsRail
// ---------------------------------------------------------------------------

export function ActiveAgentsRail({
  onOpenWorkflow,
}: ActiveAgentsRailProps): React.ReactElement {
  const [state, setState] = useState<RailState>({ kind: "loading" });

  const load = (): void => {
    setState({ kind: "loading" });

    listWorkflows({ status: ["active", "paused"], limit: 10 })
      .then(async (result) => {
        if (isError(result)) {
          setState({ kind: "error", message: result.error.message });
          return;
        }

        const workflows = result.data.items;
        if (workflows.length === 0) {
          setState({ kind: "ok", cards: [] });
          return;
        }

        // Fetch last run for each workflow to derive status
        const cards = await Promise.all(
          workflows.map(async (wf, idx): Promise<AgentCard> => {
            let agentStatus: AgentStatus = "IDLE";
            try {
              const runsResult = await listRuns(wf.id, { limit: 1 });
              if (!isError(runsResult) && runsResult.data.items.length > 0) {
                const lastRun = runsResult.data.items[0]!;
                if (lastRun.status === "running" || lastRun.status === "awaiting_approval") {
                  agentStatus = "RUNNING";
                } else if (lastRun.status === "failed") {
                  agentStatus = "BLOCKED";
                }
              }
            } catch {
              // Ignore — IDLE fallback
            }

            const cat = deriveCategory(wf.name);
            return { workflow: wf, agentStatus, seq: idx + 1, category: cat };
          }),
        );

        setState({ kind: "ok", cards });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <aside
      className="flex flex-col gap-3"
      aria-label="Active Agents"
      data-testid="active-agents-rail"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold tracking-tight text-foreground">
          Active Agents
        </h2>
        {state.kind !== "loading" && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={load}
            aria-label="Refresh agents"
          >
            <RefreshCw className="h-3.5 w-3.5 text-muted-foreground" aria-hidden={true} />
          </Button>
        )}
      </div>

      {state.kind === "loading" && <AgentRailSkeleton />}

      {state.kind === "error" && (
        <div
          role="alert"
          className="rounded-xl border bg-card px-4 py-4 text-center"
          data-testid="rail-error"
        >
          <p className="text-xs text-muted-foreground">{state.message}</p>
          <Button
            variant="ghost"
            size="sm"
            className="mt-2 h-6 text-xs"
            onClick={load}
          >
            Retry
          </Button>
        </div>
      )}

      {state.kind === "ok" && state.cards.length === 0 && (
        <div
          className="rounded-xl border bg-card px-4 py-6 text-center"
          data-testid="rail-empty"
        >
          <Bot className="mx-auto mb-2 h-6 w-6 text-muted-foreground" aria-hidden={true} />
          <p className="text-xs text-muted-foreground">No active agents yet.</p>
        </div>
      )}

      {state.kind === "ok" &&
        state.cards.map((card) => (
          <AgentCardItem
            key={card.workflow.id}
            card={card}
            onOpen={onOpenWorkflow}
          />
        ))}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// AgentCardItem
// ---------------------------------------------------------------------------

function AgentCardItem({
  card,
  onOpen,
}: {
  card: AgentCard;
  onOpen: (workflow: Workflow) => void;
}): React.ReactElement {
  const { workflow, agentStatus, seq, category } = card;
  const [opening, setOpening] = useState(false);

  const handleOpen = async (): Promise<void> => {
    setOpening(true);
    try {
      const result = await getWorkflow(workflow.id);
      if (!isError(result)) {
        onOpen(result.data);
      }
    } catch {
      // Ignore
    } finally {
      setOpening(false);
    }
  };

  const lastRunAgo = workflow.last_run_at
    ? formatDistanceToNow(parseISO(workflow.last_run_at), { addSuffix: true })
    : null;

  const nextRun = workflow.next_run_at
    ? formatDistanceToNow(parseISO(workflow.next_run_at), { addSuffix: true })
    : null;

  return (
    <div
      className="flex flex-col gap-2.5 rounded-xl border bg-card px-4 py-3.5"
      data-testid={`agent-card-${workflow.id}`}
    >
      {/* Header line */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          AGENT {String(seq).padStart(2, "0")} / {category}
        </span>
        <StatusPill status={agentStatus} />
      </div>

      {/* Workflow name */}
      <h3 className="font-serif text-sm font-semibold leading-snug text-foreground">
        {workflow.name.endsWith(".") ? workflow.name : `${workflow.name}.`}
      </h3>

      {/* Description */}
      {workflow.description && (
        <p className="line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
          {workflow.description}
        </p>
      )}

      {/* Key:value rows */}
      <div className="space-y-0.5">
        <KVRow label="MODEL" value="Pivot Engine" />
        <KVRow label="LAST" value={lastRunAgo ?? "Never"} />
        <KVRow
          label="NEXT"
          value={nextRun ?? (workflow.next_run_at === null ? "On trigger" : "Manual")}
        />
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between pt-0.5">
        <button
          type="button"
          onClick={handleOpen}
          disabled={opening}
          className="flex items-center gap-1 text-[11px] font-semibold text-primary hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
          aria-label={`View agent: ${workflow.name}`}
          data-testid={`view-agent-${workflow.id}`}
        >
          <ExternalLink className="h-3 w-3" aria-hidden={true} />
          VIEW AGENT
        </button>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-[10px] font-medium",
            categoryColor(category),
          )}
        >
          {categoryLabel(category)}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status pill
// ---------------------------------------------------------------------------

function StatusPill({ status }: { status: AgentStatus }): React.ReactElement {
  if (status === "RUNNING") {
    return (
      <span className="flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold uppercase text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" aria-hidden={true} />
        Running
      </span>
    );
  }
  if (status === "BLOCKED") {
    return (
      <span className="flex items-center gap-1 rounded-full bg-rose-50 px-2 py-0.5 text-[10px] font-semibold uppercase text-rose-700 dark:bg-rose-950/50 dark:text-rose-400">
        <span className="h-1.5 w-1.5 rounded-full bg-rose-500" aria-hidden={true} />
        Blocked
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase text-muted-foreground">
      <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50" aria-hidden={true} />
      Idle
    </span>
  );
}

// ---------------------------------------------------------------------------
// KV row
// ---------------------------------------------------------------------------

function KVRow({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div className="flex items-baseline gap-2">
      <span className="w-12 shrink-0 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="truncate text-[11px] text-foreground">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function AgentRailSkeleton(): React.ReactElement {
  return (
    <div className="space-y-2.5" data-testid="rail-loading">
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton key={i} className="h-40 w-full rounded-xl" />
      ))}
    </div>
  );
}
