"use client";

/**
 * WorkflowDraftCard — inline chat card rendered when the chatbot's
 * propose_workflow tool returns `_render_hint: "workflow_draft_card"`.
 *
 * Per docs/HANDOFF.md §5 and docs/ARCHITECTURE.md §10.
 *
 * Usage:
 *   const toolData = message.tool_result?.data;
 *   if (toolData?._render_hint === "workflow_draft_card") {
 *     return <WorkflowDraftCard draft={toolData} onOpenEditor={...} />;
 *   }
 */

import { useState } from "react";
import {
  AlertCircle, BarChart3, Bot, Check, CircleDot,
  Loader2, Play, Sparkles, Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

// ---------------------------------------------------------------------------
// Public types — matches the propose_workflow tool result from the backend
// (docs/HANDOFF.md §5)
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
  /** Called when the user clicks "Open in editor". Parent mounts AgentPanel with the draft. */
  onOpenEditor: (draft: WorkflowDraft) => void;
  /**
   * Called once Save & activate succeeds AND a manual run is kicked off.
   * The chat parent uses this to mount an inline live-run checklist.
   * Optional — if omitted, the card just shows the saved/activated state
   * without surfacing a live run.
   */
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

/** Backtest-button lifecycle. Independent of the save/activate flow. */
type BacktestState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "ineligible"; reason: string }
  | { kind: "ready"; payload: IndicatorBacktestPayload; warnings: string[] }
  | { kind: "error"; message: string };

// Map step_type prefix → icon name (same palette as mock-catalog / step-icon)
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

// Max steps to show inline before truncating (keeps the card compact)
const MAX_VISIBLE_STEPS = 5;

