"use client";

/**
 * AgentsTab — user's saved workflows (Agents).
 *
 * Per docs/UI_TABS_V1.md §1. Lists GET /api/workflows with filter chips.
 * Clicking a row opens AgentPanel for that workflow.
 */

import { useEffect, useState } from "react";
import { formatDistanceToNow, parseISO } from "date-fns";
import { AlertCircle, Bot, RefreshCw, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { createWorkflow, getWorkflow, listWorkflows } from "@/lib/api";
import { isError } from "@/lib/types";
import type { Workflow, WorkflowStatus, WorkflowSummary } from "@/lib/types";
import { DEMO_WORKFLOW } from "@/components/agent-panel/demo-workflow";

export type AgentsTabProps = {
  /** Called when a workflow is selected; parent mounts AgentPanel with the workflow. */
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

export function AgentsTab({ onOpenWorkflow }: AgentsTabProps): React.ReactElement {
  const [filter, setFilter] = useState<Filter>("all");
  const [state, setState] = useState<FetchState>({ kind: "loading" });
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);

  const load = (f: Filter): void => {
    setState({ kind: "loading" });
    const statusParam =
      f === "all" ? ["active", "paused", "draft"] : [f];
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
        // Refresh the list so the new agent appears
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
      .catch(() => {
        // Ignore — user can retry by clicking again
      })
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
      {state.kind === "loading" && <AgentsListSkeleton />}
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
        <ul className="divide-y rounded-xl border bg-card shadow-sm overflow-hidden" data-testid="agents-list">
          {state.items.map((wf) => (
            <li key={wf.id}>
              <AgentRow
                workflow={wf}
                isOpening={openingId === wf.id}
                onSelect={() => handleSelect(wf.id)}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AgentRow({
  workflow,
  isOpening,
  onSelect,
}: {
  workflow: WorkflowSummary;
  isOpening: boolean;
  onSelect: () => void;
}): React.ReactElement {
  const { label: statusLabel, variant: statusVariant } =
    STATUS_META[workflow.status] ?? { label: workflow.status, variant: "muted" as const };

  const lastRunAgo = workflow.last_run_at
    ? formatDistanceToNow(parseISO(workflow.last_run_at), { addSuffix: true })
    : null;

  const nextRunAt = workflow.next_run_at
    ? formatDistanceToNow(parseISO(workflow.next_run_at), { addSuffix: true })
    : null;

  return (
    <button
      type="button"
      className={cn(
        "flex w-full items-start gap-4 px-5 py-4 text-left",
        "hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        "transition-colors",
        isOpening && "opacity-60 pointer-events-none",
      )}
      onClick={onSelect}
      disabled={isOpening}
      aria-label={`Open agent: ${workflow.name}`}
      data-testid={`agent-row-${workflow.id}`}
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
        <Bot className="h-4 w-4 text-primary" aria-hidden="true" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-foreground">
            {workflow.name}
          </span>
          <Badge variant={statusVariant} className="shrink-0 text-[10px]">
            {statusLabel}
          </Badge>
        </div>

        {workflow.description && (
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {workflow.description}
          </p>
        )}

        <div className="mt-1 flex items-center gap-3">
          {lastRunAgo && (
            <span className="text-[11px] text-muted-foreground">
              Last run {lastRunAgo}
            </span>
          )}
          {nextRunAt && (
            <span className="text-[11px] text-muted-foreground">
              Next {nextRunAt}
            </span>
          )}
        </div>
      </div>
    </button>
  );
}

function AgentsListSkeleton(): React.ReactElement {
  return (
    <div className="space-y-2" data-testid="agents-loading">
      {Array.from({ length: 5 }).map((_, i) => (
        <Skeleton key={i} className="h-16 w-full rounded-xl" />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Style constants
// ---------------------------------------------------------------------------

type BadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "destructive"
  | "muted"
  | "outline"
  | "secondary"
  | "info";

const STATUS_META: Record<WorkflowStatus, { label: string; variant: BadgeVariant }> = {
  active: { label: "Active", variant: "success" },
  paused: { label: "Paused", variant: "warning" },
  draft: { label: "Draft", variant: "muted" },
  archived: { label: "Archived", variant: "muted" },
};
