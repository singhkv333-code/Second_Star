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
import { Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type {
  Step,
  StepTypeCatalog,
  StepTypeDef,
  Workflow,
  WorkflowStatus,
} from "@/lib/types";
import { findStepType } from "@/lib/mock-catalog";
import { StepCard } from "@/components/agent-panel/step-card";
import { StepConfigDrawer } from "@/components/agent-panel/StepConfigDrawer";
import { StepTypePicker } from "@/components/agent-panel/StepTypePicker";
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

export type WorkflowEditorMockProps = {
  /** The workflow to render. Mutations stay local in this Day 1 mock. */
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
        <div className="mt-4 flex items-center gap-2">
          <Button size="sm" variant="default">Save</Button>
          <Button size="sm" variant="outline">Run now</Button>
          <Button size="sm" variant="ghost">Activate</Button>
        </div>
      </header>

      {/* Step list + add buttons */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
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
