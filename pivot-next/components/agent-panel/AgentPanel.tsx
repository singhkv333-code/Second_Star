"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Radio, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useExclusiveSidePanel } from "@/lib/sidePanels";
import { useStepCatalog } from "@/components/agent-panel/use-step-catalog";
import {
  WorkflowEditorMock,
  WorkflowEditorSkeleton,
} from "@/components/agent-panel/workflow-editor-mock";
import { DEMO_WORKFLOW } from "@/components/agent-panel/demo-workflow";
import type { Workflow } from "@/lib/types";

export const AGENT_PANEL_MIN_WIDTH = 360;
export const AGENT_PANEL_MAX_WIDTH = 960;
export const AGENT_PANEL_DEFAULT_WIDTH = 640;

const LS_WIDTH_KEY = "pivot.agentPanelWidth";

// Below lg (1024px) the CSS in globals.css forces 100vw via !important, so
// the persisted/dragged width is irrelevant on mobile/tablet — we skip the
// resize handle there.
const DESKTOP_BREAKPOINT = 1024;

function clampWidth(w: number): number {
  const maxW = Math.min(AGENT_PANEL_MAX_WIDTH, window.innerWidth - 360);
  return Math.max(AGENT_PANEL_MIN_WIDTH, Math.min(maxW, w));
}

function readStoredWidth(): number | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(LS_WIDTH_KEY);
    if (!v) return null;
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? clampWidth(n) : null;
  } catch {
    return null;
  }
}

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
  /**
   * Controlled draft for the unsaved case. When set (id=="" and
   * status=="draft"), the editor re-renders whenever chat pushes an
   * amended draft, without remounting. The "Synced with chat" badge
   * appears in the panel header while this is set.
   */
  activeEditorDraft?: Workflow | null;
  /** Called when the editor itself mutates the draft (e.g. step edits). */
  onActiveEditorDraftChange?: (draft: Workflow | null) => void;
};

/**
 * Persistent right-side drawer for the Agent System UI.
 *
 * Why custom (not shadcn `Sheet`)? Sheet is modal — overlay + focus trap +
 * unmount on close. This panel is NON-MODAL: it sits on top without dimming
 * the chat, so the user can read the chat while editing their workflow.
 *
 * Behaviors:
 * - Esc closes (per spec keyboard support).
 * - Draggable left-edge resize handle (desktop only, ≥1024px).
 * - Width persisted to localStorage ("pivot.agentPanelWidth").
 * - Mobile/tablet (<1024px) uses 100vw/50vw from globals.css; resize is
 *   disabled and the stored width is ignored at that breakpoint.
 * - "Synced with chat" badge shows while an unsaved draft is bound, so the
 *   user knows chat amendments flow into the open editor.
 */
