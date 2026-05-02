"use client";

/**
 * AppShell — top-level navigation and tab switching for the Pivot
 * Agent System UI.
 *
 * Four tabs alongside the existing Chat surface (per the dark Quartr
 * mock the user shared): Chat / Agents / Calendar / Portfolio. Active
 * tab persists in the URL hash so reload preserves state.
 *
 * "Chat" is a thin placeholder for now — the real chatbot lives in the
 * legacy `frontend/` Vite app. v2 ports the chat into pivot-next/. The
 * placeholder explains the situation so reviewers don't think the tab
 * is broken.
 *
 * AgentPanel mounts as a persistent right-side drawer that overlays
 * any active tab. Opens via:
 *   - AgentsTab row click (selected saved workflow)
 *   - CalendarTab row click (workflow id only — fetch then open)
 *   - WorkflowDraftCard "Open in editor →" (chat-side, draft-mode)
 */

import { useCallback, useEffect, useState } from "react";
import {
  CalendarDays,
  LayoutGrid,
  MessageSquare,
  PieChart,
} from "lucide-react";
import { AgentPanel } from "@/components/agent-panel/AgentPanel";
import { AgentsTab } from "@/components/agent-panel/AgentsTab";
import { CalendarTab } from "@/components/CalendarTab";
import { PortfolioTab } from "@/components/agent-panel/PortfolioTab";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getWorkflow } from "@/lib/api";
import type { Workflow } from "@/lib/types";
import { isError } from "@/lib/types";

type TabKey = "chat" | "agents" | "calendar" | "portfolio";

const TABS: {
  key: TabKey;
  label: string;
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}[] = [
  { key: "chat", label: "Chat", Icon: MessageSquare },
  { key: "agents", label: "Agents", Icon: LayoutGrid },
  { key: "calendar", label: "Calendar", Icon: CalendarDays },
  { key: "portfolio", label: "Portfolio", Icon: PieChart },
];

const DEFAULT_TAB: TabKey = "agents";

function readHashTab(): TabKey {
  if (typeof window === "undefined") return DEFAULT_TAB;
  const raw = window.location.hash.replace(/^#/, "");
  return TABS.some((t) => t.key === raw) ? (raw as TabKey) : DEFAULT_TAB;
}

export function AppShell(): React.ReactElement {
  const [active, setActive] = useState<TabKey>(DEFAULT_TAB);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelWorkflow, setPanelWorkflow] = useState<Workflow | undefined>(
    undefined,
  );

  // Sync active tab to URL hash on mount + when hash changes externally.
  useEffect(() => {
    setActive(readHashTab());
    const onHash = (): void => setActive(readHashTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const goTab = useCallback((key: TabKey): void => {
    setActive(key);
    if (typeof window !== "undefined") {
      window.history.replaceState(null, "", `#${key}`);
    }
  }, []);

  const openWorkflow = useCallback((workflow: Workflow): void => {
    setPanelWorkflow(workflow);
    setPanelOpen(true);
  }, []);

  const openWorkflowById = useCallback(async (id: string): Promise<void> => {
    const result = await getWorkflow(id);
    if (isError(result)) return;
    openWorkflow(result.data);
  }, [openWorkflow]);

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <Header active={active} onTabChange={goTab} />

      <main className="flex-1">
        <div className="mx-auto max-w-6xl px-6 py-8">
          {active === "chat" && <ChatPlaceholder />}
          {active === "agents" && <AgentsTab onOpenWorkflow={openWorkflow} />}
          {active === "calendar" && (
            <CalendarTab onOpenWorkflow={openWorkflowById} />
          )}
          {active === "portfolio" && <PortfolioTab />}
        </div>
      </main>

      <AgentPanel
        open={panelOpen}
        onOpenChange={setPanelOpen}
        initialWorkflow={panelWorkflow}
      />
    </div>
  );
}

// ── Header (logo + tab strip) ─────────────────────────────────────────

function Header({
  active,
  onTabChange,
}: {
  active: TabKey;
  onTabChange: (key: TabKey) => void;
}): React.ReactElement {
  return (
    <header className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-6 px-6 py-3">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10">
            <span className="text-sm font-bold text-primary">P</span>
          </div>
          <span className="text-sm font-semibold tracking-tight">Pivot</span>
        </div>

        <nav
          aria-label="Primary"
          className="flex items-center gap-1"
          data-testid="tab-strip"
        >
          {TABS.map(({ key, label, Icon }) => {
            const isActive = active === key;
            return (
              <Button
                key={key}
                variant={isActive ? "default" : "ghost"}
                size="sm"
                onClick={() => onTabChange(key)}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "h-8 gap-1.5 rounded-full px-3 text-xs font-medium",
                  !isActive && "text-muted-foreground hover:text-foreground",
                )}
                data-testid={`tab-${key}`}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden={true} />
                {label}
              </Button>
            );
          })}
        </nav>

        <div className="w-16" /> {/* spacer for symmetry with the logo */}
      </div>
    </header>
  );
}

// ── Chat placeholder (real chat lives in legacy frontend/) ──────────

function ChatPlaceholder(): React.ReactElement {
  return (
    <div
      className="rounded-xl border bg-card p-8 text-center shadow-sm"
      data-testid="chat-placeholder"
    >
      <MessageSquare
        className="mx-auto mb-3 h-8 w-8 text-muted-foreground"
        aria-hidden={true}
      />
      <p className="text-sm font-medium">Chat lives in the legacy frontend</p>
      <p className="mx-auto mt-2 max-w-md text-xs text-muted-foreground">
        The Pivot chatbot is currently served from the Vite app at{" "}
        <code className="rounded bg-muted px-1 py-0.5 font-mono">/frontend</code>
        . When the chatbot calls{" "}
        <code className="rounded bg-muted px-1 py-0.5 font-mono">
          propose_workflow
        </code>{" "}
        and you click <span className="font-medium text-foreground">
          Open in editor →
        </span>
        , the draft will land in this app&apos;s editor panel.
      </p>
      <p className="mx-auto mt-3 max-w-md text-xs text-muted-foreground">
        While you&apos;re here, jump to{" "}
        <button
          type="button"
          onClick={() => {
            window.location.hash = "agents";
          }}
          className="font-medium text-primary hover:underline"
        >
          Agents
        </button>{" "}
        to see your saved agents, or{" "}
        <button
          type="button"
          onClick={() => {
            window.location.hash = "calendar";
          }}
          className="font-medium text-primary hover:underline"
        >
          Calendar
        </button>{" "}
        for upcoming runs.
      </p>
    </div>
  );
}
