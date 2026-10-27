"use client";

import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Command as CommandPrimitive } from "cmdk";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Dialog,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { StepIcon } from "@/components/agent-panel/step-icon";
import type { Step, StepCategory, StepTypeCatalog, StepTypeDef } from "@/lib/types";
import { partitionIntoBuckets, type BucketedStep } from "@/lib/step-compat";

export type StepTypePickerProps = {
  open: boolean;
  /** Where the new step would be inserted. Drives the trigger-only filter. */
  insertIndex: number;
  /** All steps currently in the workflow, used to compute capability state. */
  steps?: Step[];
  catalog: StepTypeCatalog;
  onSelect: (def: StepTypeDef) => void;
  onClose: () => void;
};

// ---------------------------------------------------------------------------
// Bucket meta — visual identity for each bucket
// ---------------------------------------------------------------------------

type BucketMeta = {
  label: string;
  /** cmdk group data-testid suffix */
  testId: string;
  /** Tailwind colour classes applied to the dot and label */
  dotClass: string;
  labelClass: string;
};

const BUCKET_META: Record<"recommended" | "available" | "needs-setup", BucketMeta> = {
  recommended: {
    label: "Recommended",
    testId: "recommended",
    dotClass: "bg-green-500",
    labelClass: "text-green-700 dark:text-green-400",
  },
  available: {
    label: "Available",
    testId: "available",
    dotClass: "bg-slate-400",
    labelClass: "text-slate-500",
  },
  "needs-setup": {
    label: "Needs setup",
    testId: "needs-setup",
    dotClass: "bg-amber-400",
    labelClass: "text-amber-600",
  },
};

/**
 * Searchable, capability-bucketed, category-grouped picker for the step-type catalog.
 *
 * Hard structural filter (unchanged from Day 1):
 *   - At index 0 (the trigger slot): show ONLY trigger.* step types.
 *   - At index > 0: hide all trigger.* step types entirely.
 *
 * Capability bucketing (mirrors docs/plans/WORKFLOW_EDITOR_PLAN.html §05):
 *   - Accumulate produced capability tags from steps[0..insertIndex-1].
 *   - Partition the visible defs into "Recommended" / "Available" / "Needs setup".
 *   - Within each bucket, render sub-group headings from the `group` field.
 *   - "Needs setup" items are CLICKABLE (hybrid strictness) but show the unmet
 *     warn text so the user understands the dependency.
 *
 * At trigger-slot (insertIndex === 0): all triggers are "Recommended" (no
 * capability state exists yet). The three-bucket UI collapses to just
 * "Recommended" with the trigger sub-groups.
 */
