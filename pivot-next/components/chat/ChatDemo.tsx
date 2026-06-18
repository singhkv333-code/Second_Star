"use client";

/**
 * ChatDemo — real chat surface wired to POST /chat/stream (SSE).
 *
 * Messages → POST /chat/stream (legacy router, no /api prefix).
 * SSE events: start | tool_start | tool_done | delta | replace | error | done.
 * When `done` arrives its raw_data/_render_hint drives the final card kind,
 * identical to the former non-streaming POST /chat dispatch.
 *
 * Conversation ID is derived per-user from the backend (u{user_id} format).
 * Client carries rolling history so backend has context.
 * Conversations sidebar is wired in AppShell via GET /api/conversations.
 */

import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Bot,
  Check,
  Copy,
  CornerUpLeft,
  RotateCw,
  Square,
  Workflow as WorkflowIcon,
  LineChart,
  X,
  Zap,
} from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  WorkflowDraftCard,
  draftToWorkflow,
  type WorkflowDraft,
} from "@/components/chat/WorkflowDraftCard";
import {
  IndicatorBacktestCard,
  type IndicatorBacktestPayload,
} from "@/components/chat/IndicatorBacktestCard";
import {
  FinancialBacktestCard,
  type FinancialBacktestPayload,
} from "@/components/chat/FinancialBacktestCard";
import {
  LogicCardChip,
  type LogicCard,
} from "@/components/chat/LogicCardChip";
import {
  SyntheticSecurityCard,
  type SyntheticSecurityPayload,
} from "@/components/chat/SyntheticSecurityCard";
import { InlineRunCard } from "@/components/chat/InlineRunCard";
import AssistantMessage from "@/components/chat/AssistantMessage";
import { IpoApplicationCard } from "@/components/chat/IpoApplicationCard";
import { IpoListCard } from "@/components/chat/IpoListCard";
import { IpoListedCard } from "@/components/chat/IpoListedCard";
import { OptionChainCard } from "@/components/chat/OptionChainCard";
import { OptionStrategyCard } from "@/components/chat/OptionStrategyCard";
import { PortfolioGreeksCard } from "@/components/chat/PortfolioGreeksCard";
import { ClarifyCard } from "@/components/chat/ClarifyCard";
import { StrategyBuilderCard } from "@/components/chat/StrategyBuilderCard";
import type { Workflow, IpoApplicationPayload, IpoListPayload, IpoListedPayload, OptionChainPayload, OptionStrategyPayload, PortfolioGreeksPayload, ClarifyCard as ClarifyCardData, StrategyBuilderCard as StrategyBuilderCardData } from "@/lib/types";

// ---------------------------------------------------------------------------
// Backend chat types
// ---------------------------------------------------------------------------

type ChatHistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

/** Shape of the `done` event payload — identical to POST /chat response. */
type ChatDonePayload = {
  response: string;
  tools_called?: string[];
  logiccard?: LogicCard | null;
  raw_data?:
    | (Record<string, unknown> & { _render_hint?: string })
    | null;
  latency_ms?: number;
  latency_breakdown?: Record<string, unknown>;
};

// SSE event discriminated union -----------------------------------------------

type SseStart = { type: "start" };
type SseToolStart = { type: "tool_start"; name: string };
type SseToolDone = { type: "tool_done"; name: string; ok: boolean; error: string | null };
type SseDelta = { type: "delta"; text: string };
type SseReplace = { type: "replace"; text: string };
type SseError = { type: "error"; message: string };
type SseDone = { type: "done" } & ChatDonePayload;

type SseEvent =
  | SseStart
  | SseToolStart
  | SseToolDone
  | SseDelta
  | SseReplace
  | SseError
  | SseDone;

// ---------------------------------------------------------------------------
// Streaming chat via POST /chat/stream (SSE)
// ---------------------------------------------------------------------------

/**
 * Connects to POST /chat/stream and yields parsed SseEvent objects.
 * The caller owns the AbortController so it can cancel mid-stream.
 * On 401 this function wipes the JWT and reloads (same guard as callChat).
 */
/** Optional mode hint the FE attaches to a chat request. The backend
 * uses this to deterministically route tool selection — picking
 * Automation forces the immediate-order family, Agent forces
 * propose_workflow, Backtest forces the backtester paths.  When
 * `null` the backend falls back to its inferred classifier. */
export type ChatMode = "automation" | "agent" | "backtest" | null;

async function* streamChat(
  userMessage: string,
  history: ChatHistoryMessage[],
  token: string | null,
  signal: AbortSignal,
  conversationId: string,
  mode: ChatMode,
  /** When the user replied-by-selecting a snippet of a prior assistant
   * answer, the highlighted excerpt is sent here so the backend can
   * thread it into the prompt as the thing being replied to. */
  quotedText: string | null,
): AsyncGenerator<SseEvent> {
  const base =
    (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
    "/api";
  const legacyBase = base.replace(/\/api\/?$/, "");
  const url = `${legacyBase}/chat/stream`;

  const messages: ChatHistoryMessage[] = [
    ...history,
    { role: "user", content: userMessage },
  ];

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify({
      messages,
      include_portfolio_context: true,
      // Per-session conversation_id — generated once per ChatDemo
      // mount in the React tree. The backend keys its Redis-stored
      // active draft / pending clarification under this id, so a
      // fresh session id ensures we never inherit yesterday's draft.
      conversation_id: conversationId,
      // Optional mode hint. When the user clicks Automation / Agent /
      // Backtest below the composer, we pass that intent to the
      // backend deterministically. Null = classifier decides.
      mode,
      // Reply-by-selecting: the highlighted excerpt the user is
      // replying to. Omitted entirely when there's no active quote.
      ...(quotedText ? { quoted_text: quotedText } : {}),
    }),
    cache: "no-store",
    signal,
  });

  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined") {
      try { window.localStorage.removeItem("pivot_jwt"); } catch { /* ignore */ }
      window.location.reload();
    }
    const text = await res.text();
    throw new Error(`Stream error ${res.status}: ${text.slice(0, 200)}`);
  }

  if (!res.body) {
    throw new Error("No response body — SSE stream unavailable");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: false });
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by double-newline.
      const parts = buffer.split("\n\n");
      // Last element may be a partial event — keep it in the buffer.
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        // Each part may contain multiple "data:" lines — our backend
        // always emits single-line JSON so we just find the data line.
        const dataLine = part
          .split("\n")
          .find((l) => l.startsWith("data:"));
        if (!dataLine) continue;

        const jsonStr = dataLine.slice("data:".length).trim();
        if (!jsonStr) continue;

        let parsed: unknown;
        try {
          parsed = JSON.parse(jsonStr);
        } catch {
          // Malformed chunk — skip silently.
          continue;
        }

        yield parsed as SseEvent;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem("pivot_jwt");
  } catch {
    return null;
  }
}

const PLACEHOLDER_TEXT =
  "Ask Pivot anything about your portfolio, markets, or strategies…";

// Phone-width fallback. The full placeholder gets truncated mid-word on
// narrow screens, so swap to a single short clause that fits one line.
const PLACEHOLDER_TEXT_MOBILE = "Ask Pivot anything…";

/** Maximum visual height of the chat textarea in pixels. Past this it
 * gains a vertical scrollbar; under it the textarea autosizes silently
 * (no scrollbar) — ChatGPT's pattern. */
const MAX_TEXTAREA_PX = 200;

const EXAMPLE_PROMPT =
  "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email.";

// ---------------------------------------------------------------------------
// Message types
// ---------------------------------------------------------------------------

/** A tool pill tracked inside the streaming bubble. Drives the
 * single-sentence status line so the user sees what's happening
 * (e.g. "Drafting workflow…") instead of just "Loading…". */
type ToolPill = {
  name: string;
  /** undefined = still running, true = success, false = error */
  ok: boolean | undefined;
};


