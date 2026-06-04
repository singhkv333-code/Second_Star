"use client";

/**
 * ChatDemo — real chat surface wired to POST /chat/stream (SSE).
 *
 * Messages → POST /chat/stream (legacy router, no /api prefix).
 * SSE events: start | tool_start | tool_done | delta | replace | error | done.
 * When `done` arrives its raw_data/_render_hint drives the final card kind,
 * identical to the former non-streaming POST /chat dispatch.
 * Bare NSE tickers → renders StockSnapshotCard (no API call).
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
  RotateCw,
  Square,
  Workflow as WorkflowIcon,
  LineChart,
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
import { StockSnapshotCard } from "@/components/chat/StockSnapshotCard";
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
import type { Workflow, IpoApplicationPayload, IpoListPayload, IpoListedPayload, OptionChainPayload, OptionStrategyPayload } from "@/lib/types";

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

/**
 * Quick "show me X" ticker shortcut. Now CASE-INSENSITIVE — "RELIANCE",
 * "Reliance", and "reliance" all open the snapshot card. Previously
 * the bare-ticker regex only accepted uppercase, which surprised users
 * who expected case-insensitive ticker recognition (PDF report:
 * "stock widget triggers only when I type the ticker in CAPS").
 *
 * Accepted shapes:
 *   1. bare ticker (case-insensitive): "RELIANCE", "Reliance", "reliance"
 *   2. snapshot phrase: "show me reliance", "what about TCS?", "INFY snapshot"
 *   3. with leading $: "$RELIANCE"
 *
 * Common-name aliases (zomato → ETERNAL after the IPO rebrand) are
 * resolved here so users don't see "no quote available for ZOMATO.NSE".
 *
 * If no phrase pattern matches we fall through to the LLM.
 */

/** Common-name → NSE-ticker aliases. Lowercased keys. */
const TICKER_ALIASES: Record<string, string> = {
  zomato: "ETERNAL",
  // Eternal Limited (formerly Zomato) — listed under ETERNAL on NSE
  // since the 2025 rebrand. Without this, the user typing "zomato"
  // gets a "no quote" error (PDF report).
  swiggy: "SWIGGY",
  paytm: "PAYTM",
  hdfc: "HDFCBANK",       // most-asked HDFC variant
  icici: "ICICIBANK",
  sbi: "SBIN",
  hul: "HINDUNILVR",
  "tata steel": "TATASTEEL",
  "tata motors": "TATAMOTORS",
  "tata power": "TATAPOWER",
  "bajaj finance": "BAJFINANCE",
  reliance: "RELIANCE",
  infy: "INFY",
  infosys: "INFY",
  tcs: "TCS",
};

function resolveTickerAlias(raw: string): string {
  const k = raw.trim().toLowerCase();
  return TICKER_ALIASES[k] ?? raw.toUpperCase();
}

