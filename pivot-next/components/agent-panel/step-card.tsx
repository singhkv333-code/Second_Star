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
          "group flex items-center gap-3 rounded-2xl border border-border/50 bg-card px-4 py-3.5 transition-all",
          "shadow-[0_1px_2px_rgba(15,23,42,0.04)]",
          clickable && "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          isDragging
            ? "border-primary/40 shadow-[0_8px_24px_-12px_rgba(15,23,42,0.15)]"
            : "hover:border-border hover:shadow-[0_2px_8px_-4px_rgba(15,23,42,0.08)]",
        )}
        data-testid={`step-card-${step.step_index}`}
      >
        <button
          type="button"
          aria-label={`Drag step ${step.step_index + 1}`}
          className="flex h-7 w-4 shrink-0 cursor-grab items-center justify-center text-muted-foreground/60 hover:text-foreground active:cursor-grabbing"
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
            <span className="rounded-md bg-muted/70 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Step {step.step_index + 1}
            </span>
            <span className="truncate text-[13px] font-medium tracking-tight text-foreground">
              {label}
            </span>
          </div>
          <p className="mt-0.5 truncate text-[11.5px] text-muted-foreground">
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
      return "bg-sky-100 text-sky-600 dark:bg-sky-500/15 dark:text-sky-300";
    case "fetch":
      return "bg-muted/70 text-muted-foreground";
    case "condition":
      return "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300";
    case "action":
      return "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300";
    case "notify":
      return "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300";
    case "control":
      return "bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300";
    default:
      return "bg-muted/70 text-muted-foreground";
  }
}
