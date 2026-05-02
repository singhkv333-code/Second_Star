"use client";

/**
 * AppShell — Quartr-style premium shell with left sidebar nav, center content,
 * and (on dashboard) right Active Agents rail.
 *
 * Layout:
 *   [Sticky top header: logo + search + metric strip + theme toggle + avatar]
 *   [Left sidebar nav | Center content pane | Right rail (dashboard only)]
 *
 * Nav items: Dashboard / Chat / Portfolio / News / Agents / Calendar / Screener
 * Active item: solid left border + bg highlight
 * Below nav: YOUR CONVERSATIONS — localStorage chat history or empty state
 *
 * Default tab is now "dashboard" (was "agents").
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  BarChart2,
  CalendarDays,
  LayoutGrid,
  MessageSquare,
  Moon,
  Newspaper,
  PieChart,
  Search,
  Settings,
  Sun,
  TrendingDown,
  TrendingUp,
  User,
} from "lucide-react";
import { AgentPanel } from "@/components/agent-panel/AgentPanel";
import { AgentsTab } from "@/components/agent-panel/AgentsTab";
import { CalendarTab } from "@/components/CalendarTab";
import { PortfolioTab } from "@/components/agent-panel/PortfolioTab";
import { ChatDemo } from "@/components/chat/ChatDemo";
import { DashboardTab } from "@/components/DashboardTab";
import { ActiveAgentsRail } from "@/components/ActiveAgentsRail";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  getPortfolioSummary,
  getWorkflow,
  listConversations,
  type PortfolioSummary,
} from "@/lib/api";
import type { Workflow } from "@/lib/types";
import { isError } from "@/lib/types";

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------

type TabKey =
  | "dashboard"
  | "chat"
  | "portfolio"
  | "news"
  | "agents"
  | "calendar"
  | "screener";

const NAV_ITEMS: {
  key: TabKey;
  label: string;
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}[] = [
  { key: "dashboard", label: "Dashboard", Icon: LayoutGrid },
  { key: "chat", label: "Chat", Icon: MessageSquare },
  { key: "portfolio", label: "Portfolio", Icon: PieChart },
  { key: "news", label: "News", Icon: Newspaper },
  { key: "agents", label: "Agents", Icon: Settings },
  { key: "calendar", label: "Calendar", Icon: CalendarDays },
  { key: "screener", label: "Screener", Icon: BarChart2 },
];

const DEFAULT_TAB: TabKey = "dashboard";
const METRIC_REFRESH_MS = 30_000;

function readHashTab(): TabKey {
  if (typeof window === "undefined") return DEFAULT_TAB;
  const raw = window.location.hash.replace(/^#/, "");
  const valid: TabKey[] = NAV_ITEMS.map((t) => t.key);
  return valid.includes(raw as TabKey) ? (raw as TabKey) : DEFAULT_TAB;
}

// ---------------------------------------------------------------------------
// Theme helpers
// ---------------------------------------------------------------------------

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
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(t: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", t === "dark");
}

// ---------------------------------------------------------------------------
// Conversation history — GET /api/conversations (wired Day 8)
// ---------------------------------------------------------------------------

type ConvEntry = { id: string; preview: string };

async function fetchConversations(): Promise<ConvEntry[]> {
  try {
    const result = await listConversations({ limit: 10 });
    if (isError(result)) return [];
    return result.data.items.map((c) => ({
      id: c.id,
      preview: c.title ?? "Untitled conversation",
    }));
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Metric strip state
// ---------------------------------------------------------------------------

type MetricState =
  | { kind: "loading" }
  | { kind: "ok"; summary: PortfolioSummary }
  | { kind: "hidden" };

// ---------------------------------------------------------------------------
// INR formatter
// ---------------------------------------------------------------------------

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
function fmt(n: number): string {
  return INR.format(n);
}

// ---------------------------------------------------------------------------
// AppShell
// ---------------------------------------------------------------------------

export function AppShell(): React.ReactElement {
  const [active, setActive] = useState<TabKey>(DEFAULT_TAB);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelWorkflow, setPanelWorkflow] = useState<Workflow | undefined>(undefined);
  const [metrics, setMetrics] = useState<MetricState>({ kind: "loading" });
  const [theme, setTheme] = useState<Theme>("light");
  const [conversations, setConversations] = useState<ConvEntry[]>([]);
  const [chatPrefill, setChatPrefill] = useState<string | undefined>(undefined);
  const metricTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Hash + theme init
  useEffect(() => {
    setActive(readHashTab());
    const onHash = (): void => setActive(readHashTab());
    window.addEventListener("hashchange", onHash);

    const initial = readStoredTheme() ?? getSystemTheme();
    setTheme(initial);
    applyTheme(initial);

    // Load conversations from real backend
    void fetchConversations().then(setConversations);

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

  // Metric strip loading
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

  /** Called from DashboardTab chips / chat input: route to Chat tab w/ prefill. */
  const handleDashboardPrompt = useCallback((prompt: string): void => {
    setChatPrefill(prompt);
    goTab("chat");
  }, [goTab]);

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* Sticky top header */}
      <TopHeader
        theme={theme}
        onToggleTheme={toggleTheme}
        metrics={metrics}
      />

      {/* Body: sidebar + content */}
      <div className="flex flex-1">
        {/* Left sidebar */}
        <Sidebar
          active={active}
          onTabChange={goTab}
          conversations={conversations}
        />

        {/* Center pane */}
        <main className="flex-1 min-w-0">
          <div
            className={cn(
              "mx-auto px-6 py-6",
              // Dashboard has right rail: constrain center width
              active === "dashboard" ? "max-w-3xl" : "max-w-3xl",
            )}
          >
            {active === "dashboard" && (
              <DashboardTab
                onSubmitPrompt={handleDashboardPrompt}
                onOpenCalendar={() => goTab("calendar")}
              />
            )}
            {active === "chat" && (
              <ChatDemo onOpenEditor={openWorkflow} prefill={chatPrefill} onPrefillConsumed={() => setChatPrefill(undefined)} />
            )}
            {active === "agents" && <AgentsTab onOpenWorkflow={openWorkflow} />}
            {active === "calendar" && (
              <CalendarTab onOpenWorkflow={openWorkflowById} />
            )}
            {active === "portfolio" && <PortfolioTab />}
            {active === "news" && <NewsPlaceholder />}
            {active === "screener" && <ScreenerPlaceholder />}
          </div>
        </main>

        {/* Right rail — dashboard only */}
        {active === "dashboard" && (
          <aside className="hidden w-72 shrink-0 border-l px-4 py-6 xl:block">
            <ActiveAgentsRail onOpenWorkflow={openWorkflow} />
          </aside>
        )}
      </div>

      <AgentPanel
        open={panelOpen}
        onOpenChange={setPanelOpen}
        initialWorkflow={panelWorkflow}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top Header