// Friendly user-facing labels for the status sentence. Anything not
// listed falls back to "Running <tool_name>…".
const TOOL_STATUS: Record<string, string> = {
  propose_workflow: "Drafting workflow",
  get_live_price: "Fetching live price",
  get_index_level: "Reading index level",
  get_ohlc: "Pulling OHLC",
  get_52wk_range: "Looking up 52-week range",
  get_price_history: "Loading chart history",
  get_market_status: "Checking market status",
  get_portfolio_summary: "Loading portfolio",
  get_holdings: "Loading holdings",
  get_holding_detail: "Looking up that holding",
  get_sector_breakdown: "Computing sector breakdown",
  get_tax_summary: "Computing tax summary",
  get_active_products: "Loading active products",
  get_product_spec: "Reading product spec",
  place_market_order: "Preparing market order",
  place_limit_order: "Preparing limit order",
  create_gtt_order: "Setting up GTT order",
  create_sl_order: "Setting up stop-loss",
  create_oco_order: "Setting up OCO order",
  create_dip_buy: "Setting up dip buy",
  place_basket_order: "Preparing basket",
  cancel_order: "Cancelling order",
  cancel_gtt: "Cancelling GTT",
  list_pending_orders: "Listing pending orders",
  list_gtt_orders: "Listing GTTs",
  squareoff_all_intraday: "Squaring off intraday",
  squareoff_symbol: "Squaring off positions",
  create_sip: "Setting up SIP",
  list_sips: "Listing SIPs",
  pause_sip: "Pausing SIP",
  resume_sip: "Resuming SIP",
  delete_sip: "Removing SIP",
  pause_all_sips: "Pausing all SIPs",
  create_strategy: "Setting up strategy",
  list_strategies: "Listing strategies",
  pause_strategy: "Pausing strategy",
  resume_strategy: "Resuming strategy",
  delete_strategy: "Removing strategy",
  run_backtest: "Running backtest",
  compare_yields: "Comparing yields",
  get_yield_recommendation: "Finding best yield",
  calculate_order_qty: "Calculating order size",
  calculate_tax_impact: "Calculating tax impact",
  calculate_sl_price: "Calculating stop-loss price",
  calculate_dip_price: "Calculating dip price",
  calculate_margin: "Calculating margin",
  get_scheduler_status: "Reading scheduler",
  list_upcoming_jobs: "Listing upcoming jobs",
  ASK_USER: "Asking you for one detail",
};


/** Self-contained status row for the streaming bubble.
 *
 * Shows in one line:
 *   <spinner>  <one-sentence description of what the model is doing>  ·  <elapsed>s
 *
 * Updates every 250ms so the elapsed counter ticks; the sentence
 * derives from the most recent `tools` and `hasText` props. The user
 * asked for a clean way to track time themselves and see what stage
 * the LLM is at — this is that widget. */
/**
 * Witty finance-themed loading verbs. One word each — single-token
 * present participles ("-ing" verbs) that read like an analyst
 * working through a step. Cycles in randomized order so each
 * session feels different. Add liberally.
 */
const WITTY_PHRASES: readonly string[] = [
  "Triangulating",
  "Stress-testing",
  "Sniffing",
  "Discounting",
  "Rebalancing",
  "Backsolving",
  "Interrogating",
  "Tagging",
  "Hedging",
  "Compounding",
  "Sweeping",
  "Repricing",
  "Unwinding",
  "Calibrating",
  "Auditing",
  "Debriefing",
  "Scoring",
  "Pricing",
  "Cross-checking",
  "Arbitraging",
  "Anchoring",
  "Smoothing",
  "Profiling",
  "Tracing",
  "Spreading",
  "Scaling",
  "De-risking",
  "Backtesting",
  "Pulling",
  "Skewing",
  "Crunching",
  "Reconciling",
  "Front-running",
  "Trimming",
  "Forecasting",
  "Indexing",
  "Stitching",
  "Quantifying",
  "Diversifying",
  "Marking",
  "Modelling",
  "Benchmarking",
  "Annotating",
  "Synthesising",
  "Filtering",
];

/** Fisher-Yates shuffle. Used once per loader mount so two adjacent
 *  cycles don't repeat in the same order. */
function shufflePhrases(src: readonly string[]): string[] {
  const a = src.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j]!, a[i]!];
  }
  return a;
}

/** Mini price-chart ticker — three bars rising/falling on
 *  independent periods so the trio reads as a live equalizer
 *  / volume strip rather than a wheel. Replaces the spinner. */
function WittyTicker(): React.ReactElement {
  return (
    <span
      className="inline-flex items-end"
      style={{ gap: 2, height: 14 }}
      aria-hidden={true}
    >
      <span className="witty-bar" />
      <span className="witty-bar" />
      <span className="witty-bar" />
    </span>
  );
}

/** Cycling phrase. Re-keys on every change so the CSS animation
 *  re-plays for the fade+slide-in. Pause behavior: when `paused` is
 *  true (e.g. text is now streaming), we freeze on the current phrase
 *  and let the parent take over. Interval jitters between 2.0s and
 *  3.0s so the cadence doesn't feel mechanical. */
function WittyPhrase({ paused = false }: { paused?: boolean }): React.ReactElement {
  const queue = useRef<string[]>(shufflePhrases(WITTY_PHRASES));
  const [phrase, setPhrase] = useState<string>(() => queue.current[0] ?? "Thinking");

  useEffect(() => {
    if (paused) return;
    let cursor = 1;
    const tick = (): void => {
      if (cursor >= queue.current.length) {
        queue.current = shufflePhrases(WITTY_PHRASES);
        cursor = 0;
      }
      setPhrase(queue.current[cursor]!);
      cursor += 1;
    };
    // Random first-phrase dwell time so two parallel mounts don't
    // breathe in lockstep. Subsequent dwells re-roll inside the
    // setTimeout chain.
    let timer: number = window.setTimeout(function loop() {
      tick();
      timer = window.setTimeout(loop, 2000 + Math.random() * 1000);
    }, 2000 + Math.random() * 1000);
    return () => window.clearTimeout(timer);
  }, [paused]);

  return (
    <span
      key={phrase}
      className="witty-phrase"
      style={{ display: "inline-block" }}
    >
      {phrase}…
    </span>
  );
}

function StreamingStatusBar({
  startedAt,
  tools,
  hasText,
}: {
  startedAt: number;
  tools: ToolPill[];
  hasText: boolean;
}): React.ReactElement {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, []);
  const elapsedSec = Math.max(0, Math.round((now - startedAt) / 1000));

  // Decide what to show:
  //   • Tool actively running          → use the tool's literal label
  //     so the user knows what's running (not a witty phrase).
  //   • Text is streaming              → "Writing reply"; phrases stop.
  //   • Otherwise (thinking/wrap-up)   → cycle witty phrases.
  const pending = tools.find((t) => t.ok === undefined);
  let mode: "phrases" | "literal" = "phrases";
  let literal = "";
  if (hasText) {
    mode = "literal";
    literal = "Writing reply";
  } else if (pending) {
    mode = "literal";
    literal = TOOL_STATUS[pending.name] ?? `Running ${pending.name}`;
  }

  return (
    <div
      className="flex items-center gap-3 text-xs text-muted-foreground"
      data-testid="streaming-status"
    >
      <WittyTicker />
      {mode === "literal" ? (
        <span key={literal} className="witty-phrase">
          {literal}…
        </span>
      ) : (
        <WittyPhrase />
      )}
      <span aria-hidden={true} className="text-muted-foreground/60">·</span>
      <span className="tabular-nums" aria-label={`${elapsedSec} seconds elapsed`}>
        {elapsedSec}s
      </span>
    </div>
  );
}


type Message =
  | { kind: "user"; text: string; timestamp?: string; quote?: string }
  | { kind: "assistant"; text: string }
  /** Transient streaming bubble — replaced by a final kind on `done`.
   * `startedAt` is the unix-ms timestamp when the bubble was created;
   * the `StreamingStatusBar` reads it to render an elapsed counter. */
  | { kind: "streaming"; text: string; tools: ToolPill[]; startedAt: number }
  | { kind: "draft"; draft: WorkflowDraft; intro: string }
  | { kind: "indicator_backtest"; payload: IndicatorBacktestPayload; intro: string }
  | { kind: "financial_backtest"; payload: FinancialBacktestPayload; intro: string }
  | { kind: "logic_card"; card: LogicCard; intro: string }
  | { kind: "synthetic_security"; payload: SyntheticSecurityPayload; intro: string }
  | { kind: "ipo_application"; payload: IpoApplicationPayload; intro: string }
  | { kind: "ipo_list"; payload: IpoListPayload; intro: string }
  | { kind: "ipo_listed"; payload: IpoListedPayload; intro: string }
  | { kind: "option_chain"; payload: OptionChainPayload; intro: string }
  | { kind: "option_strategy"; payload: OptionStrategyPayload; intro: string }
  | { kind: "portfolio_greeks"; payload: PortfolioGreeksPayload; intro: string }
  | { kind: "clarify"; card: ClarifyCardData; intro: string }
  | { kind: "strategy_builder"; card: StrategyBuilderCardData; intro: string }
  | { kind: "live_run"; runId: string; workflowName: string; workflowId: string }
  | { kind: "error"; message: string };

