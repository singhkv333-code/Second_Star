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
      className={cn(
        "relative my-1.5 w-full max-w-sm overflow-hidden rounded-xl",
        "border border-border/60",
        "bg-card/55 backdrop-blur-xl supports-[backdrop-filter]:bg-card/35",
        "shadow-[0_1px_0_rgba(255,255,255,0.05)_inset,0_12px_36px_-16px_rgba(0,0,0,0.55),0_2px_10px_-6px_rgba(0,0,0,0.3)]",
      )}
      data-testid="inline-run-card"
      role="region"
      aria-label={`Live run: ${workflowName}`}
    >
      {/* Top-edge sheen */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/15 to-transparent"
      />

      {/* Header */}
      <div className="flex items-start justify-between gap-2.5 px-3.5 pt-3 pb-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-full px-1.5 py-0",
                "text-[9px] font-medium uppercase tracking-wider",
                "bg-violet-400/10 text-violet-300 ring-1 ring-violet-400/30",
              )}
            >
              Run · {run.triggered_by}
            </span>
            <RunStatusPill status={run.status} />
          </div>
          <h3 className="mt-1 truncate text-[13px] font-semibold leading-snug">
            {runHeadline(run, workflowName)}
          </h3>
        </div>
        {isReconnecting && (
          <span
            data-testid="inline-run-reconnecting"
            className="inline-flex shrink-0 items-center gap-1 rounded-full bg-amber-400/10 px-1.5 py-0 text-[9px] font-medium text-amber-300 ring-1 ring-amber-400/30"
            role="status"
          >
            <Loader2 className="h-2 w-2 animate-spin" aria-hidden="true" />
            reconnecting
          </span>
        )}
      </div>

      {/* Step timeline — vertical track + status rings, mirroring the
          public.com "Activate your Agent" pattern. */}
      <ol
        className="relative border-t border-border/40 px-3.5 pt-3 pb-2 space-y-2"
        data-testid="inline-run-steps"
      >
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-[23px] top-5 bottom-5 w-px bg-gradient-to-b from-border/0 via-border/70 to-border/0"
        />
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
        <div className="border-t border-border/40 px-3.5 py-2">
          <Button
            size="sm"
            variant="ghost"
            className="h-6 w-full justify-between text-[10.5px]"
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
  const rightLabel = STEP_RIGHT_LABEL[status];

  return (
    <div
      className="relative flex items-center gap-2.5 pl-0"
      data-testid={`inline-step-${stepIndex}`}
      data-status={status}
    >
      {/* Timeline ring */}
      <span
        className={cn(
          "relative z-10 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full",
          presentation.ring,
        )}
        aria-hidden="true"
      >
        {status === "succeeded" ? (
          <CheckCircle2
            className="h-[14px] w-[14px] text-emerald-400"
            strokeWidth={2.5}
          />
        ) : status === "running" ? (
          <>
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            <span className="absolute inset-0 animate-ping rounded-full bg-emerald-400/40" />
          </>
        ) : status === "failed" ? (
          <XCircle className="h-[14px] w-[14px] text-rose-400" strokeWidth={2.5} />
        ) : status === "awaiting_approval" ? (
          <PauseCircle className="h-[14px] w-[14px] text-amber-300" strokeWidth={2.5} />
        ) : (
          <CircleDashed className="h-2.5 w-2.5 text-muted-foreground/50" />
        )}
      </span>
      {/* Body */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span
            className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-muted-foreground"
            aria-hidden="true"
          >
            <StepIcon name={def?.icon ?? "circle-dot"} className="h-2.5 w-2.5" />
          </span>
          <span
            className={cn(
              "truncate text-[11px] font-medium",
              status === "skipped" && "italic text-muted-foreground",
            )}
          >
            {def?.label ?? stepType}
          </span>
          <span
            className="shrink-0 rounded-full border border-border/60 bg-card/40 px-1 py-0 text-[8px] font-medium uppercase tracking-wider text-muted-foreground/80"
            aria-hidden="true"
          >
            {stepIndex + 1}
          </span>
        </div>
        {attempts > 1 && (
          <p className="mt-0.5 text-[9px] text-muted-foreground/80">
            {attempts} attempts
          </p>
        )}
      </div>
      {/* Right-aligned status label */}
      <span
        className={cn(
          "shrink-0 text-[9.5px] font-medium tabular-nums",
          presentation.rightText,
        )}
      >
        {rightLabel}
      </span>
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

// Style map per run-step status. Used to colour the timeline ring +
// the right-side status label. The ring class also encodes whether
// the cell needs a soft fill.
const STEP_TONE: Record<
  RunStepStatus,
  { ring: string; rightText: string }
> = {
  pending: {
    ring: "bg-card/60 ring-1 ring-border/60",
    rightText: "text-muted-foreground/80",
  },
  running: {
    ring: "bg-emerald-400/15 ring-2 ring-emerald-400/70",
    rightText: "text-emerald-300",
  },
  succeeded: {
    ring: "bg-emerald-400/10 ring-1 ring-emerald-400/40",
    rightText: "text-muted-foreground/80",
  },
  failed: {
    ring: "bg-rose-400/10 ring-1 ring-rose-400/50",
    rightText: "text-rose-300",
  },
  skipped: {
    ring: "bg-card/40 ring-1 ring-border/60",
    rightText: "text-muted-foreground/60",
  },
  awaiting_approval: {
    ring: "bg-amber-400/10 ring-1 ring-amber-400/50",
    rightText: "text-amber-300",
  },
};

// Human-friendly right-side label per status. Mirrors the
// public.com pattern ("Happening now", "Today, 3:35 PM",
// "Scheduled for 3:59 PM"). We don't have per-step timestamps
// from the run yet, so we fall back to status-shaped phrases.
const STEP_RIGHT_LABEL: Record<RunStepStatus, string> = {
  pending: "Upcoming",
  running: "Happening now",
  succeeded: "Done",
  failed: "Failed",
  skipped: "Skipped",
  awaiting_approval: "Awaiting approval",
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