// ---------------------------------------------------------------------------

function TopHeader({
  theme,
  onToggleTheme,
  metrics,
}: {
  theme: Theme;
  onToggleTheme: () => void;
  metrics: MetricState;
}): React.ReactElement {
  return (
    <header className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex items-center gap-4 px-6 py-3">
        {/* Logo */}
        <div className="flex shrink-0 items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10">
            <span className="text-sm font-bold text-primary">P</span>
          </div>
          <span className="text-sm font-semibold tracking-tight">Pivot</span>
        </div>

        {/* Global search */}
        <div className="flex flex-1 items-center">
          <div className="relative mx-auto w-full max-w-md">
            <Search
              className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden={true}
            />
            <input
              type="search"
              placeholder="Search stocks, strategies, conversations…"
              aria-label="Global search"
              data-testid="global-search"
              className={cn(
                "h-8 w-full rounded-full border bg-muted/40 pl-9 pr-4 text-xs",
                "placeholder:text-muted-foreground/60",
                "focus:outline-none focus:ring-2 focus:ring-ring",
              )}
            />
          </div>
        </div>

        {/* Metric strip */}
        <MetricStrip metrics={metrics} />

        {/* Theme toggle */}
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleTheme}
          aria-label={
            theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
          }
          data-testid="theme-toggle"
          className="h-8 w-8 shrink-0 rounded-full p-0"
        >
          {theme === "dark" ? (
            <Sun className="h-4 w-4" aria-hidden={true} />
          ) : (
            <Moon className="h-4 w-4" aria-hidden={true} />
          )}
        </Button>

        {/* Avatar */}
        <div
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/15"
          aria-label="User profile"
        >
          <User className="h-4 w-4 text-primary" aria-hidden={true} />
        </div>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Metric strip
// ---------------------------------------------------------------------------

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
    <div className="flex items-center gap-1" aria-label={`${label}: ${fmt(value)}`}>
      <span className="text-[11px] text-muted-foreground">{label}</span>
      {positive ? (
        <TrendingUp className="h-3 w-3 text-emerald-500" aria-hidden={true} />
      ) : (
        <TrendingDown className="h-3 w-3 text-rose-500" aria-hidden={true} />
      )}
      <span
        className={cn(
          "text-xs font-medium tabular-nums",
          positive
            ? "text-emerald-600 dark:text-emerald-400"
            : "text-rose-600 dark:text-rose-400",
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
        className="hidden items-center gap-4 lg:flex"
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
      className="hidden items-center gap-4 lg:flex"
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

// ---------------------------------------------------------------------------
// Left sidebar
// ---------------------------------------------------------------------------

function Sidebar({
  active,
  onTabChange,
  conversations,
}: {
  active: TabKey;
  onTabChange: (key: TabKey) => void;
  conversations: ConvEntry[];
}): React.ReactElement {
  return (
    <nav
      className="hidden w-52 shrink-0 border-r bg-background/50 lg:flex lg:flex-col"
      aria-label="Primary navigation"
      data-testid="sidebar-nav"
    >
      {/* Nav items */}
      <ul className="flex flex-col gap-0.5 p-3 pt-4" role="list">
        {NAV_ITEMS.map(({ key, label, Icon }) => {
          const isActive = active === key;
          return (
            <li key={key}>
              <button
                type="button"
                onClick={() => onTabChange(key)}
                aria-current={isActive ? "page" : undefined}
                data-testid={`nav-${key}`}
                className={cn(
                  "group relative flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-primary/8 font-medium text-foreground"
                    : "font-normal text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                )}
              >
                {/* Active dot on the right */}
                {isActive && (
                  <span
                    className="absolute right-2.5 top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-primary"
                    aria-hidden={true}
                  />
                )}
                <Icon
                  className={cn(
                    "h-4 w-4 shrink-0",
                    isActive ? "text-primary" : "text-muted-foreground group-hover:text-foreground",
                  )}
                  aria-hidden={true}
                />
                {label}
              </button>
            </li>
          );
        })}
      </ul>

      {/* Conversations section — wired to GET /api/conversations */}
      <div className="mt-4 flex-1 overflow-y-auto px-3">
        <p className="mb-2 px-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Your Conversations
        </p>
        {conversations.length === 0 ? (
          <p className="px-1 text-[11px] text-muted-foreground">
            Start a chat to see history.
          </p>
        ) : (
          <ul className="space-y-0.5" role="list">
            {conversations.map((conv) => (
              <li key={conv.id}>
                <button
                  type="button"
                  className="w-full truncate rounded-md px-2 py-1.5 text-left text-[11px] text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                  onClick={() => onTabChange("chat")}
                  aria-label={`Open conversation: ${conv.preview}`}
                >
                  {conv.preview}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Placeholder tabs
// ---------------------------------------------------------------------------

function NewsPlaceholder(): React.ReactElement {
  return (
    <div
      className="flex flex-col items-center justify-center py-20 text-center"
      data-testid="news-placeholder"
      aria-label="News — coming soon"
    >
      <Newspaper className="mb-4 h-10 w-10 text-muted-foreground/40" aria-hidden={true} />
      <h2 className="text-base font-semibold text-foreground">News</h2>
      <p className="mt-2 max-w-xs text-sm text-muted-foreground">
        News integration is coming in v2. Real-time market news, earnings
        announcements, and corporate actions will appear here.
      </p>
    </div>
  );
}

function ScreenerPlaceholder(): React.ReactElement {
  return (
    <div
      className="flex flex-col items-center justify-center py-20 text-center"
      data-testid="screener-placeholder"
      aria-label="Screener — coming soon"
    >
      <BarChart2 className="mb-4 h-10 w-10 text-muted-foreground/40" aria-hidden={true} />
      <h2 className="text-base font-semibold text-foreground">Screener</h2>
      <p className="mt-2 max-w-xs text-sm text-muted-foreground">
        Stock screener is coming in v2. Filter NSE stocks by fundamentals,
        technicals, and custom criteria.
      </p>
    </div>
  );
}