export function StepTypePicker({
  open,
  insertIndex,
  steps = [],
  catalog,
  onSelect,
  onClose,
}: StepTypePickerProps): React.ReactElement {
  const isTriggerSlot = insertIndex === 0;
  const [query, setQuery] = useState("");

  // Build a O(1) lookup map from step_type → def.
  const catalogMap = useMemo(
    () => new Map(catalog.step_types.map((d) => [d.step_type, d])),
    [catalog.step_types],
  );

  // Hard structural filter — never bypassed by search or capability state.
  const visibleStepTypes = useMemo<StepTypeDef[]>(
    () =>
      catalog.step_types.filter((def) =>
        isTriggerSlot ? def.trigger_only : !def.trigger_only,
      ),
    [catalog.step_types, isTriggerSlot],
  );

  // Prior steps = the slice of the workflow that comes BEFORE insertIndex.
  const priorSteps = useMemo<Step[]>(
    () => steps.filter((s) => s.step_index < insertIndex),
    [steps, insertIndex],
  );

  // Partition into three buckets. Pure client computation — no network.
  const { recommended, available, needsSetup } = useMemo(
    () => partitionIntoBuckets(visibleStepTypes, priorSteps, catalogMap),
    [visibleStepTypes, priorSteps, catalogMap],
  );

  // For the trigger slot every visible def is "recommended" — no capability
  // state has been accumulated yet. Re-map to avoid a flat "Available" list.
  const finalBuckets: { key: "recommended" | "available" | "needs-setup"; items: BucketedStep[] }[] =
    isTriggerSlot
      ? [{ key: "recommended", items: visibleStepTypes.map((def) => ({ def, result: { bucket: "recommended" as const, unmetWarn: null } })) }]
      : [
          { key: "recommended", items: recommended },
          { key: "available", items: available },
          { key: "needs-setup", items: needsSetup },
        ];

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          setQuery("");
          onClose();
        }
      }}
    >
      <DialogPortal>
        <DialogOverlay />
        <DialogPrimitive.Content
          data-testid="step-type-picker"
          className={cn(
            "fixed left-[50%] top-[50%] z-50 w-full max-w-[480px] translate-x-[-50%] translate-y-[-50%]",
            "overflow-hidden rounded-2xl border bg-background p-0 shadow-2xl duration-200",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
          )}
        >
          <DialogDescription className="sr-only">
            {isTriggerSlot
              ? "Select a trigger that will start this workflow."
              : "Pick the next step to insert into the workflow."}
          </DialogDescription>

          {/* Title bar */}
          <div className="flex items-center justify-between gap-3 border-b border-border/50 px-5 py-3">
            <DialogTitle className="text-[13px] font-semibold tracking-tight text-foreground">
              {isTriggerSlot ? "Choose a trigger" : "Add a step"}
            </DialogTitle>
            <DialogPrimitive.Close
              aria-label="Close"
              className="inline-flex items-center justify-center"
              style={{
                width: 28,
                height: 28,
                background: "transparent",
                border: "none",
                borderRadius: "999px",
                color: "var(--text-tertiary)",
                cursor: "pointer",
                padding: 0,
                transition: "color 0.18s var(--ease-quartr), background-color 0.18s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--text-primary)";
                e.currentTarget.style.background = "var(--surface-hover)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--text-tertiary)";
                e.currentTarget.style.background = "transparent";
              }}
            >
              <X size={16} strokeWidth={2} aria-hidden="true" />
            </DialogPrimitive.Close>
          </div>

          <Command shouldFilter>
            {/* Search bar */}
            <div className="px-5 pt-4 pb-4">
              <div
                className="flex items-center gap-2"
                style={{
                  height: 38,
                  padding: "0 16px",
                  background: "var(--bg-primary)",
                  border: "1px solid var(--glass-border)",
                  borderRadius: "var(--radius-pill)",
                  transition: "border-color 0.2s var(--ease-quartr)",
                }}
              >
                <Search
                  className="shrink-0"
                  size={14}
                  strokeWidth={2}
                  style={{ color: "var(--text-tertiary)" }}
                  aria-hidden={true}
                />
                <CommandPrimitive.Input
                  value={query}
                  onValueChange={setQuery}
                  placeholder={
                    isTriggerSlot
                      ? "Search triggers…"
                      : "Search step types — name or description…"
                  }
                  autoFocus
                  className="flex-1 outline-none"
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "var(--text-primary)",
                    fontFamily: "var(--font-ui)",
                    fontSize: 13,
                  }}
                />
                {query.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setQuery("")}
                    aria-label="Clear search"
                    className="inline-flex shrink-0 items-center justify-center"
                    style={{
                      width: 20,
                      height: 20,
                      background: "transparent",
                      border: "none",
                      borderRadius: "999px",
                      color: "var(--text-tertiary)",
                      cursor: "pointer",
                      padding: 0,
                      transition: "color 0.18s var(--ease-quartr), background-color 0.18s var(--ease-quartr)",
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = "var(--text-primary)";
                      e.currentTarget.style.background = "var(--surface-hover)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = "var(--text-tertiary)";
                      e.currentTarget.style.background = "transparent";
                    }}
                  >
                    <X size={14} strokeWidth={2} aria-hidden="true" />
                  </button>
                )}
              </div>
            </div>

            <CommandList className="max-h-[420px] px-2 pb-3">
              <CommandEmpty>
                <p className="text-xs text-muted-foreground">
                  No step types match.
                </p>
              </CommandEmpty>

              {finalBuckets.map(({ key, items }) => {
                if (items.length === 0) return null;
                const meta = BUCKET_META[key];
                return (
                  <BucketSection
                    key={key}
                    bucketKey={key}
                    bucketMeta={meta}
                    items={items}
                    categories={catalog.categories}
                    onSelect={(def) => {
                      onSelect(def);
                      onClose();
                    }}
                  />
                );
              })}
            </CommandList>
          </Command>
        </DialogPrimitive.Content>
      </DialogPortal>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// BucketSection — renders one bucket (Recommended / Available / Needs setup)
// with group sub-headings within it.
// ---------------------------------------------------------------------------

type BucketSectionProps = {
  bucketKey: "recommended" | "available" | "needs-setup";
  bucketMeta: BucketMeta;
  items: BucketedStep[];
  categories: StepCategory[];
  onSelect: (def: StepTypeDef) => void;
};

