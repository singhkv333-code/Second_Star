"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useStepCatalog } from "@/components/agent-panel/use-step-catalog";
import {
  WorkflowEditorMock,
  WorkflowEditorSkeleton,
} from "@/components/agent-panel/workflow-editor-mock";
import { DEMO_WORKFLOW } from "@/components/agent-panel/demo-workflow";
import type { Workflow } from "@/lib/types";

const MIN_WIDTH = 380;
const MAX_WIDTH = 920;
const DEFAULT_WIDTH = 460;

export type AgentPanelProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Optional workflow to pre-fill the editor. When omitted the panel shows
   * the built-in demo workflow. Used by:
   * - WorkflowDraftCard "Open in editor →" (draft from chat, no id yet)
   * - AgentsTab row click (saved workflow, has id)
   */
  initialWorkflow?: Workflow;
};

/**
 * Persistent right-side drawer for the Agent System UI.
 *
 * Why custom (not shadcn `Sheet`)? Sheet is modal — overlay + focus trap +
 * unmount on close. We need a *persistent* side panel that coexists with
 * the chat, with a draggable left edge to resize. ARCHITECTURE.md §11
 * called this out explicitly.
 *
 * Behaviors:
 * - Esc closes (per spec keyboard support).
 * - Left edge is a draggable resize handle (clamped to MIN/MAX).
 * - Mounts WorkflowEditorMock once the catalog loads; renders skeleton
 *   while loading and a typed error state if the catalog fetch fails.
 */
export function AgentPanel({
  open,
  onOpenChange,
  initialWorkflow,
}: AgentPanelProps): React.ReactElement | null {
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const dragState = useRef<{ startX: number; startWidth: number } | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);

  // Esc-to-close.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onOpenChange(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  // Click-outside-to-close. We attach on the next tick so the click that
  // opened the panel (e.g. "Open in editor" in chat) doesn't immediately
  // close it. Clicks inside portaled popovers/dialogs/menus that the panel
  // spawns (StepTypePicker, StepConfigDrawer, Radix dropdowns) render
  // outside the panel DOM — we treat those as "inside" so they don't dismiss
  // the panel either.
  useEffect(() => {
    if (!open) return;
    let armed = false;
    const armId = window.setTimeout(() => {
      armed = true;
    }, 0);
    const onPointerDown = (e: PointerEvent) => {
      if (!armed) return;
      const target = e.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target)) return;
      // Ignore clicks landing inside Radix portals (popovers, dropdowns,
      // dialogs) so the panel doesn't close when the user interacts with
      // a control it spawned.
      if (target instanceof Element) {
        if (target.closest("[role='dialog'], [role='menu'], [role='listbox'], [data-radix-popper-content-wrapper], [data-sonner-toaster]")) {
          return;
        }
      }
      onOpenChange(false);
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      window.clearTimeout(armId);
      document.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, [open, onOpenChange]);

  const onPointerMove = useCallback((e: PointerEvent) => {
    if (!dragState.current) return;
    const delta = dragState.current.startX - e.clientX;
    const next = clamp(
      dragState.current.startWidth + delta,
      MIN_WIDTH,
      MAX_WIDTH,
    );
    setWidth(next);
  }, []);

  const onPointerUp = useCallback(() => {
    dragState.current = null;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
  }, [onPointerMove]);

  const onResizeStart = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    dragState.current = { startX: e.clientX, startWidth: width };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
  };

  if (!open) return null;

  return (
    <aside
      ref={panelRef}
      role="dialog"
      aria-label="Agent panel"
      aria-modal="false"
      style={{ width }}
      className={cn(
        "fixed inset-y-0 right-0 z-40 flex border-l bg-background shadow-xl",
        "min-w-[380px] max-w-[920px]",
      )}
      data-testid="agent-panel"
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize agent panel"
        onPointerDown={onResizeStart}
        className="absolute inset-y-0 left-0 w-1 cursor-col-resize bg-transparent transition-colors hover:bg-primary/40"
        data-testid="agent-panel-resize-handle"
      />

      <div className="flex h-full w-full flex-col">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div className="flex items-center">
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: "var(--weight-display)" as unknown as number,
                fontSize: 18,
                letterSpacing: "-0.02em",
                color: "var(--text-primary)",
              }}
            >
              Agent
            </span>
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close agent panel"
            onClick={() => onOpenChange(false)}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
        <div className="flex-1 overflow-hidden">
          <AgentPanelBody initialWorkflow={initialWorkflow} />
        </div>
      </div>
    </aside>
  );
}

function AgentPanelBody({
  initialWorkflow,
}: {
  initialWorkflow?: Workflow;
}): React.ReactElement {
  const state = useStepCatalog();

  if (state.status === "loading") {
    return <WorkflowEditorSkeleton />;
  }

  if (state.status === "error") {
    return (
      <div
        role="alert"
        className="flex h-full flex-col items-center justify-center px-8 text-center"
      >
        <AlertCircle
          className="mb-3 h-6 w-6 text-destructive"
          aria-hidden="true"
        />
        <p className="text-sm font-medium">Couldn&apos;t load step catalog</p>
        <p className="mt-1 max-w-sm text-xs text-muted-foreground">
          {state.error.message}
        </p>
      </div>
    );
  }

  return (
    <WorkflowEditorMock
      initialWorkflow={initialWorkflow ?? DEMO_WORKFLOW}
      catalog={state.catalog}
    />
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
