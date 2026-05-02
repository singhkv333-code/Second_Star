"use client";

/**
 * AppShell — top-level navigation and tab switching for the Pivot
 * Agent System UI.
 *
 * Four tabs: Chat / Agents / Calendar / Portfolio. Active tab persists
 * in the URL hash so reload preserves state.
 *
 * Header (#40) shows portfolio metric strip (value, day P&L, total P&L)
 * refreshed on tab change and every 30s. Skeleton while loading; hides
 * gracefully on error so it never blocks tabs.
 *
 * Header (#41) also has a light/dark mode toggle (sun/moon). Persisted in
 * localStorage; defaults to prefers-color-scheme. Applies Tailwind `dark`
 * class to <html>.
 *
 * AgentPanel mounts as a persistent right-side drawer that overlays
 * any active tab. Opens via:
 *   - AgentsTab row click (selected saved workflow)
 *   - CalendarTab row click (workflow id only — fetch then open)
 *   - WorkflowDraftCard "Open in editor →" (chat-side, draft-mode)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CalendarDays,
  LayoutGrid,
  MessageSquare,
  Moon,
  PieChart,
  Sun,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { AgentPanel } from "@/components/agent-panel/AgentPanel";
import { AgentsTab } from "@/components/agent-panel/AgentsTab";
import { CalendarTab } from "@/components/CalendarTab";
import { PortfolioTab } from "@/components/agent-panel/PortfolioTab";
import { ChatDemo } from "@/components/chat/ChatDemo";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { getPortfolioSummary, getWorkflow, type PortfolioSummary } from "@/lib/api";
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
const METRIC_REFRESH_MS = 30_000;

function readHashTab(): TabKey {
  if (typeof window === "undefined") return DEFAULT_TAB;
  const raw = window.location.hash.replace(/^#/, "");
  return TABS.some((t) => t.key === raw) ? (raw as TabKey) : DEFAULT_TAB;
}

// ── Theme helpers (#41) ──────────────────────────────────────────────

type Theme = "light" | "dark";
const LS_KEY = "pivot-theme";

function readStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(LS_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

function getSystemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function applyTheme(t: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", t === "dark");
}

// ── AppShell ─────────────────────────────────────────────────────────

export function AppShell(): React.ReactElement {
  const [active, setActive] = useState<TabKey>(DEFAULT_TAB);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelWorkflow, setPanelWorkflow] = useState<Workflow | undefined>(
    undefined,
  );

  // #40 — metric strip state
  type MetricState =
    | { kind: "loading" }
    | { kind: "ok"; summary: PortfolioSummary }
    | { kind: "hidden" }; // hide on error — don't block tabs
  const [metrics, setMetrics] = useState<MetricState>({ kind: "loading" });
  const metricTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // #41 — theme state
  const [theme, setTheme] = useState<Theme>("light");

  // On mount: read hash, read theme, apply theme
  useEffect(() => {
    setActive(readHashTab());
    const onHash = (): void => setActive(readHashTab());
    window.addEventListener("hashchange", onHash);

    const initial = readStoredTheme() ?? getSystemTheme();
    setTheme(initial);
    applyTheme(initial);

    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const toggleTheme = useCallback((): void => {
    setTheme((prev) => {
      const next: Theme = prev === "light" ? "dark" : "light";
      applyTheme(next);
      try {
        localStorage.setItem(LS_KEY, next);
      } catch { /* ignore */ }
      return next;
    });
  }, []);

  // #40 — load metrics; refresh on tab change and every 30s
  const loadMetrics = useCallback((): void => {
    getPortfolioSummary()
      .then((result) => {
        if (isError(result)) {
          setMetrics({ kind: "hidden" });
          return;
        }
        setMetrics({ kind: "ok", summary: result.data });
      })
      .catch(() => setMetrics({ kind: "hidden" }));
  }, []);

  useEffect(() => {
    loadMetrics();
    if (metricTimerRef.current) clearInterval(metricTimerRef.current);
    metricTimerRef.current = setInterval(loadMetrics, METRIC_REFRESH_MS);
    return () => {
      if (metricTimerRef.current) clearInterval(metricTimerRef.current);
    };
  }, [active, loadMetrics]);

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
      <Header
        active={active}
        onTabChange={goTab}
        metrics={metrics}
        theme={theme}
        onToggleTheme={toggleTheme}
      />

      <main className="flex-1">
        <div className="mx-auto max-w-6xl px-6 py-8">
          {active === "chat" && <ChatDemo onOpenEditor={openWorkflow} />}
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

