"use client";

/**
 * InlineRunCard — public.com-style live-run checklist embedded directly
 * in the chat thread. Mounted after WorkflowDraftCard's "Save & activate"
 * triggers a manual run; subscribes to the run's WS stream and shows
 * step-by-step progress with the same status styling as RunView, but
 * compact (no header chrome, no expand panes, no approval banner —
 * those live in the full agent panel).
 *
 * Reference: Image #3 in the v1 design conversation.
 */

import {
  CheckCircle2,
  CircleDashed,
  ExternalLink,
  Loader2,
  PauseCircle,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StepIcon } from "@/components/agent-panel/step-icon";
import { useRunStream } from "@/lib/use-run-stream";
import { useStepCatalog } from "@/components/agent-panel/use-step-catalog";
import { findStepType } from "@/lib/mock-catalog";
import { cn } from "@/lib/utils";
import type { Run, RunStepStatus, StepTypeCatalog } from "@/lib/types";

export type InlineRunCardProps = {
  runId: string;
  workflowName: string;
  /** Optional: parent passes this to open the full agent panel for the workflow. */
  onOpenFullView?: () => void;
};

export function InlineRunCard({
  runId,
  workflowName,
  onOpenFullView,
}: InlineRunCardProps): React.ReactElement {
  const { run, isReconnecting, error } = useRunStream(runId);
  const catalogState = useStepCatalog();

  if (error && !run) {
    return (
      <div
        role="alert"
        data-testid="inline-run-error"
        className="my-2 w-full max-w-md rounded-xl border border-destructive/40 bg-destructive/5 p-4"
      >
        <p className="text-sm font-medium text-destructive">
          Couldn&apos;t connect to live run
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{error.message}</p>
      </div>
    );
  }

  if (!run || catalogState.status !== "ready") {
    return <InlineRunCardSkeleton />;
  }

  return (
    <div
      className="my-2 w-full max-w-md rounded-xl border bg-card shadow-sm overflow-hidden"
      data-testid="inline-run-card"
      role="region"
      aria-label={`Live run: ${workflowName}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3 px-4 pt-4 pb-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Badge
              variant="outline"
              className="text-[10px] px-1.5 py-0 uppercase tracking-wide"
            >
              Run · {run.triggered_by}
            </Badge>
            <RunStatusPill status={run.status} />
          </div>
          <h3 className="mt-1 truncate text-sm font-semibold leading-snug">
            {runHeadline(run, workflowName)}
          </h3>
        </div>
        {isReconnecting && (
          <span
            data-testid="inline-run-reconnecting"
            className="inline-flex shrink-0 items-center gap-1 rounded-full bg-warning/10 px-2 py-0.5 text-[10px] font-medium text-warning"
            role="status"
          >
            <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden="true" />
            reconnecting
          </span>
        )}
      </div>

      {/* Step checklist */}
      <ol className="border-t px-4 py-2.5 space-y-1.5" data-testid="inline-run-steps">
        {run.steps.map((step) => (
          <li key={step.step_index}>
            <InlineStepRow
              stepIndex={step.step_index}
              stepType={step.step_type}
              status={step.status}
              attempts={step.attempts}
              catalog={catalogState.catalog}
            />
          </li>
        ))}
      </ol>

      {/* CTA */}
      {onOpenFullView && (
        <div className="border-t px-4 py-2.5">
          <Button
            size="sm"
            variant="ghost"
            className="h-7 w-full justify-between text-[11px]"
            onClick={onOpenFullView}
            data-testid="inline-run-open-full"
          >
            <span>View full run</span>
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
          </Button>
        </div>
      )}
    </div>
  );
}

function InlineStepRow({
  stepIndex,
  stepType,
  status,
  attempts,
  catalog,
}: {
  stepIndex: number;
  stepType: string;
  status: RunStepStatus;
  attempts: number;
  catalog: StepTypeCatalog;
}): React.ReactElement {
  const def = findStepType(catalog, stepType);
  const presentation = STEP_TONE[status];

  return (
    <div
      className={cn(
        "flex items-center gap-2.5 rounded-lg border px-3 py-2",
        presentation.border,
        presentation.bg,
      )}
      data-testid={`inline-step-${stepIndex}`}
      data-status={status}
    >
      <span
        className={cn(
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
          presentation.iconWrap,
        )}
        aria-hidden="true"
      >
        <StepIcon name={def?.icon ?? "circle-dot"} className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="rounded-full bg-muted px-1.5 py-0 text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
            Step {stepIndex + 1}
          </span>
          <span
            className={cn(
              "truncate text-xs font-medium",
              status === "skipped" && "italic text-muted-foreground",
            )}
          >
            {def?.label ?? stepType}
          </span>
        </div>
        <p className="mt-0.5 flex items-center gap-1 text-[10px] text-muted-foreground">
          <StatusIcon status={status} />
          <span className="capitalize">{statusLabel(status)}</span>
          {attempts > 1 && <span aria-hidden="true">· {attempts} attempts</span>}
        </p>
      </div>
    </div>
  );
}

function RunStatusPill({ status }: { status: Run["status"] }): React.ReactElement {
  const tone = RUN_STATUS_TONE[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-1.5 py-0 text-[10px] font-medium",
        tone.bg,
        tone.text,
      )}
    >
      {tone.icon}
      <span className="capitalize">{statusLabel(status)}</span>
    </span>
  );
}

function StatusIcon({ status }: { status: RunStepStatus }): React.ReactElement {
  switch (status) {
    case "running":
      return <Loader2 className="h-2.5 w-2.5 animate-spin text-info" aria-hidden="true" />;
    case "succeeded":
      return <CheckCircle2 className="h-2.5 w-2.5 text-success" aria-hidden="true" />;
    case "failed":
      return <XCircle className="h-2.5 w-2.5 text-destructive" aria-hidden="true" />;
    case "awaiting_approval":
      return <PauseCircle className="h-2.5 w-2.5 text-warning" aria-hidden="true" />;
    case "skipped":
    case "pending":
    default:
      return <CircleDashed className="h-2.5 w-2.5 text-muted-foreground" aria-hidden="true" />;
  }
}

function InlineRunCardSkeleton(): React.ReactElement {
  return (
    <div
      className="my-2 w-full max-w-md rounded-xl border bg-card p-4 shadow-sm"
      data-testid="inline-run-skeleton"
    >
      <Skeleton className="h-3 w-24" />
      <Skeleton className="mt-2 h-4 w-44" />
      <div className="mt-3 space-y-1.5">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-full rounded-lg" />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Style maps — kept local to this file so the inline card can stay smaller
// than RunView's full-screen palette
// ---------------------------------------------------------------------------

const STEP_TONE: Record<
  RunStepStatus,
  { border: string; bg: string; iconWrap: string }
> = {
  pending: { border: "border-muted", bg: "bg-card", iconWrap: "bg-muted text-muted-foreground" },
  running: { border: "border-info/40", bg: "bg-info/5", iconWrap: "bg-info/15 text-info" },
  succeeded: { border: "border-success/40", bg: "bg-success/5", iconWrap: "bg-success/15 text-success" },
  failed: { border: "border-destructive/50", bg: "bg-destructive/5", iconWrap: "bg-destructive/15 text-destructive" },
  skipped: { border: "border-muted", bg: "bg-muted/40", iconWrap: "bg-muted text-muted-foreground" },
  awaiting_approval: {
    border: "border-warning/50",
    bg: "bg-warning/5",
    iconWrap: "bg-warning/15 text-warning",
  },
};

const RUN_STATUS_TONE: Record<
  Run["status"],
  { bg: string; text: string; icon: React.ReactElement }
> = {
  running: {
    bg: "bg-info/10",
    text: "text-info",
    icon: <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden="true" />,
  },
  succeeded: {
    bg: "bg-success/10",
    text: "text-success",
    icon: <CheckCircle2 className="h-2.5 w-2.5" aria-hidden="true" />,
  },
  failed: {
    bg: "bg-destructive/10",
    text: "text-destructive",
    icon: <XCircle className="h-2.5 w-2.5" aria-hidden="true" />,
  },
  cancelled: {
    bg: "bg-muted",
    text: "text-muted-foreground",
    icon: <CircleDashed className="h-2.5 w-2.5" aria-hidden="true" />,
  },
  awaiting_approval: {
    bg: "bg-warning/10",
    text: "text-warning",
    icon: <PauseCircle className="h-2.5 w-2.5" aria-hidden="true" />,
  },
};

function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

function runHeadline(run: Run, fallbackName: string): string {
  if (run.status === "running") return `Running · ${fallbackName}`;
  if (run.status === "awaiting_approval") return `Paused · ${fallbackName}`;
  if (run.status === "succeeded") return `Completed · ${fallbackName}`;
  if (run.status === "failed") return `Failed · ${fallbackName}`;
  if (run.status === "cancelled") return `Cancelled · ${fallbackName}`;
  return fallbackName;
}
