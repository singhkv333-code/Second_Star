"use client";

/**
 * AppShell — Quartr-style premium shell with left sidebar nav and center
 * content.
 *
 * Layout:
 *   [Sticky top header: logo + search + metric strip + theme toggle + avatar]
 *   [Left sidebar nav | Center content pane]
 *
 * Nav items: Chat / Portfolio / Agents / Screener
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
import { toast } from "sonner";
import {
  BookOpen,
  Bug,
  Compass,
  ChevronDown,
  ChevronLeft,
  ExternalLink,
  FileText,
  HelpCircle,
  History,
  LayoutDashboard,
  ListFilter,
  Keyboard,
  LogOut,
  Menu,
  Maximize2,
  MessagesSquare,
  Monitor,
  Moon,
  ChartNoAxesCombined,
  Pin,
  Plug,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  Sun,
  Trash2,
  WalletCards,
  Workflow as WorkflowIcon,
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
import { OrderTicketHost } from "@/components/OrderTicket";
import {
  ActiveDraftContext,
} from "@/components/agent-panel/active-draft-context";
import { AgentsTab } from "@/components/agent-panel/AgentsTab";
import { PortfolioTab } from "@/components/agent-panel/PortfolioTab";
import { ChartFrame } from "@/components/chart/ChartFrame";
import { QuickAsk } from "@/components/copilot/QuickAsk";
import { ScreenerPage } from "@/components/screener/ScreenerPage";
import { SettingsDialog } from "@/components/settings/SettingsTab";
import { DashboardTab } from "@/components/DashboardTab";
import { HomeTab } from "@/components/HomeTab";
import { CompanyAutosuggest } from "@/components/CompanyAutosuggest";
import { PivotWordmark } from "@/components/brand/PivotLogo";
import { ProductTour, START_TOUR_EVENT } from "@/components/onboarding/ProductTour";
import { LoginIntroGate } from "@/components/onboarding/LoginIntroGate";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  deleteConversation,
  getMe,
  getPortfolioSummary,
  getWorkflow,
  listConversationMessages,
  listConversations,
  listWorkflows,
  logoutUser,
  setAccountMode,
  type PortfolioSummary,
} from "@/lib/api";
import type { ResumeConversation } from "@/components/chat/ChatDemo";
import { basketAttachment } from "@/components/chat/ComposerContext";
import type { EquityBasket } from "@/lib/agentsApi";
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
  | "home"
  | "chat"
  | "portfolio"
  | "agents"
  | "screener"
  | "chart";

const NAV_ITEMS: {
  key: TabKey;
  label: string;
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}[] = [
  { key: "home", label: "Home", Icon: LayoutDashboard },
  { key: "chart", label: "Chart", Icon: ChartNoAxesCombined },
  { key: "chat", label: "Chat", Icon: MessagesSquare },
  { key: "portfolio", label: "Portfolio", Icon: WalletCards },
  { key: "agents", label: "Agents", Icon: WorkflowIcon },
  { key: "screener", label: "Screener", Icon: ListFilter },
];

// Home is the landing surface — a fresh visit to "/" (no hash), and every
// post-login/signup redirect (which lands on "/"), opens on Home, not Chat.
const DEFAULT_TAB: TabKey = "home";
const METRIC_REFRESH_MS = 30_000;
const ACTIVE_COPILOT_KEY = "pivot:active-copilot-conversation";
const COPILOT_CONTEXT_KEY = "pivot:copilot-page-context";

type CopilotPageContext =
  | { kind: "page"; page: "home" | "portfolio" | "agents" | "screener"; label: string }
  | { kind: "security"; symbol: string; name: string };

function readStoredConversationId(): string | undefined {
  if (typeof window === "undefined") return undefined;
  try { return sessionStorage.getItem(ACTIVE_COPILOT_KEY) || undefined; }
  catch { return undefined; }
}

function readStoredCopilotContext(): CopilotPageContext {
  const fallback: CopilotPageContext = { kind: "page", page: "home", label: "Home" };
  if (typeof window === "undefined") return fallback;
  try {
    const parsed = JSON.parse(sessionStorage.getItem(COPILOT_CONTEXT_KEY) || "null") as CopilotPageContext | null;
    return parsed?.kind ? parsed : fallback;
  } catch { return fallback; }
}

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
    // Show the user's full history in the sidebar, not just the last 10.
    // 200 is the backend's per-request max; conversations are already
    // Postgres-persisted, so this surfaces everything a real user accrues.
    const result = await listConversations({ limit: 200 });
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
  // A pending request to switch the Agents tab's surface toggle (Home F&O tile
  // → "options"). Nonce-bumped so repeat requests re-fire; consumed by
  // AgentsTab even when it mounts lazily after the request is set.
  const [agentsSurfaceReq, setAgentsSurfaceReq] = useState<
    { surface: "equity" | "options" | "baskets"; nonce: number } | null
  >(null);
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
  // The concrete mode the chart iframe is told to wear. `theme` is three-state
  // — "system" defers to the OS — and the chart cannot defer to anything, so
  // it is resolved here. Starts "dark" so the first server/client paint agree;
  // the effect below corrects it before the frame is ever told anything.
  const [resolvedTheme, setResolvedTheme] = useState<"dark" | "light">("dark");
  const [chartSymbol, setChartSymbol] = useState<string | undefined>(undefined);
  // Global trading mode (real/live vs paper). Mirrors the persisted store so
  // the toggle + banner re-render; the data layer reads the store directly.
  // Default 'paper' matches lib/trading-mode.ts DEFAULT_MODE so the first
  // client paint agrees with the SSR snapshot (no hydration drift) and a
  // fresh user lands in the simulated book.
  const [tradingMode, setTradingModeState] = useState<TradingMode>("paper");
  const [conversations, setConversations] = useState<ConvEntry[]>([]);
  // First letter of the signed-in user's name/email — used for the
  // avatar initial in the topbar (Quartr's TopHeader.jsx pattern).
  const [accountInitial, setAccountInitial] = useState<string>("U");
  const [accountName, setAccountName] = useState<string>("Account");
  const [accountEmail, setAccountEmail] = useState<string>("");
  // True once the user has sent ≥1 message in the chat tab. Active
  // conversations use a slightly wider reading column than the empty state.
  const [chatActive, setChatActive] = useState(false);
  // Bumped by the "New chat" button to remount DashboardTab/ChatDemo
  // and start a fresh session (clears messages + conversation_id).
  const [chatResetKey, setChatResetKey] = useState(0);
  // Presentation state only. The chat component stays mounted while this
  // toggles, so side-panel/full-workspace transitions cannot fork a thread.
  const [copilotPanelOpen, setCopilotPanelOpen] = useState(false);
  const [chartChatOpen, setChartChatOpen] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState<string | undefined>(
    readStoredConversationId,
  );
  const restoreConversationIdRef = useRef(activeConversationId);
  const [copilotContext, setCopilotContext] = useState<CopilotPageContext>(
    { kind: "page", page: "home", label: "Home" },
  );
  // A prompt seeded from the Home tab — passed to DashboardTab which fills the
  // composer and auto-submits it. Cleared once ChatDemo has consumed it.
  const [seededChatPrompt, setSeededChatPrompt] = useState<string | undefined>(undefined);
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

  const rememberConversationId = useCallback((id: string): void => {
    setActiveConversationId(id);
    try { sessionStorage.setItem(ACTIVE_COPILOT_KEY, id); } catch { /* unavailable */ }
  }, []);
  // Keep-alive tabs (2026-07-03 perf pass): non-chat tabs used to UNMOUNT on
  // switch-away, so every return re-fetched everything behind a skeleton
  // (~300-800ms measured per revisit). Tabs now mount lazily on FIRST visit
  // and stay mounted-but-hidden afterwards — the pattern Chat always used —
  // so a revisit repaints instantly from live DOM, like a proper SPA.
  const [visitedTabs, setVisitedTabs] = useState<Set<TabKey>>(
    () => new Set<TabKey>([DEFAULT_TAB]),
  );
  useEffect(() => {
    setVisitedTabs((prev) =>
      prev.has(active) ? prev : new Set(prev).add(active),
    );
  }, [active]);

  // Storage is client-only; adopt it after hydration so SSR and the first
  // client paint agree. The route-registration effect below wins when the
  // current page supplies newer context.
  useEffect(() => {
    setCopilotContext(readStoredCopilotContext());
  }, []);

  // Rehydrate the active Copilot thread when AppShell remounts across a real
  // route boundary (for example /stock/RELIANCE -> /#chat). Presentation
  // switches inside the shell never come through here and never remount chat.
  useEffect(() => {
    const id = restoreConversationIdRef.current;
    if (!id) return;
    let cancelled = false;
    void listConversationMessages(id, { limit: 200 })
      .then((result) => {
        if (cancelled || isError(result)) return;
        const messages = result.data.items
          .filter((message) => message.role === "user" || message.role === "assistant")
          .map((message) => ({
            role: message.role as "user" | "assistant",
            content: message.content,
            tool_payload: message.tool_payload,
          }));
        setResumeConv({ id, messages });
        setChatActive(messages.length > 0);
        setChatResetKey((key) => key + 1);
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  // Register the current non-chart product surface as a managed attachment.
  // Chat and chart do not overwrite it: full workspace preserves where the
  // conversation came from, while Charto owns its entirely separate context.
  useEffect(() => {
    let next: CopilotPageContext | null = null;
    const stockMatch = pathname.match(/^\/stock\/([^/]+)/i);
    if (stockMatch?.[1]) {
      const symbol = decodeURIComponent(stockMatch[1]).toUpperCase();
      next = { kind: "security", symbol, name: symbol };
    } else if (!children && active === "home" && readHashTab() === "home") {
      next = { kind: "page", page: "home", label: "Home" };
    } else if (!children && active === "portfolio") {
      next = { kind: "page", page: "portfolio", label: "My portfolio" };
    } else if (!children && active === "agents") {
      next = { kind: "page", page: "agents", label: "My agents" };
    } else if (!children && active === "screener") {
      next = { kind: "page", page: "screener", label: "Screener" };
    }
    if (!next) return;
    setCopilotContext(next);
    try { sessionStorage.setItem(COPILOT_CONTEXT_KEY, JSON.stringify(next)); } catch { /* unavailable */ }
  }, [active, children, pathname]);

  useEffect(() => {
    if (!copilotPanelOpen || active === "chat") return;
    const onEscape = (event: KeyboardEvent): void => {
      if (event.key !== "Escape") return;
      setCopilotPanelOpen(false);
      requestAnimationFrame(() => window.dispatchEvent(new Event("pivot:focus-quick-ask")));
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [active, copilotPanelOpen]);

  useEffect(() => {
    if (!copilotPanelOpen) return;
    requestAnimationFrame(() => window.dispatchEvent(new Event("pivot:focus-composer")));
  }, [copilotPanelOpen]);

  // Hash + theme init
  useEffect(() => {
    setActive(readHashTab());
    const onHash = (): void => setActive(readHashTab());
    window.addEventListener("hashchange", onHash);

    const initial = readStoredTheme() ?? "system";
    setTheme(initial);
    applyTheme(initial);
    setResolvedTheme(
      initial === "dark" || (initial === "system" && osPrefersDark())
        ? "dark" : "light",
    );

    // Trading mode: adopt the persisted choice (default 'real') and reconcile
    // the backend account mode to match, so order routing (`should_use_paper`)
    // always agrees with what the banner/UI claims — a paper UI never places
    // a live order and vice-versa.
    const storedMode = getTradingMode();
    setTradingModeState(storedMode);
    void setAccountMode(storedMode === "real" ? "live" : "paper");

    // Paper-mode intimation — shown exactly once per login/signup session.
    // The auth pages set `pivot:just-signed-in` in sessionStorage on success;
    // we consume it here so it never re-fires on a page reload.
    try {
      const justSignedIn = sessionStorage.getItem("pivot:just-signed-in");
      if (justSignedIn === "1") {
        sessionStorage.removeItem("pivot:just-signed-in");
        if (storedMode === "paper") {
          // Defer until the brand intro (if any) finishes so the toast
          // doesn't overlap with the post-login animation.
          const showPaperToast = (): void => {
            window.setTimeout(() => {
              toast("You're in Paper Trading mode", {
                description:
                  "Trades are simulated — no real money is used. Switch to Live in the sidebar when ready.",
                duration: 7000,
              });
            }, 800);
          };
          if (window.__pivotIntroPending) {
            window.addEventListener("pivot:intro-done", showPaperToast, {
              once: true,
            });
          } else {
            showPaperToast();
          }
        }
      }
    } catch {
      /* storage unavailable — skip the notice */
    }

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
      setAccountName((full_name && full_name.trim()) || email || "Account");
      setAccountEmail(email || "");
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
    const onChange = (): void => {
      applyTheme("system");
      setResolvedTheme(osPrefersDark() ? "dark" : "light");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const chooseTheme = useCallback((next: Theme): void => {
    setTheme(next);
    applyTheme(next);
    setResolvedTheme(
      next === "dark" || (next === "system" && osPrefersDark())
        ? "dark" : "light",
    );
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

  // Immediate refetch when any trade-affecting mutation fires the global
  // `pivot:portfolio-dirty` event (dispatched by lib/api.ts after orders /
  // paper fills / workflow / basket / IPO POSTs succeed). This makes
  // the header value/P&L update right away instead of waiting for the next
  // 30-second poll tick.
  useEffect(() => {
    window.addEventListener("pivot:portfolio-dirty", loadMetrics);
    return () => window.removeEventListener("pivot:portfolio-dirty", loadMetrics);
  }, [loadMetrics]);

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
    setActiveConversationId(undefined);
    try { sessionStorage.removeItem(ACTIVE_COPILOT_KEY); } catch { /* unavailable */ }
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
              tool_payload: m.tool_payload,
            }));
      setResumeConv({ id: convId, messages });
      rememberConversationId(convId);
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
    [pathname, rememberConversationId, router],
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
    if (key === "chat" || key === "chart") setCopilotPanelOpen(false);
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

  const openChart = useCallback((symbol: string): void => {
    setChartSymbol(symbol.trim().toUpperCase());
    goTab("chart");
  }, [goTab]);

  useEffect(() => {
    const handleOpenChart = (event: Event): void => {
      const symbol = (event as CustomEvent<{ symbol?: string }>).detail?.symbol;
      if (symbol) openChart(symbol);
    };
    window.addEventListener("pivot:open-chart", handleOpenChart);
    return () => window.removeEventListener("pivot:open-chart", handleOpenChart);
  }, [openChart]);

  // Seed a prompt from the Home tab into the chat composer and jump there.
  const sendChatPrompt = useCallback((prompt: string): void => {
    setSeededChatPrompt(prompt);
    goTab("chat");
  }, [goTab]);

  const askFromQuickComposer = useCallback((question: string): void => {
    if (!children && active === "chart") {
      setChartChatOpen(true);
      window.dispatchEvent(new CustomEvent("pivot:chart-ask", { detail: { text: question } }));
      return;
    }
    setCopilotPanelOpen(true);
    setSeededChatPrompt(question);
  }, [active, children]);
  const clearSeededChatPrompt = useCallback(() => setSeededChatPrompt(undefined), []);

  const openWorkflow = useCallback(
    (workflow: Workflow, originTab?: TabKey): void => {
      const isUnsaved = !workflow.id || workflow.id === "" || workflow.id.startsWith("local-");
      if (isUnsaved && workflow.status === "draft") {
        setActiveEditorDraft(workflow);
      } else {
        // Opening a SAVED/live agent ends any prior draft session — otherwise a
        // leftover activeEditorDraft would shadow it in AgentPanel (which now
        // treats "any activeEditorDraft" as the bound editable session).
        setActiveEditorDraft(null);
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

  // Home "Prebuilt strategies" tile → Agents tab with the side editor open on
  // that agent. We jump to the Agents tab immediately (so the switch feels
  // instant), then resolve the workflow: look the seeded agent up by name in
  // the user's own workflows and open the REAL one when present; otherwise
  // (e.g. the options strategy, which has no seeded workflow) open the local
  // draft the tile carried. Origin is pinned to "agents" so the editor stays
  // put after the tab switch and closes if the user navigates elsewhere.
  const openAgentFromHome = useCallback(
    (spec: { matchName: string; draft: Workflow }): void => {
      // A prebuilt AUTOMATION is a workflow agent — land on the "Equity agents"
      // surface (not whatever surface was last open) so the
      // editor opens over the agents list it belongs to.
      setAgentsSurfaceReq((prev) => ({ surface: "equity", nonce: (prev?.nonce ?? 0) + 1 }));
      goTab("agents");
      void listWorkflows({ status: ["active", "paused", "draft"], limit: 50 })
        .then(async (result) => {
          if (isError(result)) {
            openWorkflow(spec.draft, "agents");
            return;
          }
          const match = result.data.items.find((w) => w.name === spec.matchName);
          if (!match) {
            openWorkflow(spec.draft, "agents");
            return;
          }
          const full = await getWorkflow(match.id);
          openWorkflow(isError(full) ? spec.draft : full.data, "agents");
        })
        .catch(() => openWorkflow(spec.draft, "agents"));
    },
    [goTab, openWorkflow],
  );

  // Home F&O prebuilt tile → Agents tab on its "Options" surface (the
  // registered option strategies list). Bump the nonce so AgentsTab re-applies
  // it even on repeat clicks, then switch tabs.
  const openAgentsStrategies = useCallback((): void => {
    setAgentsSurfaceReq((prev) => ({ surface: "options", nonce: (prev?.nonce ?? 0) + 1 }));
    goTab("agents");
  }, [goTab]);

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
    // Start a fresh chat session rather than reusing whatever's mounted —
    // otherwise the amendment lands in the user's current conversation
    // instead of a new one seeded just for this agent.
    setResumeConv(undefined);
    setActiveConversationId(undefined);
    try { sessionStorage.removeItem(ACTIVE_COPILOT_KEY); } catch { /* unavailable */ }
    setChatResetKey((k) => k + 1);
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

  // "Edit with chat" for a saved basket — the same selection-not-sentence
  // handoff as agents, minus the side editor (baskets have no step graph).
  // The chip carries the basket id + exact legs, so "drop SUZLON" amends THIS
  // basket rather than re-resolving it from a free-text name.
  const editBasketWithChat = useCallback((basket: EquityBasket): void => {
    setResumeConv(undefined);
    setActiveConversationId(undefined);
    try { sessionStorage.removeItem(ACTIVE_COPILOT_KEY); } catch { /* unavailable */ }
    setChatResetKey((k) => k + 1);
    goTab("chat");
    requestAnimationFrame(() => {
      window.dispatchEvent(
        new CustomEvent("pivot:seed-composer", {
          detail: { attach: basketAttachment(basket) },
        }),
      );
    });
  }, [goTab]);

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

  const quickAskContextLabel = !children && active === "chart"
    ? (chartSymbol ? `${chartSymbol} chart` : "Current chart")
    : copilotContext.kind === "security"
      ? copilotContext.symbol
      : copilotContext.kind === "page"
        ? copilotContext.label
        : "Selected context";
  const quickAskPlaceholder = !children && active === "chart"
    ? `Ask about ${chartSymbol ?? "this chart"}…`
    : copilotContext.kind === "security"
      ? `Ask about ${copilotContext.symbol}…`
      : copilotContext.kind === "page" && copilotContext.page === "portfolio"
        ? "Ask about my portfolio…"
        : copilotContext.kind === "page" && copilotContext.page === "screener"
          ? "Screen stocks…"
          : copilotContext.kind === "page" && copilotContext.page === "agents"
            ? "Ask about my agents…"
            : "Ask Pivot…";

  return (
    <ActiveDraftContext.Provider value={activeDraftCtx}>
    {/* Brand intro — plays once, right after login/signup (armLoginIntro). */}
    <LoginIntroGate />
    <div
      className="app-shell-root flex flex-col h-screen bg-background"
      style={{ ["--paper-banner-h" as string]: "0px" }}
    >
        <TopHeader
          variant={!children && active === "chart" ? "chart" : "default"}
          theme={theme}
          onChooseTheme={chooseTheme}
          tradingMode={tradingMode}
          onChooseTradingMode={chooseTradingMode}
          metrics={metrics}
          accountInitial={accountInitial}
          accountName={accountName}
          accountEmail={accountEmail}
          onOpenBroker={() => setBrokerPanelOpen(true)}
          onOpenSettings={() => setSettingsOpen(true)}
          onOpenMobileNav={() => setMobileNavOpen(true)}
          onBrandClick={() => goTab("home")}
          onOpenChart={openChart}
          onLogout={async () => {
            await logoutUser();
            router.replace("/login");
          }}
          onOpenShortcuts={() => setShortcutsOpen(true)}
          onReportBug={() => setReportBugOpen(true)}
        />

      {/* The global header sits above this row on every route. The sidebar
          and page content therefore share one consistent top edge. */}
      <div className="flex flex-1 min-w-0 min-h-0">
      {(!sidebarCollapsed || !isDesktop) && (
        <Sidebar
          active={active}
          onTabChange={goTab}
          mobileOpen={mobileNavOpen}
          onMobileClose={() => setMobileNavOpen(false)}
          onBrandClick={() => goTab("home")}
        />
      )}


      <div className="flex flex-1 min-w-0 min-h-0 flex-col">

        {/* Paper-mode banner removed per owner request — the account-menu
            toggle still indicates paper vs real. */}

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

        {/* Body: content + right rail. The AgentPanel opens as a modal
            overlay on top of this body. */}
        <div className="flex flex-1 min-h-0">
        {/* Center pane — flex column so the chat tab (which hosts both
            the dashboard intro and the chat surface) can size its
            messages region to the available space and pin the composer
            to the bottom (ChatGPT/Claude-style). Other tabs get the
            old scrollable wrapper. */}
        <main
          className={`copilot-main flex flex-1 min-w-0 min-h-0 flex-col${
            copilotPanelOpen && (Boolean(children) || active !== "chat") && active !== "chart"
              ? " copilot-main-with-panel"
              : ""
          }`}
        >
          {/* Chat surface — ALWAYS mounted so the conversation survives tab
              switches; hidden via `hidden` when another surface (or custom
              children) is shown. The OTHER tabs stay conditionally mounted
              below and re-fetch on mount (desirable for fresh data, e.g. the
              Paper dashboard picking up a newly-filled order). */}
          <div
            className={
              !children && active === "chat"
                ? "relative flex h-full w-full min-h-0"
                : (!children ? active !== "chart" : true)
                  ? `copilot-side-panel ${copilotPanelOpen ? "copilot-side-panel--open" : "copilot-side-panel--closed"}`
                  : "hidden"
            }
            style={{
              // Compress the chat surface when a side editor is open so
              // draft cards / backtest charts stay visible instead of
              // hiding behind the fixed-position panel. AgentPanel
              // publishes its live width into --side-panel-width (0px
              // when closed / below lg).
              paddingRight: !children && active === "chat" ? "var(--side-panel-width, 0px)" : 0,
              transition: "padding-right 300ms cubic-bezier(0.22, 1, 0.36, 1)",
            }}
            role={copilotPanelOpen && (Boolean(children) || active !== "chat") ? "complementary" : undefined}
            aria-label={copilotPanelOpen && (Boolean(children) || active !== "chat") ? "Pivot Copilot" : undefined}
          >
              {(Boolean(children) || (active !== "chat" && active !== "chart")) && (
                <div className="copilot-panel-header" data-testid="copilot-panel-header" aria-label="Copilot panel controls">
                  <button
                    type="button"
                    className="copilot-panel-action"
                    onClick={() => goTab("chat")}
                    aria-label="Expand Copilot to full workspace"
                    title="Expand to full workspace"
                  >
                    <Maximize2 size={16} aria-hidden={true} />
                  </button>
                  <button
                    type="button"
                    className="copilot-panel-action"
                    onClick={() => {
                      setCopilotPanelOpen(false);
                      requestAnimationFrame(() => window.dispatchEvent(new Event("pivot:focus-quick-ask")));
                    }}
                    aria-label="Close Copilot panel"
                  >
                    <X size={17} aria-hidden={true} />
                  </button>
                </div>
              )}
              {!children && active === "chat" && (
                <ChatHistoryPane
                  activeConversationId={resumeConv?.id ?? activeConversationId}
                  conversations={conversations}
                  onNewChat={startNewChat}
                  onSelectConversation={(id) => void openConversation(id)}
                  onDeleteConversation={removeConversation}
                />
              )}
              <div
                className={
                  Boolean(children) || (active !== "chat" && active !== "chart")
                    ? "flex h-full w-full min-h-0 flex-col overflow-hidden"
                    : "mx-auto flex h-full w-full min-h-0 flex-col px-4 lg:px-6"
                }
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
                  onChatActiveChange={setChatActive}
                  onDraftFromChat={(draft) => {
                    setActiveEditorDraft(draft);
                  }}
                  seededPrompt={seededChatPrompt}
                  onSeededPromptConsumed={clearSeededChatPrompt}
                  resume={resumeConv}
                  pageContext={copilotContext.kind === "security" ? copilotContext : undefined}
                  conversationId={activeConversationId}
                  onConversationIdChange={rememberConversationId}
                  compact={Boolean(children) || (active !== "chat" && active !== "chart")}
                  composerPlaceholder={
                    Boolean(children) || (active !== "chat" && active !== "chart")
                      ? quickAskPlaceholder
                      : undefined
                  }
                />
              </div>
            </div>
          {/* Non-chat surfaces — KEEP-ALIVE: each pane mounts on its first
              visit and then stays mounted-but-hidden (display:none), so tab
              switches never re-fetch or re-skeleton. Wrapper classes per tab
              are unchanged; only the mount/hide policy moved. */}
          {children && (
            // Custom main-pane content (e.g. stock detail page).
            <div className="flex-1 min-h-0 overflow-y-auto px-3 pt-6 pb-8 sm:px-5 lg:px-8">
              {children}
            </div>
          )}
          {visitedTabs.has("home") && (
            // Home — fit-to-screen bento dashboard. overflow-hidden so the
            // bento grid fills the pane without an outer scrollbar; individual
            // cards fall back to scroll if a viewport is very short.
            <div
              className={
                !children && active === "home"
                  ? "flex-1 min-h-0 overflow-hidden px-4 pt-4 pb-4 lg:px-8 lg:pt-6 lg:pb-6"
                  : "hidden"
              }
              style={{ background: "var(--bg-inset)" }}
            >
              <HomeTab onGoTab={goTab} onSendPrompt={sendChatPrompt} onOpenAgent={openAgentFromHome} onOpenStrategies={openAgentsStrategies} />
            </div>
          )}
          {visitedTabs.has("chart") && (
            // The charting engine, in a same-origin iframe. Kept MOUNTED once
            // visited (the visitedTabs pattern) rather than unmounted on tab
            // switch: remounting would reload the chart and throw away the
            // user's drawings, indicators and scroll position every time they
            // glanced at Portfolio.
            <div
              className={
                !children && active === "chart"
                  ? "chart-shell-pane relative flex-1 min-h-0 flex flex-col"
                  : "hidden"
              }
            >
              {/* The rail overlays this frame's left edge (so the chart's
                  header can be the shell's one top bar and still reach the
                  window edge), and only the shell knows how wide it is —
                  48px, or 0 when it is collapsed or is the mobile drawer,
                  which floats over everything and reserves nothing. */}
              <ChartFrame
                symbol={chartSymbol}
                theme={resolvedTheme}
                onChatVisibilityChange={setChartChatOpen}
              />
            </div>
          )}
          {visitedTabs.has("screener") && (
            // Screener owns its own height (filter rail + results grid take
            // the full pane); horizontal clipped so the wide table scrolls
            // inside its own container.
            <div
              className={
                !children && active === "screener"
                  ? "flex-1 min-h-0 flex flex-col overflow-hidden"
                  : "hidden"
              }
            >
              <ScreenerPage />
            </div>
          )}
          {visitedTabs.has("portfolio") && (
            // Portfolio takes the full pane width; sections scroll inside.
            <div
              className={
                !children && active === "portfolio"
                  ? "flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 pt-4 pb-6 lg:px-8 lg:pt-6 lg:pb-8"
                  : "hidden"
              }
            >
              <PortfolioTab />
            </div>
          )}
          {visitedTabs.has("agents") && (
            <div
              className={
                !children && active === "agents"
                  ? "flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 pt-4 pb-6 lg:px-8 lg:pt-6 lg:pb-8"
                  : "hidden"
              }
            >
              <AgentsTab
                onOpenWorkflow={openWorkflow}
                onEditWithChat={editWorkflowWithChat}
                surfaceRequest={agentsSurfaceReq}
                onSendPrompt={sendChatPrompt}
                onEditBasketWithChat={editBasketWithChat}
              />
            </div>
          )}
        </main>

        </div>
      </div>

      </div>

      {(Boolean(children) || active !== "chat") && (
        <QuickAsk
          placeholder={quickAskPlaceholder}
          contextLabel={quickAskContextLabel}
          onSubmit={askFromQuickComposer}
          visible={active === "chart" ? !chartChatOpen : !copilotPanelOpen}
        />
      )}

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

      {/* Global order-ticket host — the Kite-style buy/sell bottom sheet;
          opens when anything dispatches `pivot:open-order-ticket`. */}
      <OrderTicketHost />

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

      {/* First-run guided tour (spotlight coach marks). Auto-starts once per
          browser on the home shell; replayable from Help → "Replay the tour".
          Disabled on sub-routes (children) where its targets don't exist. */}
      <ProductTour activeTab={active} onTabChange={goTab} enabled={!children} />

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
  variant = "default",
  theme,
  onChooseTheme,
  tradingMode,
  onChooseTradingMode,
  metrics,
  accountInitial,
  accountName,
  accountEmail,
  onOpenBroker,
  onOpenSettings,
  onOpenMobileNav,
  onBrandClick,
  onOpenChart,
  onLogout,
  onOpenShortcuts,
  onReportBug,
}: {
  variant?: "default" | "chart";
  theme: Theme;
  onChooseTheme: (t: Theme) => void;
  tradingMode: TradingMode;
  onChooseTradingMode: (m: TradingMode) => void;
  metrics: MetricState;
  accountInitial: string;
  accountName: string;
  accountEmail: string;
  onOpenBroker: () => void;
  onOpenSettings: () => void;
  onOpenMobileNav: () => void;
  onBrandClick: () => void;
  onOpenChart: (symbol: string) => void;
  onLogout: () => void;
  onOpenShortcuts: () => void;
  onReportBug: () => void;
}): React.ReactElement {
  const router = useRouter();
  return (
    <header
      className={`top-header relative flex shrink-0 items-center gap-6 px-3${variant === "chart" ? " top-header--chart" : ""}`}
      style={{
        height: "var(--header-h, 56px)",
        background: "var(--bg-base)",
        borderBottom: "2px solid var(--shell-seam)",
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

      {/* Brand — PivotLogo lockup (mark + heavy sans wordmark, ElevenLabs
          treatment). Mobile-only: at lg+ the sidebar owns the brand. Acts
          as a navigation link back to the chat tab. */}
      <button
        type="button"
        onClick={onBrandClick}
        aria-label="Go to Pivot home"
        data-testid="brand-home-link"
        className="brand-slot flex shrink-0 items-center pl-0"
        style={{
          color: "var(--text-primary)",
          background: "transparent",
          border: "none",
          padding: 0,
          cursor: "pointer",
        }}
      >
        <PivotWordmark className="top-header-wordmark" fontSize={22} />
      </button>

      {/* Search — Quartr pill, sized + bordered, no Tailwind background.
          Hidden below lg; mobile users get the CommandPalette via the
          account menu / keyboard shortcut. */}
      {variant === "default" && <div
        className="hidden flex-1 items-center gap-2 lg:flex"
        data-tour="search"
        style={{
          maxWidth: 300,
          height: 30,
          padding: "0 12px",
          background: "var(--bg-primary)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-pill)",
          transition: "border-color 0.2s var(--ease-quartr)",
          position: "relative",
        }}
      >
        <Search
          className="shrink-0"
          size={13}
          strokeWidth={2}
          style={{ color: "var(--text-tertiary)" }}
          aria-hidden={true}
        />
        <CompanyAutosuggest
          placeholder="Search about stocks"
          onSelect={(symbol) => router.push(`/stock/${symbol}`)}
          inputDataTestId="global-search"
          enableVoice
          onOpenChart={onOpenChart}
        />
      </div>}

      {/* Right cluster — metric stack + account menu */}
      <div className="ml-auto flex shrink-0 items-center gap-6">
        {variant === "default" && <MetricStrip metrics={metrics} />}
        <AccountMenu
          theme={theme}
          onChooseTheme={onChooseTheme}
          tradingMode={tradingMode}
          onChooseTradingMode={onChooseTradingMode}
          initial={accountInitial}
          accountName={accountName}
          accountEmail={accountEmail}
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
  accountName,
  accountEmail,
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
  accountName: string;
  accountEmail: string;
  onOpenBroker: () => void;
  onOpenSettings: () => void;
  onLogout: () => void;
  onOpenShortcuts: () => void;
  onReportBug: () => void;
}): React.ReactElement {
  const router = useRouter();
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
          width: 30,
          height: 30,
          borderRadius: "var(--radius-pill)",
          background: "#089981",
          border: "none",
          color: "#ffffff",
          fontFamily: "var(--font-ui)",
          fontSize: 13,
          fontWeight: 500,
          cursor: "pointer",
          transition:
            "color 0.25s var(--ease-quartr), border-color 0.25s var(--ease-quartr), background-color 0.25s var(--ease-quartr)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.boxShadow = "0 0 0 2px var(--bg-base), 0 0 0 4px var(--bg-elevated)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.boxShadow = "none";
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
            minWidth: 244,
            padding: 5,
            background: "color-mix(in srgb, var(--bg-card) 78%, transparent)",
            border: "none",
            borderRadius: 8,
            boxShadow:
              "inset 0 1px 0 rgba(255,255,255,0.34), inset 0 -1px 0 rgba(255,255,255,0.12), 0 14px 36px rgba(0,0,0,0.24)",
            backdropFilter: "blur(22px) saturate(145%)",
            WebkitBackdropFilter: "blur(22px) saturate(145%)",
            zIndex: 50,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "-5px -5px 0", padding: "12px 14px 11px", borderBottom: "1px solid var(--glass-border)" }}>
            <span style={{ width: 34, height: 34, flex: "none", borderRadius: "50%", display: "inline-flex", alignItems: "center", justifyContent: "center", background: "#089981", color: "#ffffff", fontSize: 14, fontWeight: 600 }}>
              {initial}
            </span>
            <span style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
              <span style={{ color: "var(--text-primary)", fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{accountName}</span>
              {accountEmail && accountEmail !== accountName ? (
                <span style={{ color: "var(--text-secondary)", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{accountEmail}</span>
              ) : null}
            </span>
          </div>
          <div style={{ margin: "0 -5px 5px", padding: "12px 14px", color: "var(--text-secondary)", fontSize: 12, lineHeight: 1.35, borderBottom: "1px solid var(--glass-border)" }}>
            Layouts, drawings and conversations are saved to this account.
          </div>
          <MenuItem icon={BookOpen} label="Paper book" onClick={() => { setOpen(false); router.push("/paper"); }} />
          <MenuItem icon={Settings} label="Settings" testId="menu-settings-chart-style" onClick={() => { setOpen(false); onOpenSettings(); }} />
          <MenuItem icon={HelpCircle} label="Help" onClick={() => { setOpen(false); onReportBug(); }} />
          <div aria-hidden={true} style={{ height: 1, background: "var(--glass-border)", margin: "5px -5px" }} />
          <div style={{ display: "flex", alignItems: "center", minHeight: 34, padding: "0 10px", gap: 10 }}>
            <Sun size={14} strokeWidth={2} aria-hidden={true} />
            <span style={{ flex: 1, color: "var(--text-secondary)", fontSize: 13, fontWeight: 500 }}>Dark mode</span>
            <Switch
              checked={theme === "dark"}
              onCheckedChange={(checked) => onChooseTheme(checked ? "dark" : "light")}
              aria-label="Toggle dark mode"
              data-testid="theme-dark-toggle"
              className="data-[state=checked]:bg-[#0d0d0e] dark:data-[state=checked]:bg-[#fbfcfc]"
            />
          </div>
          {!hideShortcuts && (
            <MenuItem icon={Keyboard} label="Keyboard shortcuts" trailing="Ctrl + /" onClick={() => { setOpen(false); onOpenShortcuts(); }} />
          )}
          <div aria-hidden={true} style={{ height: 1, background: "var(--glass-border)", margin: "5px -5px" }} />
          <MenuItem icon={LogOut} label="Sign out" testId="menu-logout-chart-style" onClick={() => { setOpen(false); onLogout(); }} />

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
  trailing: trailingContent,
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
  trailing?: React.ReactNode;
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
      {trailingContent ? <span style={{ color: "var(--text-tertiary)", fontSize: 11.5, fontWeight: 400 }}>{trailingContent}</span> : null}
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
    <div className="flex flex-col" style={{ gap: 1, lineHeight: 1.05 }}>
      <span
        style={{
          fontSize: 9.5,
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
            fontSize: emphasis ? 13.5 : 12.5,
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
                fontSize: 10,
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
            fontSize: emphasis ? 13.5 : 12.5,
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
        style={{ gap: 24 }}
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
      style={{ gap: 24 }}
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
  mobileOpen,
  onMobileClose,
  onBrandClick,
}: {
  active: TabKey;
  onTabChange: (key: TabKey) => void;
  mobileOpen: boolean;
  onMobileClose: () => void;
  /** The full-height sidebar owns the brand (ElevenLabs layout) → back to chat. */
  onBrandClick?: () => void;
}): React.ReactElement {
  const pinnedConvs: ConvEntry[] = [];
  const recentConvs: ConvEntry[] = [];
  const togglePin = (_id: string): void => undefined;
  const handleDelete = (_id: string): void => undefined;
  const activeConversationId: string | undefined = undefined;
  const onSelectConversation = (_id: string): void => undefined;
  // Pinned conversations — a per-device preference kept in localStorage.
  // Pinned entries float in their own section above Recent; unpinning
  // returns them to the recency-ordered list.
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
        background: "var(--bg-base)",
        borderRight: "2px solid var(--shell-seam)",
      }}
    >
      {/* Brand row — the full-height sidebar owns the logo (ElevenLabs-style),
          so the top header's brand is hidden at lg+. The mobile-only close
          button sits on the same row (hidden on lg+ via .sidebar-close-mobile). */}
      <div
        className="flex shrink-0 items-center justify-between lg:hidden"
        style={{ margin: "-2px 0 14px", height: 32 }}
      >
        <button
          type="button"
          onClick={onBrandClick}
          aria-label="Go to Pivot home"
          data-testid="sidebar-brand-home-link"
          className="inline-flex shrink-0 items-center"
          style={{
            color: "var(--text-primary)",
            background: "transparent",
            border: "none",
            /* Left-align the mark with the nav labels (rows use 16px
               horizontal padding inside the same container). */
            padding: "0 0 0 16px",
            cursor: "pointer",
          }}
        >
          <PivotWordmark fontSize={23} />
        </button>
        {/* Mobile-only close button — keeps the drawer escapable for
            screen-reader / keyboard users. Hidden on lg+ via
            .sidebar-close-mobile (globals.css). */}
        <button
          type="button"
          onClick={onMobileClose}
          aria-label="Close navigation"
          className="sidebar-close-mobile"
          style={{
            width: 36,
            height: 36,
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
      </div>
      {/* Nav — text-only, with a 4×4 dot indicator on the active row.
          Mirrors frontend-quartr/.../Sidebar.jsx exactly. */}
      <nav
        className="sidebar-nav-list flex flex-col"
        aria-label="Primary navigation list"
        data-tour="nav"
      >
        {NAV_ITEMS.map(({ key, label, Icon }) => {
          const isActive = active === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onTabChange(key)}
              aria-current={isActive ? "page" : undefined}
              aria-label={label}
              title={label}
              data-testid={`nav-${key}`}
              className="sidebar-nav-item inline-flex items-center justify-center"
              style={{
                position: "relative",
                background: isActive ? "var(--surface-active)" : "transparent",
                color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                border: "none",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                fontFamily: "var(--font-ui)",
                transition:
                  "color 0.35s var(--ease-quartr), background-color 0.35s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.color = "var(--text-primary)";
                  e.currentTarget.style.background = "var(--surface-active)";
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  e.currentTarget.style.color = "var(--text-secondary)";
                  e.currentTarget.style.background = "transparent";
                }
              }}
            >
              <Icon className="sidebar-nav-icon" aria-hidden={true} />
              <span className="sidebar-nav-label">{label}</span>
            </button>
          );
        })}
      </nav>

      {/* Divider */}
      <div
        className="sidebar-global-history"
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
          pinned to the chat surface's top-right).
          quartr-no-scrollbar: scroll still works, but the scrollbar track is
          hidden so the list reads as a clean column. */}
      <div
        className="sidebar-global-history quartr-no-scrollbar flex-1 overflow-y-auto flex flex-col"
        style={{ gap: 14, padding: "0 4px" }}
      >
        <button
          type="button"
          onClick={() => undefined}
          aria-label="Start new chat"
          data-testid="sidebar-new-chat-btn"
          className="inline-flex items-center"
          style={{
            gap: 10,
            padding: "9px 16px",
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

function ChatHistoryPane({
  activeConversationId,
  conversations,
  onNewChat,
  onSelectConversation,
  onDeleteConversation,
}: {
  activeConversationId?: string;
  conversations: ConvEntry[];
  onNewChat: () => void;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
}): React.ReactElement {
  const [pinnedIds, setPinnedIds] = useState<string[]>(() => readPinnedIds());
  const [historyOpen, setHistoryOpen] = useState(false);
  const togglePin = (id: string): void => {
    setPinnedIds((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      writePinnedIds(next);
      return next;
    });
  };
  const pinnedConvs = conversations.filter((c) => pinnedIds.includes(c.id));
  const recentConvs = conversations.filter((c) => !pinnedIds.includes(c.id));
  const handleDelete = (id: string): void => {
    setPinnedIds((prev) => {
      const next = prev.filter((x) => x !== id);
      writePinnedIds(next);
      return next;
    });
    onDeleteConversation(id);
  };

  return (
    <aside className="chat-history-pane flex h-full shrink-0 flex-col"
      data-testid="chat-history-pane" data-open={historyOpen ? "true" : "false"} aria-label="Chat history">
      <div className="chat-history-toolbar flex shrink-0 items-center">
        <button type="button" onClick={onNewChat} aria-label="Start new chat" title="New chat"
          data-testid="new-chat-btn" className="chat-history-action inline-flex items-center justify-center">
          <Plus size={17} strokeWidth={2} aria-hidden="true" />
        </button>
        <button type="button" onClick={() => setHistoryOpen((open) => !open)}
          aria-label={historyOpen ? "Hide chat history" : "Show chat history"}
          title={historyOpen ? "Hide history" : "Show history"} aria-expanded={historyOpen}
          className="chat-history-action inline-flex items-center justify-center">
          <History size={17} strokeWidth={2} aria-hidden="true" />
        </button>
      </div>
      {historyOpen && (
        <div className="quartr-no-scrollbar flex flex-1 flex-col overflow-y-auto" style={{ gap: 12, padding: "12px 8px" }}>
          {pinnedConvs.length > 0 && (
            <>
              <div style={convHeaderStyle}>Pinned</div>
              <div className="flex flex-col" style={{ gap: 2 }}>
                {pinnedConvs.map((conv) => (
                  <ConversationRow key={conv.id} conv={conv} pinned active={conv.id === activeConversationId}
                    onOpen={() => onSelectConversation(conv.id)} onTogglePin={() => togglePin(conv.id)}
                    onDelete={() => handleDelete(conv.id)} />
                ))}
              </div>
            </>
          )}
          <div style={convHeaderStyle}>Recent</div>
          {recentConvs.length === 0 && pinnedConvs.length === 0 ? (
            <div style={{ padding: "0 10px", fontSize: 12, color: "var(--text-tertiary)" }}>Start a chat to see history.</div>
          ) : (
            <div className="flex flex-col" style={{ gap: 2 }}>
              {recentConvs.map((conv) => (
                <ConversationRow key={conv.id} conv={conv} pinned={false} active={conv.id === activeConversationId}
                  onOpen={() => onSelectConversation(conv.id)} onTogglePin={() => togglePin(conv.id)}
                  onDelete={() => handleDelete(conv.id)} />
              ))}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

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
          fontSize: 13.5,
          // Constant weight — the active/hover state reads via background +
          // text color, never a weight change (no bold on select or hover).
          fontWeight: 500,
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
