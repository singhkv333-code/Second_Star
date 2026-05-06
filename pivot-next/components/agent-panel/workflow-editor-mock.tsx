"use client";

import { useState } from "react";
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
import { Loader2, Plus } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type {
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
};

export function WorkflowEditorMock({
  initialWorkflow,
  catalog,
}: WorkflowEditorMockProps): React.ReactElement {
  const [workflow, setWorkflow] = useState<Workflow>(initialWorkflow);
  const [editingStepId, setEditingStepId] = useState<string | null>(null);
  const [pickerInsertIndex, setPickerInsertIndex] = useState<number | null>(null);
  const [actionState, setActionState] = useState<ActionState>("idle");
  const [actionError, setActionError] = useState<string | null>(null);
  // null = editor, string = run id being viewed in RunView
  const [viewingRunId, setViewingRunId] = useState<string | null>(null);
  const [showRunHistory, setShowRunHistory] = useState(false);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const handleDragEnd = (event: DragEndEvent): void => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = workflow.steps.findIndex((s) => s.id === active.id);
    const newIndex = workflow.steps.findIndex((s) => s.id === over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    const reordered = arrayMove(workflow.steps, oldIndex, newIndex).map(
      (s, idx) => ({ ...s, step_index: idx }),
    );
    setWorkflow((w) => ({ ...w, steps: reordered }));
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
    setWorkflow((w) => ({
      ...w,
      steps: w.steps.map((s) =>
        s.id === editingStep.id ? { ...s, config } : s,
      ),
    }));
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
          <Button variant="ghost" size="sm" onClick={() => setShowRunHistory(false)}>
            ← Back to editor
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
      {/* Header: name (largest type), description, status, action buttons */}
      <header className="border-b px-6 py-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1 space-y-2">
            <Input
              aria-label="Workflow name"
              value={workflow.name}
              onChange={(e) =>
                setWorkflow((w) => ({ ...w, name: e.target.value }))
              }
              className="border-0 bg-transparent px-0 text-xl font-semibold tracking-tight shadow-none focus-visible:ring-0"
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
              className="resize-none border-0 bg-transparent px-0 text-sm text-muted-foreground shadow-none focus-visible:ring-0"
            />
          </div>
          <Badge variant={status.tone === "success" ? "success" : status.tone === "warning" ? "warning" : "muted"}>
            {status.label}
          </Badge>
        </div>
        {actionError && (
          <p
            role="alert"
            className="mt-2 rounded-md bg-destructive/10 px-3 py-1.5 text-xs text-destructive"
            data-testid="editor-action-error"
          >
            {actionError}
          </p>
        )}
        <div className="mt-4 flex items-center gap-2 flex-wrap">
          <Button
            size="sm"
            variant="default"
            onClick={() => { void handleSave(); }}
            disabled={busy}
            data-testid="save-btn"
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
            >
              History
            </Button>
          )}
        </div>
      </header>

      {/* Step list + add buttons. Background gets a subtle grid for a
          technical "canvas" feel — it's a CSS gradient, no extra
          asset, no runtime cost. The mask gradient softens the edges
          so the grid doesn't fight with the steps. */}
      <div
        className={cn(
          "relative flex-1 overflow-y-auto px-6 py-5",
        )}
      >
        <div
          aria-hidden="true"
          className={cn(
            "pointer-events-none absolute inset-0",
            "[background-image:linear-gradient(to_right,rgba(127,127,127,0.07)_1px,transparent_1px),linear-gradient(to_bottom,rgba(127,127,127,0.07)_1px,transparent_1px)]",
            "[background-size:32px_32px]",
            "[mask-image:radial-gradient(ellipse_at_center,black_55%,transparent_95%)]",
          )}
        />
        {/* Faint vignette for depth */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-background/40"
        />
        <div className="relative">
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={workflow.steps.map((s) => s.id)}
            strategy={verticalListSortingStrategy}
          >
            <ol className="space-y-3">
              {workflow.steps.map((step, idx) => (
                <li key={step.id}>
                  <SortableStepRow
                    step={step}
                    catalogEntry={
                      findStepType(catalog, step.step_type)
                    }
                    onConfigure={() => setEditingStepId(step.id)}
                  />
                  {idx < workflow.steps.length - 1 && (
                    <AddStepDivider
                      label={`Add step after step ${idx + 1}`}
                      onClick={() => setPickerInsertIndex(idx + 1)}
                    />
                  )}
                </li>
              ))}
            </ol>
          </SortableContext>
        </DndContext>

        <div className="mt-4">
          <Button
            variant="outline"
            size="sm"
            className="w-full justify-center"
            onClick={() => setPickerInsertIndex(workflow.steps.length)}
            data-testid="add-step-button"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            {workflow.steps.length === 0 ? "Add a trigger" : "Add step"}
          </Button>
        </div>
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
          catalog={catalog}
          onSelect={(def) => handleAddStep(pickerInsertIndex, def)}
          onClose={() => setPickerInsertIndex(null)}
        />
      )}
    </div>
  );
}

function SortableStepRow({
  step,
  catalogEntry,
  onConfigure,
}: {
  step: Step;
  catalogEntry: ReturnType<typeof findStepType>;
  onConfigure: (step: Step) => void;
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
        onConfigure={onConfigure}
      />
    </div>
  );
}

function AddStepDivider({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}): React.ReactElement {
  return (
    <div className="relative my-2 flex items-center justify-center">
      <span className="absolute inset-x-0 top-1/2 -z-10 h-px bg-border/60" />
      <button
        type="button"
        aria-label={label}
        onClick={onClick}
        className="flex h-6 w-6 items-center justify-center rounded-full border bg-background text-muted-foreground opacity-0 transition hover:bg-accent hover:text-foreground hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Plus className="h-3 w-3" aria-hidden="true" />
      </button>
    </div>
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
