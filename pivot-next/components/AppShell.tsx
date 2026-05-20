"use client";

/**
 * AppShell — Quartr-style premium shell with left sidebar nav, center content,
 * and (on dashboard) right Active Agents rail.
 *
 * Layout:
 *   [Sticky top header: logo + search + metric strip + theme toggle + avatar]
 *   [Left sidebar nav | Center content pane | Right rail (dashboard only)]
 *
 * Nav items: Chat / Portfolio / News / Agents / Calendar / Screener / Backtest
 * Active item: solid left border + bg highlight
 * Below nav: YOUR CONVERSATIONS — opens the Chat tab
 *
 * Chat is the default tab and the home of all conversational interaction
 * with Pivot. Until the user sends their first message it shows a
 * dashboard intro (greeting + index strip + quick-action chips) above
 * the composer; once a message lands the intro disappears and the
 * transcript fills the pane with the composer pinned at the bottom.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  BarChart2,
  Bug,
  CalendarDays,
  ChevronLeft,
  ExternalLink,
  FileText,
  HelpCircle,
  Keyboard,
  KeyRound,
  LogOut,
  MessageSquare,
  Monitor,
  Moon,
  PieChart,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  Sun,
  X,
} from "lucide-react";
import { CommandPalette } from "@/components/CommandPalette";
import {
  KiteCredentialsPanel,
  type KiteOAuthResult,
} from "@/components/KiteCredentialsPanel";
import { AgentPanel, AGENT_PANEL_DEFAULT_WIDTH } from "@/components/agent-panel/AgentPanel";
import { AgentsTab } from "@/components/agent-panel/AgentsTab";
import { CalendarTab } from "@/components/CalendarTab";
import { PortfolioTab } from "@/components/agent-panel/PortfolioTab";
import { ScreenerPage } from "@/components/screener/ScreenerPage";
import { DashboardTab } from "@/components/DashboardTab";
import { ActiveAgentsRail } from "@/components/ActiveAgentsRail";
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
  | "chat"
  | "portfolio"
  | "agents"
  | "calendar"
  | "screener";

const NAV_ITEMS: {
  key: TabKey;
  label: string;
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}[] = [
  { key: "chat", label: "Chat", Icon: MessageSquare },
  { key: "portfolio", label: "Portfolio", Icon: PieChart },
  { key: "agents", label: "Agents", Icon: Settings },
  { key: "calendar", label: "Calendar", Icon: CalendarDays },
  { key: "screener", label: "Screener", Icon: BarChart2 },
];

const DEFAULT_TAB: TabKey = "chat";
const METRIC_REFRESH_MS = 30_000;

function readHashTab(): TabKey {
  if (typeof window === "undefined") return DEFAULT_TAB;
  const raw = window.location.hash.replace(/^#/, "");
  const valid: TabKey[] = NAV_ITEMS.map((t) => t.key);
  return valid.includes(raw as TabKey) ? (raw as TabKey) : DEFAULT_TAB;
}

// ---------------------------------------------------------------------------
// Theme helpers — three modes (Dark / Light / System).
//
// "system" defers to the OS preference and live-updates if the user changes
// their system theme. The actual class applied to <html> is always "dark"
// or no class — only the *resolution* is three-state.
// ---------------------------------------------------------------------------

type Theme = "light" | "dark" | "system";
const LS_KEY = "pivot-theme";

function readStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(LS_KEY);
    return v === "light" || v === "dark" || v === "system" ? v : null;
  } catch {
    return null;
  }
}

function osPrefersDark(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Apply a theme choice to the <html> element. With "system", resolve to
 *  the current OS preference; otherwise honor the explicit choice. */
function applyTheme(t: Theme): void {
  if (typeof document === "undefined") return;
  const isDark = t === "dark" || (t === "system" && osPrefersDark());
  document.documentElement.classList.toggle("dark", isDark);
}

/** Resolve "system" to a concrete dark/light boolean so the topbar can
 *  pick the right Pivot logo asset for the current theme. */
