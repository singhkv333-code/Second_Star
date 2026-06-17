"use client";

import { useEffect, useRef } from "react";
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
export const AGENT_PANEL_DEFAULT_WIDTH = 520;

// Fixed (non-resizable) panel width on desktop — kept in lockstep with the
// Backtest sheet's `clamp(340px, 25vw, 520px)` so the two side panels are
// always exactly the same width on any screen. The <lg overrides (100vw /
// tablet 50vw) live in globals.css.
const PANEL_WIDTH = "clamp(340px, 25vw, 520px)";

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
 * unmount on close. This panel matches that modal behaviour but stays a
 * hand-rolled overlay so its width can be pinned to the exact same value as
 * the Backtest sheet (`PANEL_WIDTH`).
 *
 * Behaviors:
 * - Esc closes (per spec keyboard support).
 * - Fixed width (no resize) — identical to the Backtest panel on every screen.
 * - Mounts WorkflowEditorMock once the catalog loads; renders skeleton
 *   while loading and a typed error state if the catalog fetch fails.
 */
export function AgentPanel({
  open,
  onOpenChange,
  initialWorkflow,
}: AgentPanelProps): React.ReactElement | null {
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

  if (!open) return null;

  // Every agent editor — a chat draft, or an active/paused agent opened from
  // the Active Agents rail or the Agents tab — renders as a modal overlay:
  // a full-screen dark scrim + white panel, matching the Backtest sheet.
  // Dismiss via the scrim, Esc, or the header X.
  return (
    <>
      <div
        aria-hidden="true"
        onClick={() => onOpenChange(false)}
        className="agent-panel-backdrop fixed inset-0 z-40 bg-black/60 animate-in fade-in-0"
        data-testid="agent-panel-backdrop"
      />
      <aside
      ref={panelRef}
      role="dialog"
      aria-label="Agent panel"
      aria-modal="true"
      style={{
        width: PANEL_WIDTH,
        maxWidth: "100%",
        top: 0,
        animation:
          "agentPanelIn-quartr 300ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
      className={cn(
        // Covers full height and sits above the backdrop scrim. Fixed width
        // (PANEL_WIDTH) matches the Backtest sheet exactly. The mobile/tablet
        // overrides (100vw / 50vw) live in globals.css.
        "agent-panel-shell fixed bottom-0 right-0 z-50 flex border-l bg-background shadow-xl",
      )}
      data-testid="agent-panel"
    >
      <div className="flex h-full w-full flex-col">
        <div className="flex items-center justify-between px-4 py-3">
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
    </>
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
