"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ArrowLeft, Loader2, Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type {
  Diagnostic,
  Step,
  StepTypeCatalog,
  StepTypeDef,
  Workflow,
  WorkflowStatus,
} from "@/lib/types";
import { isError } from "@/lib/types";
import { findStepType } from "@/lib/mock-catalog";
import {
  activateWorkflow,
  createWorkflow,
  lintWorkflow,
  pauseWorkflow,
  runWorkflow,
  updateWorkflow,
} from "@/lib/api";
import { StepCard } from "@/components/agent-panel/step-card";
import { StepConfigDrawer } from "@/components/agent-panel/StepConfigDrawer";
import { StepTypePicker } from "@/components/agent-panel/StepTypePicker";
import { RunHistory } from "@/components/agent-panel/RunHistory";
import { RunView } from "@/components/agent-panel/RunView";
import { defaultConfigFromSchema } from "@/lib/json-schema-to-zod";

const LINT_DEBOUNCE_MS = 250;

let stepIdCounter = 0;
const newStepId = (): string => {
  stepIdCounter += 1;
  return `local-step-${Date.now().toString(36)}-${stepIdCounter}`;
};

const STATUS_COPY: Record<WorkflowStatus, { label: string; tone: "muted" | "success" | "warning" }> = {
  draft: { label: "Draft", tone: "muted" },
  active: { label: "Active", tone: "success" },
  paused: { label: "Paused", tone: "warning" },
  archived: { label: "Archived", tone: "muted" },
};

type ActionState = "idle" | "saving" | "activating" | "pausing" | "running";

export type WorkflowEditorMockProps = {
  /** The workflow to render. Mutations persist via PATCH /api/workflows/{id}. */
  initialWorkflow: Workflow;
  catalog: StepTypeCatalog;
  /**
   * When set, the editor is CONTROLLED by this value — changes pushed from
   * chat (amended drafts) arrive here and re-render the editor without
   * remounting. Only used for unsaved drafts. Undefined = uncontrolled (the
   * default, same as today for saved workflows and the demo).
   */
  controlledWorkflow?: Workflow;
  /**
   * Called when the user edits the controlled draft inside the editor so the
   * caller can keep its state in sync. Only called when `controlledWorkflow`
   * is set.
   */
  onControlledWorkflowChange?: (draft: Workflow | null) => void;
};

