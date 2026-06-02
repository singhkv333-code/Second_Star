"use client";

/**
 * WorkflowDraftCard — inline chat card rendered when the chatbot's
 * propose_workflow tool returns `_render_hint: "workflow_draft_card"`.
 *
 * Per docs/HANDOFF.md §5 and docs/ARCHITECTURE.md §10.
 *
 * Design: single calm surface with three breathing zones (no internal
 * dividers), step list as the hero, one primary CTA pill, secondary
 * actions as ghost text-links. Steps stagger-fade in on mount; the
 * saved state cross-fades to a confirmation layout.
 */

import { useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Check,
  CheckCircle2,
  History,
  Loader2,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { StepIcon } from "@/components/agent-panel/step-icon";
import {
  activateWorkflow,
  backtestDraftWorkflow,
  createWorkflow,
  runWorkflow,
  type BacktestDraftEligible,
} from "@/lib/api";
import { isError } from "@/lib/types";
import {
  IndicatorBacktestCard,
  type IndicatorBacktestPayload,
} from "@/components/chat/IndicatorBacktestCard";
import { NewsStepRow } from "@/components/chat/steps/NewsStepRow";
import { IpoStepRow } from "@/components/chat/steps/IpoStepRow";
import type { NewsStepConfig } from "@/lib/news-types";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type DraftStep = {
  step_type: string;
  label: string | null;
  config: Record<string, unknown>;
};

export type WorkflowDraft = {
  name: string;
  description: string;
  steps: DraftStep[];
  rationale: string;
  warnings: string[];
  _render_hint: "workflow_draft_card";
};

export type WorkflowDraftCardProps = {
  draft: WorkflowDraft;
  onOpenEditor: (draft: WorkflowDraft) => void;
  onActivatedAndRunning?: (info: {
    workflowId: string;
    workflowName: string;
    runId: string;
  }) => void;
};

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; workflowId: string; workflowName: string }
  | { kind: "error"; message: string };

type BacktestState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "ineligible"; reason: string }
  | { kind: "ready"; payload: IndicatorBacktestPayload; warnings: string[] }
  | { kind: "error"; message: string };

const CATEGORY_ICON: Record<string, string> = {
  trigger: "calendar-clock",
  fetch: "wallet",
  condition: "git-branch",
  action: "shopping-cart",
  notify: "send",
  control: "skip-forward",
};

function stepIconName(stepType: string): string {
  const prefix = stepType.split(".")[0] ?? "";
  return CATEGORY_ICON[prefix] ?? "circle-dot";
}

// All steps render in the chat-side card now — the row layout was
// compacted (smaller icon chip, tighter padding, smaller label) so the
// full 5-step demo workflow fits in one screen alongside the side editor.
const MAX_VISIBLE_STEPS = 5;

// ---------------------------------------------------------------------------
// WorkflowDraftCard
// ---------------------------------------------------------------------------