type ChatDemoProps = {
  /** Called when user clicks "Open in editor →" on a draft card. */
  onOpenEditor: (workflow: Workflow) => void;
  /** Optional prompt prefilled into the textarea. */
  prefill?: string;
  /** When true, a non-empty `prefill` is sent through the chat
   * pipeline immediately on arrival (used by dashboard quick-action
   * chips). When false (the default), the prefill only populates the
   * textarea so the user can edit before pressing Send. */
  prefillAutoSubmit?: boolean;
  /** Optional mode to force when consuming a prefill — overrides the
   * current pill state for that one submission. Used by news-gated
   * demo chips to guarantee `propose_workflow` is in scope. */
  prefillMode?: ChatMode;
  /** Called after the prefill has been consumed so parent can clear it. */
  onPrefillConsumed?: () => void;
  /** Custom intro shown above the composer when no messages have been
   * sent yet. Replaces the default "Describe your strategy" card. The
   * dashboard passes its greeting + index strip + action chips here. */
  intro?: React.ReactNode;
  /** Notifies the parent whenever the conversation transitions between
   * "empty" and "active" (≥1 message). The dashboard uses this signal
   * to hide ancillary rails (e.g. Active Agents) once a chat has
   * started so the chat column can fill the freed width. */
  onActiveChange?: (active: boolean) => void;
  /** Offline demo seed — when set, ChatDemo bypasses the LLM and
   * plays a hardcoded user → streaming → workflow-draft sequence,
   * then auto-opens the editor panel via `onOpenEditor`. Used by the
   * dashboard "Play demo" chip while the live LLM is disabled. */
  demoSeed?: ChatDemoSeed;
  /** Called after a `demoSeed` has been consumed so parent can clear it
   * (same pattern as `onPrefillConsumed`). */
  onDemoSeedConsumed?: () => void;
};

export type ChatDemoSeed = {
  /** Text shown in the user bubble at the top. */
  userText: string;
  /** Assistant intro line printed above the draft card. */
  intro: string;
  /** Hardcoded workflow draft surfaced as the assistant's response. */
  draft: WorkflowDraft;
};

/** Per-mount session id. Generated once on component mount; sent
 * with every chat request so the backend keys its Redis-stored
 * active draft + pending clarification + conversation history
 * under this id. A new ChatDemo mount = a fresh session = no
 * inherited state from prior sessions.
 *
 * Why per-mount and not per-user: the backend used to key under
 * `u{user_id}` which is stable for the user's lifetime, so opening
 * a new chat the next day still resurfaced the prior session's
 * workflow draft (PDF report). Per-mount fixes that root cause.
 */
