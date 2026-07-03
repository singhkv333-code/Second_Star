"use client";

/**
 * AppShell — Quartr-style premium shell with left sidebar nav, center content,
 * and (on dashboard) right Active Agents rail.
 *
 * Layout:
 *   [Sticky top header: logo + search + metric strip + theme toggle + avatar]
 *   [Left sidebar nav | Center content pane | Right rail (dashboard only)]
 *
 * Nav items: Chat / Portfolio / Agents / Calendar / Screener
 * Active item: solid left border + bg highlight
 * Below nav: YOUR CONVERSATIONS — opens the Chat tab
 *
 * Chat is the default tab and the home of all conversational interaction
 * with Pivot. Until the user sends their first message it shows a
 * dashboard intro (greeting + index strip + quick-action chips) above
 * the composer; once a message lands the intro disappears and the
 * transcript fills the pane with the composer pinned at the bottom.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  BarChart2,
  Bug,
  CalendarDays,
  ChevronDown,
  ChevronLeft,
  ExternalLink,
  FileText,
  FlaskConical,
  HelpCircle,
  Info,
  Keyboard,
  LogOut,
  Menu,
  MessageSquare,
  Monitor,
  Moon,
  PieChart,
  Pin,
  Plug,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  Sun,
  Telescope,
  Trash2,
  X,
} from "lucide-react";
import { CommandPalette } from "@/components/CommandPalette";
import { KeyboardShortcutsModal } from "@/components/KeyboardShortcutsModal";
import { ReportBugDialog } from "@/components/feedback/ReportBugDialog";
import { CHORD_NAV_MAP } from "@/lib/shortcuts";
import {
  BrokerOnboarding,
  type BrokerOAuthResult,
} from "@/components/brokers";
import { AgentPanel } from "@/components/agent-panel/AgentPanel";
import { OptionChainLauncherCard } from "@/components/chat/OptionChainLauncherCard";
import {
  ActiveDraftContext,
} from "@/components/agent-panel/active-draft-context";
import { AgentsTab } from "@/components/agent-panel/AgentsTab";
import { ViewsTab } from "@/components/views/ViewsTab";
import { CalendarTab } from "@/components/CalendarTab";
import { PortfolioTab } from "@/components/agent-panel/PortfolioTab";
import { ScreenerPage } from "@/components/screener/ScreenerPage";
import { SettingsDialog } from "@/components/settings/SettingsTab";
import { DashboardTab } from "@/components/DashboardTab";
import { CompanyAutosuggest } from "@/components/CompanyAutosuggest";
import { ActiveAgentsRail } from "@/components/ActiveAgentsRail";
import { PivotMark } from "@/components/brand/PivotMark";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  deleteConversation,
  getMe,
  getPortfolioSummary,
  getWorkflow,
  listConversationMessages,
  listConversations,
  logoutUser,
  setAccountMode,
  type PortfolioSummary,
} from "@/lib/api";
import type { ResumeConversation } from "@/components/chat/ChatDemo";
import type { Workflow } from "@/lib/types";
import { isError } from "@/lib/types";
import {
  getTradingMode,
  setTradingMode,
  type TradingMode,
} from "@/lib/trading-mode";

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------

type TabKey =
  | "chat"
  | "portfolio"
  | "agents"
  | "calendar"
  | "screener"
  | "views";

const NAV_ITEMS: {
  key: TabKey;
  label: string;
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}[] = [
  { key: "chat", label: "Chat", Icon: MessageSquare },
  { key: "views", label: "Views", Icon: Telescope },
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
  // Always-fresh mirror of `active` so callbacks memoized with [] deps can read
  // the current tab without being recreated on every tab change.
  const activeRef = useRef<TabKey>(DEFAULT_TAB);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelWorkflow, setPanelWorkflow] = useState<Workflow | undefined>(undefined);
  // The tab the side editor was opened from. When the user navigates away from
  // this tab, the editor closes — it never lingers over an unrelated surface.
  const [panelOriginTab, setPanelOriginTab] = useState<TabKey | null>(null);
  // Shared active-draft state: the workflow currently open in the editor
  // (unsaved only — id "" or "local-…", status "draft").
  const [activeEditorDraft, setActiveEditorDraft] = useState<Workflow | null>(null);
  // The AgentPanel renders as a modal overlay at a fixed width (matched to
  // the Backtest sheet via CSS clamp inside AgentPanel) — no width state or
  // side-by-side padding to track here.
  // Broker onboarding dialog (replaces the old Kite-only credentials panel).
  // `brokerOauth` carries the broker id + outcome from an OAuth return trip so
  // the dialog can deep-open onto that broker's connect panel with a banner.
  const [brokerPanelOpen, setBrokerPanelOpen] = useState(false);
  const [brokerOauth, setBrokerOauth] = useState<{
    broker: string | null;
    result: BrokerOAuthResult;
  } | null>(null);
  const [metrics, setMetrics] = useState<MetricState>({ kind: "loading" });
  const [theme, setTheme] = useState<Theme>("system");
  // Global trading mode (real/live vs paper). Mirrors the persisted store so
  // the toggle + banner re-render; the data layer reads the store directly.
  const [tradingMode, setTradingModeState] = useState<TradingMode>("real");
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
  // Set when the user opens a sidebar conversation — ChatDemo remounts
  // with this thread's id + transcript so the chat continues in place.
  const [resumeConv, setResumeConv] = useState<ResumeConversation | undefined>(
    undefined,
  );
  // Mobile (<lg) only — controls the slide-in sidebar drawer.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  // Keyboard shortcuts panel (opened via Ctrl/⌘+/ or the account menu).
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // Report-a-bug widget (opened from the account menu's Help submenu).
  const [reportBugOpen, setReportBugOpen] = useState(false);
  // Settings modal (opened from the account menu's "Settings" item).
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Desktop-only collapse of the inline sidebar (Ctrl/⌘+B). On mobile the
  // sidebar is a drawer driven by `mobileNavOpen`, so collapse is ignored.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [isDesktop, setIsDesktop] = useState(true);
  const metricTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Hash + theme init
  useEffect(() => {
    setActive(readHashTab());
    const onHash = (): void => setActive(readHashTab());
    window.addEventListener("hashchange", onHash);

    const initial = readStoredTheme() ?? "system";
    setTheme(initial);
    applyTheme(initial);

    // Trading mode: adopt the persisted choice (default 'real') and reconcile
    // the backend account mode to match, so order routing (`should_use_paper`)
    // always agrees with what the banner/UI claims — a paper UI never places
    // a live order and vice-versa.
    const storedMode = getTradingMode();
    setTradingModeState(storedMode);
    void setAccountMode(storedMode === "real" ? "live" : "paper");

    // Load conversations from real backend
    void fetchConversations().then(setConversations);

    // Load /auth/me for the avatar initial. Mirrors Quartr's
    // TopHeader.jsx::getInitial(user) pattern (first letter of name
    // or email, uppercase, fallback "U").
    //
    // AppShell only mounts once AppBootstrap's own effect has already run
    // (it gates children behind a "ready" phase reached only after
    // `setAuthTokenProvider` is wired), so `getMe()` already has a token
    // reader by the time this fires. Going through the shared `getMe()`
    // (instead of a bespoke fetch) also means this coalesces with
    // DashboardTab's own `getMe()` mount call via lib/api.ts's in-flight
    // GET de-dupe, instead of firing a second, uncoalesced /auth/me request.
    void getMe().then((result) => {
      if (isError(result)) return;
      const { full_name, email } = result.data;
      const src = (full_name && full_name.trim()) || email || "";
      const letter = src.trim()[0];
      if (letter) setAccountInitial(letter.toUpperCase());
    });

    // Detect a broker OAuth return trip — the backend bounces here with
    // ?broker=connected (or ?broker=error&reason=…). We also honor the legacy
    // ?kite=… param so old redirects / bookmarks don't break (mapped to the
    // "kite" broker). Either way we auto-open the broker onboarding dialog onto
    // that broker's panel with the outcome banner, then strip the params so a
    // refresh doesn't re-surface stale state.
    try {
      const params = new URLSearchParams(window.location.search);
      const brokerParam = params.get("broker");
      const kiteParam = params.get("kite"); // legacy fallback
      const outcome = brokerParam ?? kiteParam;
      // Which broker connected: `?broker=connected` has no id, so use the
      // explicit `?broker_id=` if present, else infer "kite" from the legacy
      // param, else null (the dialog still shows the picker + banner).
      const brokerId = brokerParam
        ? params.get("broker_id")
        : kiteParam
          ? "kite"
          : null;
      if (outcome === "connected") {
        setBrokerOauth({ broker: brokerId, result: { kind: "connected" } });
        setBrokerPanelOpen(true);
      } else if (outcome === "error") {
        setBrokerOauth({
          broker: brokerId,
          result: { kind: "error", reason: params.get("reason") ?? "unknown" },
        });
        setBrokerPanelOpen(true);
      }
      if (outcome) {
        params.delete("broker");
        params.delete("broker_id");
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
    // `tradingMode` is included so the strip re-fetches (paper vs real) the
    // instant the mode flips.
  }, [active, tradingMode, loadMetrics]);

  // Switch trading mode: optimistically flip the store (persists + notifies
  // every useTradingMode subscriber), push the change to the backend account
  // mode, and revert on failure so the UI never claims a mode the backend
  // isn't actually in.
  const chooseTradingMode = useCallback(
    async (next: TradingMode): Promise<void> => {
      const prev = getTradingMode();
      if (next === prev) return;
      setTradingMode(next);
      setTradingModeState(next);
      const res = await setAccountMode(next === "real" ? "live" : "paper");
      if (isError(res)) {
        setTradingMode(prev);
        setTradingModeState(prev);
        return;
      }
      loadMetrics();
    },
    [loadMetrics],
  );

  const startNewChat = useCallback((): void => {
    setChatActive(false);
    setResumeConv(undefined);
    setChatResetKey((k) => k + 1);
    setMobileNavOpen(false);
    setActive("chat");
    if (typeof window === "undefined") return;
    if (pathname === "/") {
      window.history.replaceState(null, "", `#chat`);
    } else {
      router.push(`/#chat`);
    }
  }, [pathname, router]);

  // Sidebar conversation click — fetch the stored transcript, then remount
  // the chat surface on that thread (same id → the backend appends to it;
  // the transcript seeds the visible messages + rolling LLM history).
  const openConversation = useCallback(
    async (convId: string): Promise<void> => {
      setMobileNavOpen(false);
      const res = await listConversationMessages(convId, { limit: 200 });
      const messages = isError(res)
        ? []
        : res.data.items
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => ({
              role: m.role as "user" | "assistant",
              content: m.content,
            }));
      setResumeConv({ id: convId, messages });
      setChatActive(messages.length > 0);
      setChatResetKey((k) => k + 1);
      setActive("chat");
      if (typeof window !== "undefined") {
        if (pathname === "/") {
          window.history.replaceState(null, "", `#chat`);
        } else {
          router.push(`/#chat`);
        }
      }
    },
    [pathname, router],
  );

  // Delete a conversation from the sidebar. Optimistic removal (the row
  // disappears immediately), then a refetch reconciles with the server.
  const removeConversation = useCallback((id: string): void => {
    setConversations((prev) => prev.filter((c) => c.id !== id));
    void deleteConversation(id).then(() => {
      void fetchConversations().then(setConversations);
    });
  }, []);

  // Keep the sidebar list fresh: ChatDemo pings this event after every
  // completed turn (the backend persisted it), and we refetch on focus
  // return so another tab's chats appear too.
  useEffect(() => {
    const refresh = (): void => {
      void fetchConversations().then(setConversations);
    };
    window.addEventListener("pivot:conversations-changed", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener("pivot:conversations-changed", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, []);

  const goTab = useCallback((key: TabKey): void => {
    setActive(key);
    setMobileNavOpen(false);
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

  const openWorkflow = useCallback(
    (workflow: Workflow, originTab?: TabKey): void => {
      const isUnsaved = !workflow.id || workflow.id === "" || workflow.id.startsWith("local-");
      if (isUnsaved && workflow.status === "draft") {
        setActiveEditorDraft(workflow);
      }
      setPanelWorkflow(workflow);
      // Remember the tab the editor was opened from so it auto-closes when the
      // user navigates away. `originTab` is passed explicitly when the opener
      // also switches tabs in the same tick (Edit-with-chat), where the ref
      // hasn't caught up yet; otherwise use the live tab.
      setPanelOriginTab(originTab ?? activeRef.current);
      setPanelOpen(true);
    },
    [],
  );

  const openWorkflowById = useCallback(async (id: string): Promise<void> => {
    const result = await getWorkflow(id);
    if (isError(result)) return;
    openWorkflow(result.data);
  }, [openWorkflow]);

  // Keep the activeRef in sync, and close the side editor once the user leaves
  // the tab it was opened from — it never floats over an unrelated surface.
  useEffect(() => {
    activeRef.current = active;
    if (panelOpen && panelOriginTab !== null && active !== panelOriginTab) {
      setPanelOpen(false);
      setActiveEditorDraft(null);
      setPanelOriginTab(null);
    }
  }, [active, panelOpen, panelOriginTab]);

  // "Edit with chat" — jump to the chat surface with the chosen agent
  // SELECTED (a context chip in the composer) and the side editor open on
  // that agent, so the user just says what they want changed. No canned
  // sentence to finish — the selection + editor carry the targeting.
  //
  // Targeting: the FULL workflow (incl. steps) rides the seed event so the
  // chat surface attaches it as the `editor_draft` of the NEXT outgoing
  // turn. The backend seeds its active_draft from that exact payload, so
  // the amendment lands on THIS agent's steps — it never has to guess
  // which agent from the free-text name (which mis-targeted before when
  // two agents shared a similar name). Mode is pinned to "agent" so the
  // follow-up routes to the workflow tool.
  const editWorkflowWithChat = useCallback((workflow: Workflow): void => {
    const seededDraft = {
      // Anchor the seed to THIS exact agent so Save & Activate updates it in
      // place rather than registering a duplicate (the id is threaded through
      // the seed event → ChatDemo's one-shot ref → the editor_draft payload).
      workflow_id: workflow.id,
      name: workflow.name,
      description: workflow.description ?? "",
      steps: workflow.steps.map((s) => ({
        step_type: s.step_type,
        label: s.label,
        config: s.config,
      })),
      rationale: "",
      warnings: [],
      _render_hint: "workflow_draft_card" as const,
    };
    goTab("chat");
    // Open the side editor on the agent being edited — the user sees the
    // steps they're talking about while they chat. Origin is pinned to "chat"
    // since we just switched there (the ref hasn't updated in this tick).
    openWorkflow(workflow, "chat");
    // Wait a frame so the chat surface is the active pane before we drop the
    // selection in and focus the composer.
    requestAnimationFrame(() => {
      window.dispatchEvent(
        new CustomEvent("pivot:seed-composer", {
          detail: {
            mode: "agent",
            draft: seededDraft,
            attach: {
              kind: "agent",
              workflow_id: workflow.id,
              name: workflow.name,
              description: workflow.description ?? "",
              status: workflow.status,
            },
          },
        }),
      );
    });
  }, [goTab, openWorkflow]);

  // True when the panel is open and actively bound to an unsaved draft.
  const panelOpenWithDraft = panelOpen && activeEditorDraft !== null;

  // Context value — memoized so consumers only re-render when these change.
  const activeDraftCtx = useMemo(
    () => ({ activeEditorDraft, setActiveEditorDraft, panelOpenWithDraft }),
    [activeEditorDraft, panelOpenWithDraft],
  );

  // Track the lg breakpoint so the sidebar-collapse hotkey only takes effect
  // on desktop (mobile keeps the drawer reachable via the hamburger).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(min-width: 1024px)");
    const sync = (): void => setIsDesktop(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  // Global keyboard shortcuts. Cmd+K (command palette) and the chat composer
  // keys live in their own components; everything else documented in the
  // shortcuts panel is wired here. A pending "G" arms the navigation chord
  // (G then C/P/A/S/L) for a short window.
  useEffect(() => {
    let gPending = false;
    let gTimer: ReturnType<typeof setTimeout> | null = null;
    const clearChord = (): void => {
      gPending = false;
      if (gTimer) {
        clearTimeout(gTimer);
        gTimer = null;
      }
    };
    const isTyping = (): boolean => {
      const el = document.activeElement as HTMLElement | null;
      if (!el) return false;
      const tag = el.tagName;
      return (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        el.isContentEditable
      );
    };

    const onKey = (e: KeyboardEvent): void => {
      const mod = e.metaKey || e.ctrlKey;

      // Modifier combos fire from anywhere (including while typing).
      if (mod && !e.altKey) {
        const k = e.key.toLowerCase();
        if (k === "/") {
          e.preventDefault();
          setShortcutsOpen((o) => !o);
          return;
        }
        if (k === "b") {
          e.preventDefault();
          setSidebarCollapsed((c) => !c);
          return;
        }
        if (e.shiftKey && k === "o") {
          e.preventDefault();
          startNewChat();
          return;
        }
        return;
      }
      if (mod || e.altKey) return;

      // Bare keys are ignored while the user is typing in a field.
      if (isTyping()) {
        clearChord();
        return;
      }

      const k = e.key.toLowerCase();
      if (gPending) {
        const tab = CHORD_NAV_MAP[k];
        clearChord();
        if (tab) {
          e.preventDefault();
          goTab(tab as TabKey);
        }
        return;
      }
      if (k === "g") {
        gPending = true;
        if (gTimer) clearTimeout(gTimer);
        gTimer = setTimeout(() => {
          gPending = false;
        }, 1200);
        return;
      }
      if (k === "/") {
        // Jump focus into the chat composer (ChatDemo listens for this).
        e.preventDefault();
        window.dispatchEvent(new Event("pivot:focus-composer"));
      }
    };

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      clearChord();
    };
  }, [goTab, startNewChat]);

  return (
    <ActiveDraftContext.Provider value={activeDraftCtx}>
    <div
      className="app-shell-root flex h-screen flex-col bg-background"
      style={{ ["--paper-banner-h" as string]: tradingMode === "paper" ? "30px" : "0px" }}
    >

      {/* Sticky top header */}
      <TopHeader
        theme={theme}
        onChooseTheme={chooseTheme}
        tradingMode={tradingMode}
        onChooseTradingMode={chooseTradingMode}
        metrics={metrics}
        accountInitial={accountInitial}
        onOpenBroker={() => setBrokerPanelOpen(true)}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenMobileNav={() => setMobileNavOpen(true)}
        onBrandClick={() => goTab("chat")}
        onLogout={async () => {
          await logoutUser();
          router.replace("/login");
        }}
        onOpenShortcuts={() => setShortcutsOpen(true)}
        onReportBug={() => setReportBugOpen(true)}
      />

      {/* Paper-mode banner — full-width, unmissable, on every page. Sits
          between the header and the body so it spans sidebar + content. */}
      {tradingMode === "paper" && <PaperModeBanner />}

      {/* Mobile nav backdrop — fades in behind the drawer; tap to close. */}
      {mobileNavOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => setMobileNavOpen(false)}
          className="fixed inset-0 z-40 cursor-default lg:hidden"
          style={{ background: "rgba(0,0,0,0.4)" }}
        />
      )}

      {/* Body: sidebar + content. The AgentPanel always opens as a modal
          overlay (dark scrim + white panel) on top of this body, so we no
          longer reserve side-by-side padding for it. */}
      <div className="flex flex-1 min-h-0">
        {/* Left sidebar — inline at lg+, slide-in drawer below. Collapsed
            away on desktop via Ctrl/⌘+B; always mounted on mobile so the
            drawer + hamburger keep working. */}
        {(!sidebarCollapsed || !isDesktop) && (
          <Sidebar
            active={active}
            activeConversationId={active === "chat" ? resumeConv?.id : undefined}
            onTabChange={goTab}
            onNewChat={startNewChat}
            onSelectConversation={(id) => void openConversation(id)}
            onDeleteConversation={removeConversation}
            conversations={conversations}
            mobileOpen={mobileNavOpen}
            onMobileClose={() => setMobileNavOpen(false)}
          />
        )}

        {/* Center pane — flex column so the chat tab (which hosts both
            the dashboard intro and the chat surface) can size its
            messages region to the available space and pin the composer
            to the bottom (ChatGPT/Claude-style). Other tabs get the
            old scrollable wrapper. */}
        <main className="flex flex-1 min-w-0 min-h-0 flex-col">
          {/* Chat surface — ALWAYS mounted so the conversation survives tab
              switches; hidden via `hidden` when another surface (or custom
              children) is shown. The OTHER tabs stay conditionally mounted
              below and re-fetch on mount (desirable for fresh data, e.g. the
              Paper dashboard picking up a newly-filled order). */}
          <div
            className={
              !children && active === "chat"
                ? "relative flex h-full w-full min-h-0"
                : "hidden"
            }
            style={{
              // Compress the chat surface when a side editor is open so
              // draft cards / backtest charts stay visible instead of
              // hiding behind the fixed-position panel. AgentPanel
              // publishes its live width into --side-panel-width (0px
              // when closed / below lg).
              paddingRight: "var(--side-panel-width, 0px)",
              transition: "padding-right 300ms cubic-bezier(0.22, 1, 0.36, 1)",
            }}
          >
              <div
                className="mx-auto flex h-full w-full min-h-0 flex-col px-4 lg:px-6"
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
                  onDraftFromChat={(draft) => {
                    setActiveEditorDraft(draft);
                  }}
                  resume={resumeConv}
                />
              </div>
            </div>
          {/* Non-chat surfaces — conditionally mounted (re-fetch on mount). */}
          {children ? (
            // Custom main-pane content (e.g. stock detail page).
            <div className="flex-1 min-h-0 overflow-y-auto px-8 pt-6 pb-8">
              {children}
            </div>
          ) : active === "chat" ? null : active === "calendar" ? (
            // Calendar gets full pane height (the day panel + month grid
            // consume vertical space; no outer scroll). Mobile tightens.
            <div className="flex-1 min-h-0 px-4 pt-4 lg:px-8 lg:pt-6 flex flex-col overflow-hidden">
              <CalendarTab onOpenWorkflow={openWorkflowById} />
            </div>
          ) : active === "screener" ? (
            // Screener also owns its own height (filter rail + results
            // grid take the full pane). The results table is intentionally
            // wider than phone viewports and scrolls inside its own
            // `screener-results` container, so we clip horizontal here so
            // the whole page doesn't grow with it.
            <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
              <ScreenerPage />
            </div>
          ) : active === "portfolio" ? (
            // Portfolio takes the full pane width — same padding pattern
            // as Quartr's PortfolioTab in frontend-quartr/.../Dashboard.jsx
            // ("padding: 24px 32px"). Sections scroll inside. Mobile tightens.
            <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 pt-4 pb-6 lg:px-8 lg:pt-6 lg:pb-8">
              <PortfolioTab />
            </div>
          ) : active === "views" ? (
            // Views tab — curated market beliefs grid + detail page.
            // Same scrollable wrapper as agents/portfolio.
            <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 pt-4 pb-6 lg:px-8 lg:pt-6 lg:pb-8">
              <ViewsTab onOpenWorkflowById={openWorkflowById} />
            </div>
          ) : (
            <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 pt-4 pb-6 lg:px-8 lg:pt-6 lg:pb-8">
              {active === "agents" && (
                <AgentsTab
                  onOpenWorkflow={openWorkflow}
                  onEditWithChat={editWorkflowWithChat}
                  onBrowseViews={() => goTab("views")}
                />
              )}
            </div>
          )}
        </main>

        {/* Right rail — chat tab only, and only while no conversation
            has started yet. Min-h-0 + overflow-y-auto so the rail
            scrolls *inside itself*; the topbar + sidebar stay put. */}
        {/* Quartr's right rail is exactly 320px with padding 24/20.
            w-80 = 320px; px:20 py:24 mirrors Quartr's padding so the
            cards line up with the same horizontal margins. */}
        {/* Also hidden while a side editor is open: the panel overlays this
            exact strip, and keeping the rail mounted double-reserved the
            right edge (rail width + panel compression) — the chat column
            ended up centered far left of the visible area. */}
        {!children && active === "chat" && !chatActive && !panelOpen && (
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
        onOpenChange={(next) => {
          setPanelOpen(next);
          if (!next) {
            setActiveEditorDraft(null);
            setPanelOriginTab(null);
          }
        }}
        initialWorkflow={panelWorkflow}
        activeEditorDraft={activeEditorDraft}
        onActiveEditorDraftChange={setActiveEditorDraft}
      />

      {/* Global option-chain host — trigger-less; opens the full-screen chain
          when anything (e.g. the stock hover bar) dispatches
          `pivot:open-option-chain` with an underlying. */}
      <OptionChainLauncherCard variant="global" />

      <BrokerOnboarding
        open={brokerPanelOpen}
        onOpenChange={(next) => {
          setBrokerPanelOpen(next);
          if (!next) setBrokerOauth(null);
        }}
        oauth={brokerOauth}
      />

      <CommandPalette
        conversations={conversations}
        onNavigate={goTab}
        onOpenConversation={() => goTab("chat")}
      />

      <KeyboardShortcutsModal
        open={shortcutsOpen}
        onOpenChange={setShortcutsOpen}
      />

      <ReportBugDialog
        open={reportBugOpen}
        onOpenChange={setReportBugOpen}
        currentTab={active}
      />

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        theme={theme}
        onChooseTheme={chooseTheme}
        tradingMode={tradingMode}
        onChooseTradingMode={chooseTradingMode}
        onOpenBroker={() => {
          // Close settings first so the broker dialog isn't stacked behind it.
          setSettingsOpen(false);
          setBrokerPanelOpen(true);
        }}
        onLogout={async () => {
          setSettingsOpen(false);
          await logoutUser();
          router.replace("/login");
        }}
        onOpenShortcuts={() => {
          setSettingsOpen(false);
          setShortcutsOpen(true);
        }}
      />
    </div>
    </ActiveDraftContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Top Header
// ---------------------------------------------------------------------------

function TopHeader({
  theme,
  onChooseTheme,
  tradingMode,
  onChooseTradingMode,
  metrics,
  accountInitial,
  onOpenBroker,
  onOpenSettings,
  onOpenMobileNav,
  onBrandClick,
  onLogout,
  onOpenShortcuts,
  onReportBug,
}: {
  theme: Theme;
  onChooseTheme: (t: Theme) => void;
  tradingMode: TradingMode;
  onChooseTradingMode: (m: TradingMode) => void;
  metrics: MetricState;
  accountInitial: string;
  onOpenBroker: () => void;
  onOpenSettings: () => void;
  onOpenMobileNav: () => void;
  onBrandClick: () => void;
  onLogout: () => void;
  onOpenShortcuts: () => void;
  onReportBug: () => void;
}): React.ReactElement {
  const router = useRouter();
  return (
    <header
      className="top-header relative flex shrink-0 items-center gap-3 px-3 lg:gap-6 lg:px-5"
      style={{
        height: "var(--header-h, 56px)",
        background: "var(--bg-base)",
        borderBottom: "1px solid var(--glass-border)",
      }}
    >
      {/* Mobile-only hamburger — opens the sidebar drawer at <lg. */}
      <button
        type="button"
        onClick={onOpenMobileNav}
        aria-label="Open navigation menu"
        data-testid="mobile-nav-trigger"
        className="inline-flex shrink-0 items-center justify-center lg:hidden"
        style={{
          width: 44,
          height: 44,
          marginLeft: -8,
          background: "transparent",
          border: "none",
          borderRadius: "var(--radius-sm)",
          color: "var(--text-primary)",
          cursor: "pointer",
        }}
      >
        <Menu size={20} strokeWidth={2} aria-hidden="true" />
      </button>

      {/* Brand — serif logotype, fixed-width slot at lg+. Uses --font-experiment
          so we can swap typefaces in one place (globals.css) while we
          decide on the final brand serif. On mobile the brand sits
          inline next to the hamburger (no fixed width). Acts as a
          navigation link back to the chat tab on every breakpoint. */}
      <button
        type="button"
        onClick={onBrandClick}
        aria-label="Go to Pivot chat"
        data-testid="brand-home-link"
        className="brand-slot flex shrink-0 items-center pl-0 lg:pl-1"
        style={{
          gap: 0,
          fontFamily: "var(--font-experiment)",
          fontWeight: "var(--weight-display)" as unknown as number,
          fontSize: 22,
          letterSpacing: "-0.02em",
          color: "var(--text-primary)",
          background: "transparent",
          border: "none",
          padding: 0,
          cursor: "pointer",
        }}
      >
        {/* Pivot brand mark — crisp inline SVG (see PivotMark). Paints with
            currentColor, so it inherits `--text-primary` and flips between
            black (light) and white (dark) automatically, with no raster
            asset or per-theme file swap. */}
        <PivotMark size={19} className="shrink-0" title="Pivot" />
        {/* Small gap between the mark and the serif wordmark. */}
        <span style={{ marginLeft: 8 }}>pivot</span>
      </button>

      {/* Search — Quartr pill, sized + bordered, no Tailwind background.
          Hidden below lg; mobile users get the CommandPalette via the
          account menu / keyboard shortcut. */}
      <div
        className="hidden flex-1 items-center gap-2 lg:flex"
        style={{
          maxWidth: 360,
          height: 38,
          padding: "0 16px",
          background: "var(--bg-primary)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-pill)",
          transition: "border-color 0.2s var(--ease-quartr)",
          position: "relative",
        }}
      >
        <Search
          className="shrink-0"
          size={14}
          strokeWidth={2}
          style={{ color: "var(--text-tertiary)" }}
          aria-hidden={true}
        />
        <CompanyAutosuggest
          placeholder="Search stocks, strategies, conversations…"
          onSelect={(symbol) => router.push(`/stock/${symbol}`)}
          inputDataTestId="global-search"
          enableVoice
        />
      </div>

      {/* Right cluster — metric stack + account menu */}
      <div className="ml-auto flex shrink-0 items-center gap-7">
        <MetricStrip metrics={metrics} />
        <AccountMenu
          theme={theme}
          onChooseTheme={onChooseTheme}
          tradingMode={tradingMode}
          onChooseTradingMode={onChooseTradingMode}
          initial={accountInitial}
          onOpenBroker={onOpenBroker}
          onOpenSettings={onOpenSettings}
          onLogout={onLogout}
          onOpenShortcuts={onOpenShortcuts}
          onReportBug={onReportBug}
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
  tradingMode,
  onChooseTradingMode,
  initial,
  onOpenBroker,
  onOpenSettings,
  onLogout,
  onOpenShortcuts,
  onReportBug,
}: {
  theme: Theme;
  onChooseTheme: (t: Theme) => void;
  tradingMode: TradingMode;
  onChooseTradingMode: (m: TradingMode) => void;
  initial: string;
  onOpenBroker: () => void;
  onOpenSettings: () => void;
  onLogout: () => void;
  onOpenShortcuts: () => void;
  onReportBug: () => void;
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [isNarrow, setIsNarrow] = useState(false);
  // Touch-primary devices (phone/tablet) have no physical keyboard, so the
  // keyboard-shortcuts entry is hidden there. Keyed off pointer capability,
  // not screen width — a narrow/windowed desktop still has a keyboard.
  const [hideShortcuts, setHideShortcuts] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const helpCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // On phones the side flyout pops off the left edge of the screen, so
  // collapse Help into an inline expansion below the menu item instead.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(max-width: 639px)");
    const sync = (): void => setIsNarrow(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(pointer: coarse)");
    const sync = (): void => setHideShortcuts(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

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
            icon={Plug}
            label="Brokers"
            onClick={() => {
              setOpen(false);
              onOpenBroker();
            }}
            testId="menu-brokers"
          />
          <MenuItem
            icon={Settings}
            label="Settings"
            testId="menu-settings"
            onClick={() => {
              setOpen(false);
              onOpenSettings();
            }}
          />
          <div
            style={{ position: "relative" }}
            onMouseEnter={() => {
              // On touch (coarse pointer) a tap fires a synthetic mouseenter
              // that would open the submenu, then onClick toggles it shut —
              // so the menu never opens. Let click own it on touch devices.
              if (isNarrow || hideShortcuts) return;
              cancelHelpClose();
              setHelpOpen(true);
            }}
            onMouseLeave={() => {
              if (isNarrow || hideShortcuts) return;
              scheduleHelpClose();
            }}
          >
            <MenuItem
              icon={HelpCircle}
              label="Help"
              hasChevron={true}
              chevronDirection={isNarrow ? "down" : "side"}
              active={helpOpen}
              onClick={() => setHelpOpen((v) => !v)}
            />
            {helpOpen && (
              <div
                role="menu"
                data-testid="account-menu-help-submenu"
                onMouseEnter={isNarrow || hideShortcuts ? undefined : cancelHelpClose}
                onMouseLeave={isNarrow || hideShortcuts ? undefined : scheduleHelpClose}
                style={
                  isNarrow
                    ? {
                        // Phone: render submenu inline below the Help row.
                        // No absolute positioning so it can't fall off the
                        // left edge of the viewport.
                        marginTop: 4,
                        marginLeft: 8,
                        padding: 4,
                        background: "var(--bg-elevated)",
                        border: "1px solid var(--glass-border)",
                        borderRadius: "var(--radius-md)",
                        display: "flex",
                        flexDirection: "column",
                      }
                    : {
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
                      }
                }
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
                    onReportBug();
                  }}
                />
                {!hideShortcuts && (
                  <>
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
                        onOpenShortcuts();
                      }}
                    />
                  </>
                )}
              </div>
            )}
          </div>
          <MenuItem
            icon={LogOut}
            label="Log out"
            testId="menu-logout"
            onClick={() => {
              setOpen(false);
              onLogout();
            }}
          />

          {/* Divider */}
          <div
            aria-hidden={true}
            style={{
              height: 1,
              background: "var(--glass-border)",
              margin: "4px 6px",
            }}
          />

          {/* Trading-mode toggle — Real vs Paper. Switches the WHOLE app's
              data source (portfolio/holdings/orders/P&L) and routes
              buys/sells to the isolated paper book when Paper. */}
          <div
            style={{
              padding: "2px 10px 4px",
              fontSize: 10.5,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-tertiary)",
            }}
          >
            Trading mode
          </div>
          <div
            className="flex items-center justify-center"
            style={{ padding: "0 10px 8px", gap: 12 }}
          >
            <span
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                color:
                  tradingMode === "real"
                    ? "var(--text-primary)"
                    : "var(--text-tertiary)",
                transition: "color 0.2s var(--ease-quartr)",
              }}
            >
              Real
            </span>
            <Switch
              checked={tradingMode === "paper"}
              onCheckedChange={(checked) =>
                onChooseTradingMode(checked ? "paper" : "real")
              }
              aria-label="Toggle paper trading mode"
              data-testid="trading-mode-switch"
              className="data-[state=checked]:bg-[#d97706]"
            />
            <span
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                color:
                  tradingMode === "paper"
                    ? "#d97706"
                    : "var(--text-tertiary)",
                transition: "color 0.2s var(--ease-quartr)",
              }}
            >
              Paper
            </span>
          </div>

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
  chevronDirection = "down",
  hasExternalArrow = false,
  active = false,
  testId,
}: {
  icon?: React.ComponentType<{ size?: number; strokeWidth?: number }>;
  label: string;
  onClick: () => void;
  hasChevron?: boolean;
  /** "down" rotates 180° when active (inline expand on phones); "side"
   *  uses a left-pointing chevron that mirrors the actual flyout
   *  direction on desktop, where the submenu opens to the left of the
   *  AccountMenu. */
  chevronDirection?: "down" | "side";
  hasExternalArrow?: boolean;
  active?: boolean;
  testId?: string;
}): React.ReactElement {
  const trailing = hasChevron ? (
    chevronDirection === "side" ? (
      <ChevronLeft size={14} strokeWidth={2} aria-hidden={true} />
    ) : (
      <ChevronDown
        size={14}
        strokeWidth={2}
        aria-hidden={true}
        style={{
          transform: active ? "rotate(180deg)" : "rotate(0deg)",
          transition: "transform 0.18s var(--ease-quartr)",
        }}
      />
    )
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
// Paper-mode banner — full-width, unmissable bar shown on every page while
// the app is in paper (simulated) mode.
// ---------------------------------------------------------------------------

function PaperModeBanner(): React.ReactElement {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="paper-mode-banner"
      className="flex shrink-0 items-center justify-center gap-1.5"
      style={{
        background: "#d97706",
        color: "#ffffff",
        padding: "5px 16px",
        fontFamily: "var(--font-ui)",
        fontSize: 12,
        fontWeight: 600,
        letterSpacing: "0.01em",
        borderBottom: "1px solid rgba(0,0,0,0.10)",
      }}
    >
      <FlaskConical size={13} strokeWidth={2.25} aria-hidden={true} />
      <span>Paper Trading Mode</span>
      <TooltipProvider delayDuration={100}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label="What is paper trading?"
              data-testid="paper-mode-info"
              className="inline-flex items-center justify-center rounded-full"
              style={{
                width: 16,
                height: 16,
                marginLeft: 1,
                color: "#ffffff",
                opacity: 0.85,
                cursor: "help",
              }}
            >
              <Info size={13} strokeWidth={2.5} aria-hidden={true} />
            </button>
          </TooltipTrigger>
          <TooltipContent
            side="bottom"
            className="max-w-[280px] text-left font-normal leading-relaxed"
          >
            Paper trading places{" "}
            <strong className="font-semibold">simulated</strong> buys &amp;
            sells with virtual cash. Balances, holdings, and P&amp;L here are
            tracked separately — no real money is used and your real portfolio
            is never touched.
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
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
                fontFamily: "var(--font-display)",
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
  activeConversationId,
  onTabChange,
  onNewChat,
  onSelectConversation,
  onDeleteConversation,
  conversations,
  mobileOpen,
  onMobileClose,
}: {
  active: TabKey;
  /** Id of the conversation currently open in the chat surface (highlighted). */
  activeConversationId?: string;
  onTabChange: (key: TabKey) => void;
  onNewChat: () => void;
  /** Open a persisted conversation in the chat surface. */
  onSelectConversation: (id: string) => void;
  /** Delete a conversation (server + list). */
  onDeleteConversation: (id: string) => void;
  conversations: ConvEntry[];
  mobileOpen: boolean;
  onMobileClose: () => void;
}): React.ReactElement {
  // Pinned conversations — a per-device preference kept in localStorage.
  // Pinned entries float in their own section above Recent; unpinning
  // returns them to the recency-ordered list.
  const [pinnedIds, setPinnedIds] = useState<string[]>(() => readPinnedIds());
  const togglePin = (id: string): void => {
    setPinnedIds((prev) => {
      const next = prev.includes(id)
        ? prev.filter((x) => x !== id)
        : [...prev, id];
      writePinnedIds(next);
      return next;
    });
  };
  const pinnedConvs = conversations.filter((c) => pinnedIds.includes(c.id));
  const recentConvs = conversations.filter((c) => !pinnedIds.includes(c.id));

  const handleDelete = (id: string): void => {
    // A deleted conversation must not linger in the pin set.
    setPinnedIds((prev) => {
      if (!prev.includes(id)) return prev;
      const next = prev.filter((x) => x !== id);
      writePinnedIds(next);
      return next;
    });
    onDeleteConversation(id);
  };

  // On lg+ the sidebar sits inline (in the flex row) — same look as before.
  // Below lg it becomes a fixed slide-in drawer driven by `mobileOpen`.
  // We do NOT use `hidden` so the transform transition stays smooth.
  return (
    <nav
      className="sidebar-shell shrink-0 flex flex-col"
      aria-label="Primary navigation"
      data-testid="sidebar-nav"
      data-mobile-open={mobileOpen ? "true" : "false"}
      style={{
        width: 240,
        background: "var(--bg-base)",
        borderRight: "1px solid var(--glass-border)",
        padding: "18px 14px 16px",
      }}
    >
      {/* Mobile-only close button — keeps the drawer escapable for
          screen-reader / keyboard users (clicking nav or backdrop also
          closes). The .sidebar-close-mobile class hides this on lg+
          (see globals.css); inline `display: inline-flex` was fighting
          Tailwind's lg:hidden, so we drive display from CSS. */}
      <button
        type="button"
        onClick={onMobileClose}
        aria-label="Close navigation"
        className="sidebar-close-mobile self-end"
        style={{
          width: 36,
          height: 36,
          margin: "-4px -4px 6px 0",
          background: "transparent",
          border: "none",
          borderRadius: "var(--radius-sm)",
          color: "var(--text-secondary)",
          cursor: "pointer",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <X size={18} strokeWidth={2} aria-hidden="true" />
      </button>
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

      {/* Conversation history — uppercase header + truncated titles.
          A "New chat" pill sits above the list so it's always reachable
          from the sidebar (replaces the old floating button that was
          pinned to the chat surface's top-right). */}
      <div
        className="flex-1 overflow-y-auto flex flex-col"
        style={{ gap: 14, padding: "0 4px" }}
      >
        <button
          type="button"
          onClick={onNewChat}
          aria-label="Start new chat"
          data-testid="new-chat-btn"
          className="inline-flex items-center"
          style={{
            gap: 10,
            padding: "9px 14px",
            background: "transparent",
            border: "none",
            // Match the sidebar nav items' edge radius (Chat / Portfolio / …)
            // so this button reads as a peer to those rows, not a pill CTA.
            borderRadius: "var(--radius-sm)",
            color: "var(--text-secondary)",
            fontFamily: "var(--font-ui)",
            fontSize: 13.5,
            fontWeight: 500,
            letterSpacing: "-0.005em",
            cursor: "pointer",
            textAlign: "left",
            justifyContent: "flex-start",
            transition:
              "color 0.35s var(--ease-quartr), background-color 0.35s var(--ease-quartr)",
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
          <Plus size={14} strokeWidth={2} aria-hidden="true" />
          New chat
        </button>

        {/* Pinned conversations float above Recent; pin state is a
            per-device preference (localStorage), toggled from the hover
            pin on each row. */}
        {pinnedConvs.length > 0 && (
          <>
            <div style={convHeaderStyle}>Pinned</div>
            <div className="flex flex-col" style={{ gap: 2 }}>
              {pinnedConvs.map((conv) => (
                <ConversationRow
                  key={conv.id}
                  conv={conv}
                  pinned={true}
                  active={conv.id === activeConversationId}
                  onOpen={() => onSelectConversation(conv.id)}
                  onTogglePin={() => togglePin(conv.id)}
                  onDelete={() => handleDelete(conv.id)}
                />
              ))}
            </div>
          </>
        )}

        <div style={convHeaderStyle}>Recent</div>

        {recentConvs.length === 0 && pinnedConvs.length === 0 ? (
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
            {recentConvs.map((conv) => (
              <ConversationRow
                key={conv.id}
                conv={conv}
                pinned={false}
                active={conv.id === activeConversationId}
                onOpen={() => onSelectConversation(conv.id)}
                onTogglePin={() => togglePin(conv.id)}
                onDelete={() => handleDelete(conv.id)}
              />
            ))}
          </div>
        )}
      </div>
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Conversation rows — pinnable history entries under Pinned / Recent.
// ---------------------------------------------------------------------------

const convHeaderStyle: React.CSSProperties = {
  padding: "0 10px",
  fontSize: 11,
  fontFamily: "var(--font-ui)",
  fontWeight: 500,
  color: "var(--text-tertiary)",
  letterSpacing: "0.04em",
  textTransform: "uppercase",
};

const PINNED_LS_KEY = "pivot.pinnedConversations";

function readPinnedIds(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(PINNED_LS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function writePinnedIds(ids: string[]): void {
  try {
    window.localStorage.setItem(PINNED_LS_KEY, JSON.stringify(ids));
  } catch {
    /* storage unavailable — pin just won't persist */
  }
}

function ConversationRow({
  conv,
  pinned,
  active = false,
  onOpen,
  onTogglePin,
  onDelete,
}: {
  conv: ConvEntry;
  pinned: boolean;
  /** True when this is the conversation currently open in the chat surface. */
  active?: boolean;
  onOpen: () => void;
  onTogglePin: () => void;
  onDelete: () => void;
}): React.ReactElement {
  const [hovered, setHovered] = useState(false);
  // The open conversation carries the same highlight as hover, held
  // persistently so the user can see which thread they're reading.
  const highlighted = hovered || active;
  return (
    <div
      className="flex items-center"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      aria-current={active ? "page" : undefined}
      style={{
        borderRadius: "var(--radius-sm)",
        background: highlighted ? "var(--surface-active)" : "transparent",
        transition: "background-color 0.2s var(--ease-quartr)",
      }}
    >
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Open conversation: ${conv.preview}`}
        style={{
          flex: 1,
          minWidth: 0,
          padding: "7px 4px 7px 10px",
          background: "transparent",
          border: "none",
          color: highlighted ? "var(--text-primary)" : "var(--text-secondary)",
          fontFamily: "var(--font-ui)",
          fontSize: 12.5,
          fontWeight: active ? 600 : 500,
          textAlign: "left",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          cursor: "pointer",
          transition: "color 0.2s var(--ease-quartr)",
        }}
      >
        {conv.preview}
      </button>
      {/* Row actions — hover-revealed: pin (always visible while pinned so
          the state reads at a glance) + delete. */}
      {(hovered || pinned) && (
        <div className="flex shrink-0 items-center" style={{ marginRight: 4 }}>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onTogglePin();
            }}
            aria-label={pinned ? "Unpin conversation" : "Pin conversation"}
            title={pinned ? "Unpin" : "Pin"}
            className="inline-flex items-center justify-center"
            style={{
              width: 24,
              height: 24,
              background: "transparent",
              border: "none",
              borderRadius: "var(--radius-sm)",
              color: pinned ? "var(--text-primary)" : "var(--text-tertiary)",
              cursor: "pointer",
              transition: "color 0.2s var(--ease-quartr)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "var(--text-primary)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = pinned
                ? "var(--text-primary)"
                : "var(--text-tertiary)";
            }}
          >
            <Pin
              size={12.5}
              strokeWidth={2}
              aria-hidden="true"
              fill={pinned ? "currentColor" : "none"}
            />
          </button>
          {hovered && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
              aria-label="Delete conversation"
              title="Delete"
              className="inline-flex items-center justify-center"
              style={{
                width: 24,
                height: 24,
                background: "transparent",
                border: "none",
                borderRadius: "var(--radius-sm)",
                color: "var(--text-tertiary)",
                cursor: "pointer",
                transition: "color 0.2s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--color-loss, #ea4335)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--text-tertiary)";
              }}
            >
              <Trash2 size={12.5} strokeWidth={2} aria-hidden="true" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// (NewsPlaceholder removed — replaced by TriggersTab)

