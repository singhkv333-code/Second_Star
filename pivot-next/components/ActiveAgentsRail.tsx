"use client";

/**
 * ActiveAgentsRail — right-side "Active Agents" panel shown on the dashboard.
 *
 * Fetches GET /api/workflows (active + paused) and for each fetches:
 *   - GET /api/workflows/{id}/runs?limit=1     (last run summary)
 *   - GET /api/runs/{lastRunId}                (last run with steps)
 *
 * Card design (post-redesign): clean rounded surface with a category tag
 * chip and status pill in the header, the workflow name as the hero, and
 * a checklist of recent step events with right-aligned timestamps —
 * matches the visual language of IndicatorBacktestCard and the agent
 * step-list reference designs (soft borders, generous padding, no
 * loud category footer).
 */

import { useEffect, useState } from "react";
import { format, formatDistanceToNow, parseISO } from "date-fns";
import {
  Bot,
  Calendar,
  CheckCircle2,
  Circle,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { getRun, getWorkflow, listRuns, listWorkflows } from "@/lib/api";
import { isError } from "@/lib/types";
import type {
  Run,
  RunStep,
  Workflow,
  WorkflowSummary,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type AgentStatus = "RUNNING" | "BLOCKED" | "IDLE";

type AgentCard = {
  workflow: WorkflowSummary;
  agentStatus: AgentStatus;
  category: string;
  lastRun: Run | null;
};

type RailState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; cards: AgentCard[] };

type ActiveAgentsRailProps = {
  onOpenWorkflow: (workflow: Workflow) => void;
};

// ---------------------------------------------------------------------------
// Category derivation (from workflow name)
// ---------------------------------------------------------------------------

function deriveCategory(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("cash") || n.includes("sweep") || n.includes("fund")) return "CASH";
  if (n.includes("research") || n.includes("report") || n.includes("analyse") || n.includes("analyze")) return "RESEARCH";
  if (n.includes("risk") || n.includes("hedge")) return "RISK";
  if (n.includes("income") || n.includes("dividend")) return "INCOME";
  return "AGENT";
}

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

        // Fetch last run + step detail for each workflow in parallel.
        const cards = await Promise.all(
          workflows.map(async (wf): Promise<AgentCard> => {
            let agentStatus: AgentStatus = "IDLE";
            let lastRun: Run | null = null;

            try {
              const runsResult = await listRuns(wf.id, { limit: 1 });
              if (!isError(runsResult) && runsResult.data.items.length > 0) {
                const lastRunSummary = runsResult.data.items[0]!;
                if (lastRunSummary.status === "running" || lastRunSummary.status === "awaiting_approval") {
                  agentStatus = "RUNNING";
                } else if (lastRunSummary.status === "failed") {
                  agentStatus = "BLOCKED";
                }
                const runDetail = await getRun(lastRunSummary.id);
                if (!isError(runDetail)) {
                  lastRun = runDetail.data;
                }
              }
            } catch {
              // Ignore — IDLE fallback, lastRun stays null.
            }

            return {
              workflow: wf,
              agentStatus,
              category: deriveCategory(wf.name),
              lastRun,
            };
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
        <h2
          className="m-0"
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: "var(--weight-display)" as unknown as number,
            fontSize: 18,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
          }}
        >
          Active Agents
        </h2>
        {state.kind === "ok" && state.cards.length > 0 && (
          <button
            type="button"
            onClick={load}
            aria-label="Refresh agents"
            className="inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden={true} />
          </button>
        )}
      </div>

      {state.kind === "loading" && <AgentRailSkeleton />}

      {state.kind === "error" && (
        <div
          role="alert"
          className="rounded-2xl border border-border/50 bg-card px-4 py-4 text-center"
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
          className="rounded-2xl border border-border/50 bg-card px-4 py-6 text-center"
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
// AgentCardItem — clean, soft-bordered card with chip + title + status pill
// + step checklist. Matches the IndicatorBacktestCard design language.
// ---------------------------------------------------------------------------

function AgentCardItem({
  card,
  onOpen,
}: {
  card: AgentCard;
  onOpen: (workflow: Workflow) => void;
}): React.ReactElement {
  const { workflow, agentStatus, category, lastRun } = card;
  const [opening, setOpening] = useState(false);

  const handleOpen = async (): Promise<void> => {
    if (opening) return;
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

  const handleKey = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      void handleOpen();
    }
  };

  const checklistItems = buildChecklistItems(workflow, lastRun);

  return (
    <div
      data-testid={`agent-card-${workflow.id}`}
      role="button"
      tabIndex={0}
      aria-label={`Open agent: ${workflow.name}`}
      onClick={() => void handleOpen()}
      onKeyDown={handleKey}
      className={cn(
        "group flex cursor-pointer flex-col gap-4 rounded-2xl border border-border/50 bg-card px-5 py-5",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_8px_20px_-12px_rgba(15,23,42,0.08)]",
        "transition-colors hover:border-border focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        opening && "opacity-70",
      )}
    >
      {/* Header: category chip + status pill */}
      <div className="flex items-center justify-between gap-3">
        <CategoryChip category={category} label={categoryLabel(category)} />
        <StatusPill status={agentStatus} />
      </div>

      {/* Title — workflow name. Two-line clamp so long names don't blow
          up the card height. */}
      <h3
        className="line-clamp-2 m-0 text-[20px] leading-[1.2] font-semibold tracking-tight text-foreground"
      >
        {workflow.name}
      </h3>

      {/* Checklist — last 4 events. */}
      {checklistItems.length > 0 && (
        <ul className="m-0 flex flex-col gap-2.5 border-t border-border/40 pt-4">
          {checklistItems.map((item, i) => (
            <ChecklistRow key={i} item={item} />
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Category chip — soft tag, mirrors IndicatorBacktestCard's "Indicator
// Backtest" chip rhythm so the dashboard reads as a single design family.
// ---------------------------------------------------------------------------

function categoryChipClass(category: string): string {
  switch (category) {
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

function CategoryChip({
  category,
  label,
}: {
  category: string;
  label: string;
}): React.ReactElement {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-2.5 py-0.5 text-[11px] font-medium tracking-tight",
        categoryChipClass(category),
      )}
    >
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Status pill — matches the bench-delta pill in IndicatorBacktestDetail:
// rounded-full, soft tinted background, small dot, weight-tuned label.
// ---------------------------------------------------------------------------

function StatusPill({ status }: { status: AgentStatus }): React.ReactElement {
  // RUNNING uses the same #4CAF50 outline pill used everywhere else in the
  // agent widget family (WorkflowDraftCard.SavedState, InlineRunCard).
  if (status === "RUNNING") {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full border bg-transparent px-2.5 py-1 text-[11px] font-medium"
        style={{ borderColor: "#4CAF50", color: "#4CAF50" }}
      >
        <span
          aria-hidden={true}
          className="h-1.5 w-1.5 rounded-full"
          style={{
            background: "#4CAF50",
            animation: "pulse-quartr 1.6s ease-in-out infinite",
          }}
        />
        Active
      </span>
    );
  }
  const palette: Record<
    Exclude<AgentStatus, "RUNNING">,
    { bg: string; text: string; dot: string; label: string }
  > = {
    BLOCKED: {
      bg: "bg-rose-50 dark:bg-rose-500/10",
      text: "text-rose-700 dark:text-rose-300",
      dot: "bg-rose-500",
      label: "Blocked",
    },
    IDLE: {
      bg: "bg-muted",
      text: "text-muted-foreground",
      dot: "bg-muted-foreground/60",
      label: "Idle",
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
// Checklist
// ---------------------------------------------------------------------------

type ChecklistKind = "succeeded" | "failed" | "running" | "pending" | "info";

type ChecklistItem = {
  kind: ChecklistKind;
  label: string;
  timestamp: string;
};

function buildChecklistItems(
  workflow: WorkflowSummary,
  lastRun: Run | null,
): ChecklistItem[] {
  const items: ChecklistItem[] = [];

  if (lastRun) {
    // Most-recent-step-first: take the last 3 step events from the latest run.
    const recentSteps = [...lastRun.steps]
      .sort((a, b) => b.step_index - a.step_index)
      .slice(0, 3)
      .reverse();

    for (const step of recentSteps) {
      items.push({
        kind: stepKind(step),
        label: stepLabel(step),
        timestamp: formatStepTimestamp(step),
      });
    }
  }

  // Always append a "next" or "ongoing" row so the user knows what comes
  // next without opening the workflow.
  if (workflow.next_run_at) {
    items.push({
      kind: "pending",
      label: "Next run scheduled",
      timestamp: format(parseISO(workflow.next_run_at), "h:mma").toLowerCase(),
    });
  } else if (workflow.last_run_at) {
    items.push({
      kind: "info",
      label: "Awaiting trigger",
      timestamp: relativeShort(workflow.last_run_at),
    });
  } else {
    items.push({
      kind: "info",
      label: "Never run",
      timestamp: "—",
    });
  }

  return items;
}

function stepKind(step: RunStep): ChecklistKind {
  switch (step.status) {
    case "succeeded":
    case "skipped":
      return "succeeded";
    case "failed":
      return "failed";
    case "running":
    case "awaiting_approval":
      return "running";
    default:
      return "pending";
  }
}

function stepLabel(step: RunStep): string {
  // step_type strings look like "trigger.schedule" / "control.skip_if" /
  // "tool.place_order". Convert to a human-readable phrase.
  const segments = step.step_type.split(".");
  const tail = segments[segments.length - 1] ?? step.step_type;
  return tail
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatStepTimestamp(step: RunStep): string {
  const ts = step.finished_at ?? step.started_at;
  if (!ts) return "Pending";
  return format(parseISO(ts), "h:mma").toLowerCase();
}

function relativeShort(iso: string): string {
  return formatDistanceToNow(parseISO(iso), { addSuffix: true });
}

function ChecklistRow({ item }: { item: ChecklistItem }): React.ReactElement {
  return (
    <li className="flex items-center justify-between gap-3 text-[12.5px]">
      <span className="flex min-w-0 items-center gap-2.5">
        <ChecklistIcon kind={item.kind} />
        <span className="truncate text-foreground/85">{item.label}</span>
      </span>
      <span className="shrink-0 tabular-nums text-[11.5px] text-muted-foreground">
        {item.timestamp}
      </span>
    </li>
  );
}

function ChecklistIcon({ kind }: { kind: ChecklistKind }): React.ReactElement {
  switch (kind) {
    case "succeeded":
      return (
        <CheckCircle2
          className="h-3.5 w-3.5 shrink-0"
          strokeWidth={2.25}
          aria-hidden={true}
          style={{ color: "#4CAF50" }}
        />
      );
    case "failed":
      return (
        <XCircle
          className="h-3.5 w-3.5 shrink-0 text-rose-500"
          strokeWidth={2.25}
          aria-hidden={true}
        />
      );
    case "running":
      return (
        <Loader2
          className="h-3.5 w-3.5 shrink-0 animate-spin text-sky-500"
          strokeWidth={2.25}
          aria-hidden={true}
        />
      );
    case "pending":
      return (
        <Calendar
          className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70"
          strokeWidth={2}
          aria-hidden={true}
        />
      );
    default:
      return (
        <Circle
          className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50"
          strokeWidth={2}
          aria-hidden={true}
        />
      );
  }
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function AgentRailSkeleton(): React.ReactElement {
  return (
    <div className="flex flex-col gap-3" data-testid="rail-loading">
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton key={i} className="h-44 w-full rounded-2xl" />
      ))}
    </div>
  );
}