function BucketSection({
  bucketKey,
  bucketMeta,
  items,
  categories,
  onSelect,
}: BucketSectionProps): React.ReactElement | null {
  // Group items by their `group` field, preserving catalog order.
  const groups = groupBySubgroup(items, categories);

  return (
    <>
      {/* Bucket heading — sticky separator */}
      <div
        className="flex items-center gap-1.5 px-2 pb-1 pt-3"
        data-testid={`step-picker-bucket-${bucketKey}`}
        aria-label={bucketMeta.label}
      >
        <span
          className={cn("inline-block h-2 w-2 rounded-full shrink-0", bucketMeta.dotClass)}
          aria-hidden="true"
        />
        <span className={cn("text-[10.5px] font-semibold uppercase tracking-wide", bucketMeta.labelClass)}>
          {bucketMeta.label}
        </span>
        <span className="ml-auto text-[10.5px] text-muted-foreground">{items.length}</span>
      </div>

      {groups.map(({ categoryId, groupLabel, items: groupItems }) => (
        <CommandGroup
          key={`${bucketKey}-${categoryId}-${groupLabel}`}
          heading={groupLabel}
          data-testid={`step-picker-group-${bucketKey}-${categoryId}`}
        >
          {groupItems.map(({ def, result }) => (
            <StepItem
              key={def.step_type}
              def={def}
              unmetWarn={result.unmetWarn}
              isNeedsSetup={bucketKey === "needs-setup"}
              onSelect={onSelect}
            />
          ))}
        </CommandGroup>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// StepItem — single row in a CommandGroup
// ---------------------------------------------------------------------------

type StepItemProps = {
  def: StepTypeDef;
  unmetWarn: string | null;
  isNeedsSetup: boolean;
  onSelect: (def: StepTypeDef) => void;
};

function StepItem({ def, unmetWarn, isNeedsSetup, onSelect }: StepItemProps): React.ReactElement {
  return (
    <CommandItem
      key={def.step_type}
      value={`${def.step_type} ${def.label} ${def.description}`}
      onSelect={() => onSelect(def)}
      data-testid={`step-picker-item-${def.step_type}`}
      className={cn(isNeedsSetup && "opacity-80")}
    >
      <span
        className={cn(
          "mr-2 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-muted",
          isNeedsSetup && "opacity-60",
        )}
      >
        <StepIcon name={def.icon} className="h-3.5 w-3.5" />
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-medium">{def.label}</span>
        {isNeedsSetup && unmetWarn ? (
          <span className="truncate text-[11px] text-amber-600 dark:text-amber-400">
            {unmetWarn}
          </span>
        ) : (
          <span className="truncate text-[11px] text-muted-foreground">
            {def.description}
          </span>
        )}
      </span>
    </CommandItem>
  );
}

// ---------------------------------------------------------------------------
// groupBySubgroup — partition BucketedStep[] by (category, group), in the
// catalog-supplied category order. Each (category, group) pair is one entry.
// ---------------------------------------------------------------------------

type SubgroupEntry = {
  categoryId: string;
  groupLabel: string;
  items: BucketedStep[];
};

function groupBySubgroup(
  items: BucketedStep[],
  categories: StepCategory[],
): SubgroupEntry[] {
  // Build ordered keys: category order from catalog, then group order by first
  // appearance within each category (stable — catalog is already sorted).
  const seen = new Map<string, SubgroupEntry>();

  for (const bitem of items) {
    const { def } = bitem;
    // Sub-group key: category:group (group may be undefined → use category label).
    const groupLabel = def.group ?? def.category;
    const key = `${def.category}::${groupLabel}`;
    const entry = seen.get(key);
    if (entry) {
      entry.items.push(bitem);
    } else {
      seen.set(key, { categoryId: def.category, groupLabel, items: [bitem] });
    }
  }

  // Sort by catalog-supplied category order, then by first-appearance of group.
  // Use a plain object for O(1) lookup without a union-key narrowing issue.
  const catOrder: Record<string, number> = Object.fromEntries(
    categories.map((c, i) => [c.id, i]),
  );
  return [...seen.values()].sort((a, b) => {
    const aCat = catOrder[a.categoryId] ?? 99;
    const bCat = catOrder[b.categoryId] ?? 99;
    return aCat !== bCat ? aCat - bCat : 0;
  });
}