function extractTicker(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  // Strip a trailing "?" so "Reliance?" still snapshots.
  const noQ = trimmed.replace(/\?+$/, "").trim();

  // Bare ticker. Two acceptance branches so we don't snapshot common
  // English words ("something", "anything") just because they look
  // like tickers:
  //   1. ALL-UPPERCASE — a deliberate ticker keystroke ("RELIANCE").
  //   2. Alias hit — case-insensitive match against the alias map
  //      ("Reliance", "zomato", "hdfc"). Mixed case without an
  //      alias entry falls through to the LLM.
  // M&M / L&T / BAJAJ-AUTO survive because they're uppercase or
  // already aliased.
  if (/^[A-Z][A-Z0-9&\-_]{1,14}$/.test(noQ)) {
    const upper = noQ.toUpperCase();
    if (STOPWORDS.has(upper)) return null;
    return upper;
  }
  if (/^[A-Za-z][A-Za-z0-9&\- _]{1,19}$/.test(noQ)) {
    const lower = noQ.toLowerCase();
    if (TICKER_ALIASES[lower]) return TICKER_ALIASES[lower] ?? null;
  }

  // $RELIANCE
  const dollarMatch = /^\$([A-Za-z]{2,15})\b/.exec(noQ);
  if (dollarMatch) {
    const sym = dollarMatch[1] ?? null;
    if (!sym || STOPWORDS.has(sym.toUpperCase())) return null;
    return resolveTickerAlias(sym);
  }

  // Phrase patterns: require an explicit verb cue AND a short total
  // message length. Snapshot intents are phrases ("show me INFY",
  // "TCS quote"), workflow descriptions are sentences. Without this
  // length gate, a phrase like "...sells if price decreases..." inside
  // a long workflow description would match `/(\w)\s+price/` and
  // mis-route to the snapshot card with the conjunction as the
  // ticker (e.g. "no quote available for IF.NSE").
  if (noQ.length > 40) return null;

  const lower = noQ.toLowerCase();
  const phrasePatterns = [
    /^(?:show|show me|what about|how(?:'s| is| about)|tell me (?:more )?about|price of|quote for|snapshot of|chart for)\s+([a-z]{2,15})\b/,
    /^([a-z]{2,15})\s+(?:snapshot|quote|price|chart)\s*\??\s*$/,
  ];
  for (const re of phrasePatterns) {
    const m = re.exec(lower);
    if (m) {
      const raw = m[1];
      if (!raw) continue;
      const candidate = raw.toUpperCase();
      if (STOPWORDS.has(candidate)) continue;
      return resolveTickerAlias(raw);
    }
  }
  return null;
}

/** Words that look like tickers but are clearly conversational filler.
 *  Expanded with the cases reported in the PDF: "ALL" (treated as a
 *  ticker on the exit-positions confirmation), "INTRADAY", and several
 *  short follow-up replies that should never become snapshot cards. */
const STOPWORDS = new Set([
  // Greetings / acknowledgements
  "HI", "HELLO", "HEY", "OK", "OKAY", "YES", "NO", "PLEASE", "THANKS", "BYE",
  "SURE", "MAYBE", "FINE",
  // Question words
  "WHAT", "WHEN", "WHY", "WHO", "HOW", "WHICH", "WHERE",
  // Conjunctions / prepositions (caught when message slips past length gate)
  "IF", "AT", "ON", "IN", "BY", "AS", "IS", "OF", "TO", "OR", "AND", "BUT",
  "FOR", "WITH", "FROM", "THE", "AN", "BE",
  // Verb-ish words that look like tickers
  "SHOW", "TELL", "BUY", "SELL", "RUN", "GET", "SET", "ADD", "EDIT",
  // Quantifiers / scope words that surfaced as fake tickers in PDF
  "ALL", "NONE", "ANY", "EVERY", "EACH", "BOTH",
  // Order-type / scope qualifiers (PDF: "INTRADAY only" turned into a ticker)
  "INTRADAY", "DELIVERY", "MIS", "CNC", "MARKET", "LIMIT", "GTT", "SL",
  // Acknowledgement / clarification answers
  "DONE", "GOT", "GOTIT", "ACTIVATE", "CONFIRM", "PROCEED", "CANCEL",
]);

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
  | { kind: "user"; text: string; timestamp?: string }
  | { kind: "assistant"; text: string }
  /** Transient streaming bubble — replaced by a final kind on `done`.
   * `startedAt` is the unix-ms timestamp when the bubble was created;
   * the `StreamingStatusBar` reads it to render an elapsed counter. */
  | { kind: "streaming"; text: string; tools: ToolPill[]; startedAt: number }
  | { kind: "draft"; draft: WorkflowDraft; intro: string }
  | { kind: "snapshot"; symbol: string; intro: string }
  | { kind: "indicator_backtest"; payload: IndicatorBacktestPayload; intro: string }
  | { kind: "financial_backtest"; payload: FinancialBacktestPayload; intro: string }
  | { kind: "logic_card"; card: LogicCard; intro: string }
  | { kind: "synthetic_security"; payload: SyntheticSecurityPayload; intro: string }
  | { kind: "ipo_application"; payload: IpoApplicationPayload; intro: string }
  | { kind: "ipo_list"; payload: IpoListPayload; intro: string }
  | { kind: "ipo_listed"; payload: IpoListedPayload; intro: string }
  | { kind: "option_chain"; payload: OptionChainPayload; intro: string }
  | { kind: "option_strategy"; payload: OptionStrategyPayload; intro: string }
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
    } else {
      finalMessage = { kind: "assistant", text: data.response ?? "" };
    }

    setMessages((prev) => {
      const next = [...prev];
      next[streamingIdx] = finalMessage;
      return next;
    });
  }

  const submit = async (text: string, modeOverride?: ChatMode): Promise<void> => {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    setMessages((prev) => [
      ...prev,
      { kind: "user", text: trimmed, timestamp: new Date().toISOString() },
    ]);
    setIntent("");

    // Bare ticker shortcut — no API call. SKIP the shortcut when the
    // last assistant turn ended with a clarification ask: a one-word
    // reply ("ALL", "yes", "intraday only") is the answer to that
    // question, not a snapshot request. Without this gate the user's
    // "ALL" reply to "do you want all positions or intraday only?"
    // got rendered as a `no quote available for ALL.NSE` error
    // (PDF report).
    const lastAssistant = [...historyRef.current]
      .reverse()
      .find((m) => m.role === "assistant");
    const lookedLikeClarification =
      !!lastAssistant &&
      /\?\s*$|please (?:share|specify|provide|confirm|reply|answer)|\bwhich\b|\bhow many\b|\b(all|intraday)\b/i
        .test(lastAssistant.content.trim().slice(-300));

    const ticker = lookedLikeClarification ? null : extractTicker(trimmed);
    if (ticker) {
      setMessages((prev) => [
        ...prev,
        {
          kind: "snapshot",
          symbol: ticker,
          intro: `Here's a quick snapshot for ${ticker} — price, day range, and the basics are below.`,
        },
      ]);
      // CRITICAL: seed the rolling history with synthetic turns so the
      // BACKEND knows the user just looked at this ticker. Without this,
      // typing "zomato" → snapshot → "buy 10 shares" sends an empty
      // context to /chat and the LLM asks "which stock?". The user
      // saw the ETERNAL card a turn ago — context resolution should
      // be obvious. We fake the assistant turn the FE rendered locally
      // so the LLM sees a coherent transcript.
      historyRef.current = [
        ...historyRef.current,
        { role: "user" as const, content: trimmed },
        {
          role: "assistant" as const,
          content: (
            `Here's a quick snapshot for ${ticker}. ` +
            `(Most recently mentioned ticker: ${ticker}.)`
          ),
        },
      ].slice(-20);
      return;
    }

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

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void submit(intent);
    }
  };

  const handleExampleClick = (): void => {
    setIntent(EXAMPLE_PROMPT);
    textareaRef.current?.focus();
  };

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="chat-demo">
      {/* Scrollable message region — fills available space, composer
          stays pinned at the bottom (ChatGPT/Claude-style). */}
      <div
        ref={scrollRef}
        className="quartr-no-scrollbar flex-1 min-h-0 overflow-y-auto pt-6 pb-4"
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
            if (msg.kind === "snapshot") {
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
                    <StockSnapshotCard symbol={msg.symbol} />
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
          while messages stream in. */}
      <div className="shrink-0 pb-5 pt-3">
        <ChatComposer
          textareaRef={textareaRef}
          value={intent}
          onChange={setIntent}
          onKeyDown={handleKeyDown}
          onSubmit={() => void submit(intent)}
          onStop={stop}
          loading={loading}
          mode={mode}
          onModeChange={setMode}
        />
      </div>
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
  timestamp,
  onRetry,
}: {
  text: string;
  timestamp?: string;
  onRetry: () => void;
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
      className="flex flex-col items-end"
      style={{ marginBottom: 4 }}
    >
      <div
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
        }}
      >
        {text}
      </div>

      {/* Hover-only action row — also stays visible briefly after a
          successful copy so the "Copied" toast has a chance to read. */}
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
            <Check
              size={14}
              strokeWidth={2.5}
              aria-hidden={true}
            />
          ) : (
            <Copy size={14} strokeWidth={2} aria-hidden={true} />
          )}
        </ActionIconButton>
        {/* Inline confirmation badge — shown for ~1.5s after a
            successful copy so the click registers visually even when
            the cursor leaves the hover row immediately. */}
        {copied && (
          <span
            className="copy-toast"
            style={{
              fontSize: 11.5,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              fontFamily: "var(--font-ui)",
              letterSpacing: "0.01em",
            }}
          >
            Copied
          </span>
        )}
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
              <Check
                size={14}
                strokeWidth={2.5}
                aria-hidden={true}
              />
            ) : (
              <Copy size={14} strokeWidth={2} aria-hidden={true} />
            )}
          </ActionIconButton>
          {onRetry && (
            <ActionIconButton label="Retry" onClick={onRetry}>
              <RotateCw size={14} strokeWidth={2} aria-hidden={true} />
            </ActionIconButton>
          )}
          {copied && (
            <span
              className="copy-toast"
              style={{
                fontSize: 11.5,
                fontWeight: 500,
                color: "var(--text-tertiary)",
                fontFamily: "var(--font-ui)",
                letterSpacing: "0.01em",
              }}
            >
              Copied
            </span>
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
}): React.ReactElement {
  // The right-side button is in one of three states:
  //   • idle     — empty input, button is dim, disabled
  //   • ready    — input has text, button is ink-fill, sends on click
  //   • loading  — response in flight, button shows Square (stop)
  const canSend = !!value.trim() && !loading;
  const showStop = loading;
  const [focused, setFocused] = useState(false);

  return (
    <div className="space-y-3" data-testid="chat-composer">
      {/* Quartr-style composer pill — single rounded shell, leading
          textarea + trailing controls cluster (paperclip · Cmd+Enter
          hint · circular send). Mirrors frontend-quartr's ChatLanding
          composer with theme-aware tokens. */}
      <div
        className="flex items-center"
        style={{
          gap: 10,
          background: "var(--bg-primary)",
          // Quartr composer is a true pill with padding 4/4/4/20.
          borderRadius: "var(--radius-pill)",
          border: `1px solid ${focused ? "var(--glass-border-focus)" : "var(--glass-border)"}`,
          padding: "4px 4px 4px 20px",
          transition: "border-color 0.2s var(--ease-quartr)",
        }}
      >

        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={
            mode === "automation"
              ? "What order should I place? — e.g. 'buy 10 RELIANCE at market'"
              : mode === "agent"
                ? "Describe an automation — e.g. 'every weekday 15:25 buy 5 NIFTYBEES'"
                : mode === "backtest"
                  ? "Describe a strategy to backtest — e.g. 'RELIANCE when RSI < 30'"
                  : mode === "trigger"
                    ? "Describe a market event — e.g. 'When RBI cuts rates, buy PSU bank ETF'"
                    : PLACEHOLDER_TEXT
          }
          rows={1}
          className={cn(
            "flex-1 resize-none border-0 bg-transparent shadow-none",
            "!min-h-[44px]",
            "focus-visible:ring-0 focus-visible:ring-offset-0",
            "px-2 py-3",
          )}
          style={{
            background: "transparent",
            color: "var(--text-primary)",
            fontFamily: "var(--font-ui)",
            fontSize: 14,
            lineHeight: "20px",
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
          className="flex shrink-0 items-center justify-center"
          style={{
            width: 40,
            height: 40,
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
              size={14}
              strokeWidth={0}
              fill="currentColor"
              aria-hidden={true}
              style={{ borderRadius: 2 }}
            />
          ) : (
            <ArrowUp size={18} strokeWidth={2} aria-hidden={true} />
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
