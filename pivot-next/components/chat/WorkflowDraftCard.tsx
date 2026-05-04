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
  AlertCircle, ArrowRight, BarChart3, Bot, Check, Loader2, Play, Zap,
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

  return (
    <div
      className={cn(
        "my-2 w-full max-w-md rounded-xl border bg-card shadow-sm",
        "overflow-hidden",
      )}
      data-testid="workflow-draft-card"
      role="region"
      aria-label={`Agent proposal: ${draft.name}`}
    >
      {/* Header */}
      <div className="flex items-start gap-3 px-4 pt-4 pb-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <Bot className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="text-[10px] px-1.5 py-0">
              Agent proposal
            </Badge>
          </div>
          <h3 className="mt-1 text-sm font-semibold leading-snug text-foreground">
            {draft.name}
          </h3>
          {draft.description && (
            <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
              {draft.description}
            </p>
          )}
        </div>
      </div>

      {/* Step mini-list */}
      <div className="border-t px-4 py-2.5 space-y-1.5">
        {visibleSteps.map((step, idx) => (
          <DraftStepRow key={idx} step={step} index={idx} />
        ))}
        {hiddenCount > 0 && (
          <p className="text-xs text-muted-foreground pl-7">
            +{hiddenCount} more step{hiddenCount > 1 ? "s" : ""}
          </p>
        )}
      </div>

      {/* Rationale */}
      {draft.rationale && (
        <div className="border-t px-4 py-2.5">
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {draft.rationale}
          </p>
        </div>
      )}

      {/* Warnings */}
      {draft.warnings.length > 0 && (
        <div className="border-t bg-warning/5 px-4 py-2.5 space-y-1">
          {draft.warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-1.5">
              <AlertCircle
                className="mt-0.5 h-3 w-3 shrink-0 text-warning"
                aria-hidden="true"
              />
              <p className="text-[11px] text-warning">{w}</p>
            </div>
          ))}
        </div>
      )}

      {/* CTA */}
      {saveState.kind === "saved" ? (
        <div
          className="border-t bg-emerald-500/10 px-4 py-3"
          data-testid="workflow-saved"
        >
          <div className="flex items-start gap-2 text-emerald-700 dark:text-emerald-400">
            <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1 text-xs">
              <p className="font-medium">
                Saved & activated · {saveState.workflowName}
              </p>
              <p className="mt-0.5 text-[11px] text-emerald-700/70 dark:text-emerald-400/70 truncate">
                Workflow id: {saveState.workflowId}
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="border-t px-4 py-3 space-y-2">
          <Button
            size="sm"
            className="w-full justify-between"
            onClick={() => void handleSaveAndActivate()}
            disabled={saveState.kind === "saving"}
            data-testid="save-activate-button"
          >
            <span className="flex items-center gap-1.5">
              {saveState.kind === "saving" ? (
                <Loader2
                  className="h-3.5 w-3.5 animate-spin"
                  aria-hidden="true"
                />
              ) : (
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {saveState.kind === "saving" ? "Saving…" : "Save & activate"}
            </span>
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="w-full justify-between"
            onClick={() => onOpenEditor(draft)}
            data-testid="open-in-editor-button"
          >
            <span className="flex items-center gap-1.5">
              <Zap className="h-3.5 w-3.5" aria-hidden="true" />
              Open in editor
            </span>
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
          {/* Backtest button — only renders for shapes the simulator
              recognises. The button is always shown so users can try;
              the response carries the eligibility verdict. */}
          <Button
            size="sm"
            variant="outline"
            className="w-full justify-between"
            onClick={() => void handleBacktest()}
            disabled={backtestState.kind === "running"}
            data-testid="backtest-draft-button"
          >
            <span className="flex items-center gap-1.5">
              {backtestState.kind === "running" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <BarChart3 className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {backtestState.kind === "running"
                ? "Running backtest…"
                : backtestState.kind === "ready"
                  ? "Backtest re-run"
                  : "Backtest this agent"}
            </span>
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
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
}: {
  step: DraftStep;
  index: number;
}): React.ReactElement {
  const iconName = stepIconName(step.step_type);
  const label = step.label ?? step.step_type;
  const prefix = step.step_type.split(".")[0] ?? "control";

  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground">
        <StepIcon name={iconName} className="h-3.5 w-3.5" />
      </span>
      <span
        className="rounded-full bg-muted px-1.5 py-0 text-[10px] font-medium uppercase tracking-wide text-muted-foreground shrink-0"
        aria-hidden="true"
      >
        {index + 1}
      </span>
      <span className="truncate text-xs text-foreground/80">{label}</span>
      <span className="shrink-0 text-[10px] text-muted-foreground/60">
        {prefix}
      </span>
    </div>
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
