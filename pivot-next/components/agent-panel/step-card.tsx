"use client";

import { forwardRef } from "react";
import { AlertCircle, GripVertical, Info, MoreHorizontal } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import type { Diagnostic, Step, StepTypeDef } from "@/lib/types";
import { StepIcon } from "@/components/agent-panel/step-icon";

export type StepCardProps = {
  step: Step;
  catalogEntry: StepTypeDef | undefined;
  /** Drag-handle props from `@dnd-kit/sortable` listeners; injected by the editor. */
  dragHandleProps?: React.HTMLAttributes<HTMLButtonElement>;
  isDragging?: boolean;
  onConfigure?: (step: Step) => void;
  onDuplicate?: (step: Step) => void;
  onDelete?: (step: Step) => void;
  /** Diagnostics for this step from the debounced lintWorkflow call. */
  diagnostics?: Diagnostic[];
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
      diagnostics,
    },
    ref,
  ) {
    const label =
      step.label ?? catalogEntry?.label ?? step.step_type;
    const iconName = catalogEntry?.icon ?? "help-circle";

    const clickable = Boolean(onConfigure);
    const hasDiagnostics = diagnostics && diagnostics.length > 0;
    // The card border shifts to red when any error-severity diagnostic is present.
    const hasError = hasDiagnostics && diagnostics.some((d) => d.severity === "error");
    const hasWarning =
      !hasError &&
      hasDiagnostics &&
      diagnostics.some((d) => d.severity === "warning");

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
          "agent-step-card group flex flex-col gap-2 rounded-2xl border bg-card px-4 py-3.5 transition-all",
          "shadow-[0_1px_2px_rgba(15,23,42,0.04)]",
          clickable && "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          isDragging
            ? "border-primary/40 shadow-[0_8px_24px_-12px_rgba(15,23,42,0.15)]"
            : hasError
            ? "border-destructive/50 hover:border-destructive/70"
            : hasWarning
            ? "border-amber-400/50 hover:border-amber-400/70"
            : "border-border/50 hover:border-border hover:shadow-[0_2px_8px_-4px_rgba(15,23,42,0.08)]",
        )}
        data-testid={`step-card-${step.step_index}`}
      >
        {/* Main row: grip + icon + label + menu */}
        <div className="flex items-center gap-3">
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
              "step-icon-tile flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
              categoryTint(catalogEntry?.category),
            )}
            aria-hidden="true"
          >
            <StepIcon name={iconName} className="h-4 w-4" />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <span className="shrink-0 rounded-md bg-muted/70 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Step {step.step_index + 1}
              </span>
              <span className="min-w-0 truncate text-[13px] font-medium tracking-tight text-foreground">
                {label}
              </span>
            </div>
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

        {/* Diagnostic chips — reuse CritiqueFlag icon+colour system */}
        {hasDiagnostics && (
          <DiagnosticChips diagnostics={diagnostics} />
        )}
      </div>
    );
  },
);

// ---------------------------------------------------------------------------
// Diagnostic chips — mirrors the CritiqueBlock colour conventions in
// OptionStrategyCard: error→rose, warning→amber, info→sky.
// ---------------------------------------------------------------------------

function DiagnosticChips({
  diagnostics,
}: {
  diagnostics: Diagnostic[];
}): React.ReactElement {
  return (
    <ul
      aria-label="Step diagnostics"
      className="ml-7 flex flex-col gap-1.5"
      data-step-card-noclick
      onClick={(e) => e.stopPropagation()}
    >
      {diagnostics.map((d, i) => (
        <li key={i} className="flex items-start gap-2">
          {d.severity === "error" && (
            <AlertCircle
              className="mt-0.5 h-4 w-4 shrink-0 text-rose-500"
              aria-hidden="true"
            />
          )}
          {d.severity === "warning" && (
            <AlertCircle
              className="mt-0.5 h-4 w-4 shrink-0 text-amber-500"
              aria-hidden="true"
            />
          )}
          {d.severity === "info" && (
            <Info
              className="mt-0.5 h-4 w-4 shrink-0 text-sky-500"
              aria-hidden="true"
            />
          )}
          <span
            className={cn(
              "text-xs leading-snug",
              d.severity === "error" && "text-rose-700 dark:text-rose-300",
              d.severity === "warning" && "text-amber-700 dark:text-amber-300",
              d.severity === "info" && "text-sky-700 dark:text-sky-300",
            )}
          >
            {d.message}
            {d.suggested_fix && (
              <span className="ml-1 opacity-70">— {d.suggested_fix}</span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

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
