"use client";

import { useMemo } from "react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { StepIcon } from "@/components/agent-panel/step-icon";
import type { StepCategory, StepTypeCatalog, StepTypeDef } from "@/lib/types";

export type StepTypePickerProps = {
  open: boolean;
  /** Where the new step would be inserted. Drives the trigger-only filter. */
  insertIndex: number;
  catalog: StepTypeCatalog;
  onSelect: (def: StepTypeDef) => void;
  onClose: () => void;
};

/**
 * Searchable, category-grouped picker for the step-type catalog.
 *
 * Single-track invariant per ARCHITECTURE.md §5.1 + §13:
 *   - At index 0 (the trigger slot), show ONLY trigger.* step types.
 *   - At any index > 0, hide all trigger.* step types entirely.
 *
 * Categories are read off `catalog.categories` and rendered in their
 * server-supplied order — no hardcoded category list. Only categories
 * with at least one matching (post-filter) entry render.
 */
export function StepTypePicker({
  open,
  insertIndex,
  catalog,
  onSelect,
  onClose,
}: StepTypePickerProps): React.ReactElement {
  const isTriggerSlot = insertIndex === 0;

  // Filter catalog by the single-track invariant. Done outside the search
  // so it can never be bypassed.
  const visibleStepTypes = useMemo<StepTypeDef[]>(
    () =>
      catalog.step_types.filter((def) =>
        isTriggerSlot ? def.trigger_only : !def.trigger_only,
      ),
    [catalog.step_types, isTriggerSlot],
  );

  const grouped = useMemo(() => groupByCategory(visibleStepTypes, catalog.categories), [
    visibleStepTypes,
    catalog.categories,
  ]);

  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent
        className="overflow-hidden p-0 sm:max-w-[480px]"
        data-testid="step-type-picker"
      >
        <DialogTitle className="sr-only">
          {isTriggerSlot ? "Choose a trigger" : "Add a step"}
        </DialogTitle>
        <DialogDescription className="sr-only">
          {isTriggerSlot
            ? "Select a trigger that will start this workflow."
            : "Pick the next step to insert into the workflow."}
        </DialogDescription>
        <Command shouldFilter>
          <CommandInput
            placeholder={
              isTriggerSlot
                ? "Search triggers…"
                : "Search step types — name or description…"
            }
            autoFocus
          />
          <CommandList className="max-h-[420px]">
            <CommandEmpty>
              <p className="text-xs text-muted-foreground">
                No step types match.
              </p>
            </CommandEmpty>

            {grouped.map(({ category, items }) => (
              <CommandGroup
                key={category.id}
                heading={category.label}
                data-testid={`step-picker-group-${category.id}`}
              >
                {items.map((def) => (
                  <CommandItem
                    key={def.step_type}
                    value={`${def.step_type} ${def.label} ${def.description}`}
                    onSelect={() => {
                      onSelect(def);
                      onClose();
                    }}
                    data-testid={`step-picker-item-${def.step_type}`}
                  >
                    <span className="mr-2 flex h-7 w-7 items-center justify-center rounded-md bg-muted">
                      <StepIcon name={def.icon} className="h-3.5 w-3.5" />
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate text-sm font-medium">
                        {def.label}
                      </span>
                      <span className="truncate text-[11px] text-muted-foreground">
                        {def.description}
                      </span>
                    </span>
                    <span className="ml-2 shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                      {def.step_type}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}

function groupByCategory(
  defs: StepTypeDef[],
  categories: StepCategory[],
): Array<{ category: StepCategory; items: StepTypeDef[] }> {
  const buckets = new Map<string, StepTypeDef[]>();
  for (const def of defs) {
    const arr = buckets.get(def.category);
    if (arr) arr.push(def);
    else buckets.set(def.category, [def]);
  }
  // Render categories in the catalog-supplied order.
  return categories
    .map((category) => ({
      category,
      items: buckets.get(category.id) ?? [],
    }))
    .filter(({ items }) => items.length > 0);
}