// ── Header (logo + tab strip + metric strip + theme toggle) ───────────

type MetricState =
  | { kind: "loading" }
  | { kind: "ok"; summary: PortfolioSummary }
  | { kind: "hidden" };

function Header({
  active,
  onTabChange,
  metrics,
  theme,
  onToggleTheme,
}: {
  active: TabKey;
  onTabChange: (key: TabKey) => void;
  metrics: MetricState;
  theme: "light" | "dark";
  onToggleTheme: () => void;
}): React.ReactElement {
  return (
    <header className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="mx-auto flex max-w-6xl items-center gap-4 px-6 py-3">
        {/* Logo */}
        <div className="flex items-center gap-2 shrink-0">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10">
            <span className="text-sm font-bold text-primary">P</span>
          </div>
          <span className="text-sm font-semibold tracking-tight">Pivot</span>
        </div>

        {/* #40 — Metric strip */}
        <MetricStrip metrics={metrics} />

        {/* Spacer */}
        <div className="flex-1" />

        {/* Tab strip */}
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

        {/* #41 — Theme toggle */}
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleTheme}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          data-testid="theme-toggle"
          className="h-8 w-8 shrink-0 rounded-full p-0"
        >
          {theme === "dark" ? (
            <Sun className="h-4 w-4" aria-hidden={true} />
          ) : (
            <Moon className="h-4 w-4" aria-hidden={true} />
          )}
        </Button>
      </div>
    </header>
  );
}

// ── Metric strip (#40) ───────────────────────────────────────────────

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

function fmt(n: number): string {
  return INR.format(n);
}

function PnlChip({
  value,
  pct,
  label,
}: {
  value: number;
  pct?: number;
  label: string;
}): React.ReactElement {
  const positive = value >= 0;
  return (
    <div
      className="flex items-center gap-1"
      aria-label={`${label}: ${fmt(value)}`}
    >
      <span className="text-[11px] text-muted-foreground">{label}</span>
      {positive ? (
        <TrendingUp className="h-3 w-3 text-emerald-500" aria-hidden={true} />
      ) : (
        <TrendingDown className="h-3 w-3 text-rose-500" aria-hidden={true} />
      )}
      <span
        className={cn(
          "text-xs font-medium tabular-nums",
          positive ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400",
        )}
      >
        {positive ? "+" : ""}
        {fmt(value)}
        {pct !== undefined && (
          <span className="ml-0.5 text-[10px] opacity-70">
            ({positive ? "+" : ""}{pct.toFixed(2)}%)
          </span>
        )}
      </span>
    </div>
  );
}

function MetricStrip({ metrics }: { metrics: MetricState }): React.ReactElement | null {
  if (metrics.kind === "hidden") return null;

  if (metrics.kind === "loading") {
    return (
      <div
        className="flex items-center gap-4"
        data-testid="metric-strip-loading"
        aria-label="Loading portfolio metrics"
      >
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-4 w-20" />
      </div>
    );
  }

  const { summary } = metrics;
  return (
    <div
      className="flex items-center gap-4"
      data-testid="metric-strip"
      role="status"
      aria-label="Portfolio metrics"
    >
      <div aria-label={`Portfolio value: ${fmt(summary.total_value)}`}>
        <span className="text-[11px] text-muted-foreground">Portfolio </span>
        <span className="text-xs font-semibold tabular-nums">
          {fmt(summary.total_value)}
        </span>
      </div>
      <PnlChip value={summary.day_pnl} label="Day" />
      <PnlChip value={summary.total_pnl} pct={summary.total_pnl_pct} label="Total" />
    </div>
  );
}
