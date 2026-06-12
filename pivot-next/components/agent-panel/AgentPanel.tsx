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

export const AGENT_PANEL_MIN_WIDTH = 340;
export const AGENT_PANEL_MAX_WIDTH = 920;
export const AGENT_PANEL_DEFAULT_WIDTH = 460;

const MIN_WIDTH = AGENT_PANEL_MIN_WIDTH;
const MAX_WIDTH = AGENT_PANEL_MAX_WIDTH;
const DEFAULT_WIDTH = AGENT_PANEL_DEFAULT_WIDTH;

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
  /** Controlled width. When provided, the panel renders at this width and
   * notifies the parent through `onWidthChange` so the parent can reserve
   * matching space in its own layout (avoid panel-over-content overlap). */
  width?: number;
  onWidthChange?: (width: number) => void;
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
  width: controlledWidth,
  onWidthChange,
}: AgentPanelProps): React.ReactElement | null {
  const [internalWidth, setInternalWidth] = useState(DEFAULT_WIDTH);
  const width = controlledWidth ?? internalWidth;
  const setWidth = useCallback(
    (next: number) => {
      if (controlledWidth === undefined) setInternalWidth(next);
      onWidthChange?.(next);
    },
    [controlledWidth, onWidthChange],
  );
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

  // Click-outside-to-close was removed when the shell started reserving
  // `paddingRight` on the body to host the panel as a true side-by-side
  // surface: the chat composer + cards are visible next to the editor, and
  // every click there would otherwise dismiss the editor. The Esc key
  // listener + the header X button remain the supported close affordances.

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
        "agent-panel-shell fixed inset-y-0 right-0 z-40 flex border-l bg-background shadow-xl",
        // Desktop sizing constraints; mobile override lives in globals.css
        // (forces 100vw and ignores the inline width so the panel becomes
        // a full-screen sheet at <lg).
        "lg:min-w-[340px] lg:max-w-[920px]",
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
            className="rounded-full"
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

  const resolved = initialWorkflow ?? DEMO_WORKFLOW;
  // Key on the workflow id so switching agents while the panel is open
  // remounts the editor — WorkflowEditorMock seeds its internal state from
  // `initialWorkflow` only on first mount, so a prop change alone is silent.
  return (
    <WorkflowEditorMock
      key={resolved.id}
      initialWorkflow={resolved}
      catalog={state.catalog}
    />
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