export function AgentPanel({
  open,
  onOpenChange,
  initialWorkflow,
  activeEditorDraft,
  onActiveEditorDraftChange,
}: AgentPanelProps): React.ReactElement | null {
  const panelRef = useRef<HTMLElement | null>(null);

  // Mutually exclusive with the other side editors — opening one closes the rest.
  useExclusiveSidePanel("workflow-editor", open, () => onOpenChange(false));

  // Panel width state — initialised from localStorage on first render.
  const [panelWidth, setPanelWidth] = useState<number>(() => {
    return readStoredWidth() ?? AGENT_PANEL_DEFAULT_WIDTH;
  });

  // Whether we're currently on a desktop viewport (>= DESKTOP_BREAKPOINT).
  const [isDesktop, setIsDesktop] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    return window.innerWidth >= DESKTOP_BREAKPOINT;
  });

  // Track viewport changes so resize handle hides below lg.
  useEffect(() => {
    const mq = window.matchMedia(`(min-width: ${DESKTOP_BREAKPOINT}px)`);
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener("change", handler);
    setIsDesktop(mq.matches);
    return () => mq.removeEventListener("change", handler);
  }, []);

  // Publish the panel's effective width as a global CSS variable so the
  // chat surface can compress instead of being hidden behind the overlay
  // (draft cards / backtest charts stayed covered before). Desktop only —
  // below lg the panel is 100vw/50vw via globals.css and the chat isn't
  // usable alongside it anyway. Cleared on close/unmount.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    if (open && isDesktop) {
      root.style.setProperty("--side-panel-width", `${panelWidth}px`);
    } else {
      root.style.setProperty("--side-panel-width", "0px");
    }
    return () => {
      root.style.setProperty("--side-panel-width", "0px");
    };
  }, [open, isDesktop, panelWidth]);

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

  // Drag-resize logic — attached only on desktop.
  const isDraggingRef = useRef(false);
  const dragStartXRef = useRef(0);
  const dragStartWidthRef = useRef(0);

  const onMouseMove = useCallback((e: MouseEvent) => {
    if (!isDraggingRef.current) return;
    // Panel is on the right; dragging left (smaller x) makes it wider.
    const delta = dragStartXRef.current - e.clientX;
    const newWidth = clampWidth(dragStartWidthRef.current + delta);
    setPanelWidth(newWidth);
  }, []);

  const onMouseUp = useCallback(() => {
    if (!isDraggingRef.current) return;
    isDraggingRef.current = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    // Persist to localStorage after drag ends.
    setPanelWidth((w) => {
      try {
        window.localStorage.setItem(LS_WIDTH_KEY, String(w));
      } catch {
        // Storage may be unavailable; silently ignore.
      }
      return w;
    });
    window.removeEventListener("mousemove", onMouseMove);
    window.removeEventListener("mouseup", onMouseUp);
  }, [onMouseMove]);

  const onHandleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (!isDesktop) return;
      e.preventDefault();
      isDraggingRef.current = true;
      dragStartXRef.current = e.clientX;
      dragStartWidthRef.current = panelWidth;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [isDesktop, panelWidth, onMouseMove, onMouseUp],
  );

  // Cleanup listeners if the component unmounts while dragging.
  useEffect(() => {
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [onMouseMove, onMouseUp]);

  if (!open) return null;

  // The effective inline width: only applied on desktop; on mobile/tablet the
  // CSS in globals.css overrides it via !important.
  const inlineWidth = isDesktop ? panelWidth : undefined;

  return (
    <>
      {/*
        NON-MODAL: transparent, pointer-events-none layer — no dark overlay.
        The chat stays fully visible and interactive while the panel is open.
        Closing is handled by the X button and Esc only (no click-to-dismiss
        scrim, which would block chat interaction).
      */}
      <div
        aria-hidden="true"
        className="agent-panel-backdrop fixed inset-0 z-40 pointer-events-none"
        data-testid="agent-panel-backdrop"
      />
      <aside
        ref={panelRef}
        role="dialog"
        aria-label="Agent panel"
        aria-modal="false"
        style={{
          width: inlineWidth,
          maxWidth: "100%",
          top: 0,
          animation:
            "agentPanelIn-quartr 300ms cubic-bezier(0.22, 1, 0.36, 1) both",
        }}
        className={cn(
          // Covers full height and sits above content. The mobile/tablet
          // overrides (100vw / 50vw) live in globals.css.
          "agent-panel-shell fixed bottom-0 right-0 z-50 flex border-l bg-background shadow-xl",
        )}
        data-testid="agent-panel"
      >
        {/* Drag-resize handle — left edge, desktop only */}
        {isDesktop && (
          <div
            role="separator"
            aria-label="Resize agent panel"
            aria-orientation="vertical"
            onMouseDown={onHandleMouseDown}
            className={cn(
              "agent-panel-resize-handle",
              "absolute left-0 top-0 z-10 flex h-full w-1.5 cursor-col-resize items-center justify-center",
              "bg-transparent transition-colors hover:bg-primary/10 active:bg-primary/20",
              // Subtle grip dots visible on hover
              "group",
            )}
            data-testid="agent-panel-resize-handle"
          >
            {/* Three faint dots as a grip affordance */}
            <span
              aria-hidden="true"
              className="flex flex-col gap-[3px] opacity-0 transition-opacity group-hover:opacity-60"
            >
              <span className="h-[3px] w-[3px] rounded-full bg-muted-foreground" />
              <span className="h-[3px] w-[3px] rounded-full bg-muted-foreground" />
              <span className="h-[3px] w-[3px] rounded-full bg-muted-foreground" />
            </span>
          </div>
        )}

        <div className="flex h-full w-full flex-col">
          <div className="flex items-center justify-between px-4 py-3">
            <div className="flex items-center gap-2">
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
              {/* "Synced with chat" badge — visible only while an unsaved
                  draft is bound so the user knows edits can come from chat. */}
              {activeEditorDraft !== null && activeEditorDraft !== undefined && (
                <span
                  data-testid="synced-with-chat-badge"
                  className="inline-flex items-center gap-1 rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-medium text-sky-700 dark:bg-sky-500/15 dark:text-sky-300"
                >
                  <Radio className="h-2.5 w-2.5" aria-hidden="true" />
                  Synced with chat
                </span>
              )}
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
            <AgentPanelBody
              initialWorkflow={initialWorkflow}
              activeEditorDraft={activeEditorDraft}
              onActiveEditorDraftChange={onActiveEditorDraftChange}
            />
          </div>
        </div>
      </aside>
    </>
  );
}

function AgentPanelBody({
  initialWorkflow,
  activeEditorDraft,
  onActiveEditorDraftChange,
}: {
  initialWorkflow?: Workflow;
  activeEditorDraft?: Workflow | null;
  onActiveEditorDraftChange?: (draft: Workflow | null) => void;
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

  // The panel is bound to an editable draft SESSION whenever a non-null
  // activeEditorDraft is present. It stays bound across Save/Activate — which
  // give the draft a real id and flip its status draft→active — so the editor
  // keeps rendering the fresh controlled copy (activeEditorDraft) instead of
  // falling back to the stale `initialWorkflow` snapshot. `initialWorkflow`
  // (panelWorkflow) is captured at open time and never sees the post-activate
  // status, so the old draft/local-id gate flipped this off the instant the
  // draft was activated and remounted the editor on the stale snapshot — which
  // is why the "Activate" button stayed stuck until a tab switch refetched it.
  //   AppShell.openWorkflow clears activeEditorDraft when a SAVED workflow is
  // opened, so a leftover draft never shadows a real agent opened from a list.
  const hasDraftSession =
    activeEditorDraft !== null && activeEditorDraft !== undefined;

  const resolved = hasDraftSession
    ? activeEditorDraft
    : (initialWorkflow ?? DEMO_WORKFLOW);

  // Key logic:
  //   - Draft session: key="active-draft-session" so the editor stays mounted
  //     across chat amendments AND the save/activate transition. Changes arrive
  //     via the controlledWorkflow prop; the status pill + Activate/Pause button
  //     update in place with no remount.
  //   - Saved workflow: key=id so switching agents remounts cleanly.
  //   - Demo: key="demo".
  const editorKey = hasDraftSession
    ? "active-draft-session"
    : (resolved.id || "demo");

  return (
    <WorkflowEditorMock
      key={editorKey}
      initialWorkflow={resolved}
      catalog={state.catalog}
      controlledWorkflow={hasDraftSession ? activeEditorDraft : undefined}
      onControlledWorkflowChange={hasDraftSession ? onActiveEditorDraftChange : undefined}
    />
  );
}
