"use client";

/**
 * InlineRunCard — live-run checklist embedded directly in the chat thread.
 * Mounted after WorkflowDraftCard's "Save & activate" triggers a manual
 * run; subscribes to the run's WS stream and shows step-by-step progress.
 *
 * Design language matches WorkflowDraftCard: rounded-3xl card, soft shadow,
 * sky tag chip + status pill in the header, large title, tile-style step
 * rows, single brand green (#4CAF50) for positive state, ShieldAlert
 * disclaimer footer. Step tiles use the same skeleton as the draft card —
 * just with live status sub-lines (Running… / Succeeded · Xs / Failed /
 * Awaiting / Skipped) instead of a static state.
 */

import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Loader2,
  PauseCircle,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { StepIcon } from "@/components/agent-panel/step-icon";
import { useRunStream } from "@/lib/use-run-stream";
import { useStepCatalog } from "@/components/agent-panel/use-step-catalog";
import { findStepType } from "@/lib/mock-catalog";
import { cn } from "@/lib/utils";
import type { Run, RunStep, RunStepStatus, StepTypeCatalog } from "@/lib/types";

// Single brand green — kept in lockstep with WorkflowDraftCard.
const BRAND_GREEN = "#4CAF50";

export type InlineRunCardProps = {
  runId: string;
  workflowName: string;
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
        className="mb-2 mt-1 w-full max-w-[388px] rounded-3xl border border-destructive/40 bg-destructive/5 px-6 py-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]"
      >
        <p className="inline-flex items-center gap-1.5 text-[13px] font-medium text-destructive">
          <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
          Couldn&apos;t connect to live run
        </p>
        <p className="mt-1.5 text-[12px] text-muted-foreground">{error.message}</p>
      </div>
    );
  }

  if (!run || catalogState.status !== "ready") {
    return <InlineRunCardSkeleton />;
  }

  const isFailed = run.status === "failed";

  return (
    <div
      data-testid="inline-run-card"
      role="region"
      aria-label={`Live run: ${workflowName}`}
      className={cn(
        "mb-2 mt-1 w-full max-w-[388px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "transition-all duration-500 ease-out",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
      )}
      style={{
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      <div className="flex flex-col">
        <div className="flex flex-col gap-3 px-5 pt-4 pb-4">
          {/* HEADER — Run · trigger chip on the left, run-status pill +
              optional reconnecting indicator on the right. Tightened to
              match WorkflowDraftCard's header rhythm one-for-one so the
              card stays the *same height* as the draft / saved cards
              (no jump in the chat thread on activation → live run). */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <span className="inline-flex items-center rounded-md bg-sky-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
                Run · {run.triggered_by}
              </span>
              <div className="flex items-center gap-1.5">
                {isReconnecting && (
                  <span
                    data-testid="inline-run-reconnecting"
                    role="status"
                    className="inline-flex items-center gap-1 rounded-full bg-amber-100/80 px-2 py-0.5 text-[10.5px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
                  >
                    <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden="true" />
                    reconnecting
                  </span>
                )}
                <RunStatusPill status={run.status} />
              </div>
            </div>

            {/* TITLE — runHeadline keeps the existing copy contract
                (`Running · …`, `Completed · …`, `Failed · …`, etc).
                Scaled to match the draft card's h3 so titles wrap at
                the same line count and reserve the same vertical slot. */}
            <h3
              className="text-[15px] leading-[1.25] font-semibold tracking-tight text-foreground"
              style={{
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {runHeadline(run, workflowName)}
            </h3>
          </div>

          {/* STEP LIST — same compact rhythm as DraftBody. Status info
              now rides as a right-aligned indicator on each row instead
              of an extra sub-line, so the rows stay single-line and the
              card's total height matches DraftBody / SavedState. */}
          <ol
            className="m-0 flex flex-col gap-1.5"
            data-testid="inline-run-steps"
          >
            {run.steps.map((step, idx) => (
              <InlineStepRow
                key={step.step_index}
                step={step}
                index={idx}
                catalog={catalogState.catalog}
              />
            ))}
          </ol>

          {/* Secondary action — same ghost text-link rhythm as the draft
              card's Backtest / Open in editor row. */}
          {onOpenFullView && (
            <div className="flex items-center justify-center text-[11.5px]">
              <button
                type="button"
                onClick={onOpenFullView}
                data-testid="inline-run-open-full"
                className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
              >
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
                View full run
              </button>
            </div>
          )}
        </div>

        {/* DISCLAIMER — same padding/tone as the draft surface so the
            footer strip matches across states (no height jump). */}
        <div
          className={cn(
            "flex items-center gap-1.5 border-t border-border/40 px-5 py-1.5",
            isFailed
              ? "bg-rose-50/40 dark:bg-rose-500/[0.04]"
              : "bg-amber-50/40 dark:bg-amber-500/[0.04]",
          )}
        >
          <ShieldAlert
            className={cn(
              "h-3 w-3 shrink-0",
              isFailed
                ? "text-rose-600/80 dark:text-rose-400/80"
                : "text-amber-600/80 dark:text-amber-400/80",
            )}
            aria-hidden="true"
          />
          <p
            className={cn(
              "text-[10.5px] leading-snug",
              isFailed
                ? "text-rose-700/90 dark:text-rose-300/90"
                : "text-amber-700/90 dark:text-amber-300/90",
            )}
          >
            This is automation of your instructions, not financial advice.
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// InlineStepRow — tile-style row, mirrors WorkflowDraftCard.DraftStepRow.
// Status sub-line under the step label tells the live story:
//   succeeded  → "Succeeded · Xs"
//   running    → "Running…"
//   pending    → no sub-line (the dim chrome is signal enough)
//   failed     → "Failed"
//   skipped    → "Skipped"
//   awaiting   → "Awaiting approval"
// ---------------------------------------------------------------------------

function InlineStepRow({
  step,
  index,
  catalog,
}: {
  step: RunStep;
  index: number;
  catalog: StepTypeCatalog;
}): React.ReactElement {
  const def = findStepType(catalog, step.step_type);
  const label = def?.label ?? step.step_type;
  const iconName = def?.icon ?? "circle-dot";

  const isSucceeded = step.status === "succeeded";
  const isRunning = step.status === "running";
  const isFailed = step.status === "failed";
  const isAwaiting = step.status === "awaiting_approval";
  const isSkipped = step.status === "skipped";
  const isPending = step.status === "pending";

  // Tile-level styling — succeeded uses brand green, running uses a
  // softer brand-green pulse, failed uses rose, awaiting uses amber.
  // Pending/skipped keep the neutral tile shell.
  const tileStyle: React.CSSProperties = (() => {
    if (isSucceeded || isRunning) {
      // No border on active tiles — just the soft tint.
      return { backgroundColor: `${BRAND_GREEN}14` };
    }
    return {};
  })();

  // Active rows (succeeded / running) skip the outline — they read as
  // soft tinted tiles instead of bordered cards so the column doesn't
  // feel like 4 stacked sub-cards. Pre-run, failed, and awaiting
  // states keep their border so the state still announces itself.
  const tileClass = cn(
    "flex items-center gap-2.5 rounded-xl px-2.5 py-1.5 transition-colors",
    !isSucceeded && !isRunning && !isFailed && !isAwaiting && "border border-border/50 bg-card",
    isFailed && "border border-rose-500/40 bg-rose-50/60 dark:border-rose-500/30 dark:bg-rose-500/[0.06]",
    isAwaiting && "border border-amber-500/40 bg-amber-50/60 dark:border-amber-500/30 dark:bg-amber-500/[0.06]",
    isSkipped && "opacity-60",
  );

  const iconChipStyle: React.CSSProperties =
    isSucceeded || isRunning
      ? { backgroundColor: `${BRAND_GREEN}26`, color: BRAND_GREEN }
      : {};

  const iconChipClass = cn(
    "flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
    !isSucceeded && !isRunning && !isFailed && !isAwaiting && "bg-muted/70 text-muted-foreground",
    isFailed && "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400",
    isAwaiting && "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400",
  );

  // Computed elapsed/attempts text — preserved here as the indicator's
  // accessible title so the step's timing info isn't lost when the
  // sub-line collapses into a single-icon indicator.
  const seconds = elapsedSeconds(step);
  const statusTooltip = (() => {
    switch (step.status) {
      case "succeeded":
        return [
          "Succeeded",
          seconds !== null ? `${seconds}s` : null,
          step.attempts > 1 ? `${step.attempts} attempts` : null,
        ]
          .filter(Boolean)
          .join(" · ");
      case "running":
        return "Running…";
      case "failed":
        return [
          "Failed",
          step.attempts > 1 ? `${step.attempts} attempts` : null,
        ]
          .filter(Boolean)
          .join(" · ");
      case "awaiting_approval":
        return "Awaiting approval";
      case "skipped":
        return "Skipped";
      default:
        return undefined;
    }
  })();

  return (
    <li
      data-testid={`inline-step-${step.step_index}`}
      data-status={step.status}
      className={tileClass}
      style={{
        animation: `stepIn-quartr 320ms cubic-bezier(0.22, 1, 0.36, 1) both`,
        animationDelay: `${index * 50}ms`,
        ...tileStyle,
      }}
    >
      <span aria-hidden="true" className={iconChipClass} style={iconChipStyle}>
        <StepIcon name={iconName} className="h-3.5 w-3.5" />
      </span>

      {/* Step label — single-line. Status info collapses into a small
          right-aligned indicator below so the row height matches
          DraftStepRow (no extra sub-line eating vertical space). */}
      <div className="flex min-w-0 flex-1 items-center">
        <span
          className={cn(
            "truncate text-[12.5px] font-medium tracking-tight text-foreground",
            isSkipped && "italic text-muted-foreground",
          )}
        >
          {label}
        </span>
      </div>

      <InlineStepStatusIndicator
        step={step}
        brandGreen={BRAND_GREEN}
        tooltip={statusTooltip}
      />
    </li>
  );
}

/** Right-aligned status icon — replaces the old sub-line so the row
 *  stays single-line. Each state gets a tone-matched icon; the
 *  human-readable status (including timing / attempt count) rides as
 *  `title` and aria-label so the data isn't lost. */
function InlineStepStatusIndicator({
  step,
  brandGreen,
  tooltip,
}: {
  step: RunStep;
  brandGreen: string;
  tooltip?: string;
}): React.ReactElement | null {
  const iconBase = "h-3.5 w-3.5 shrink-0";
  switch (step.status) {
    case "succeeded":
      return (
        <CheckCircle2
          className={iconBase}
          strokeWidth={2.25}
          style={{ color: brandGreen }}
          aria-label={tooltip ?? "Succeeded"}
        >
          <title>{tooltip ?? "Succeeded"}</title>
        </CheckCircle2>
      );
    case "running":
      return (
        <Loader2
          className={cn(iconBase, "animate-spin")}
          strokeWidth={2.25}
          style={{ color: brandGreen }}
          aria-label="Running"
        />
      );
    case "failed":
      return (
        <XCircle
          className={cn(iconBase, "text-rose-700 dark:text-rose-400")}
          strokeWidth={2.25}
          aria-label={tooltip ?? "Failed"}
        />
      );
    case "awaiting_approval":
      return (
        <PauseCircle
          className={cn(iconBase, "text-amber-700 dark:text-amber-400")}
          strokeWidth={2.25}
          aria-label="Awaiting approval"
        />
      );
    case "skipped":
      return (
        <span
          aria-label="Skipped"
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/50"
        />
      );
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// RunStatusPill — succeeded uses the same #4CAF50 solid pill as the
// WorkflowDraftCard "Active" pill; everything else gets a soft tinted
// pill in the appropriate tone.
// ---------------------------------------------------------------------------

function RunStatusPill({ status }: { status: Run["status"] }): React.ReactElement {
  if (status === "succeeded") {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full border bg-transparent px-2.5 py-1 text-[11px] font-medium"
        style={{ borderColor: BRAND_GREEN, color: BRAND_GREEN }}
      >
        <CheckCircle2 className="h-2.5 w-2.5 shrink-0" strokeWidth={3} aria-hidden="true" />
        succeeded
      </span>
    );
  }
  if (status === "running") {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full border bg-transparent px-2.5 py-1 text-[11px] font-medium"
        style={{ borderColor: BRAND_GREEN, color: BRAND_GREEN }}
      >
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 rounded-full"
          style={{
            background: BRAND_GREEN,
            animation: "pulse-quartr 1.6s ease-in-out infinite",
          }}
        />
        running
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-100 px-2.5 py-1 text-[11px] font-medium text-rose-700 dark:bg-rose-500/15 dark:text-rose-300">
        <XCircle className="h-2.5 w-2.5" aria-hidden="true" />
        failed
      </span>
    );
  }
  if (status === "awaiting_approval") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-2.5 py-1 text-[11px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
        <PauseCircle className="h-2.5 w-2.5" aria-hidden="true" />
        awaiting
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-[11px] font-medium text-muted-foreground">
      <span
        aria-hidden="true"
        className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50"
      />
      {statusLabel(status)}
    </span>
  );
}

function InlineRunCardSkeleton(): React.ReactElement {
  return (
    <div
      data-testid="inline-run-skeleton"
      className="mb-2 mt-1 w-full max-w-[388px] rounded-3xl border border-border/50 bg-card px-5 py-4 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]"
    >
      <div className="flex items-center justify-between">
        <Skeleton className="h-5 w-20 rounded-md" />
        <Skeleton className="h-6 w-16 rounded-full" />
      </div>
      <Skeleton className="mt-2 h-5 w-3/4 rounded-md" />
      <div className="mt-3 space-y-1.5">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full rounded-xl" />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function elapsedSeconds(step: RunStep): number | null {
  if (!step.started_at || !step.finished_at) return null;
  const ms = new Date(step.finished_at).getTime() - new Date(step.started_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  return Math.max(0, Math.round(ms / 1000));
}

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
