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
  const [query, setQuery] = useState("");

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

          {/* Title bar — explicit title on the left clearly anchors the
              close button on the right as "close this dialog" (not "clear
              the search field below"). */}
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
            {/* Search bar — matches the home-page global search exactly:
                Quartr pill, lucide Search at size=14 stroke=2 (text-tertiary),
                13px input in --font-ui. Clear-X behaves identically too. */}
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
        </DialogPrimitive.Content>
      </DialogPortal>
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
