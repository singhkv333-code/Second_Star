"use client";

import { useState } from "react";
import { formatDistanceStrict } from "date-fns";
import {
  AlertCircle,
  CheckCircle2,
  CircleDashed,
  Loader2,
  PauseCircle,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StepIcon } from "@/components/agent-panel/step-icon";
import { findStepType } from "@/lib/mock-catalog";
import { decideApproval } from "@/lib/api";
import { toast } from "sonner";
import { useRunStream } from "@/lib/use-run-stream";
import type {
  Approval,
  Run,
  RunStep,
  RunStepStatus,
  StepTypeCatalog,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export type RunViewProps = {
  /** Run id to subscribe to. The hook wires up either the mock simulator or the real WS via getBackendSource(). */
  runId: string;
  catalog: StepTypeCatalog;
  /** Optional: render a back button + label for navigation. */
  onClose?: () => void;
};

export function RunView({ runId, catalog, onClose }: RunViewProps): React.ReactElement {
  const { run, isReconnecting, error, pendingApprovals } = useRunStream(runId);
  const [resolvedApprovalIds, setResolvedApprovalIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [approvalInFlight, setApprovalInFlight] = useState<string | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);

  const visibleApproval =
    pendingApprovals.find((a) => !resolvedApprovalIds.has(a.id)) ?? null;

  const resolveApproval = (id: string): void => {
    setResolvedApprovalIds((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  };

  /** Wire to real POST /api/approvals/{id}/decision */
  const handleApprovalDecision = async (
    approvalId: string,
    decision: "approved" | "rejected",
  ): Promise<void> => {
    setApprovalInFlight(approvalId);
    setApprovalError(null);
    const result = await decideApproval(approvalId, { decision });
    if ("error" in result) {
      setApprovalError(result.error.message);
      toast.error(result.error.message);
    } else {
      resolveApproval(approvalId);
      toast.success(decision === "approved" ? "Step approved" : "Step rejected");
    }
    setApprovalInFlight(null);
  };

  if (error && !run) {
    return (
      <div role="alert" className="flex h-full flex-col items-center justify-center px-8 text-center">
        <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
        <p className="text-sm font-medium">Couldn&apos;t load run</p>
        <p className="mt-1 max-w-sm text-xs text-muted-foreground">{error.message}</p>
        {onClose && (
          <Button className="mt-4" variant="outline" size="sm" onClick={onClose}>
            Back
          </Button>
        )}
      </div>
    );
  }

  if (!run) {
    return <RunViewSkeleton />;
  }

  return (
    <div className="flex h-full flex-col" data-testid="run-view">
      <header className="border-b px-6 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Run · {run.triggered_by} · {run.id.slice(0, 8)}
            </p>
            <h2 className="mt-1 truncate text-lg font-semibold tracking-tight">
              {runHeadline(run)}
            </h2>
          </div>
          <RunStatusBadge status={run.status} />
        </div>
        {isReconnecting && (
          <div
            data-testid="reconnecting-indicator"
            className="mt-3 inline-flex items-center gap-2 rounded-full bg-warning/10 px-2.5 py-0.5 text-[11px] font-medium text-warning"
            role="status"
          >
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            reconnecting…
          </div>
        )}
      </header>

      {approvalError && (
        <p
          role="alert"
          className="border-b border-destructive/20 bg-destructive/5 px-6 py-2 text-xs text-destructive"
        >
          {approvalError}
        </p>
      )}
      {visibleApproval && (
        <ApprovalBanner
          approval={visibleApproval}
          inFlight={approvalInFlight === visibleApproval.id}
          onApprove={() => { void handleApprovalDecision(visibleApproval.id, "approved"); }}
          onReject={() => { void handleApprovalDecision(visibleApproval.id, "rejected"); }}
        />
      )}

      <ol className="flex-1 space-y-3 overflow-y-auto px-6 py-5">
        {run.steps.map((step) => (
          <li key={step.step_index}>
            <RunStepCard step={step} catalog={catalog} />
          </li>
        ))}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

function RunStepCard({
  step,
  catalog,
}: {
  step: RunStep;
  catalog: StepTypeCatalog;
}): React.ReactElement {
  const def = findStepType(catalog, step.step_type);
  const [expanded, setExpanded] = useState(
    step.status === "failed" || step.status === "awaiting_approval",
  );

  const presentation = STATUS_STYLES[step.status];
  const duration =
    step.started_at && step.finished_at
      ? formatDistanceStrict(new Date(step.started_at), new Date(step.finished_at), {
          addSuffix: false,
        })
      : null;

  return (
    <div
      className={cn(
        "rounded-xl border bg-card shadow-sm transition-colors",
        presentation.border,
        presentation.bg,
      )}
      data-testid={`run-step-${step.step_index}`}
      data-status={step.status}
    >
      <button
        type="button"
        className="flex w-full items-start gap-3 px-4 py-3 text-left"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span
          className={cn(
            "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
            presentation.iconWrap,
          )}
          aria-hidden="true"
        >
          <StepIcon name={def?.icon ?? "help-circle"} className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Step {step.step_index + 1}
            </span>
            <span
              className={cn(
                "truncate text-[13px] font-medium",
                step.status === "skipped" && "italic text-muted-foreground",
              )}
            >
              {def?.label ?? step.step_type}
            </span>
          </div>
          <p className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
            <StatusIcon status={step.status} />
            <span className="capitalize">{statusLabel(step.status)}</span>
            {duration && <span aria-hidden="true">· {duration}</span>}
            {step.attempts > 1 && (
              <span aria-hidden="true">· {step.attempts} attempts</span>
            )}
          </p>
        </div>
      </button>

      {expanded && (
        <div
          className="space-y-2 border-t px-4 py-3"
          data-testid={`run-step-${step.step_index}-detail`}
        >
          {step.error_message && (
            <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {step.error_message}
            </p>
          )}
          {step.output && Object.keys(step.output).length > 0 && (
            <pre className="max-h-48 overflow-auto rounded-md bg-muted/60 px-3 py-2 font-mono text-[11px]">
              {JSON.stringify(step.output, null, 2)}
            </pre>
          )}
          {!step.error_message && !step.output && (
            <p className="text-[11px] text-muted-foreground">No output yet.</p>
          )}
        </div>
      )}
    </div>
  );
}

function ApprovalBanner({
  approval,
  inFlight,
  onApprove,
  onReject,
}: {
  approval: Approval;
  inFlight: boolean;
  onApprove: () => void;
  onReject: () => void;
}): React.ReactElement {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 border-b border-warning/30 bg-warning/10 px-6 py-3"
      data-testid="approval-banner"
    >
      <ShieldAlert className="mt-0.5 h-4 w-4 text-warning" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">Action requires your approval</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{approval.summary}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button size="sm" variant="ghost" onClick={onReject} disabled={inFlight}>
          {inFlight && <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden="true" />}
          Reject
        </Button>
        <Button size="sm" onClick={onApprove} disabled={inFlight}>
          {inFlight && <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden="true" />}
          Approve
        </Button>
      </div>
    </div>
  );
}

function RunStatusBadge({ status }: { status: Run["status"] }): React.ReactElement {
  const tone = RUN_STATUS_TONE[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium",
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
      return <Loader2 className="h-3 w-3 animate-spin text-info" aria-hidden="true" />;
    case "succeeded":
      return <CheckCircle2 className="h-3 w-3 text-success" aria-hidden="true" />;
    case "failed":
      return <XCircle className="h-3 w-3 text-destructive" aria-hidden="true" />;
    case "skipped":
      return <CircleDashed className="h-3 w-3 text-muted-foreground" aria-hidden="true" />;
    case "awaiting_approval":
      return <PauseCircle className="h-3 w-3 text-warning" aria-hidden="true" />;
    case "pending":
    default:
      return <CircleDashed className="h-3 w-3 text-muted-foreground" aria-hidden="true" />;
  }
}

function RunViewSkeleton(): React.ReactElement {
  return (
    <div className="flex h-full flex-col" data-testid="run-view-skeleton">
      <header className="space-y-2 border-b px-6 py-4">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-5 w-48" />
      </header>
      <div className="flex-1 space-y-3 px-6 py-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full rounded-xl" />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Style maps
// ---------------------------------------------------------------------------

type StepPresentation = {
  border: string;
  bg: string;
  iconWrap: string;
};

const STATUS_STYLES: Record<RunStepStatus, StepPresentation> = {
  pending: {
    border: "border-muted",
    bg: "bg-card",
    iconWrap: "bg-muted text-muted-foreground",
  },
  running: {
    border: "border-info/40",
    // Tailwind doesn't auto-detect dynamic class strings; this animation
    // string is in the source verbatim so the JIT picks it up.
    bg: "bg-info/5 animate-pulse",
    iconWrap: "bg-info/15 text-info",
  },
  succeeded: {
    border: "border-success/40",
    bg: "bg-success/5",
    iconWrap: "bg-success/15 text-success",
  },
  failed: {
    border: "border-destructive/50",
    bg: "bg-destructive/5",
    iconWrap: "bg-destructive/15 text-destructive",
  },
  skipped: {
    border: "border-muted",
    bg: "bg-muted/40",
    iconWrap: "bg-muted text-muted-foreground",
  },
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
    icon: <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />,
  },
  succeeded: {
    bg: "bg-success/10",
    text: "text-success",
    icon: <CheckCircle2 className="h-3 w-3" aria-hidden="true" />,
  },
  failed: {
    bg: "bg-destructive/10",
    text: "text-destructive",
    icon: <XCircle className="h-3 w-3" aria-hidden="true" />,
  },
  cancelled: {
    bg: "bg-muted",
    text: "text-muted-foreground",
    icon: <CircleDashed className="h-3 w-3" aria-hidden="true" />,
  },
  awaiting_approval: {
    bg: "bg-warning/10",
    text: "text-warning",
    icon: <PauseCircle className="h-3 w-3" aria-hidden="true" />,
  },
};

function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

function runHeadline(run: Run): string {
  if (run.status === "running") return "Run in progress";
  if (run.status === "awaiting_approval") return "Run paused for approval";
  if (run.status === "succeeded") return "Run completed";
  if (run.status === "failed") return "Run failed";
  if (run.status === "cancelled") return "Run cancelled";
  return "Run";
}