// Friendly label for the right-side "what will happen" hint on each
// step row. Same per-prefix bucketing the icon palette uses, but
// human-shaped — "Scheduled", "Will fetch", "Will check", etc.
const STEP_PHASE_LABEL: Record<string, string> = {
  trigger: "Scheduled",
  fetch: "Will fetch",
  condition: "Will check",
  action: "Will execute",
  notify: "Will notify",
  control: "Will route",
};

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
      // 5-year default mirrors the indicator backtest UX. Users who
      // want a different period will use chat's natural-language path.
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
    // Hand the payload to the same chart card the indicator
    // backtester uses. Cast: BacktestDraftEligible already has every
    // field IndicatorBacktestPayload needs.
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
      // Saved but couldn't activate — still useful to show the user the
      // draft persisted; they can activate from the editor.
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

    // Kick off a manual run so the chat can show a live checklist
    // immediately. Failure here is non-fatal: the workflow is already
    // active and will fire on its trigger; we just don't surface a
    // run card. Parent only gets notified on success.
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

  return (
    <div
      className={cn(
        "relative my-1.5 w-full max-w-sm overflow-hidden rounded-xl",
        "border border-border/60",
        // Glassy fill — translucent over whatever the chat thread sits
        // on, with a backdrop blur for the liquid-glass read.
        "bg-card/55 backdrop-blur-xl supports-[backdrop-filter]:bg-card/35",
        // Soft outer glow + faint inner highlight at the top.
        "shadow-[0_1px_0_rgba(255,255,255,0.05)_inset,0_12px_36px_-16px_rgba(0,0,0,0.55),0_2px_10px_-6px_rgba(0,0,0,0.3)]",
      )}
      data-testid="workflow-draft-card"
      role="region"
      aria-label={`Agent proposal: ${draft.name}`}
    >
      {/* Top-edge sheen */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/15 to-transparent"
      />
      {/* Activated success ribbon — subtle emerald wash along the
          left edge so the user instantly sees the state shift. */}
      {isSaved && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 left-0 w-px bg-gradient-to-b from-emerald-400/0 via-emerald-400/60 to-emerald-400/0"
        />
      )}

      {/* Header */}
      <div className="flex items-start gap-2.5 px-3.5 pt-3 pb-2.5">
        <div
          className={cn(
            "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
            "border border-violet-400/30 bg-violet-400/10",
            "shadow-[0_0_0_1px_rgba(167,139,250,0.15),0_4px_14px_-6px_rgba(167,139,250,0.45)]",
          )}
        >
          <Bot className="h-3.5 w-3.5 text-violet-300" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-full px-1.5 py-0",
                "text-[9px] font-medium uppercase tracking-wider",
                "bg-violet-400/10 text-violet-300 ring-1 ring-violet-400/30",
              )}
            >
              <Sparkles className="h-2 w-2" aria-hidden="true" />
              Agent
            </span>
            {isSaved && (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-emerald-400/10 px-1.5 py-0 text-[9px] font-medium uppercase tracking-wider text-emerald-300 ring-1 ring-emerald-400/30"
                data-testid="agent-active-pill"
              >
                <span className="h-1 w-1 rounded-full bg-emerald-400 shadow-[0_0_0_2px_rgba(52,211,153,0.25)]" />
                Active
              </span>
            )}
          </div>
          <h3 className="mt-1 text-[13px] font-semibold leading-snug text-foreground">
            {draft.name}
          </h3>
          {draft.description && (
            <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground line-clamp-2">
              {draft.description}
            </p>
          )}
        </div>
      </div>

      {/* Step timeline — the public.com-style "what's executed and
          what's coming" pattern. Pre-activation everything is a
          preview ring; activated workflows show step states. */}
      <div className="border-t border-border/40 px-3.5 pt-2.5 pb-1.5">
        <ol className="relative space-y-2" data-testid="draft-step-timeline">
          {/* Vertical track that connects the rings */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-[9px] top-2 bottom-2 w-px bg-gradient-to-b from-border/0 via-border/70 to-border/0"
          />
          {visibleSteps.map((step, idx) => (
            <DraftStepRow
              key={idx}
              step={step}
              index={idx}
              activated={isSaved}
              isFirst={isSaved && idx === 0}
            />
          ))}
        </ol>
        {hiddenCount > 0 && (
          <p className="mt-1.5 pl-6 text-[10px] text-muted-foreground">
            +{hiddenCount} more step{hiddenCount > 1 ? "s" : ""}
          </p>
        )}
      </div>

      {/* Rationale */}
      {draft.rationale && (
        <div className="border-t border-border/40 px-3.5 py-2">
          <p className="text-[10.5px] leading-snug text-muted-foreground line-clamp-2">
            {draft.rationale}
          </p>
        </div>
      )}

      {/* Warnings */}
      {draft.warnings.length > 0 && (
        <div className="border-t border-amber-400/20 bg-amber-400/5 px-3.5 py-1.5 space-y-0.5">
          {draft.warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-1.5">
              <AlertCircle
                className="mt-0.5 h-2.5 w-2.5 shrink-0 text-amber-300"
                aria-hidden="true"
              />
              <p className="text-[10px] leading-snug text-amber-200/90">{w}</p>
            </div>
          ))}
        </div>
      )}

      {/* CTA */}
      {saveState.kind === "saved" ? (
        <div
          className={cn(
            "border-t border-emerald-400/20 px-3.5 py-2",
            "bg-gradient-to-r from-emerald-400/10 via-emerald-400/5 to-transparent",
          )}
          data-testid="workflow-saved"
        >
          <div className="flex items-start gap-1.5 text-emerald-300">
            <Check className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1 text-[11px]">
              <p className="font-medium">
                Saved & activated · {saveState.workflowName}
              </p>
              <p className="mt-0.5 text-[10px] text-emerald-300/70 truncate">
                {saveState.workflowId}
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="border-t border-border/40 px-3.5 py-2.5 grid grid-cols-3 gap-1.5">
          <Button
            size="sm"
            className={cn(
              "h-7 col-span-3 justify-center gap-1 text-[11px] px-2",
              "bg-gradient-to-r from-violet-500 to-fuchsia-500",
              "text-white hover:from-violet-400 hover:to-fuchsia-400",
              "shadow-[0_4px_18px_-6px_rgba(167,139,250,0.5)]",
            )}
            onClick={() => void handleSaveAndActivate()}
            disabled={saveState.kind === "saving"}
            data-testid="save-activate-button"
          >
            {saveState.kind === "saving" ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            ) : (
              <Play className="h-3 w-3" aria-hidden="true" />
            )}
            {saveState.kind === "saving" ? "Saving…" : "Save & activate"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className={cn(
              "h-7 justify-center gap-1 text-[11px] px-2",
              "border-border/60 bg-card/30 backdrop-blur-md",
              "supports-[backdrop-filter]:bg-card/20",
              "hover:bg-card/50",
            )}
            onClick={() => onOpenEditor(draft)}
            data-testid="open-in-editor-button"
          >
            <Zap className="h-3 w-3" aria-hidden="true" />
            Editor
          </Button>
          <Button
            size="sm"
            variant="outline"
            className={cn(
              "h-7 col-span-2 justify-center gap-1 text-[11px] px-2",
              "border-border/60 bg-card/30 backdrop-blur-md",
              "supports-[backdrop-filter]:bg-card/20",
              "hover:bg-card/50",
            )}
            onClick={() => void handleBacktest()}
            disabled={backtestState.kind === "running"}
            data-testid="backtest-draft-button"
          >
            {backtestState.kind === "running" ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            ) : (
              <BarChart3 className="h-3 w-3" aria-hidden="true" />
            )}
            {backtestState.kind === "running"
              ? "Running…"
              : backtestState.kind === "ready"
                ? "Backtest re-run"
                : "Backtest this agent"}
          </Button>
          {saveState.kind === "error" && (
            <p
              role="alert"
              data-testid="workflow-save-error"
              className="rounded-md bg-destructive/10 px-2.5 py-1.5 text-[11px] text-destructive"
            >
              {saveState.message}
            </p>
          )}
          {backtestState.kind === "ineligible" && (
            <p
              role="status"
              data-testid="backtest-ineligible"
              className="rounded-md bg-muted px-2.5 py-1.5 text-[11px] text-muted-foreground"
            >
              <span className="font-medium">Can't backtest this shape:</span>{" "}
              {backtestState.reason}
            </p>
          )}
          {backtestState.kind === "error" && (
            <p
              role="alert"
              data-testid="backtest-draft-error"
              className="rounded-md bg-destructive/10 px-2.5 py-1.5 text-[11px] text-destructive"
            >
              {backtestState.message}
            </p>
          )}
        </div>
      )}
      {/* Inline backtest result — same chart card the indicator
          backtest path uses, so the FE doesn't grow a second renderer. */}
      {backtestState.kind === "ready" && (
        <div className="border-t" data-testid="backtest-draft-result">
          <IndicatorBacktestCard payload={backtestState.payload} />
          {backtestState.warnings.length > 0 && (
            <div className="border-t bg-muted/40 px-4 py-2 space-y-1">
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

function DraftStepRow({
  step,
  index,
  activated,
  isFirst,
}: {
  step: DraftStep;
  index: number;
  /** True once the user has clicked Save & activate. */
  activated: boolean;
  /** True for the very first step on an activated agent — gets the
   *  "live" pulsing ring + "Happening now" label. */
  isFirst: boolean;
}): React.ReactElement {
  const iconName = stepIconName(step.step_type);
  const label = step.label ?? step.step_type;
  const prefix = step.step_type.split(".")[0] ?? "control";
  const phaseLabel = STEP_PHASE_LABEL[prefix] ?? "Will run";

  // Status logic for the timeline ring.
  //
  //   pre-activation    → all steps show as "upcoming" rings
  //   post-activation   → first step is "live" (pulsing emerald ring),
  //                       rest stay "upcoming". Real run state comes
  //                       from InlineRunCard once the run kicks off.
  const ringState: "upcoming" | "live" =
    activated && isFirst ? "live" : "upcoming";
  const rightLabel = activated
    ? isFirst
      ? "Happening now"
      : phaseLabel
    : phaseLabel;

  return (
    <li className="relative flex items-center gap-2.5 pl-0">
      {/* Status ring */}
      <span
        className={cn(
          "relative z-10 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full",
          ringState === "live"
            ? "bg-emerald-400/15 ring-2 ring-emerald-400/70"
            : "bg-card/60 ring-1 ring-border/60",
        )}
        aria-hidden="true"
      >
        {ringState === "live" ? (
          <>
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            <span className="absolute inset-0 animate-ping rounded-full bg-emerald-400/40" />
          </>
        ) : (
          <CircleDot className="h-2.5 w-2.5 text-muted-foreground/50" />
        )}
      </span>
      {/* Body */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span
            className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-muted-foreground"
            aria-hidden="true"
          >
            <StepIcon name={iconName} className="h-2.5 w-2.5" />
          </span>
          <span className="truncate text-[11px] font-medium text-foreground/90">
            {label}
          </span>
          <span
            className="shrink-0 rounded-full border border-border/60 bg-card/40 px-1 py-0 text-[8px] font-medium uppercase tracking-wider text-muted-foreground/80"
            aria-hidden="true"
          >
            {prefix}
          </span>
        </div>
      </div>
      {/* Right-aligned status label */}
      <span
        className={cn(
          "shrink-0 text-[9.5px] font-medium tabular-nums",
          ringState === "live"
            ? "text-emerald-300"
            : "text-muted-foreground/80",
        )}
      >
        {rightLabel}
      </span>
    </li>
  );
}

/**
 * Convert a WorkflowDraft (from chat) into the Workflow shape expected by
 * WorkflowEditorMock / AgentPanel so the "Open in editor" path works without
 * any backend call.
 *
 * The resulting Workflow has no `id` or server-side fields — those come from
 * the POST /api/workflows response when the user clicks Activate.
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