function resolvedDark(t: Theme): boolean {
  return t === "dark" || (t === "system" && osPrefersDark());
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

/**
 * AppShell supports an optional `children` slot. When provided
 * (e.g. by the stock detail route), `children` renders inside the
 * main pane in place of the tab-router content, and the right rail
 * stays hidden. The topbar + sidebar are unchanged so the user keeps
 * navigation consistent across the app.
 */
export type AppShellProps = {
  /** Optional override content for the main pane. */
  children?: React.ReactNode;
};

export function AppShell({ children }: AppShellProps = {}): React.ReactElement {
  const router = useRouter();
  const pathname = usePathname();
  const [active, setActive] = useState<TabKey>(DEFAULT_TAB);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelWorkflow, setPanelWorkflow] = useState<Workflow | undefined>(undefined);
  // Track the right-side AgentPanel's width here so the main pane can reserve
  // matching padding-right when the panel is open — keeps chat and editor
  // side-by-side instead of letting the panel overlap the chat column.
  const [panelWidth, setPanelWidth] = useState(AGENT_PANEL_DEFAULT_WIDTH);
  const [kitePanelOpen, setKitePanelOpen] = useState(false);
  const [kiteOauthResult, setKiteOauthResult] = useState<KiteOAuthResult | null>(
    null,
  );
  const [metrics, setMetrics] = useState<MetricState>({ kind: "loading" });
  const [theme, setTheme] = useState<Theme>("system");
  const [conversations, setConversations] = useState<ConvEntry[]>([]);
  // First letter of the signed-in user's name/email — used for the
  // avatar initial in the topbar (Quartr's TopHeader.jsx pattern).
  const [accountInitial, setAccountInitial] = useState<string>("U");
  // True once the user has sent ≥1 message in the chat tab. AppShell
  // hides the Active Agents rail in that state so the chat column
  // takes the freed width (Quartr-style).
  const [chatActive, setChatActive] = useState(false);
  // Bumped by the "New chat" button to remount DashboardTab/ChatDemo
  // and start a fresh session (clears messages + conversation_id).
  const [chatResetKey, setChatResetKey] = useState(0);
  const metricTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Hash + theme init
  useEffect(() => {
    setActive(readHashTab());
    const onHash = (): void => setActive(readHashTab());
    window.addEventListener("hashchange", onHash);

    const initial = readStoredTheme() ?? "system";
    setTheme(initial);
    applyTheme(initial);

    // Load conversations from real backend
    void fetchConversations().then(setConversations);

    // Load /auth/me for the avatar initial. Mirrors Quartr's
    // TopHeader.jsx::getInitial(user) pattern (first letter of name
    // or email, uppercase, fallback "U").
    //
    // AppBootstrap wires its `setAuthTokenProvider` in a sibling
    // useEffect that runs AFTER this one (React fires child effects
    // before parent effects). So we cannot use `getMe()` here — its
    // bearer token reader is empty at this moment. Read the JWT
    // straight from localStorage and call /auth/me with an explicit
    // header instead.
    void (async () => {
      let token: string | null = null;
      try { token = localStorage.getItem("pivot_jwt"); } catch { token = null; }
      if (!token) return;
      try {
        const base =
          (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
          "http://127.0.0.1:8000";
        const trimmed = base.replace(/\/api\/?$/, "");
        const res = await fetch(`${trimmed}/auth/me`, {
          headers: {
            Accept: "application/json",
            Authorization: `Bearer ${token}`,
          },
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as { full_name?: string | null; email?: string | null };
        const src = (data.full_name && data.full_name.trim()) || data.email || "";
        const letter = src.trim()[0];
        if (letter) setAccountInitial(letter.toUpperCase());
      } catch { /* silent */ }
    })();

    // Detect Kite OAuth return trip — `/kite/callback` redirects here with
    // ?kite=connected or ?kite=error&reason=…. Auto-open the credentials
    // panel with the outcome, then strip the params so refreshes don't
    // re-surface old state.
    try {
      const params = new URLSearchParams(window.location.search);
      const kite = params.get("kite");
      if (kite === "connected") {
        setKiteOauthResult({ kind: "connected" });
        setKitePanelOpen(true);
      } else if (kite === "error") {
        setKiteOauthResult({
          kind: "error",
          reason: params.get("reason") ?? "unknown",
        });
        setKitePanelOpen(true);
      }
      if (kite) {
        params.delete("kite");
        params.delete("reason");
        const qs = params.toString();
        const next = `${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash}`;
        window.history.replaceState(null, "", next);
      }
    } catch {
      /* ignore */
    }

    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Live-react to OS theme changes when in "system" mode.
  useEffect(() => {
    if (theme !== "system") return;
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (): void => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const chooseTheme = useCallback((next: Theme): void => {
    setTheme(next);
    applyTheme(next);
    try {
      localStorage.setItem(LS_KEY, next);
    } catch { /* ignore */ }
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
    if (typeof window === "undefined") return;
    // When the user is on a sub-route (e.g. /stock/HDFCBANK), the
    // sidebar nav lands them back on the home shell (/) with the
    // right tab hash. We use Next's router so the navigation is a
    // soft client-side push — no full-page reload, no SSR roundtrip,
    // shared layout state stays mounted. On the home route, just
    // rewrite the hash so we don't push a fresh history entry.
    if (pathname === "/") {
      window.history.replaceState(null, "", `#${key}`);
    } else {
      router.push(`/#${key}`);
    }
  }, [pathname, router]);

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
    <div className="flex h-screen flex-col bg-background">
      {/* Sticky top header */}
      <TopHeader
        theme={theme}
        onChooseTheme={chooseTheme}
        metrics={metrics}
        accountInitial={accountInitial}
        onOpenKite={() => setKitePanelOpen(true)}
      />

      {/* Body: sidebar + content. When the right-side AgentPanel is open we
          reserve `paddingRight` equal to its current width so the panel sits
          beside the chat instead of overlapping it. Animated to match the
          panel's resize feel. */}
      <div
        className="flex flex-1 min-h-0"
        style={{
          paddingRight: panelOpen ? `${panelWidth}px` : 0,
          transition: "padding-right 220ms cubic-bezier(0.22, 1, 0.36, 1)",
        }}
      >
        {/* Left sidebar */}
        <Sidebar
          active={active}
          onTabChange={goTab}
          conversations={conversations}
        />

        {/* Center pane — flex column so the chat tab (which hosts both
            the dashboard intro and the chat surface) can size its
            messages region to the available space and pin the composer
            to the bottom (ChatGPT/Claude-style). Other tabs get the
            old scrollable wrapper. */}
        <main className="flex flex-1 min-w-0 min-h-0 flex-col">
          {children ? (
            // Custom main-pane content (e.g. stock detail page). Scrolls
            // independently; same px-8 / py-6 padding the other full-bleed
            // tabs use so the page chrome lines up.
            <div className="flex-1 min-h-0 overflow-y-auto px-8 pt-6 pb-8">
              {children}
            </div>
          ) : active === "chat" ? (
            <div className="relative flex h-full w-full min-h-0">
              {/* Floating "New chat" button — pinned to the top-right
                  of the entire chat surface (not the thread column),
                  so it doesn't collide with right-aligned user bubbles.
                  Bumping `chatResetKey` remounts DashboardTab, which
                  clears messages and starts a new session. */}
              {chatActive && !panelOpen && (
                <button
                  type="button"
                  onClick={() => {
                    setChatActive(false);
                    setChatResetKey((k) => k + 1);
                  }}
                  aria-label="Start new chat"
                  data-testid="new-chat-btn"
                  className="absolute z-10 inline-flex items-center"
                  style={{
                    top: 14,
                    // Track the chat column's right edge so the button
                    // always sits in the right-side gap (just outside the
                    // column), regardless of pane width. Math:
                    //   right = pane_right - column_right - 8px - button_width
                    //         = 50% - col_half - 8px - ~93px
                    // With col = 58rem (col_half = 29rem) → 50% - 29rem - 101px.
                    // Clamps to 18px on narrow viewports where the column
                    // hits the pane edge.
                    right: "max(18px, calc(50% - 29rem - 101px))",
                    gap: 6,
                    height: 32,
                    padding: "0 12px",
                    background: "var(--bg-base)",
                    border: "1px solid var(--glass-border)",
                    borderRadius: "999px",
                    color: "var(--text-secondary)",
                    fontFamily: "var(--font-ui)",
                    fontSize: 12.5,
                    fontWeight: 500,
                    cursor: "pointer",
                    transition:
                      "color 0.18s var(--ease-quartr), border-color 0.18s var(--ease-quartr), background-color 0.18s var(--ease-quartr)",
                    boxShadow: "0 2px 6px rgba(0,0,0,0.04)",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.color = "var(--text-primary)";
                    e.currentTarget.style.background = "var(--bg-elevated)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.color = "var(--text-secondary)";
                    e.currentTarget.style.background = "var(--bg-base)";
                  }}
                >
                  <Plus size={14} strokeWidth={2} aria-hidden="true" />
                  New chat
                </button>
              )}
              <div
                className="mx-auto flex h-full w-full min-h-0 flex-col px-6"
                style={{
                  // Slightly narrower active column (58rem vs 64rem before)
                  // so the floating "New chat" button — positioned via calc
                  // against the column's right edge — always lands in the
                  // right-side gap on common viewports (1280+) without
                  // colliding with right-aligned user bubbles.
                  maxWidth: chatActive ? "58rem" : "48rem",
                  paddingTop: 0,
                  transition:
                    "max-width 500ms cubic-bezier(0.22, 1, 0.36, 1)",
                }}
              >
                <DashboardTab
                  key={chatResetKey}
                  onOpenWorkflow={openWorkflow}
                  onOpenCalendar={() => goTab("calendar")}
                  onChatActiveChange={setChatActive}
                />
              </div>
            </div>
          ) : active === "calendar" ? (
            // Calendar gets full pane height (the day panel + month grid
            // consume vertical space; no outer scroll).
            <div className="flex-1 min-h-0 px-8 pt-6 flex flex-col">
              <CalendarTab onOpenWorkflow={openWorkflowById} />
            </div>
          ) : active === "screener" ? (
            // Screener also owns its own height (filter rail + results
            // grid take the full pane).
            <div className="flex-1 min-h-0 flex flex-col">
              <ScreenerPage />
            </div>
          ) : active === "portfolio" ? (
            // Portfolio takes the full pane width — same padding pattern
            // as Quartr's PortfolioTab in frontend-quartr/.../Dashboard.jsx
            // ("padding: 24px 32px"). Sections scroll inside.
            <div className="flex-1 min-h-0 overflow-y-auto px-8 pt-6 pb-8">
              <PortfolioTab />
            </div>
          ) : (
            <div className="flex-1 min-h-0 overflow-y-auto px-8 pt-6 pb-8">
              {active === "agents" && <AgentsTab onOpenWorkflow={openWorkflow} />}
            </div>
          )}
        </main>

        {/* Right rail — chat tab only, and only while no conversation
            has started yet. Min-h-0 + overflow-y-auto so the rail
            scrolls *inside itself*; the topbar + sidebar stay put. */}
        {/* Quartr's right rail is exactly 320px with padding 24/20.
            w-80 = 320px; px:20 py:24 mirrors Quartr's padding so the
            cards line up with the same horizontal margins. */}
        {!children && active === "chat" && !chatActive && (
          <aside
            className="hidden w-80 shrink-0 min-h-0 overflow-y-auto xl:block"
            style={{ padding: "24px 20px" }}
          >
            <ActiveAgentsRail onOpenWorkflow={openWorkflow} />
          </aside>
        )}
      </div>

      <AgentPanel
        open={panelOpen}
        onOpenChange={setPanelOpen}
        initialWorkflow={panelWorkflow}
        width={panelWidth}
        onWidthChange={setPanelWidth}
      />

      <KiteCredentialsPanel
        open={kitePanelOpen}
        onOpenChange={(next) => {
          setKitePanelOpen(next);
          if (!next) setKiteOauthResult(null);
        }}
        oauthResult={kiteOauthResult}
      />

      <CommandPalette
        conversations={conversations}
        onNavigate={goTab}
        onOpenConversation={() => goTab("chat")}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top Header
// ---------------------------------------------------------------------------

function TopHeader({
  theme,
  onChooseTheme,
  metrics,
  accountInitial,
  onOpenKite,
}: {
  theme: Theme;
  onChooseTheme: (t: Theme) => void;
  metrics: MetricState;
  accountInitial: string;
  onOpenKite: () => void;
}): React.ReactElement {
  // Local state drives the custom Lucide-X clear control. The native
  // browser "search" input renders its own (blue, ugly) clear button
  // — we hide that via the global-search-input class and render our
  // own only when the field has text.
  const [searchValue, setSearchValue] = useState("");
  return (
    <header
      className="flex shrink-0 items-center gap-6 px-5"
      style={{
        height: 60,
        background: "var(--bg-base)",
        borderBottom: "1px solid var(--glass-border)",
      }}
    >
      {/* Brand — serif logotype, fixed-width slot. Uses --font-experiment
          so we can swap typefaces in one place (globals.css) while we
          decide on the final brand serif. */}
      <div
        className="flex shrink-0 items-center pl-1"
        style={{
          width: 200,
          gap: 0,
          fontFamily: "var(--font-experiment)",
          fontWeight: "var(--weight-display)" as unknown as number,
          fontSize: 22,
          letterSpacing: "-0.02em",
          color: "var(--text-primary)",
        }}
      >
        {/* Pivot brand mark — theme-aware: dark mode uses pivot-icon.png
            (the standalone dark logo), light mode uses pivot-light.png
            (the user-supplied light variant).

            The two PNGs share a 2000×2000 canvas but their inner glyph
            crops differ — pivot-icon is ~810px wide, pivot-light is
            ~650px wide. To make the rendered glyph appear the *same*
            visual size in both themes, we render the light variant at
            a slightly larger box (44 × 810/650 ≈ 55) so the glyph
            inside both boxes hits the same rendered width. */}
        {(() => {
          const isDark = resolvedDark(theme);
          const size = isDark ? 44 : 55;
          return (
            <img
              src={isDark ? "/pivot-icon.png" : "/pivot-light.png"}
              alt="Pivot"
              width={size}
              height={size}
              className="shrink-0"
              style={{ display: "block", objectFit: "contain" }}
            />
          );
        })()}
        {/* Negative margin pulls "pivot" back over the transparent
            right padding baked into the logo PNG. Adjust the px value
            to taste — more negative = closer. */}
        <span style={{ marginLeft: -2 }}>pivot</span>
      </div>

      {/* Search — Quartr pill, sized + bordered, no Tailwind background. */}
      <div
        className="flex flex-1 items-center gap-2"
        style={{
          maxWidth: 360,
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
        <input
          type="search"
          value={searchValue}
          onChange={(e) => setSearchValue(e.target.value)}
          placeholder="Search stocks, strategies, conversations…"
          aria-label="Global search"
          data-testid="global-search"
          className="global-search-input flex-1 outline-none"
          style={{
            background: "transparent",
            border: "none",
            color: "var(--text-primary)",
            fontFamily: "var(--font-ui)",
            fontSize: 13,
          }}
        />
        {searchValue.length > 0 && (
          <button
            type="button"
            onClick={() => setSearchValue("")}
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

      {/* Right cluster — metric stack + account menu */}
      <div className="ml-auto flex shrink-0 items-center gap-7">
        <MetricStrip metrics={metrics} />
        <AccountMenu
          theme={theme}
          onChooseTheme={onChooseTheme}
          initial={accountInitial}
          onOpenKite={onOpenKite}
        />
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// AccountMenu — Quartr-style avatar dropdown.
//
// Mirrors frontend-quartr/.../TopHeader.jsx::MenuItem usage (Settings, Help,
// Log out). Adds a single-row icon-only theme toggle BENEATH those items
// (Moon / Sun / Monitor) to switch between Dark / Light / System.
// ---------------------------------------------------------------------------

function AccountMenu({
  theme,
  onChooseTheme,
  initial,
  onOpenKite,
}: {
  theme: Theme;
  onChooseTheme: (t: Theme) => void;
  initial: string;
  onOpenKite: () => void;
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const helpCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelHelpClose = useCallback(() => {
    if (helpCloseTimer.current) {
      clearTimeout(helpCloseTimer.current);
      helpCloseTimer.current = null;
    }
  }, []);
  const scheduleHelpClose = useCallback(() => {
    cancelHelpClose();
    helpCloseTimer.current = setTimeout(() => setHelpOpen(false), 120);
  }, [cancelHelpClose]);

  // Close on outside click + Escape
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setHelpOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        setOpen(false);
        setHelpOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (!open) setHelpOpen(false);
  }, [open]);

  useEffect(() => () => cancelHelpClose(), [cancelHelpClose]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="Account"
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="account-menu-trigger"
        className="inline-flex shrink-0 items-center justify-center"
        style={{
          width: 34,
          height: 34,
          borderRadius: "var(--radius-pill)",
          background: "var(--bg-primary)",
          border: "1px solid var(--glass-border)",
          color: "var(--text-secondary)",
          fontFamily: "var(--font-ui)",
          fontSize: 13,
          fontWeight: 500,
          cursor: "pointer",
          transition:
            "color 0.25s var(--ease-quartr), border-color 0.25s var(--ease-quartr), background-color 0.25s var(--ease-quartr)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = "var(--glass-border-hover)";
          e.currentTarget.style.color = "var(--text-primary)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = "var(--glass-border)";
          e.currentTarget.style.color = "var(--text-secondary)";
        }}
      >
        {initial}
      </button>

      {open && (
        <div
          role="menu"
          data-testid="account-menu"
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            right: 0,
            minWidth: 200,
            padding: 4,
            background: "var(--bg-primary)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
            zIndex: 50,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <MenuItem
            icon={KeyRound}
            label="Kite credentials"
            onClick={() => {
              setOpen(false);
              onOpenKite();
            }}
            testId="menu-kite-credentials"
          />
          <MenuItem icon={Settings} label="Settings" onClick={() => setOpen(false)} />
          <div
            style={{ position: "relative" }}
            onMouseEnter={() => {
              cancelHelpClose();
              setHelpOpen(true);
            }}
            onMouseLeave={scheduleHelpClose}
          >
            <MenuItem
              icon={HelpCircle}
              label="Help"
              hasChevron={true}
              active={helpOpen}
              onClick={() => setHelpOpen((v) => !v)}
            />
            {helpOpen && (
              <div
                role="menu"
                data-testid="account-menu-help-submenu"
                onMouseEnter={cancelHelpClose}
                onMouseLeave={scheduleHelpClose}
                style={{
                  position: "absolute",
                  top: -4,
                  right: "calc(100% + 6px)",
                  minWidth: 200,
                  padding: 4,
                  background: "var(--bg-primary)",
                  border: "1px solid var(--glass-border)",
                  borderRadius: "var(--radius-md)",
                  boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
                  zIndex: 60,
                  display: "flex",
                  flexDirection: "column",
                }}
              >
                <MenuItem
                  icon={ShieldCheck}
                  label="Privacy Policy"
                  hasExternalArrow={true}
                  onClick={() => {
                    setHelpOpen(false);
                    setOpen(false);
                  }}
                />
                <MenuItem
                  icon={FileText}
                  label="Terms of Service"
                  hasExternalArrow={true}
                  onClick={() => {
                    setHelpOpen(false);
                    setOpen(false);
                  }}
                />
                <MenuItem
                  icon={Bug}
                  label="Report a bug"
                  onClick={() => {
                    setHelpOpen(false);
                    setOpen(false);
                  }}
                />
                <div
                  aria-hidden={true}
                  style={{
                    height: 1,
                    background: "var(--glass-border)",
                    margin: "4px 6px",
                  }}
                />
                <MenuItem
                  icon={Keyboard}
                  label="Keyboard shortcuts"
                  onClick={() => {
                    setHelpOpen(false);
                    setOpen(false);
                  }}
                />
              </div>
            )}
          </div>
          <MenuItem icon={LogOut} label="Log out" onClick={() => setOpen(false)} />

          {/* Divider */}
          <div
            aria-hidden={true}
            style={{
              height: 1,
              background: "var(--glass-border)",
              margin: "4px 6px",
            }}
          />

          {/* Theme toggle row — three icon-only buttons in one horizontal row */}
          <div
            role="radiogroup"
            aria-label="Theme"
            className="flex items-center justify-between"
            style={{ padding: "6px 8px", gap: 6 }}
          >
            <ThemeIconButton
              active={theme === "dark"}
              onClick={() => onChooseTheme("dark")}
              ariaLabel="Dark mode"
              testId="theme-dark"
            >
              <Moon size={14} strokeWidth={2} aria-hidden={true} />
            </ThemeIconButton>
            <ThemeIconButton
              active={theme === "light"}
              onClick={() => onChooseTheme("light")}
              ariaLabel="Light mode"
              testId="theme-light"
            >
              <Sun size={14} strokeWidth={2} aria-hidden={true} />
            </ThemeIconButton>
            <ThemeIconButton
              active={theme === "system"}
              onClick={() => onChooseTheme("system")}
              ariaLabel="System mode"
              testId="theme-system"
            >
              <Monitor size={14} strokeWidth={2} aria-hidden={true} />
            </ThemeIconButton>
          </div>
        </div>
      )}
    </div>
  );
}

function MenuItem({
  icon: Icon,
  label,
  onClick,
  hasChevron = false,
  hasExternalArrow = false,
  active = false,
  testId,
}: {
  icon?: React.ComponentType<{ size?: number; strokeWidth?: number }>;
  label: string;
  onClick: () => void;
  hasChevron?: boolean;
  hasExternalArrow?: boolean;
  active?: boolean;
  testId?: string;
}): React.ReactElement {
  const trailing = hasChevron ? (
    <ChevronLeft size={14} strokeWidth={2} aria-hidden={true} />
  ) : hasExternalArrow ? (
    <ExternalLink size={12} strokeWidth={2} aria-hidden={true} />
  ) : null;
  return (
    <button
      type="button"
      role="menuitem"
      data-testid={testId}
      onClick={onClick}
      className="inline-flex items-center w-full"
      style={{
        gap: 10,
        padding: "8px 10px",
        background: active ? "var(--bg-elevated)" : "transparent",
        border: "none",
        borderRadius: "var(--radius-sm)",
        color: active ? "var(--text-primary)" : "var(--text-secondary)",
        fontFamily: "var(--font-ui)",
        fontSize: 13,
        fontWeight: 500,
        textAlign: "left",
        cursor: "pointer",
        transition: "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "var(--bg-elevated)";
        e.currentTarget.style.color = "var(--text-primary)";
      }}
      onMouseLeave={(e) => {
        if (active) return;
        e.currentTarget.style.background = "transparent";
        e.currentTarget.style.color = "var(--text-secondary)";
      }}
    >
      {Icon ? <Icon size={14} strokeWidth={2} /> : null}
      <span style={{ flex: 1 }}>{label}</span>
      {trailing}
    </button>
  );
}

function ThemeIconButton({
  active,
  onClick,
  ariaLabel,
  testId,
  children,
}: {
  active: boolean;
  onClick: () => void;
  ariaLabel: string;
  testId: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      aria-label={ariaLabel}
      data-testid={testId}
      onClick={onClick}
      className="flex flex-1 items-center justify-center"
      style={{
        // Borderless — same active treatment as the sidebar nav:
        // subtle elevated bg + ink text, no border, no ring.
        height: 28,
        background: active ? "var(--surface-active)" : "transparent",
        border: "none",
        borderRadius: "var(--radius-sm)",
        color: active ? "var(--text-primary)" : "var(--text-tertiary)",
        cursor: "pointer",
        transition:
          "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        if (active) return;
        e.currentTarget.style.background = "var(--surface-active)";
        e.currentTarget.style.color = "var(--text-primary)";
      }}
      onMouseLeave={(e) => {
        if (active) return;
        e.currentTarget.style.background = "transparent";
        e.currentTarget.style.color = "var(--text-tertiary)";
      }}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Metric strip
// ---------------------------------------------------------------------------

/**
 * Quartr-style metric — two-line stack: tiny uppercase label on top,
 * large display-weight value beneath. Trend arrows live INSIDE the value.
 */
function MetricStack({
  label,
  value,
  pnl,
  pct,
  emphasis,
}: {
  label: string;
  value?: string;
  pnl?: number;
  pct?: number;
  emphasis?: boolean;
}): React.ReactElement {
  const positive = (pnl ?? 0) >= 0;
  return (
    <div className="flex flex-col" style={{ gap: 2, lineHeight: 1.1 }}>
      <span
        style={{
          fontSize: 10.5,
          color: "var(--metric-label)",
          fontWeight: "var(--weight-medium)" as unknown as number,
          letterSpacing: "0.02em",
        }}
      >
        {label}
      </span>
      {pnl !== undefined ? (
        <span
          className="inline-flex items-baseline gap-1.5 tabular-nums"
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: "var(--weight-display)" as unknown as number,
            fontSize: emphasis ? 16 : 14,
            letterSpacing: "-0.025em",
            color: positive ? "var(--color-profit)" : "var(--color-loss)",
          }}
          aria-label={`${label}: ${fmt(pnl)}`}
        >
          {positive ? "+" : "−"}
          {fmt(Math.abs(pnl)).replace(/^[-−]/, "")}
          {pct !== undefined && (
            <span
              style={{
                fontSize: 11.5,
                fontFamily: "var(--font-mono)",
                opacity: 0.85,
              }}
            >
              ({positive ? "+" : ""}{pct.toFixed(2)}%)
            </span>
          )}
        </span>
      ) : (
        <span
          className="tabular-nums"
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: "var(--weight-display)" as unknown as number,
            fontSize: emphasis ? 16 : 14,
            color: "var(--text-primary)",
            letterSpacing: "-0.025em",
          }}
        >
          {value}
        </span>
      )}
    </div>
  );
}

function MetricStrip({ metrics }: { metrics: MetricState }): React.ReactElement | null {
  if (metrics.kind === "hidden") return null;
  if (metrics.kind === "loading") {
    return (
      <div
        className="hidden items-center lg:flex"
        style={{ gap: 28 }}
        data-testid="metric-strip-loading"
        aria-label="Loading portfolio metrics"
      >
        <Skeleton className="h-7 w-24" />
        <Skeleton className="h-7 w-20" />
        <Skeleton className="h-7 w-28" />
      </div>
    );
  }
  const { summary } = metrics;
  return (
    <div
      className="hidden items-center lg:flex"
      style={{ gap: 28 }}
      data-testid="metric-strip"
      role="status"
      aria-label="Portfolio metrics"
    >
      <MetricStack
        label="Portfolio value"
        value={fmt(summary.total_value)}
        emphasis
      />
      <MetricStack label="Day P&L" pnl={summary.day_pnl} />
      <MetricStack
        label="Total P&L"
        pnl={summary.total_pnl}
        pct={summary.total_pnl_pct}
      />
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
      className="hidden shrink-0 lg:flex lg:flex-col"
      aria-label="Primary navigation"
      data-testid="sidebar-nav"
      style={{
        width: 240,
        background: "var(--bg-base)",
        borderRight: "1px solid var(--glass-border)",
        padding: "18px 14px 16px",
      }}
    >
      {/* Nav — text-only, with a 4×4 dot indicator on the active row.
          Mirrors frontend-quartr/.../Sidebar.jsx exactly. */}
      <nav className="flex flex-col" style={{ gap: 2 }} aria-label="Primary navigation list">
        {NAV_ITEMS.map(({ key, label }) => {
          const isActive = active === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onTabChange(key)}
              aria-current={isActive ? "page" : undefined}
              data-testid={`nav-${key}`}
              style={{
                position: "relative",
                padding: "9px 14px",
                background: isActive ? "var(--surface-active)" : "transparent",
                color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                border: "none",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                fontFamily: "var(--font-ui)",
                fontSize: 13.5,
                fontWeight: 500,
                letterSpacing: "-0.005em",
                textAlign: "left",
                transition:
                  "color 0.35s var(--ease-quartr), background-color 0.35s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                if (!isActive) e.currentTarget.style.color = "var(--text-primary)";
              }}
              onMouseLeave={(e) => {
                if (!isActive) e.currentTarget.style.color = "var(--text-secondary)";
              }}
            >
              {label}
              {isActive && (
                <span
                  aria-hidden={true}
                  style={{
                    position: "absolute",
                    right: 14,
                    top: "50%",
                    marginTop: -2,
                    width: 4,
                    height: 4,
                    borderRadius: "50%",
                    background: "var(--text-primary)",
                  }}
                />
              )}
            </button>
          );
        })}
      </nav>

      {/* Divider */}
      <div
        aria-hidden={true}
        style={{
          height: 1,
          margin: "18px 8px 14px",
          background: "var(--glass-border)",
        }}
      />

      {/* Conversation history — uppercase header + truncated titles */}
      <div
        className="flex-1 overflow-y-auto flex flex-col"
        style={{ gap: 14, padding: "0 4px" }}
      >
        <div
          style={{
            padding: "0 10px",
            fontSize: 11,
            fontFamily: "var(--font-ui)",
            fontWeight: 500,
            color: "var(--text-tertiary)",
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}
        >
          Your conversations
        </div>

        {conversations.length === 0 ? (
          <div
            style={{
              padding: "0 10px",
              fontSize: 12,
              color: "var(--text-tertiary)",
            }}
          >
            Start a chat to see history.
          </div>
        ) : (
          <div className="flex flex-col" style={{ gap: 2 }}>
            {conversations.map((conv) => (
              <button
                key={conv.id}
                type="button"
                onClick={() => onTabChange("chat")}
                aria-label={`Open conversation: ${conv.preview}`}
                style={{
                  padding: "7px 10px",
                  background: "transparent",
                  border: "none",
                  borderRadius: "var(--radius-sm)",
                  color: "var(--text-secondary)",
                  fontFamily: "var(--font-ui)",
                  fontSize: 12.5,
                  fontWeight: 500,
                  textAlign: "left",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  cursor: "pointer",
                  transition:
                    "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = "var(--text-primary)";
                  e.currentTarget.style.background = "var(--surface-active)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = "var(--text-secondary)";
                  e.currentTarget.style.background = "transparent";
                }}
              >
                {conv.preview}
              </button>
            ))}
          </div>
        )}
      </div>
    </nav>
  );
}

// (NewsPlaceholder removed — replaced by TriggersTab)