export function WorkflowDraftCard({
  draft,
  onOpenEditor,
  onActivatedAndRunning,
}: WorkflowDraftCardProps): React.ReactElement {
  const visibleSteps = draft.steps.slice(0, MAX_VISIBLE_STEPS);
  const hiddenCount = draft.steps.length - visibleSteps.length;
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const [backtestState, setBacktestState] = useState<BacktestState>({
    kind: "idle",
  });
  const [showWhy, setShowWhy] = useState(false);

  const handleBacktest = async (): Promise<void> => {
    setBacktestState({ kind: "running" });
    const res = await backtestDraftWorkflow({
      name: draft.name,
      description: draft.description ?? null,
      steps: draft.steps.map((s) => ({
        step_type: s.step_type,
        label: s.label,
        config: s.config,
      })),
      period: "5y",
    });
    if (isError(res)) {
      setBacktestState({
        kind: "error",
        message: res.error.message ?? "Backtest failed",
      });
      return;
    }
    if (!res.data.eligible) {
      setBacktestState({
        kind: "ineligible",
        reason: res.data.reason,
      });
      return;
    }
    const payload = res.data as unknown as IndicatorBacktestPayload;
    setBacktestState({
      kind: "ready",
      payload,
      warnings: (res.data as BacktestDraftEligible).warnings,
    });
  };

  const handleSaveAndActivate = async (): Promise<void> => {
    setSaveState({ kind: "saving" });
    const created = await createWorkflow({
      name: draft.name,
      description: draft.description ?? null,
      single_instance: true,
      steps: draft.steps.map((s, idx) => ({
        step_index: idx,
        step_type: s.step_type,
        label: s.label,
        config: s.config,
      })),
    });
    if (isError(created)) {
      setSaveState({
        kind: "error",
        message: created.error.message ?? "Failed to save workflow",
      });
      return;
    }
    const activated = await activateWorkflow(created.data.id);
    if (isError(activated)) {
      setSaveState({
        kind: "error",
        message: `Saved as draft, but activation failed: ${
          activated.error.message ?? "unknown error"
        }`,
      });
      return;
    }
    setSaveState({
      kind: "saved",
      workflowId: activated.data.id,
      workflowName: activated.data.name,
    });

    if (onActivatedAndRunning) {
      const ran = await runWorkflow(activated.data.id);
      if (!isError(ran)) {
        onActivatedAndRunning({
          workflowId: activated.data.id,
          workflowName: activated.data.name,
          runId: ran.data.run_id,
        });
      }
    }
  };

  const isSaved = saveState.kind === "saved";
  const isSaving = saveState.kind === "saving";

  return (
    <div
      data-testid="workflow-draft-card"
      role="region"
      aria-label={`Agent proposal: ${draft.name}`}
      className={cn(
        "my-2 w-full max-w-[440px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
        "transition-all duration-500 ease-out",
        isSaved && "shadow-[0_1px_2px_rgba(76,175,80,0.08),0_18px_36px_-18px_rgba(76,175,80,0.22)]",
      )}
      style={{
        // Cards animate in from below on mount.
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      {isSaved ? (
        <SavedState
          workflowName={(saveState as { workflowName: string }).workflowName}
          steps={visibleSteps}
          backtestState={backtestState}
          onOpenEditor={() => onOpenEditor(draft)}
          onBacktest={() => void handleBacktest()}
        />
      ) : (
        <DraftBody
          draft={draft}
          visibleSteps={visibleSteps}
          hiddenCount={hiddenCount}
          showWhy={showWhy}
          onToggleWhy={() => setShowWhy((v) => !v)}
          isSaving={isSaving}
          saveError={saveState.kind === "error" ? saveState.message : null}
          backtestState={backtestState}
          onSaveAndActivate={() => void handleSaveAndActivate()}
          onOpenEditor={() => onOpenEditor(draft)}
          onBacktest={() => void handleBacktest()}
        />
      )}

      {backtestState.kind === "ready" && (
        <div
          className="px-2 pb-2 pt-0"
          data-testid="backtest-draft-result"
          style={{
            animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
          }}
        >
          <IndicatorBacktestCard payload={backtestState.payload} />
          {backtestState.warnings.length > 0 && (
            <div className="px-4 pt-2 space-y-1">
              {backtestState.warnings.map((w, i) => (
                <p key={i} className="text-[11px] text-muted-foreground">
                  Note: {w}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Draft body (idle / saving / error states)
// ---------------------------------------------------------------------------

function DraftBody({
  draft,
  visibleSteps,
  hiddenCount,
  showWhy,
  onToggleWhy,
  isSaving,
  saveError,
  backtestState,
  onSaveAndActivate,
  onOpenEditor,
  onBacktest,
}: {
  draft: WorkflowDraft;
  visibleSteps: DraftStep[];
  hiddenCount: number;
  showWhy: boolean;
  onToggleWhy: () => void;
  isSaving: boolean;
  saveError: string | null;
  backtestState: BacktestState;
  onSaveAndActivate: () => void;
  onOpenEditor: () => void;
  onBacktest: () => void;
}): React.ReactElement {
  const hasContext = !!(draft.description || draft.rationale);
  const hasWarnings = draft.warnings.length > 0;

  return (
    <div className="flex flex-col">
      <div className="flex flex-col gap-3 px-5 pt-4 pb-4">
      {/* HEADER — chip on the left, optional warning indicator + status on
          the right. Title sits below on its own line for breathing room. */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center rounded-md bg-sky-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
            Agent
          </span>
          <div className="flex items-center gap-1.5">
            {hasWarnings && (
              <WarningIndicator warnings={draft.warnings} />
            )}
            <span className="inline-flex items-center gap-1.5 text-[10.5px] font-medium text-muted-foreground">
              <span
                aria-hidden="true"
                className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50"
              />
              Draft
            </span>
          </div>
        </div>

        <h3 className="text-[15px] leading-[1.25] font-semibold tracking-tight text-foreground">
          {draft.name}
        </h3>

        {hasContext && (
          <div className="flex flex-col gap-1.5">
            {draft.description && (
              <p
                className="text-[12px] leading-snug text-muted-foreground"
                style={{
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                  overflow: "hidden",
                }}
              >
                {draft.description}
              </p>
            )}
            {draft.rationale && (
              <button
                type="button"
                onClick={onToggleWhy}
                className="inline-flex w-fit items-center gap-1 text-[11px] font-medium text-muted-foreground/80 transition-colors hover:text-foreground"
              >
                <Sparkles className="h-3 w-3 shrink-0" aria-hidden="true" />
                {showWhy ? "Hide reasoning" : "Why this?"}
              </button>
            )}
            {showWhy && draft.rationale && (
              <p
                className="rounded-xl bg-muted/60 px-3 py-2 text-[11.5px] leading-relaxed text-muted-foreground"
                style={{
                  animation: "draftCardIn-quartr 240ms cubic-bezier(0.22, 1, 0.36, 1) both",
                }}
              >
                {draft.rationale}
              </p>
            )}
          </div>
        )}
      </div>

      {/* STEP LIST — hero zone, tile-style, staggered fade-in. */}
      <ol
        className="m-0 flex flex-col gap-1.5"
        data-testid="draft-step-timeline"
      >
        {visibleSteps.map((step, idx) => (
          <DraftStepRow key={idx} step={step} index={idx} />
        ))}
        {hiddenCount > 0 && (
          <li
            className="px-3 pt-1 text-[11.5px] text-muted-foreground/70"
            style={{
              animation: `stepIn-quartr 320ms cubic-bezier(0.22, 1, 0.36, 1) both`,
              animationDelay: `${visibleSteps.length * 50}ms`,
            }}
          >
            +{hiddenCount} more step{hiddenCount > 1 ? "s" : ""}
          </li>
        )}
      </ol>

      {/* CTA RAIL — one primary pill, secondary actions as ghost links
          beneath. No 3-button grid, no border-top. */}
      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={onSaveAndActivate}
          disabled={isSaving}
          data-testid="save-activate-button"
          className={cn(
            "inline-flex h-9 w-full items-center justify-center gap-1.5 rounded-full bg-primary text-[12.5px] font-medium tracking-tight text-primary-foreground transition-all",
            "hover:bg-primary/90 active:scale-[0.98]",
            "disabled:cursor-not-allowed disabled:opacity-70",
          )}
        >
          {isSaving ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              <span>Activating…</span>
            </>
          ) : (
            <>
              <span>Save &amp; activate</span>
              <ArrowUpRight className="h-4 w-4 shrink-0" aria-hidden="true" />
            </>
          )}
        </button>

        <div className="flex items-center justify-center gap-1 text-[11.5px]">
          <button
            type="button"
            onClick={onBacktest}
            disabled={backtestState.kind === "running"}
            data-testid="backtest-draft-button"
            className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:opacity-60"
          >
            {backtestState.kind === "running" ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            ) : (
              <History className="h-3 w-3" aria-hidden="true" />
            )}
            {backtestState.kind === "running"
              ? "Running…"
              : backtestState.kind === "ready"
                ? "Re-run backtest"
                : "Backtest"}
          </button>
          <span className="text-muted-foreground/40">·</span>
          <button
            type="button"
            onClick={onOpenEditor}
            data-testid="open-in-editor-button"
            className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            Open in editor
          </button>
        </div>

        {saveError && (
          <p
            role="alert"
            data-testid="workflow-save-error"
            className="rounded-lg bg-destructive/10 px-3 py-2 text-[11.5px] text-destructive"
            style={{
              animation: "draftCardIn-quartr 240ms cubic-bezier(0.22, 1, 0.36, 1) both",
            }}
          >
            {saveError}
          </p>
        )}
        {backtestState.kind === "ineligible" && (
          <p
            role="status"
            data-testid="backtest-ineligible"
            className="rounded-lg bg-muted px-3 py-2 text-[11.5px] text-muted-foreground"
          >
            <span className="font-medium">Can&apos;t backtest this shape:</span>{" "}
            {backtestState.reason}
          </p>
        )}
        {backtestState.kind === "error" && (
          <p
            role="alert"
            data-testid="backtest-draft-error"
            className="rounded-lg bg-destructive/10 px-3 py-2 text-[11.5px] text-destructive"
          >
            {backtestState.message}
          </p>
        )}
      </div>
      </div>

      {/* DISCLAIMER — quiet, full-bleed footer strip with a hairline top
          border. Soft amber tint to signal "advisory", not destructive. */}
      <div className="flex items-center gap-1.5 border-t border-border/40 bg-amber-50/40 px-5 py-1.5 dark:bg-amber-500/[0.04]">
        <ShieldAlert
          className="h-3 w-3 shrink-0 text-amber-600/80 dark:text-amber-400/80"
          aria-hidden="true"
        />
        <p className="text-[10.5px] leading-snug text-amber-700/90 dark:text-amber-300/90">
          This is automation of your instructions, not financial advice.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Saved state — celebratory cross-fade. Reuses the same card chrome so the
// transition feels like the card resolves, not replaces.
// ---------------------------------------------------------------------------

function SavedState({
  workflowName,
  steps,
  backtestState,
  onOpenEditor,
  onBacktest,
}: {
  workflowName: string;
  steps: DraftStep[];
  backtestState: BacktestState;
  onOpenEditor: () => void;
  onBacktest: () => void;
}): React.ReactElement {
  return (
    <div data-testid="workflow-saved" className="flex flex-col">
      <div className="flex flex-col gap-5 px-6 pt-6 pb-5">
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center rounded-md bg-sky-100 px-2.5 py-0.5 text-[11px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
          Agent
        </span>
        <span
          data-testid="agent-active-pill"
          className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium bg-transparent"
          style={{ borderColor: "#4CAF50", color: "#4CAF50" }}
        >
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: "#4CAF50",
              animation: "pulse-quartr 1.6s ease-in-out infinite",
            }}
          />
          Active
        </span>
      </div>

      <div className="flex items-start gap-3">
        <Check
          aria-hidden="true"
          className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground"
          strokeWidth={2.5}
          style={{
            animation: "savedCheck-quartr 480ms cubic-bezier(0.22, 1, 0.36, 1) both",
          }}
        />
        <div className="min-w-0 flex-1">
          <p className="text-[15px] leading-tight font-semibold tracking-tight text-foreground">
            Saved &amp; activated
          </p>
          <p className="mt-1 truncate text-[12.5px] text-muted-foreground">
            {workflowName}
          </p>
        </div>
      </div>

      {/* Compact step list — same shape, but every row reads as ready
          since the agent is live. */}
      <ol className="m-0 flex flex-col gap-2" data-testid="draft-step-timeline">
        {steps.map((step, idx) => (
          <DraftStepRow key={idx} step={step} index={idx} active />
        ))}
      </ol>

      {/* Secondary actions — stay available after activation so the user
          can still backtest or jump into the editor. */}
      <div className="flex items-center justify-center gap-1 text-[11.5px]">
        <button
          type="button"
          onClick={onBacktest}
          disabled={backtestState.kind === "running"}
          data-testid="backtest-draft-button"
          className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:opacity-60"
        >
          {backtestState.kind === "running" ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
          ) : (
            <History className="h-3 w-3" aria-hidden="true" />
          )}
          {backtestState.kind === "running"
            ? "Running…"
            : backtestState.kind === "ready"
              ? "Re-run backtest"
              : "Backtest"}
        </button>
        <span className="text-muted-foreground/40">·</span>
        <button
          type="button"
          onClick={onOpenEditor}
          data-testid="open-in-editor-button"
          className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
        >
          Open in editor
        </button>
      </div>
      </div>

      {/* DISCLAIMER — same as draft surface, kept consistent across states. */}
      <div className="flex items-center gap-1.5 border-t border-border/40 bg-amber-50/40 px-6 py-2.5 dark:bg-amber-500/[0.04]">
        <ShieldAlert
          className="h-3 w-3 shrink-0 text-amber-600/80 dark:text-amber-400/80"
          aria-hidden="true"
        />
        <p className="text-[11px] leading-snug text-amber-700/90 dark:text-amber-300/90">
          This is automation of your instructions, not financial advice.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DraftStepRow — tile-style step card. Each step is its own bordered
// surface with an icon chip on the left and a STEP N micro-label above
// the step name. Matches the agent run-view reference layout. Status
// indicators (running/pending) are intentionally suppressed before
// activation — pre-activation, every step is just "planned".
// ---------------------------------------------------------------------------

function DraftStepRow({
  step,
  index,
  active = false,
}: {
  step: DraftStep;
  index: number;
  active?: boolean;
}): React.ReactElement {
  const iconName = stepIconName(step.step_type);
  const label = step.label ?? step.step_type;

  // Single brand green for the entire activated state.
  const BRAND_GREEN = "#4CAF50";

  // News step types get their own rich row.
  if (
    step.step_type === "fetch.news" ||
    step.step_type === "trigger.event"
  ) {
    return (
      <li
        style={{
          animation: `stepIn-quartr 320ms cubic-bezier(0.22, 1, 0.36, 1) both`,
          animationDelay: `${index * 50}ms`,
          listStyle: "none",
        }}
      >
        <NewsStepRow
          step={{
            step_type: step.step_type as "fetch.news" | "trigger.event",
            config: step.config as NewsStepConfig,
            label: step.label,
          }}
        />
      </li>
    );
  }

  // IPO step types get their own rich row.
  if (
    step.step_type === "trigger.ipo_open" ||
    step.step_type === "action.arm_ipo_intent"
  ) {
    return (
      <li
        style={{
          animation: `stepIn-quartr 320ms cubic-bezier(0.22, 1, 0.36, 1) both`,
          animationDelay: `${index * 50}ms`,
          listStyle: "none",
        }}
      >
        <IpoStepRow
          step={{
            step_type: step.step_type as "trigger.ipo_open" | "action.arm_ipo_intent",
            config: step.config,
            label: step.label,
          }}
        />
      </li>
    );
  }

  return (
    <li
      className={cn(
        "flex items-center gap-2.5 rounded-xl border px-2.5 py-1.5 transition-colors",
        !active && "border-border/50 bg-card hover:border-border",
      )}
      style={{
        animation: `stepIn-quartr 320ms cubic-bezier(0.22, 1, 0.36, 1) both`,
        animationDelay: `${index * 50}ms`,
        ...(active
          ? {
              borderColor: `${BRAND_GREEN}66`, // ~40% alpha
              backgroundColor: `${BRAND_GREEN}14`, // ~8% alpha tint
            }
          : {}),
      }}
    >
      {/* Icon chip — square rounded tile. */}
      <span
        aria-hidden="true"
        className={cn(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
          !active && "bg-muted/70 text-muted-foreground",
        )}
        style={
          active
            ? {
                backgroundColor: `${BRAND_GREEN}26`, // ~15% alpha
                color: BRAND_GREEN,
              }
            : undefined
        }
      >
        <StepIcon name={iconName} className="h-3.5 w-3.5" />
      </span>

      {/* Step label + (after activation) succeeded sub-line. */}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-[12.5px] font-medium tracking-tight text-foreground">
          {label}
        </span>
        {active && (
          <span
            className="inline-flex items-center gap-1 text-[11px]"
            style={{ color: BRAND_GREEN }}
          >
            <CheckCircle2
              className="h-3 w-3 shrink-0"
              strokeWidth={2.25}
              aria-hidden="true"
            />
            Succeeded · 0 seconds
          </span>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Warning indicator — small icon button, shows count on hover. No more
// amber strip eating vertical space.
// ---------------------------------------------------------------------------

function WarningIndicator({
  warnings,
}: {
  warnings: string[];
}): React.ReactElement {
  return (
    <div className="group relative inline-flex">
      <span
        className="inline-flex items-center gap-1 rounded-full bg-amber-100/80 px-2 py-0.5 text-[10.5px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
        aria-label={`${warnings.length} warning${warnings.length === 1 ? "" : "s"}`}
      >
        <AlertCircle className="h-2.5 w-2.5" aria-hidden="true" />
        {warnings.length}
      </span>
      {/* Hover tooltip with the actual warning text. */}
      <div className="pointer-events-none absolute right-0 top-full z-10 mt-1.5 w-64 rounded-xl border border-border/60 bg-popover p-3 opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100">
        <ul className="m-0 space-y-1.5">
          {warnings.map((w, i) => (
            <li
              key={i}
              className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-foreground/85"
            >
              <AlertCircle
                className="mt-0.5 h-3 w-3 shrink-0 text-amber-600 dark:text-amber-400"
                aria-hidden="true"
              />
              <span>{w}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/**
 * Convert a WorkflowDraft (from chat) into the Workflow shape expected by
 * WorkflowEditorMock / AgentPanel so the "Open in editor" path works without
 * any backend call.
 */
export function draftToWorkflow(draft: WorkflowDraft): import("@/lib/types").Workflow {
  return {
    id: "",
    name: draft.name,
    description: draft.description ?? null,
    status: "draft",
    version: 1,
    single_instance: true,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    activated_at: null,
    last_run_at: null,
    next_run_at: null,
    steps: draft.steps.map((s, idx) => ({
      id: `draft-step-${idx}`,
      step_index: idx,
      step_type: s.step_type,
      label: s.label,
      config: s.config,
    })),
  };
}