function newSessionId(): string {
  // crypto.randomUUID is available in modern browsers; fall back
  // to a low-entropy random string for very old environments.
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `s_${crypto.randomUUID()}`;
  }
  return `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

export function ChatDemo({
  onOpenEditor,
  prefill,
  prefillAutoSubmit = false,
  prefillMode,
  onPrefillConsumed,
  intro,
  onActiveChange,
  demoSeed,
  onDemoSeedConsumed,
}: ChatDemoProps): React.ReactElement {
  const [intent, setIntent] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  // Reply-by-selecting state. `reply` is the snippet the user committed
  // to reply to (shown as a quote chip above the composer + sent with
  // the next message). `selectionReply` is the transient floating
  // "Reply" button that appears while text is highlighted inside an
  // assistant message — committing it sets `reply`.
  const [reply, setReply] = useState<string | null>(null);
  const [selectionReply, setSelectionReply] = useState<
    { text: string; left: number; top: number } | null
  >(null);
  // Mode pill state. null = auto (classifier decides). Toggled from
  // the pills below the composer. Persists across turns so the user
  // can stay "in agent mode" while iterating on a workflow.
  const [mode, setMode] = useState<ChatMode>(null);
  // Rolling history for the backend's conversation context
  const historyRef = useRef<ChatHistoryMessage[]>([]);
  // Stable per-session id. Generated lazily inside useRef so it survives
  // re-renders but is regenerated on a fresh mount.
  const sessionIdRef = useRef<string>("");
  if (!sessionIdRef.current) sessionIdRef.current = newSessionId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Scroll container — kept pinned to the bottom while messages stream
  // in, the same auto-follow behaviour ChatGPT/Claude use.
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  /** Holds the in-flight AbortController so the composer's Stop button
   *  can cancel the SSE stream mid-response. Cleared in submit()'s
   *  finally block. */
  const abortRef = useRef<AbortController | null>(null);
  // Ref to the floating "Reply" button so the document mousedown
  // dismiss-handler can tell a button click from a click elsewhere.
  const selBtnRef = useRef<HTMLButtonElement | null>(null);

  // Reply-by-selecting: surface a floating "Reply" button whenever the
  // user highlights text inside an assistant message (marked with
  // `data-reply-source`). Mirrors the Claude / ChatGPT gesture. The
  // button is positioned just above the selection's bounding box.
  useEffect(() => {
    // mouseup only ever SHOWS the button (when a fresh, valid in-source
    // selection exists). It never hides — hiding is owned by mousedown
    // below. This is deliberate: a parent re-render can collapse the
    // live selection a frame later, and we must NOT let that yank the
    // button away before the user can click it. The excerpt is captured
    // into state here, so the click no longer depends on the selection
    // still being alive.
    const computeFromSelection = (): void => {
      const sel = typeof window !== "undefined" ? window.getSelection() : null;
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
      const text = sel.toString().trim();
      if (!text) return;
      // Both ends of the selection must live inside an assistant message
      // body — never the composer, a user bubble, or a card.
      const inSource = (node: Node | null): boolean => {
        const el =
          node && node.nodeType === Node.ELEMENT_NODE
            ? (node as Element)
            : node?.parentElement ?? null;
        return !!el?.closest("[data-reply-source]");
      };
      if (!inSource(sel.anchorNode) || !inSource(sel.focusNode)) return;
      const rect = sel.getRangeAt(0).getBoundingClientRect();
      if (!rect || (rect.width === 0 && rect.height === 0)) return;
      setSelectionReply({
        text,
        left: rect.left + rect.width / 2,
        top: rect.top,
      });
    };

    // Any mousedown that isn't on the button dismisses it (starting a new
    // selection, clicking the composer, etc.). A mousedown ON the button
    // is its own commit path — leave it alone.
    const onDocMouseDown = (e: MouseEvent): void => {
      const t = e.target as Node | null;
      if (selBtnRef.current && t && selBtnRef.current.contains(t)) return;
      setSelectionReply(null);
    };

    // A scroll detaches the fixed-position button from the text it points
    // at — just dismiss it.
    const onScroll = (): void => setSelectionReply(null);
    const scrollEl = scrollRef.current;
    document.addEventListener("mouseup", computeFromSelection);
    document.addEventListener("mousedown", onDocMouseDown);
    scrollEl?.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      document.removeEventListener("mouseup", computeFromSelection);
      document.removeEventListener("mousedown", onDocMouseDown);
      scrollEl?.removeEventListener("scroll", onScroll);
    };
  }, []);

  /** Commit the highlighted excerpt into the composer's reply chip, then
   * dismiss the button and refocus the textarea. Reads the LIVE selection
   * first (still intact at mousedown time) and falls back to the text we
   * captured when the button appeared — so it never depends on the
   * selection surviving the click, which it doesn't in a webview. */
  const commitSelectionReply = (): void => {
    // Prefer the text captured when the button appeared — the live
    // selection may already have been collapsed by a re-render. Fall
    // back to the live selection only if we somehow have no stored text.
    const stored = selectionReply?.text || "";
    const live =
      (typeof window !== "undefined"
        ? window.getSelection()?.toString().trim()
        : "") || "";
    const text = stored || live;
    if (!text) return;
    setReply(text);
    setSelectionReply(null);
    try {
      window.getSelection()?.removeAllRanges();
    } catch {
      /* ignore */
    }
    // Focus after the current event settles so the webview doesn't
    // swallow it while the selection is being torn down.
    setTimeout(() => textareaRef.current?.focus(), 0);
  };

  // Consume prefill once when it arrives. With `prefillAutoSubmit`, the
  // prefill is sent through the chat pipeline immediately (dashboard
  // quick-action chips). Otherwise it just populates the textarea so
  // the user can edit before pressing Send.
  useEffect(() => {
    if (!prefill) return;
    if (prefillAutoSubmit) {
      onPrefillConsumed?.();
      // When the chip carries a forced mode (e.g. news-gated chips → "agent"),
      // pin the mode state BEFORE submitting so the classifier doesn't
      // mis-route. The mode persists for follow-up turns until the user
      // toggles it off, which matches the existing mode-pill behaviour.
      if (prefillMode) setMode(prefillMode);
      void submit(prefill, prefillMode);
    } else {
      setIntent(prefill);
      if (prefillMode) setMode(prefillMode);
      onPrefillConsumed?.();
      textareaRef.current?.focus();
    }

  }, [prefill, prefillAutoSubmit, prefillMode, onPrefillConsumed]);

  // Offline demo playback — when `demoSeed` is set, push a canned
  // user → streaming → draft sequence and auto-open the editor panel.
  // No backend call. Used while the live LLM is disabled.
  //
  // Callbacks are deref'd through refs so this effect keys ONLY on
  // `demoSeed`. Parents typically pass inline arrows for the callbacks,
  // so including them in the deps would tear down the playback timer
  // on every parent re-render and the streaming bubble would never
  // resolve. `playedSeedRef` guards against replaying when the parent
  // clears the seed and against StrictMode double-invocation.
  const onOpenEditorRef = useRef(onOpenEditor);
  const onDemoSeedConsumedRef = useRef(onDemoSeedConsumed);
  useEffect(() => {
    onOpenEditorRef.current = onOpenEditor;
  });
  useEffect(() => {
    onDemoSeedConsumedRef.current = onDemoSeedConsumed;
  });
  const playedSeedRef = useRef<ChatDemoSeed | null>(null);
  useEffect(() => {
    if (!demoSeed) return;
    if (playedSeedRef.current === demoSeed) return;
    playedSeedRef.current = demoSeed;

    const { userText, intro: demoIntro, draft } = demoSeed;
    const startedAt = Date.now();
    setMessages([
      { kind: "user", text: userText, timestamp: new Date().toISOString() },
      { kind: "streaming", text: "", tools: [], startedAt },
    ]);

    const t = window.setTimeout(() => {
      setMessages((prev) => {
        const next = [...prev];
        const streamingIdx = next.findIndex((m) => m.kind === "streaming");
        if (streamingIdx >= 0) {
          next[streamingIdx] = { kind: "draft", draft, intro: demoIntro };
        }
        return next;
      });
      onOpenEditorRef.current(draftToWorkflow(draft));
      onDemoSeedConsumedRef.current?.();
    }, 1400);

    return () => window.clearTimeout(t);
  }, [demoSeed]);

  // Track whether the user is at the bottom. If they scroll up to read
  // earlier output, we stop auto-scrolling so we don't yank them down.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = (): void => {
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      stickToBottomRef.current = distanceFromBottom < 80;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Tell the parent when the conversation flips between empty and active.
  useEffect(() => {
    onActiveChange?.(messages.length > 0);
  }, [messages.length, onActiveChange]);

  // ChatGPT-style autosize: every keystroke resets the textarea's
  // height to its scrollHeight so it grows with content and shrinks
  // back when content is deleted. Capped at MAX_TEXTAREA_PX; only at
  // the cap do we surface a scrollbar (until then `overflow-y: hidden`
  // hides any residual scroll affordance).
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, MAX_TEXTAREA_PX);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_PX ? "auto" : "hidden";
  }, [intent]);

  // Auto-scroll to the bottom whenever messages change (new message,
  // streaming delta) — but only if the user hasn't scrolled away.
  useEffect(() => {
    if (!stickToBottomRef.current) return;
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  /**
   * Dispatch the final `done` payload to the right Message kind and replace
   * the streaming bubble at `streamingIdx` in the message list.
   * Reuses the same render-hint switch as the former non-streaming path.
   */
  function resolveStreamingMessage(
    data: ChatDonePayload,
    streamingIdx: number,
  ): void {
    const rawData = data.raw_data;
    const hint = rawData?._render_hint;

    let finalMessage: Message;

    if (hint === "workflow_draft_card" && rawData) {
      const r = rawData as unknown as {
        name?: string;
        description?: string;
        steps?: Array<{ step_type: string; label: string | null; config: Record<string, unknown> }>;
        rationale?: string;
        warnings?: string[];
      };
      if (r.name && r.steps) {
        const draft: WorkflowDraft = {
          name: r.name,
          description: r.description ?? "",
          steps: r.steps.map((s) => ({
            step_type: s.step_type,
            label: s.label,
            config: s.config,
          })),
          rationale: r.rationale ?? "",
          warnings: r.warnings ?? [],
          _render_hint: "workflow_draft_card",
        };
        finalMessage = { kind: "draft", draft, intro: data.response ?? "" };
      } else {
        finalMessage = { kind: "assistant", text: data.response ?? "" };
      }
    } else if (hint === "logic_card" && data.logiccard) {
      finalMessage = {
        kind: "logic_card",
        card: data.logiccard,
        intro: data.response ?? "",
      };
    } else if (hint === "synthetic_security_card" && rawData) {
      finalMessage = {
        kind: "synthetic_security",
        payload: rawData as unknown as SyntheticSecurityPayload,
        intro: data.response ?? "",
      };
    } else if (hint === "indicator_backtest_chart" && rawData) {
      finalMessage = {
        kind: "indicator_backtest",
        payload: rawData as unknown as IndicatorBacktestPayload,
        intro: data.response ?? "",
      };
    } else if (hint === "financial_backtest_chart" && rawData) {
      finalMessage = {
        kind: "financial_backtest",
        payload: rawData as unknown as FinancialBacktestPayload,
        intro: data.response ?? "",
      };
    } else if (hint === "ipo_application_card" && rawData) {
      finalMessage = {
        kind: "ipo_application",
        payload: rawData as unknown as IpoApplicationPayload,
        intro: data.response ?? "",
      };
    } else if (hint === "ipo_list_card" && rawData) {
      finalMessage = {
        kind: "ipo_list",
        payload: rawData as unknown as IpoListPayload,
        intro: data.response ?? "",
      };
    } else if (hint === "ipo_listed_card" && rawData) {
      finalMessage = {
        kind: "ipo_listed",
        payload: rawData as unknown as IpoListedPayload,
        intro: data.response ?? "",
      };
    } else if (hint === "option_chain_card" && rawData) {
      finalMessage = {
        kind: "option_chain",
        payload: rawData as unknown as OptionChainPayload,
        intro: data.response ?? "",
      };
    } else if (hint === "option_strategy_card" && rawData) {
      finalMessage = {
        kind: "option_strategy",
        payload: rawData as unknown as OptionStrategyPayload,
        intro: data.response ?? "",
      };
    } else if (hint === "portfolio_greeks_card" && rawData) {
      finalMessage = {
        kind: "portfolio_greeks",
        payload: rawData as unknown as PortfolioGreeksPayload,
        intro: data.response ?? "",
      };
    } else if (hint === "clarify_card" && rawData) {
      // raw_data = { _render_hint: "clarify_card", clarify: <ClarifyCard> }.
      const clarify = (rawData as { clarify?: ClarifyCardData }).clarify;
      if (clarify && Array.isArray(clarify.questions)) {
        finalMessage = {
          kind: "clarify",
          card: clarify,
          intro: data.response ?? "",
        };
      } else {
        finalMessage = { kind: "assistant", text: data.response ?? "" };
      }
    } else if (hint === "strategy_builder_card" && rawData) {
      // The StrategyBuilderCard fields are spread at the top level of raw_data
      // alongside the render hint (mirrors the executor's
      // `{ "_render_hint": ..., **card.model_dump() }`).
      const card = rawData as unknown as StrategyBuilderCardData;
      if (Array.isArray(card.constituents)) {
        finalMessage = {
          kind: "strategy_builder",
          card,
          intro: data.response ?? "",
        };
      } else {
        finalMessage = { kind: "assistant", text: data.response ?? "" };
      }
    } else {
      finalMessage = { kind: "assistant", text: data.response ?? "" };
    }

    setMessages((prev) => {
      const next = [...prev];
      next[streamingIdx] = finalMessage;
      return next;
    });
  }

  const submit = async (
    text: string,
    modeOverride?: ChatMode,
    quotedText?: string | null,
  ): Promise<void> => {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    // Snapshot the quoted excerpt (if any) for this turn.
    const quote = (quotedText ?? "").trim() || null;

    setMessages((prev) => [
      ...prev,
      {
        kind: "user",
        text: trimmed,
        timestamp: new Date().toISOString(),
        ...(quote ? { quote } : {}),
      },
    ]);
    setIntent("");

    // NOTE: the old "bare ticker → local StockSnapshotCard (no API call)"
    // shortcut was removed. Every turn — including a lone ticker like
    // "TCS" — now goes through the LLM via /chat/stream, so routing,
    // grounding, and the answer are consistent and never hardwired
    // behind the model's back.
    setLoading(true);

    // Index of the streaming bubble we're about to append.
    // Use a ref-captured value; the state setter gives us prev.length.
    let streamingIdx = -1;

    const abortCtrl = new AbortController();
    abortRef.current = abortCtrl;

    try {
      const token = getToken();

      // Append the transient streaming bubble and capture its index.
      // `startedAt` drives the elapsed-time counter in the status bar.
      setMessages((prev) => {
        streamingIdx = prev.length;
        return [
          ...prev,
          { kind: "streaming", text: "", tools: [], startedAt: Date.now() },
        ];
      });

      // We need streamingIdx synchronously after the setState call.
      // React batches state but the closure captures the local variable
      // set inside the setter above — wait one tick for the flush.
      await Promise.resolve();

      const gen = streamChat(
        trimmed,
        historyRef.current,
        token,
        abortCtrl.signal,
        sessionIdRef.current,
        // A caller-supplied mode (e.g. news-gated chips forcing "agent")
        // wins over the mode state, which may not have flushed yet this
        // tick — the prefill path calls setMode(mode) AND passes it here.
        modeOverride ?? mode,
        quote,
      );

      for await (const event of gen) {
        switch (event.type) {
          case "start":
            // Bubble already appended; nothing extra to do.
            break;

          case "tool_start":
            setMessages((prev) => {
              const next = [...prev];
              const bubble = next[streamingIdx];
              if (bubble?.kind !== "streaming") return prev;
              next[streamingIdx] = {
                ...bubble,
                tools: [...bubble.tools, { name: event.name, ok: undefined }],
              };
              return next;
            });
            break;

          case "tool_done":
            setMessages((prev) => {
              const next = [...prev];
              const bubble = next[streamingIdx];
              if (bubble?.kind !== "streaming") return prev;
              // Update the last pill with this name that is still pending.
              const tools = bubble.tools.map((p) =>
                p.name === event.name && p.ok === undefined
                  ? { ...p, ok: event.ok }
                  : p,
              );
              next[streamingIdx] = { ...bubble, tools };
              return next;
            });
            break;

          case "delta":
            setMessages((prev) => {
              const next = [...prev];
              const bubble = next[streamingIdx];
              if (bubble?.kind !== "streaming") return prev;
              next[streamingIdx] = {
                ...bubble,
                text: bubble.text + event.text,
              };
              return next;
            });
            break;

          case "replace":
            setMessages((prev) => {
              const next = [...prev];
              const bubble = next[streamingIdx];
              if (bubble?.kind !== "streaming") return prev;
              next[streamingIdx] = { ...bubble, text: event.text };
              return next;
            });
            break;

          case "error":
            // Terminal error — backend will still send `done`.
            // Just surface it inside the streaming bubble so the user can see it.
            setMessages((prev) => {
              const next = [...prev];
              const bubble = next[streamingIdx];
              if (bubble?.kind !== "streaming") return prev;
              next[streamingIdx] = {
                ...bubble,
                text: bubble.text
                  ? `${bubble.text}\n\n(Error: ${event.message})`
                  : `Error: ${event.message}`,
              };
              return next;
            });
            break;

          case "done":
            // Update rolling history first.
            historyRef.current = [
              ...historyRef.current,
              { role: "user" as const, content: trimmed },
              { role: "assistant" as const, content: event.response ?? "" },
            ].slice(-20);

            resolveStreamingMessage(event, streamingIdx);
            break;
        }
      }
    } catch (err) {
      if ((err as { name?: string }).name === "AbortError") return;
      const msg = err instanceof Error ? err.message : "Network error";
      if (streamingIdx >= 0) {
        setMessages((prev) => {
          const next = [...prev];
          next[streamingIdx] = { kind: "error", message: msg };
          return next;
        });
      } else {
        setMessages((prev) => [...prev, { kind: "error", message: msg }]);
      }
    } finally {
      setLoading(false);
      abortCtrl.abort();
      if (abortRef.current === abortCtrl) abortRef.current = null;
    }
  };

  /** Stop button handler: cancels the in-flight SSE stream mid-response.
   *  The streaming bubble is replaced with whatever text has arrived so
   *  far so the user keeps the partial answer. */
  const stop = (): void => {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    // Convert any "streaming" bubble into a final "assistant" so the
    // partial markdown stays readable and the status bar disappears.
    setMessages((prev) =>
      prev.map((m) =>
        m.kind === "streaming"
          ? { kind: "assistant", text: m.text || "_(interrupted)_" }
          : m,
      ),
    );
  };

  /** Send the composer's contents, consuming the active reply quote (if
   * any) so it rides along with this one message and is then cleared. */
  const submitComposer = (): void => {
    const q = reply;
    setReply(null);
    void submit(intent, undefined, q);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submitComposer();
    }
  };

  const handleExampleClick = (): void => {
    setIntent(EXAMPLE_PROMPT);
    textareaRef.current?.focus();
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col" data-testid="chat-demo">
      {/* Scrollable message region — fills available space, composer
          stays pinned at the bottom (ChatGPT/Claude-style). Extra bottom
          padding lets the last message scroll up clear of the composer
          so nothing stays permanently hidden behind the fade overlay. */}
      <div
        ref={scrollRef}
        className="quartr-no-scrollbar flex-1 min-h-0 overflow-y-auto pt-6 pb-6"
        data-testid="chat-scroll"
      >
      {/* Intro (only shown before any messages). Callers can pass a
          custom node — e.g. the dashboard's greeting + index strip +
          quick-action chips — via the `intro` prop. The min-h-full +
          centered flex makes the empty-state cluster sit visually
          centered between the topbar and the docked composer instead
          of clinging to the top of the scroll region. */}
      {messages.length === 0 && (
        <div className="flex min-h-full flex-col items-center justify-center">
          {intro ?? (
            <div className="rounded-xl border bg-card p-6 text-center shadow-sm">
              <Bot
                className="mx-auto mb-3 h-8 w-8 text-muted-foreground"
                aria-hidden={true}
              />
              <p className="text-sm font-medium">Describe your strategy</p>
              <p className="mx-auto mt-1.5 max-w-sm text-xs text-muted-foreground">
                Type below and the AI will propose a structured workflow you can
                review and activate.
              </p>
              <button
                type="button"
                className="mt-3 text-xs text-primary hover:underline"
                onClick={handleExampleClick}
                data-testid="example-prompt-btn"
              >
                Try: RELIANCE 3:55 PM buy example
              </button>
            </div>
          )}
        </div>
      )}

      {/* Message thread */}
      {messages.length > 0 && (
        <div className="flex flex-col gap-6" data-testid="chat-messages">
          {messages.map((msg, idx) => {
            // Retry on an assistant message re-submits the most recent
            // user message above it. Walking backwards gives us the
            // closest preceding user turn.
            const priorUserMessage = ((): string | null => {
              for (let i = idx - 1; i >= 0; i--) {
                const m = messages[i];
                if (m && m.kind === "user") return m.text;
              }
              return null;
            })();
            const onRetryAssistant = priorUserMessage
              ? () => void submit(priorUserMessage)
              : null;

            if (msg.kind === "user") {
              return (
                <UserBubble
                  key={idx}
                  text={msg.text}
                  quote={msg.quote}
                  timestamp={msg.timestamp}
                  onRetry={() => void submit(msg.text)}
                />
              );
            }
            if (msg.kind === "streaming") {
              return (
                <div key={idx} className="flex justify-start">
                  <div className="flex w-full items-start">
                    <div className="w-full max-w-3xl">
                      {/* Status row stays — shows what the model is
                          doing + elapsed counter — but flows above the
                          markdown body instead of inside a card. */}
                      <StreamingStatusBar
                        startedAt={msg.startedAt}
                        tools={msg.tools}
                        hasText={msg.text.length > 0}
                      />
                      {msg.text ? (
                        // While text is streaming we still want copy/retry
                        // available the moment any text exists, so wrap
                        // in AssistantBubble. The bubble's hover row is
                        // gated on text.length > 0 so it won't render an
                        // empty action strip during the first delta.
                        <div className="mt-2">
                          <AssistantBubble
                            text={msg.text}
                            onRetry={onRetryAssistant}
                          >
                            <AssistantMessage text={msg.text} />
                          </AssistantBubble>
                        </div>
                      ) : (
                        /* No text yet — show shimmer placeholder while
                           first delta arrives. */
                        <div className="mt-3 flex flex-col gap-1.5 py-0.5">
                          <Skeleton className="h-3 w-40" />
                          <Skeleton className="h-3 w-28" />
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            }
            if (msg.kind === "draft") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <WorkflowDraftCard
                      draft={msg.draft}
                      onOpenEditor={(draft) => onOpenEditor(draftToWorkflow(draft))}
                      onActivatedAndRunning={(info) => {
                        // Append a live-run card right after the draft so
                        // the user sees the workflow's first run streaming
                        // step-by-step in the same chat thread.
                        setMessages((prev) => [
                          ...prev,
                          {
                            kind: "live_run",
                            runId: info.runId,
                            workflowName: info.workflowName,
                            workflowId: info.workflowId,
                          },
                        ]);
                      }}
                    />
                  </div>
                </div>
              );
            }
            if (msg.kind === "live_run") {
              return (
                <div key={idx} className="flex justify-start">
                  <InlineRunCard
                    runId={msg.runId}
                    workflowName={msg.workflowName}
                  />
                </div>
              );
            }
            if (msg.kind === "assistant") {
              return (
                <div key={idx} className="flex justify-start">
                  <div className="flex w-full items-start">
                    <AssistantBubble text={msg.text} onRetry={onRetryAssistant}>
                      <AssistantMessage text={msg.text} />
                    </AssistantBubble>
                  </div>
                </div>
              );
            }
            if (msg.kind === "indicator_backtest") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <div className="w-full max-w-2xl">
                      <IndicatorBacktestCard payload={msg.payload} />
                    </div>
                  </div>
                </div>
              );
            }
            if (msg.kind === "financial_backtest") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <FinancialBacktestCard payload={msg.payload} />
                  </div>
                </div>
              );
            }
            if (msg.kind === "logic_card") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <LogicCardChip
                      card={msg.card}
                      conversationId={sessionIdRef.current}
                    />
                  </div>
                </div>
              );
            }
            if (msg.kind === "synthetic_security") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <SyntheticSecurityCard payload={msg.payload} />
                  </div>
                </div>
              );
            }
            if (msg.kind === "ipo_list") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <IpoListCard
                      payload={msg.payload}
                      onSelectIpo={(sym) =>
                        void submit(`apply for the ${sym} IPO`)
                      }
                      onRemindIpo={(sym) =>
                        void submit(
                          `set up open-day reminders for the ${sym} IPO`,
                          "agent",
                        )
                      }
                    />
                  </div>
                </div>
              );
            }
            if (msg.kind === "ipo_application") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <IpoApplicationCard
                      payload={msg.payload}
                      onSetupReminders={(sym) =>
                        void submit(
                          `Set up open-day reminders for the ${sym} IPO`,
                          "agent",
                        )
                      }
                    />
                  </div>
                </div>
              );
            }
            if (msg.kind === "ipo_listed") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <IpoListedCard payload={msg.payload} />
                  </div>
                </div>
              );
            }
            if (msg.kind === "option_chain") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <OptionChainCard payload={msg.payload} />
                  </div>
                </div>
              );
            }
            if (msg.kind === "option_strategy") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <OptionStrategyCard payload={msg.payload} />
                  </div>
                </div>
              );
            }
            if (msg.kind === "portfolio_greeks") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <PortfolioGreeksCard payload={msg.payload} />
                  </div>
                </div>
              );
            }
            if (msg.kind === "clarify") {
              // Only the latest clarify card is interactive — older ones in the
              // thread are already answered (the backend advances the N-of-M
              // flow in-band, re-surfacing a fresh card each turn).
              const isLatestClarify = !messages
                .slice(idx + 1)
                .some((m) => m.kind === "clarify" || m.kind === "strategy_builder");
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <ClarifyCard
                      card={msg.card}
                      disabled={loading || !isLatestClarify}
                      onSendMessage={(text) => void submit(text)}
                    />
                  </div>
                </div>
              );
            }
            if (msg.kind === "strategy_builder") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start">
                        <AssistantBubble text={msg.intro} onRetry={onRetryAssistant}>
                          <AssistantMessage text={msg.intro} />
                        </AssistantBubble>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <StrategyBuilderCard card={msg.card} />
                  </div>
                </div>
              );
            }
            // error
            return (
              <div key={idx} className="flex justify-start">
                <div
                  className="max-w-sm rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-2.5 text-xs text-destructive"
                  role="alert"
                  data-testid="chat-error"
                >
                  {msg.message}
                </div>
              </div>
            );
          })}

          {/* Pre-stream loading bubble — shown only when loading=true but no
              streaming bubble exists yet (race guard for the one-tick gap
              before streamingIdx is set). Uses the witty phrase rotator
              instead of skeleton bars so the wait reads as "the analyst is
              working" rather than "frame is loading." */}
          {loading && !messages.some((m) => m.kind === "streaming") && (
            <div
              className="flex justify-start"
              data-testid="chat-loading"
              aria-live="polite"
              aria-label="Generating response"
            >
              <div
                className="inline-flex items-center"
                style={{
                  gap: 12,
                  padding: "10px 14px",
                  borderRadius: "var(--radius-md)",
                  background: "var(--bg-primary)",
                  border: "1px solid var(--glass-border)",
                  fontFamily: "var(--font-ui)",
                  fontSize: 13,
                  color: "var(--text-secondary)",
                  position: "relative",
                  overflow: "hidden",
                }}
              >
                <WittyTicker />
                <WittyPhrase />
                {/* Subtle shimmer across the bubble's bottom edge so
                    something is always animating even when the phrase
                    is between cycles. */}
                <span
                  aria-hidden={true}
                  className="witty-shimmer"
                  style={{
                    position: "absolute",
                    left: 0,
                    right: 0,
                    bottom: 0,
                    height: 2,
                    pointerEvents: "none",
                  }}
                />
              </div>
            </div>
          )}
        </div>
      )}
      </div>

      {/* Pinned composer — sits below the scrolling thread, never moves
          while messages stream in. Pulled up to overlap the scroll area
          with a transparent→background gradient so the thread fades out
          behind the pill (ChatGPT-style) instead of hard-stopping against
          a flat white band. Tighter bottom padding on phones so the pill
          doesn't dominate the landing surface. */}
      <div className="relative z-10 -mt-6 shrink-0 bg-gradient-to-t from-background via-background to-transparent pb-3 pt-6 sm:pb-5 sm:pt-7">
        <ChatComposer
          textareaRef={textareaRef}
          value={intent}
          onChange={setIntent}
          onKeyDown={handleKeyDown}
          onSubmit={submitComposer}
          onStop={stop}
          loading={loading}
          mode={mode}
          onModeChange={setMode}
          reply={reply}
          onClearReply={() => setReply(null)}
        />
      </div>

      {/* Floating "Reply" affordance — appears above any text selection
          made inside an assistant message (Claude / ChatGPT gesture).
          `onMouseDown preventDefault` keeps the selection alive long
          enough for the click handler to read it. */}
      {selectionReply && (
        <button
          ref={selBtnRef}
          type="button"
          // Commit on mousedown — it fires BEFORE the click collapses the
          // selection, and preventDefault stops the highlight from being
          // cleared / focus from being stolen. onClick is too late: by
          // then the selection (and our button state) is already gone.
          onMouseDown={(e) => {
            e.preventDefault();
            commitSelectionReply();
          }}
          data-testid="selection-reply-btn"
          className="inline-flex items-center"
          style={{
            position: "fixed",
            left: selectionReply.left,
            top: Math.max(8, selectionReply.top - 44),
            transform: "translateX(-50%)",
            zIndex: 60,
            gap: 6,
            padding: "7px 12px",
            borderRadius: "var(--radius-pill)",
            background: "var(--bg-elevated)",
            border: "1px solid var(--glass-border)",
            color: "var(--text-primary)",
            fontFamily: "var(--font-ui)",
            fontSize: 12.5,
            fontWeight: 500,
            cursor: "pointer",
            boxShadow: "0 6px 20px rgba(0,0,0,0.28)",
            transition: "opacity 0.12s var(--ease-quartr)",
          }}
        >
          <CornerUpLeft size={13} strokeWidth={2} aria-hidden={true} />
          <span>Reply</span>
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ChatComposer — glassy pill composer with attachment + cmd icons,
// Send button on the right, and a row of mode pills below
// (Automation / Agent / Backtest). Inspired by the user's reference
// image (Image #3 in the v1 design conversation).
// ---------------------------------------------------------------------------

type ModeMeta = {
  id: Exclude<ChatMode, null>;
  label: string;
  icon: React.ComponentType<{ size?: number; strokeWidth?: number; "aria-hidden"?: boolean }>;
  /** Hint shown on hover. */
  description: string;
};

const MODES: ModeMeta[] = [
  {
    id: "automation",
    label: "Automation",
    icon: Zap,
    description: "Single deterministic action — buy, sell, GTT, SIP, square-off",
  },
  {
    id: "agent",
    label: "Agent",
    icon: WorkflowIcon,
    description: "Multi-step workflow with triggers, fetches, conditions",
  },
  {
    id: "backtest",
    label: "Backtest",
    icon: LineChart,
    description: "Historical simulation on a strategy or expression",
  },
];

// ---------------------------------------------------------------------------
// UserBubble — Quartr-style user message with asymmetric radius and a
// hover-only action row beneath (date · retry · copy). The retry handler
// re-runs `submit(text)` with the original message; copy writes to the
// system clipboard via navigator.clipboard.
// ---------------------------------------------------------------------------

function fmtBubbleDate(iso?: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
    });
  } catch {
    return "";
  }
}

function UserBubble({
  text,
  quote,
  timestamp,
  onRetry,
}: {
  text: string;
  /** When set, the excerpt this message was a reply-by-selecting to.
   * Rendered as a quote block above the user's text. */
  quote?: string;
  timestamp?: string;
  onRetry: () => void;
}): React.ReactElement {
  const [hovered, setHovered] = useState(false);
  const [tappedOpen, setTappedOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  // <sm = Tailwind's phone breakpoint. We use the same boundary the rest
  // of the chat surface uses so the click-to-reveal behaviour kicks in
  // exactly where hover stops being a reliable input modality.
  const [isPhone, setIsPhone] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(max-width: 639px)");
    const sync = (): void => setIsPhone(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  // Phone only: dismiss the tap-revealed actions when the next pointer
  // event lands outside this bubble, so the row doesn't linger after the
  // user moves on to another message or the composer.
  useEffect(() => {
    if (!isPhone || !tappedOpen) return;
    const handler = (e: PointerEvent): void => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setTappedOpen(false);
      }
    };
    document.addEventListener("pointerdown", handler);
    return () => document.removeEventListener("pointerdown", handler);
  }, [isPhone, tappedOpen]);

  const handleCopy = async (): Promise<void> => {
    const ok = await copyToClipboard(text);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  // Phone uses tap-to-toggle (hover doesn't fire reliably on touch);
  // laptop keeps the original hover behaviour untouched.
  const showActions = (!isPhone && hovered) || (isPhone && tappedOpen) || copied;

  return (
    <div
      ref={wrapRef}
      onMouseEnter={isPhone ? undefined : () => setHovered(true)}
      onMouseLeave={isPhone ? undefined : () => setHovered(false)}
      // Flush-right at every breakpoint. The phone-only pr-12 used to
      // exist so user bubbles wouldn't slide under the floating "New
      // chat" button pinned at the chat surface's top-right; that
      // button has since moved into the sidebar, so the extra phone
      // padding was just creating dead space on the right edge.
      className="flex flex-col items-end"
      style={{ marginBottom: 4 }}
    >
      {quote && (
        <div
          className="flex items-start"
          style={{
            maxWidth: "78%",
            marginBottom: 6,
            gap: 7,
            paddingLeft: 10,
            borderLeft: "2px solid var(--glass-border)",
            color: "var(--text-tertiary)",
            fontSize: 13,
            lineHeight: 1.45,
            fontFamily: "var(--font-ui)",
          }}
        >
          <CornerUpLeft
            size={13}
            strokeWidth={2}
            aria-hidden={true}
            style={{ marginTop: 3, flexShrink: 0, opacity: 0.8 }}
          />
          <span
            style={{
              display: "-webkit-box",
              WebkitLineClamp: 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
              wordBreak: "break-word",
            }}
          >
            {quote}
          </span>
        </div>
      )}
      <div
        onClick={isPhone ? () => setTappedOpen((v) => !v) : undefined}
        className="whitespace-pre-wrap"
        style={{
          maxWidth: "78%",
          padding: "12px 18px",
          borderRadius: "16px 16px 2px 16px",
          background: "var(--bg-elevated)",
          border: "none",
          fontSize: 14.5,
          color: "var(--text-primary)",
          lineHeight: 1.5,
          fontFamily: "var(--font-ui)",
          wordBreak: "break-word",
          cursor: isPhone ? "pointer" : undefined,
        }}
      >
        {text}
      </div>

      {/* Action row — laptop reveals on hover, phone reveals on tap.
          Stays visible briefly after a successful copy so the "Copied"
          confirmation has a chance to read. */}
      <div
        className="flex items-center"
        style={{
          marginTop: 6,
          gap: 6,
          color: "var(--text-tertiary)",
          opacity: showActions ? 1 : 0,
          transition: "opacity 0.18s var(--ease-quartr)",
          pointerEvents: showActions ? "auto" : "none",
        }}
      >
        {timestamp && (
          <span style={{ fontSize: 11.5, color: "var(--text-tertiary)" }}>
            {fmtBubbleDate(timestamp)}
          </span>
        )}
        <ActionIconButton label="Retry" onClick={onRetry}>
          <RotateCw size={14} strokeWidth={2} aria-hidden={true} />
        </ActionIconButton>
        <ActionIconButton
          label={copied ? "Copied" : "Copy"}
          onClick={() => void handleCopy()}
        >
          {copied ? (
            <span className="copy-tick-pop" key="copied">
              <Check
                size={14}
                strokeWidth={2.5}
                aria-hidden={true}
              />
            </span>
          ) : (
            <Copy size={14} strokeWidth={2} aria-hidden={true} />
          )}
        </ActionIconButton>
      </div>
    </div>
  );
}

/**
 * Copy `text` to the user's system clipboard. Tries the modern
 * Clipboard API first; on failure (insecure context, headless
 * browser, missing permission, etc.) falls back to a hidden
 * <textarea> + document.execCommand("copy"). Returns true when
 * the write succeeded, false otherwise.
 */
async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false;
  if (typeof navigator !== "undefined" && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through to execCommand path */
    }
  }
  if (typeof document === "undefined") return false;
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.left = "0";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/**
 * AssistantBubble — wrapper around an assistant message body that
 * shows a hover-only action row (Copy · Retry) beneath. Mirrors the
 * UserBubble pattern but lives on the left side of the thread.
 *
 * `text` is the plain string the Copy button writes to clipboard.
 * `onRetry` re-runs the most recent user message that triggered
 * this response — wired by the parent so the bubble itself doesn't
 * need to know about the message history.
 */
function AssistantBubble({
  text,
  onRetry,
  children,
}: {
  text: string;
  onRetry: (() => void) | null;
  children: React.ReactNode;
}): React.ReactElement {
  const [hovered, setHovered] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = async (): Promise<void> => {
    const ok = await copyToClipboard(text);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="flex flex-col items-start"
      style={{ width: "100%" }}
    >
      {children}
      {/* Hover-only action row — only rendered when there's something
          worth copying. Streaming-in-progress bubbles pass empty text
          and skip the row entirely. The row also stays visible for
          ~1.5s after a successful copy so the "Copied" badge has a
          chance to read even if the cursor leaves immediately. */}
      {text.length > 0 && (
        <div
          className="flex items-center"
          style={{
            marginTop: 6,
            gap: 6,
            color: "var(--text-tertiary)",
            opacity: hovered || copied ? 1 : 0,
            transition: "opacity 0.18s var(--ease-quartr)",
            pointerEvents: hovered || copied ? "auto" : "none",
          }}
        >
          <ActionIconButton
            label={copied ? "Copied" : "Copy"}
            onClick={() => void handleCopy()}
          >
            {copied ? (
              <span className="copy-tick-pop" key="copied">
                <Check
                  size={14}
                  strokeWidth={2.5}
                  aria-hidden={true}
                />
              </span>
            ) : (
              <Copy size={14} strokeWidth={2} aria-hidden={true} />
            )}
          </ActionIconButton>
          {onRetry && (
            <ActionIconButton label="Retry" onClick={onRetry}>
              <RotateCw size={14} strokeWidth={2} aria-hidden={true} />
            </ActionIconButton>
          )}
        </div>
      )}
    </div>
  );
}

function ActionIconButton({
  children,
  label,
  onClick,
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className="inline-flex items-center justify-center"
      style={{
        width: 28,
        height: 28,
        background: "transparent",
        border: "none",
        borderRadius: "var(--radius-sm)",
        color: "var(--text-tertiary)",
        cursor: "pointer",
        transition:
          "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = "var(--text-primary)";
        e.currentTarget.style.background = "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = "var(--text-tertiary)";
        e.currentTarget.style.background = "transparent";
      }}
    >
      {children}
    </button>
  );
}

function ChatComposer({
  textareaRef,
  value,
  onChange,
  onKeyDown,
  onSubmit,
  onStop,
  loading,
  mode,
  onModeChange,
  reply,
  onClearReply,
}: {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (v: string) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: () => void;
  /** Cancel the in-flight SSE stream. Wired only when `loading` flips
   *  the right-side button into its Stop state. */
  onStop: () => void;
  loading: boolean;
  mode: ChatMode;
  onModeChange: (m: ChatMode) => void;
  /** Active reply-by-selecting excerpt, shown as a dismissible quote
   * chip above the input. Null when there's no pending reply. */
  reply: string | null;
  onClearReply: () => void;
}): React.ReactElement {
  // The right-side button is in one of three states:
  //   • idle     — empty input, button is dim, disabled
  //   • ready    — input has text, button is ink-fill, sends on click
  //   • loading  — response in flight, button shows Square (stop)
  const canSend = !!value.trim() && !loading;
  const showStop = loading;

  // Phone vs. desktop placeholder text. `isMobile` defaults to false on
  // SSR/first paint and snaps to true via useEffect on phone — so for a
  // single frame phones may show the longer desktop placeholder. That's
  // cosmetically OK because the pill's *layout* is now CSS-driven via
  // the `sm:` Tailwind variants below, so the long string just truncates
  // horizontally inside the rows=1 textarea instead of stretching the
  // composer (the actual bug in the user-reported flash).
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(max-width: 639px)");
    const sync = (): void => setIsMobile(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  const placeholder =
    mode === "automation"
      ? isMobile
        ? "What order should I place?"
        : "What order should I place? — e.g. 'buy 10 RELIANCE at market'"
      : mode === "agent"
        ? isMobile
          ? "Describe an automation…"
          : "Describe an automation — e.g. 'every weekday 15:25 buy 5 NIFTYBEES'"
        : mode === "backtest"
          ? isMobile
            ? "Describe a strategy…"
            : "Describe a strategy to backtest — e.g. 'RELIANCE when RSI < 30'"
          : isMobile
            ? PLACEHOLDER_TEXT_MOBILE
            : PLACEHOLDER_TEXT;

  return (
    <div className="space-y-1.5 sm:space-y-3" data-testid="chat-composer">
      {/* Reply-by-selecting quote chip — the excerpt the user picked
          from an assistant message. Sits just above the pill (Claude /
          ChatGPT pattern) with a dismiss button. */}
      {reply && (
        <div
          data-testid="composer-reply-chip"
          className="flex items-start"
          style={{
            gap: 9,
            padding: "9px 12px",
            borderRadius: "var(--radius-lg, 12px)",
            background: "var(--bg-elevated)",
            border: "1px solid var(--glass-border)",
          }}
        >
          <CornerUpLeft
            size={14}
            strokeWidth={2}
            aria-hidden={true}
            style={{
              marginTop: 2,
              flexShrink: 0,
              color: "var(--text-tertiary)",
            }}
          />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.02em",
                textTransform: "uppercase",
                color: "var(--text-tertiary)",
                marginBottom: 2,
                fontFamily: "var(--font-ui)",
              }}
            >
              Replying to
            </div>
            <div
              style={{
                fontSize: 13,
                lineHeight: 1.45,
                color: "var(--text-secondary)",
                fontFamily: "var(--font-ui)",
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
                wordBreak: "break-word",
              }}
            >
              {reply}
            </div>
          </div>
          <button
            type="button"
            onClick={onClearReply}
            aria-label="Clear reply"
            title="Clear reply"
            className="inline-flex items-center justify-center"
            style={{
              width: 24,
              height: 24,
              flexShrink: 0,
              background: "transparent",
              border: "none",
              borderRadius: "var(--radius-sm)",
              color: "var(--text-tertiary)",
              cursor: "pointer",
              transition: "color 0.18s var(--ease-quartr)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "var(--text-primary)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "var(--text-tertiary)";
            }}
          >
            <X size={15} strokeWidth={2} aria-hidden={true} />
          </button>
        </div>
      )}

      {/* ChatGPT-style single-line composer pill. The textarea sits at
          exactly one line of content height and grows via the autosize
          effect when the user types past one line; outer padding gives
          breathing room. No min-h-44 dead space below the placeholder. */}
      {/* borderRadius is --radius-xl (24px), NOT --radius-pill (9999px):
          CSS clamps any radius to half the box height, so a single-line
          composer still renders as a full stadium pill, while the tall
          multiline state stays a clean 24px rounded rectangle instead of
          ballooning into oversized side arcs that curve inward and clip
          the text.

          Stays items-center for the tuned single-line placeholder/button
          centering; the send button itself is self-end (see below) so it
          drops to the bottom only once the textarea grows multiline. */}
      <div
        className="flex items-center gap-1.5 p-1 pl-[14px] sm:gap-2 sm:p-1.5 sm:pl-[18px]"
        style={{
          background: "var(--bg-primary)",
          borderRadius: "var(--radius-xl)",
          border: `1px solid var(--glass-border)`,
        }}
      >

        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          rows={1}
          className={cn(
            "flex-1 resize-none border-0 bg-transparent shadow-none",
            // Single-line height: 24px box matches the lineHeight below
            // so the placeholder sits centered against the send button
            // with no empty bottom strip inside the textarea.
            "!min-h-[24px] px-0 py-0 text-[13px] sm:text-sm",
            "focus-visible:ring-0 focus-visible:ring-offset-0",
          )}
          style={{
            background: "transparent",
            color: "var(--text-primary)",
            fontFamily: "var(--font-ui)",
            lineHeight: "24px",
            overflowY: "hidden",
            maxHeight: MAX_TEXTAREA_PX,
          }}
          // Stay typable even while a response is streaming — the user
          // should be able to compose their next message in parallel.
          data-testid="chat-textarea"
          aria-label="Describe your strategy"
        />

        {/* Trailing — circular Send button (idle/ready) OR Stop button
            (while a response is streaming). Same geometry, different
            icon/handler. ChatGPT pattern: a Square stops the stream and
            keeps the partial answer. */}
        <button
          type="button"
          onClick={showStop ? onStop : onSubmit}
          disabled={!showStop && !canSend}
          data-testid={showStop ? "chat-stop-btn" : "chat-submit-btn"}
          aria-label={showStop ? "Stop response" : "Send"}
          // self-end: stays centered against a single-line textarea (the
          // button is then the taller child), drops to the bottom once the
          // textarea grows multiline.
          className="flex h-7 w-7 shrink-0 items-center justify-center self-end sm:h-8 sm:w-8"
          style={{
            background: showStop || canSend ? "var(--text-primary)" : "var(--bg-elevated)",
            color: showStop || canSend ? "var(--bg-primary)" : "var(--text-disabled)",
            border: "none",
            borderRadius: "var(--radius-pill)",
            cursor: showStop || canSend ? "pointer" : "not-allowed",
            transition:
              "color 0.18s var(--ease-quartr), background-color 0.2s var(--ease-quartr), transform 0.18s var(--ease-quartr)",
          }}
        >
          {showStop ? (
            // 12×12 filled square — the ChatGPT stop glyph.
            <Square
              size={12}
              strokeWidth={0}
              fill="currentColor"
              aria-hidden={true}
              style={{ borderRadius: 2 }}
            />
          ) : (
            // CSS-driven responsive size so the first paint on phone is
            // correct without a JS check (Tailwind size classes win over
            // the lucide width/height attributes).
            <ArrowUp
              className="h-3.5 w-3.5 sm:h-4 sm:w-4"
              strokeWidth={2.25}
              aria-hidden={true}
            />
          )}
        </button>
      </div>

      {/* Mode pills — Automation / Agent / Backtest (extras kept). Quartr-styled
          so they read as a quiet row rather than glassy chips. */}
      <div className="flex items-center justify-center" style={{ gap: 8 }}>
        {MODES.map((m) => {
          const Icon = m.icon;
          const isActive = mode === m.id;
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => onModeChange(isActive ? null : m.id)}
              data-testid={`mode-${m.id}`}
              data-active={isActive}
              aria-pressed={isActive}
              title={m.description}
              className="inline-flex items-center"
              style={{
                // Borderless mode pills — same active treatment as the
                // sidebar nav: subtle elevated bg + ink text. No border.
                gap: 6,
                padding: "6px 12px",
                borderRadius: "var(--radius-pill)",
                background: isActive ? "var(--surface-active)" : "transparent",
                border: "none",
                color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                fontFamily: "var(--font-ui)",
                fontSize: 11.5,
                fontWeight: 500,
                cursor: "pointer",
                transition:
                  "color 0.35s var(--ease-quartr), background-color 0.35s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                if (isActive) return;
                e.currentTarget.style.color = "var(--text-primary)";
              }}
              onMouseLeave={(e) => {
                if (isActive) return;
                e.currentTarget.style.color = "var(--text-secondary)";
              }}
            >
              <Icon size={12} strokeWidth={2} aria-hidden={true} />
              <span>{m.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
