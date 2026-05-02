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

import { AlertCircle, ArrowRight, Bot, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { StepIcon } from "@/components/agent-panel/step-icon";

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
};

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
}: WorkflowDraftCardProps): React.ReactElement {
  const visibleSteps = draft.steps.slice(0, MAX_VISIBLE_STEPS);
  const hiddenCount = draft.steps.length - visibleSteps.length;

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
      <div className="border-t px-4 py-3">
        <Button
          size="sm"
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
      </div>
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
