"use client";

import { forwardRef } from "react";
import { GripVertical, MoreHorizontal } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import type { Step, StepTypeDef } from "@/lib/types";
import { StepIcon } from "@/components/agent-panel/step-icon";
import { previewStepConfig } from "@/components/agent-panel/config-preview";

export type StepCardProps = {
  step: Step;
  catalogEntry: StepTypeDef | undefined;
  /** Drag-handle props from `@dnd-kit/sortable` listeners; injected by the editor. */
  dragHandleProps?: React.HTMLAttributes<HTMLButtonElement>;
  isDragging?: boolean;
  onConfigure?: (step: Step) => void;
  onDuplicate?: (step: Step) => void;
  onDelete?: (step: Step) => void;
};

export const StepCard = forwardRef<HTMLDivElement, StepCardProps>(
  function StepCard(
    {
      step,
      catalogEntry,
      dragHandleProps,
      isDragging,
      onConfigure,
      onDuplicate,
      onDelete,
    },
    ref,
  ) {
    const label =
      step.label ?? catalogEntry?.label ?? step.step_type;
    const description = catalogEntry
      ? previewStepConfig(step)
      : `Unknown step type: ${step.step_type}`;
    const iconName = catalogEntry?.icon ?? "help-circle";

    const clickable = Boolean(onConfigure);
    return (
      <div
        ref={ref}
        role={clickable ? "button" : undefined}
        tabIndex={clickable ? 0 : undefined}
        aria-label={clickable ? `Configure step ${step.step_index + 1}: ${label}` : undefined}
        onClick={(e) => {
          // Ignore clicks that originated inside the drag handle or menu.
          const target = e.target as HTMLElement;
          if (target.closest("[data-step-card-noclick]")) return;
          onConfigure?.(step);
        }}
        onKeyDown={(e) => {
          if (!clickable) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onConfigure?.(step);
          }
        }}
        className={cn(
          "group flex items-start gap-3 rounded-xl border bg-card p-4 shadow-sm transition-shadow",
          clickable && "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          isDragging
            ? "border-primary/50 shadow-lg"
            : "hover:border-foreground/20 hover:shadow-md",
        )}
        data-testid={`step-card-${step.step_index}`}
      >
        <button
          type="button"
          aria-label={`Drag step ${step.step_index + 1}`}
          className="mt-0.5 flex h-7 w-5 shrink-0 cursor-grab items-center justify-center text-muted-foreground hover:text-foreground active:cursor-grabbing"
          data-step-card-noclick
          onClick={(e) => e.stopPropagation()}
          {...dragHandleProps}
        >
          <GripVertical className="h-4 w-4" aria-hidden="true" />
        </button>

        <div
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
            categoryTint(catalogEntry?.category),
          )}
          aria-hidden="true"
        >
          <StepIcon name={iconName} className="h-4 w-4" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Step {step.step_index + 1}
            </span>
            <span className="truncate text-[13px] font-medium text-foreground">
              {label}
            </span>
          </div>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {description}
          </p>
        </div>

        <div data-step-card-noclick onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground opacity-0 transition hover:bg-accent hover:text-foreground group-hover:opacity-100 focus:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Step actions"
            >
              <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-40">
              <DropdownMenuItem
                onClick={() => onConfigure?.(step)}
                disabled={!onConfigure}
              >
                Edit step
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => onDuplicate?.(step)}
                disabled={!onDuplicate}
              >
                Duplicate
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => onDelete?.(step)}
                disabled={!onDelete}
                className="text-destructive focus:text-destructive"
              >
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    );
  },
);

function categoryTint(category: StepTypeDef["category"] | undefined): string {
  switch (category) {
    case "trigger":
      return "bg-info/10 text-info";
    case "fetch":
      return "bg-primary/10 text-primary";
    case "condition":
      return "bg-warning/10 text-warning";
    case "action":
      return "bg-destructive/10 text-destructive";
    case "notify":
      return "bg-success/10 text-success";
    case "control":
      return "bg-muted text-muted-foreground";
    default:
      return "bg-muted text-muted-foreground";
  }
}