export function WorkflowEditorMock({
  initialWorkflow,
  catalog,
  controlledWorkflow,
  onControlledWorkflowChange,
}: WorkflowEditorMockProps): React.ReactElement {
  const [workflow, setWorkflow] = useState<Workflow>(
    controlledWorkflow ?? initialWorkflow,
  );
  const [editingStepId, setEditingStepId] = useState<string | null>(null);
  const [pickerInsertIndex, setPickerInsertIndex] = useState<number | null>(null);
  const [actionState, setActionState] = useState<ActionState>("idle");
  const [actionError, setActionError] = useState<string | null>(null);
  // null = editor, string = run id being viewed in RunView
  const [viewingRunId, setViewingRunId] = useState<string | null>(null);
  const [showRunHistory, setShowRunHistory] = useState(false);
  // Diagnostics keyed by step_index (authoritative from debounced /lint call).
  // Seeded from the server-computed `workflow.diagnostics` so a pre-loaded
  // workflow renders its advisories instantly, before the mount-time lint
  // reconciles them.
  const [diagnosticsMap, setDiagnosticsMap] = useState<Map<number, Diagnostic[]>>(() => {
    const map = new Map<number, Diagnostic[]>();
    for (const d of initialWorkflow.diagnostics ?? []) {
      const bucket = map.get(d.step_index) ?? [];
      bucket.push(d);
      map.set(d.step_index, bucket);
    }
    return map;
  });
  const lintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Controlled-draft sync: when the parent pushes a new version of the draft
  // (e.g. chat amended it), update local state so the editor re-renders.
  // We skip this if the user is actively editing (editingStepId is set) to
  // avoid clobbering mid-edit; the next chat turn will still carry the latest
  // user-visible state because the FE reads activeEditorDraft from context.
  const prevControlledRef = useRef<Workflow | undefined>(controlledWorkflow);
  useEffect(() => {
    if (!controlledWorkflow) return;
    if (controlledWorkflow === prevControlledRef.current) return;
    prevControlledRef.current = controlledWorkflow;
    // Only apply if not mid-step-edit (preserve user's in-progress edits).
    if (editingStepId) return;
    setWorkflow(controlledWorkflow);
    scheduleLint(controlledWorkflow.steps);
  // editingStepId intentionally NOT in deps — we only want to recheck when
  // controlledWorkflow itself changes from outside.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controlledWorkflow]);

  // Notify caller whenever local workflow state changes (user edits).
  const prevLocalRef = useRef<Workflow>(workflow);
  useEffect(() => {
    if (workflow === prevLocalRef.current) return;
    prevLocalRef.current = workflow;
    onControlledWorkflowChange?.(workflow);
  }, [workflow, onControlledWorkflowChange]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  // ---------------------------------------------------------------------------
  // Debounced lint — fires ~250ms after any step change. Keyed by step_index.
  // ---------------------------------------------------------------------------

  const scheduleLint = useCallback((steps: Step[]): void => {
    if (lintTimerRef.current !== null) {
      clearTimeout(lintTimerRef.current);
    }
    lintTimerRef.current = setTimeout(() => {
      const lintSteps = steps.map((s) => ({
        step_type: s.step_type,
        label: s.label,
        config: s.config,
      }));
      void lintWorkflow(lintSteps).then((result) => {
        if (isError(result)) return; // lint errors are best-effort; don't surface UI noise
        const map = new Map<number, Diagnostic[]>();
        for (const d of result.data.diagnostics) {
          const bucket = map.get(d.step_index) ?? [];
          bucket.push(d);
          map.set(d.step_index, bucket);
        }
        setDiagnosticsMap(map);
      });
    }, LINT_DEBOUNCE_MS);
  }, []);

  // Run lint on mount (so a pre-loaded workflow shows its diagnostics immediately).
  useEffect(() => {
    scheduleLint(workflow.steps);
    return () => {
      if (lintTimerRef.current !== null) clearTimeout(lintTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------------------
  // Step mutations — delete and duplicate. Both re-index and schedule lint.
  // ---------------------------------------------------------------------------

  const handleDeleteStep = useCallback((step: Step): void => {
    setWorkflow((w) => {
      const next = w.steps
        .filter((s) => s.id !== step.id)
        .map((s, idx) => ({ ...s, step_index: idx }));
      scheduleLint(next);
      return { ...w, steps: next };
    });
  }, [scheduleLint]);

  const handleDuplicateStep = useCallback((step: Step): void => {
    setWorkflow((w) => {
      const srcIdx = w.steps.findIndex((s) => s.id === step.id);
      if (srcIdx < 0) return w;
      const clone: Step = {
        ...step,
        id: newStepId(),
        step_index: srcIdx + 1,
      };
      const next = [...w.steps];
      next.splice(srcIdx + 1, 0, clone);
      const renumbered = next.map((s, idx) => ({ ...s, step_index: idx }));
      scheduleLint(renumbered);
      return { ...w, steps: renumbered };
    });
  }, [scheduleLint]);

  const handleDragEnd = (event: DragEndEvent): void => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = workflow.steps.findIndex((s) => s.id === active.id);
    const newIndex = workflow.steps.findIndex((s) => s.id === over.id);
    if (oldIndex < 0 || newIndex < 0) return;

    const draggedStep = workflow.steps[oldIndex];
    if (!draggedStep) return;
    const draggedEntry = findStepType(catalog, draggedStep.step_type);

    // Structural invariant: triggers must stay at slot 0; non-triggers must not
    // land in slot 0. Snap back silently if the move would violate this.
    const wouldBeTriggerSlot = newIndex === 0;
    const isTrigger = draggedEntry?.trigger_only ?? draggedEntry?.category === "trigger";
    if (wouldBeTriggerSlot && !isTrigger) {
      toast.warning("Non-trigger steps cannot be placed at position 1 — move cancelled.");
      return;
    }
    if (!wouldBeTriggerSlot && isTrigger && oldIndex === 0) {
      toast.warning("The trigger must remain at position 1 — move cancelled.");
      return;
    }

    const reordered = arrayMove(workflow.steps, oldIndex, newIndex).map(
      (s, idx) => ({ ...s, step_index: idx }),
    );
    setWorkflow((w) => ({ ...w, steps: reordered }));
    // Lint will surface any remaining sequence warnings on the new order.
    scheduleLint(reordered);
  };

  const status = STATUS_COPY[workflow.status];
  const editingStep = editingStepId
    ? workflow.steps.find((s) => s.id === editingStepId) ?? null
    : null;
  const editingCatalogEntry = editingStep
    ? findStepType(catalog, editingStep.step_type)
    : undefined;

  const handleSaveStepConfig = (
    config: Record<string, unknown>,
  ): { error?: undefined } => {
    if (!editingStep) return {};
    setWorkflow((w) => {
      const next = w.steps.map((s) =>
        s.id === editingStep.id ? { ...s, config } : s,
      );
      scheduleLint(next);
      return { ...w, steps: next };
    });
    return {};
  };

  const handleAddStep = (insertAt: number, def: StepTypeDef): void => {
    const newStep: Step = {
      id: newStepId(),
      step_index: insertAt,
      step_type: def.step_type,
      label: null,
      config: defaultConfigFromSchema(def.config_schema),
    };
    setWorkflow((w) => {
      const next = [...w.steps];
      next.splice(insertAt, 0, newStep);
      // Renumber.
      const renumbered = next.map((s, idx) => ({ ...s, step_index: idx }));
      scheduleLint(renumbered);
      return { ...w, steps: renumbered };
    });
  };

  /** Persist local state to backend. Creates if no id; patches otherwise. */
  const handleSave = async (): Promise<void> => {
    if (actionState !== "idle") return;
    setActionState("saving");
    setActionError(null);

    const stepPayload = workflow.steps.map((s) => ({
      step_type: s.step_type,
      label: s.label,
      config: s.config,
    }));

    let result;
    if (!workflow.id || workflow.id.startsWith("local-") || workflow.id.startsWith("00000000-")) {
      // New draft — create first, then patch id into local state.
      result = await createWorkflow({
        name: workflow.name,
        description: workflow.description ?? undefined,
        single_instance: workflow.single_instance,
        steps: stepPayload,
      });
    } else {
      result = await updateWorkflow(workflow.id, {
        name: workflow.name,
        description: workflow.description ?? undefined,
        single_instance: workflow.single_instance,
        steps: stepPayload,
      });
    }

    if (isError(result)) {
      setActionError(result.error.message);
      toast.error(result.error.message);
    } else {
      setWorkflow(result.data);
      toast.success("Workflow saved");
    }
    setActionState("idle");
  };

  const handleActivateOrPause = async (): Promise<void> => {
    if (actionState !== "idle") return;
    const isDraft = workflow.status === "draft" || workflow.status === "paused";

    if (isDraft) {
      // Must save first if we have local changes.
      setActionState("activating");
      setActionError(null);

      // If not yet persisted, create first.
      let targetId = workflow.id;
      if (!targetId || targetId.startsWith("00000000-")) {
        const saveResult = await createWorkflow({
          name: workflow.name,
          description: workflow.description ?? undefined,
          single_instance: workflow.single_instance,
          steps: workflow.steps.map((s) => ({
            step_type: s.step_type,
            label: s.label,
            config: s.config,
          })),
        });
        if (isError(saveResult)) {
          setActionError(saveResult.error.message);
          setActionState("idle");
          return;
        }
        targetId = saveResult.data.id;
        setWorkflow(saveResult.data);
      }

      const result = await activateWorkflow(targetId);
      if (isError(result)) {
        setActionError(result.error.message);
        toast.error(result.error.message);
      } else {
        setWorkflow(result.data);
        toast.success("Workflow activated");
      }
    } else if (workflow.status === "active") {
      setActionState("pausing");
      setActionError(null);
      const result = await pauseWorkflow(workflow.id);
      if (isError(result)) {
        setActionError(result.error.message);
        toast.error(result.error.message);
      } else {
        setWorkflow(result.data);
        toast.success("Workflow paused");
      }
    }

    setActionState("idle");
  };

  const handleRunNow = async (): Promise<void> => {
    if (actionState !== "idle") return;
    setActionState("running");
    setActionError(null);

    // Archive can't run — guard
    if (workflow.status === "archived") {
      setActionError("Archived workflows cannot be run.");
      setActionState("idle");
      return;
    }

    // If draft/no-id, save first.
    let targetId = workflow.id;
    if (!targetId || targetId.startsWith("00000000-")) {
      const saveResult = await createWorkflow({
        name: workflow.name,
        description: workflow.description ?? undefined,
        single_instance: workflow.single_instance,
        steps: workflow.steps.map((s) => ({
          step_type: s.step_type,
          label: s.label,
          config: s.config,
        })),
      });
      if (isError(saveResult)) {
        setActionError(saveResult.error.message);
        setActionState("idle");
        return;
      }
      targetId = saveResult.data.id;
      setWorkflow(saveResult.data);
    }

    const result = await runWorkflow(targetId);
    if (isError(result)) {
      setActionError(result.error.message);
      toast.error(result.error.message);
    } else {
      toast.success("Run started");
      setViewingRunId(result.data.run_id);
    }
    setActionState("idle");
  };

  // If viewing a run, show RunView
  if (viewingRunId) {
    return (
      <RunView
        runId={viewingRunId}
        catalog={catalog}
        onClose={() => setViewingRunId(null)}
      />
    );
  }

  // If viewing run history, show RunHistory
  if (showRunHistory && workflow.id && !workflow.id.startsWith("00000000-")) {
    return (
      <div className="flex h-full flex-col">
        <div className="border-b px-6 py-3">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Back to editor"
            onClick={() => setShowRunHistory(false)}
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
        <div className="flex-1 overflow-hidden">
          <RunHistory
            workflowId={workflow.id}
            onSelectRun={(runId) => {
              setShowRunHistory(false);
              setViewingRunId(runId);
            }}
          />
        </div>
      </div>
    );
  }

  const activateLabel = workflow.status === "active"
    ? "Pause"
    : workflow.status === "paused"
    ? "Resume"
    : "Activate";

  const busy = actionState !== "idle";

  return (
    <div className="relative flex h-full flex-col">
      {/* Header — chat-card style: chip + status pill, then title, then
          description, then a primary CTA pill with ghost secondaries. */}
      <header className="shrink-0 px-6 pt-3 pb-4">
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center rounded-md bg-sky-100 px-2.5 py-0.5 text-[11px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
            Agent
          </span>
          <StatusPill status={workflow.status} label={status.label} />
        </div>

        <div className="mt-4 space-y-1.5">
          <Input
            aria-label="Workflow name"
            value={workflow.name}
            onChange={(e) =>
              setWorkflow((w) => ({ ...w, name: e.target.value }))
            }
            className="h-auto border-0 bg-transparent px-0 py-0 text-[20px] font-semibold leading-[1.2] tracking-tight shadow-none focus-visible:ring-0"
          />
          <Textarea
            aria-label="Workflow description"
            value={workflow.description ?? ""}
            onChange={(e) =>
              setWorkflow((w) => ({
                ...w,
                description: e.target.value || null,
              }))
            }
            rows={2}
            placeholder="Describe what this agent does…"
            className="min-h-0 resize-none border-0 bg-transparent px-0 py-0 text-[12.5px] leading-relaxed text-muted-foreground shadow-none focus-visible:ring-0"
          />
        </div>

        {actionError && (
          <p
            role="alert"
            className="mt-3 rounded-md bg-destructive/10 px-3 py-1.5 text-xs text-destructive"
            data-testid="editor-action-error"
          >
            {actionError}
          </p>
        )}

        {/* Action bar — a primary Save + Run now pair, then the lifecycle /
            history controls as quiet ghost buttons so the hierarchy reads at
            a glance instead of a flat row of equal-weight buttons. */}
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="default"
            onClick={() => { void handleSave(); }}
            disabled={busy}
            data-testid="save-btn"
            className="rounded-lg"
          >
            {actionState === "saving" && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
            Save
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => { void handleRunNow(); }}
            disabled={busy || workflow.status === "archived"}
            data-testid="run-now-btn"
            className="rounded-lg"
          >
            {actionState === "running" && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
            Run now
          </Button>
          {workflow.status !== "archived" && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => { void handleActivateOrPause(); }}
              disabled={busy}
              data-testid="activate-btn"
              className="rounded-lg text-muted-foreground hover:text-foreground"
            >
              {(actionState === "activating" || actionState === "pausing") && (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              )}
              {activateLabel}
            </Button>
          )}
          {workflow.id && !workflow.id.startsWith("00000000-") && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowRunHistory(true)}
              disabled={busy}
              data-testid="history-btn"
              className="rounded-lg text-muted-foreground hover:text-foreground"
            >
              History
            </Button>
          )}
        </div>
      </header>

      {/* Step flow — cards linked by vertical connectors so the sequence
          reads as a pipeline (trigger → … → action), like Zapier/n8n. Each
          connector reveals a "+" to insert between steps on hover, and the
          flow terminates in a connected dashed "Add step" node so there's
          never a disconnected button floating in empty space. */}
      <div className="flex flex-1 min-h-0 flex-col border-t border-border/40 bg-muted/20">
        <div className="flex flex-1 flex-col overflow-y-auto px-5 py-5">
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={workflow.steps.map((s) => s.id)}
              strategy={verticalListSortingStrategy}
            >
              <ol className="flex flex-col">
                {workflow.steps.map((step, i) => (
                  <li key={step.id} className="flex flex-col">
                    <SortableStepRow
                      step={step}
                      catalogEntry={findStepType(catalog, step.step_type)}
                      diagnostics={diagnosticsMap.get(step.step_index)}
                      onConfigure={() => setEditingStepId(step.id)}
                      onDelete={handleDeleteStep}
                      onDuplicate={handleDuplicateStep}
                    />
                    {/* Connector only between steps — never trailing past the
                        last one, since the Add button is pinned to the bottom. */}
                    {i < workflow.steps.length - 1 && (
                      <FlowConnector
                        onInsert={() => setPickerInsertIndex(i + 1)}
                      />
                    )}
                  </li>
                ))}
              </ol>
            </SortableContext>
          </DndContext>

          {/* Terminal node — pinned near the bottom of the scroll area via
              mt-auto so it sits low regardless of step count, yet collapses
              and scrolls naturally once the steps overflow the viewport. */}
          <button
            type="button"
            onClick={() => setPickerInsertIndex(workflow.steps.length)}
            data-testid="add-step-button"
            className="mt-auto flex h-12 w-full shrink-0 items-center justify-center gap-1.5 rounded-2xl border border-dashed border-border/70 bg-background/40 text-[12.5px] font-medium text-muted-foreground transition-colors hover:border-primary/50 hover:bg-background hover:text-foreground"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            {workflow.steps.length === 0 ? "Add a trigger" : "Add step"}
          </button>
        </div>
      </div>

      {editingStep && editingCatalogEntry && (
        <StepConfigDrawer
          step={editingStep}
          catalogEntry={editingCatalogEntry}
          workflow={workflow}
          onSave={handleSaveStepConfig}
          onClose={() => setEditingStepId(null)}
        />
      )}

      {pickerInsertIndex !== null && (
        <StepTypePicker
          open
          insertIndex={pickerInsertIndex}
          steps={workflow.steps}
          catalog={catalog}
          onSelect={(def) => handleAddStep(pickerInsertIndex, def)}
          onClose={() => setPickerInsertIndex(null)}
        />
      )}
    </div>
  );
}

/** Vertical connector drawn between consecutive step cards (and into the
 *  terminal Add node). Hovering reveals a "+" that inserts a step at this
 *  position, so the flow reads as a real pipeline rather than loose tiles. */
function FlowConnector({
  onInsert,
}: {
  onInsert: () => void;
}): React.ReactElement {
  return (
    <div className="group/conn relative flex h-7 items-center justify-center">
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border/70"
      />
      <button
        type="button"
        onClick={onInsert}
        aria-label="Insert a step here"
        data-step-card-noclick
        className="relative z-10 flex h-5 w-5 items-center justify-center rounded-full border border-border/50 bg-background text-muted-foreground opacity-0 shadow-sm transition-all hover:border-border hover:text-foreground hover:shadow-[0_2px_8px_-4px_rgba(15,23,42,0.08)] group-hover/conn:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Plus className="h-3 w-3" aria-hidden="true" />
      </button>
    </div>
  );
}

function SortableStepRow({
  step,
  catalogEntry,
  diagnostics,
  onConfigure,
  onDelete,
  onDuplicate,
}: {
  step: Step;
  catalogEntry: ReturnType<typeof findStepType>;
  diagnostics: Diagnostic[] | undefined;
  onConfigure: (step: Step) => void;
  onDelete: (step: Step) => void;
  onDuplicate: (step: Step) => void;
}): React.ReactElement {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: step.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };

  return (
    <div ref={setNodeRef} style={style}>
      <StepCard
        step={step}
        catalogEntry={catalogEntry}
        isDragging={isDragging}
        dragHandleProps={{ ...attributes, ...listeners }}
        diagnostics={diagnostics}
        onConfigure={onConfigure}
        onDelete={onDelete}
        onDuplicate={onDuplicate}
      />
    </div>
  );
}

/** Status pill mirroring the chat card's active/draft/paused chip. */
function StatusPill({
  status,
  label,
}: {
  status: WorkflowStatus;
  label: string;
}): React.ReactElement {
  if (status === "active") {
    return (
      <span
        data-testid="agent-active-pill"
        className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium bg-transparent"
        style={{ borderColor: "#4CAF50", color: "#4CAF50" }}
      >
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: "#4CAF50" }}
        />
        {label}
      </span>
    );
  }
  if (status === "paused") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/40 px-2.5 py-1 text-[11px] font-medium text-amber-600 dark:text-amber-300">
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-amber-500" />
        {label}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50" />
      {label}
    </span>
  );
}

/** Loading state — used by AgentPanel while the catalog is fetching. */
export function WorkflowEditorSkeleton(): React.ReactElement {
  return (
    <div className="flex h-full flex-col">
      <header className="border-b px-6 py-5 space-y-3">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-4 w-3/4" />
        <div className="flex gap-2">
          <Skeleton className="h-8 w-16" />
          <Skeleton className="h-8 w-20" />
        </div>
      </header>
      <div className="flex-1 px-6 py-5 space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full rounded-xl" />
        ))}
      </div>
    </div>
  );
}
