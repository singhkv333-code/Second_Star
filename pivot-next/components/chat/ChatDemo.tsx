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
  Bot,
  Command,
  Loader2,
  Paperclip,
  Send,
  Sparkles,
  Workflow as WorkflowIcon,
  LineChart,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
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
import type { Workflow } from "@/lib/types";

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
  "Describe your strategy, e.g. \"Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email.\"";

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

  // Pick the most informative status sentence:
  //   1. If text deltas have started → "Writing reply"
  //   2. Else if a tool is currently running → its friendly label
  //   3. Else if every tool finished but no text yet → "Wrapping up"
  //   4. Else (nothing has happened) → "Thinking"
  let sentence = "Thinking";
  if (hasText) {
    sentence = "Writing reply";
  } else {
    const pending = tools.find((t) => t.ok === undefined);
    if (pending) {
      sentence = TOOL_STATUS[pending.name] ?? `Running ${pending.name}`;
    } else if (tools.length > 0) {
      sentence = "Wrapping up";
    }
  }

  return (
    <div
      className="flex items-center gap-2 text-xs text-muted-foreground"
      data-testid="streaming-status"
    >
      <Loader2 className="h-3 w-3 animate-spin" aria-hidden={true} />
      <span>{sentence}…</span>
      <span aria-hidden={true} className="text-muted-foreground/60">·</span>
      <span className="tabular-nums" aria-label={`${elapsedSec} seconds elapsed`}>
        {elapsedSec}s
      </span>
    </div>
  );
}


type Message =
  | { kind: "user"; text: string }
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
  | { kind: "live_run"; runId: string; workflowName: string; workflowId: string }
  | { kind: "error"; message: string };

type ChatDemoProps = {
  /** Called when user clicks "Open in editor →" on a draft card. */
  onOpenEditor: (workflow: Workflow) => void;
  /** Optional prompt prefilled from dashboard chips. Auto-submits on mount. */
  prefill?: string;
  /** Called after the prefill has been consumed so parent can clear it. */
  onPrefillConsumed?: () => void;
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

export function ChatDemo({ onOpenEditor, prefill, onPrefillConsumed }: ChatDemoProps): React.ReactElement {
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

  // Consume prefill once when it arrives
  useEffect(() => {
    if (prefill) {
      setIntent(prefill);
      onPrefillConsumed?.();
      textareaRef.current?.focus();
    }
  }, [prefill, onPrefillConsumed]);

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
    } else {
      finalMessage = { kind: "assistant", text: data.response ?? "" };
    }

    setMessages((prev) => {
      const next = [...prev];
      next[streamingIdx] = finalMessage;
      return next;
    });
  }

  const submit = async (text: string): Promise<void> => {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    setMessages((prev) => [...prev, { kind: "user", text: trimmed }]);
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
        mode,
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
    }
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
        className="flex-1 min-h-0 overflow-y-auto pt-6 pb-4"
        data-testid="chat-scroll"
      >
      {/* Intro (only shown before any messages) */}
      {messages.length === 0 && (
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

      {/* Message thread */}
      {messages.length > 0 && (
        <div className="flex flex-col gap-6" data-testid="chat-messages">
          {messages.map((msg, idx) => {
            if (msg.kind === "user") {
              return (
                <div key={idx} className="flex justify-end">
                  <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl bg-primary px-4 py-2.5 text-sm leading-6 text-primary-foreground">
                    {msg.text}
                  </div>
                </div>
              );
            }
            if (msg.kind === "streaming") {
              return (
                <div key={idx} className="flex justify-start">
                  <div className="flex w-full items-start gap-3">
                    <Bot
                      className="mt-1 h-5 w-5 shrink-0 text-muted-foreground"
                      aria-hidden={true}
                    />
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
                        <div className="mt-2">
                          <AssistantMessage text={msg.text} />
                        </div>
                      ) : (
                        /* No text yet — show skeleton while first delta arrives */
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
                      <div className="flex w-full items-start gap-3">
                        <Bot className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden={true} />
                        <AssistantMessage text={msg.intro} />
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
                  <div className="flex w-full items-start gap-3">
                    <Bot className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden={true} />
                    <AssistantMessage text={msg.text} />
                  </div>
                </div>
              );
            }
            if (msg.kind === "snapshot") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start gap-3">
                        <Bot className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden={true} />
                        <AssistantMessage text={msg.intro} />
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
                      <div className="flex w-full items-start gap-3">
                        <Bot className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden={true} />
                        <AssistantMessage text={msg.intro} />
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
                      <div className="flex w-full items-start gap-3">
                        <Bot className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden={true} />
                        <AssistantMessage text={msg.intro} />
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
                      <div className="flex w-full items-start gap-3">
                        <Bot className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden={true} />
                        <AssistantMessage text={msg.intro} />
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <LogicCardChip card={msg.card} />
                  </div>
                </div>
              );
            }
            if (msg.kind === "synthetic_security") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex w-full items-start gap-3">
                        <Bot className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden={true} />
                        <AssistantMessage text={msg.intro} />
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <SyntheticSecurityCard payload={msg.payload} />
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

          {/* Loading indicator shown only when loading=true but no streaming bubble
              exists yet (race guard for the one-tick gap before streamingIdx is set). */}
          {loading && !messages.some((m) => m.kind === "streaming") && (
            <div
              className="flex justify-start"
              data-testid="chat-loading"
              aria-live="polite"
              aria-label="Generating response"
            >
              <div className="flex items-center gap-2 rounded-xl border bg-card px-4 py-3">
                <Bot className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden={true} />
                <div className="flex flex-col gap-1.5">
                  <Skeleton className="h-3 w-48" />
                  <Skeleton className="h-3 w-32" />
                </div>
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
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  /** Accent ring/glow when active. */
  accent: string;
  /** Hint shown on hover. */
  description: string;
};

const MODES: ModeMeta[] = [
  {
    id: "automation",
    label: "Automation",
    icon: Zap,
    accent:
      "ring-amber-400/60 bg-amber-400/10 text-amber-200 [--accent:251,191,36]",
    description: "Single deterministic action — buy, sell, GTT, SIP, square-off",
  },
  {
    id: "agent",
    label: "Agent",
    icon: WorkflowIcon,
    accent:
      "ring-violet-400/60 bg-violet-400/10 text-violet-200 [--accent:167,139,250]",
    description: "Multi-step workflow with triggers, fetches, conditions",
  },
  {
    id: "backtest",
    label: "Backtest",
    icon: LineChart,
    accent:
      "ring-emerald-400/60 bg-emerald-400/10 text-emerald-200 [--accent:52,211,153]",
    description: "Historical simulation on a strategy or expression",
  },
];

function ChatComposer({
  textareaRef,
  value,
  onChange,
  onKeyDown,
  onSubmit,
  loading,
  mode,
  onModeChange,
}: {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (v: string) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: () => void;
  loading: boolean;
  mode: ChatMode;
  onModeChange: (m: ChatMode) => void;
}): React.ReactElement {
  const activeMeta = mode ? MODES.find((m) => m.id === mode) : null;

  return (
    <div className="space-y-3" data-testid="chat-composer">
      {/* Glassmorphism pill — the composer body. The double-border
          + backdrop-blur + soft outer glow gives the liquid-glass feel
          without depending on backdrop-filter being supported. */}
      <div
        className={cn(
          "relative rounded-2xl border border-border/60",
          "bg-card/60 backdrop-blur-xl",
          "supports-[backdrop-filter]:bg-card/40",
          "shadow-[0_1px_0_rgba(255,255,255,0.04)_inset,0_8px_32px_-8px_rgba(0,0,0,0.45)]",
          "transition-all duration-200",
          activeMeta &&
            "ring-1 ring-[rgb(var(--accent)/0.45)] " +
              "shadow-[0_1px_0_rgba(255,255,255,0.04)_inset,0_8px_32px_-4px_rgba(var(--accent),0.25)]",
        )}
        style={
          activeMeta
            ? ({
                ["--accent" as string]: activeMeta.accent
                  .match(/--accent:([^\]]+)/)?.[1]
                  ?.trim(),
              } as React.CSSProperties)
            : undefined
        }
      >
        {/* Top sheen — subtle gradient that catches the "glass" reading
            without becoming AI slop. Visible only in dark mode where
            the contrast is strongest. */}
        <div
          aria-hidden={true}
          className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/15 to-transparent"
        />
        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            mode === "automation"
              ? "What order should I place? — e.g. 'buy 10 RELIANCE at market'"
              : mode === "agent"
                ? "Describe an automated agent — e.g. 'every weekday 15:25, if cash > ₹50k, buy 5 NIFTYBEES'"
                : mode === "backtest"
                  ? "Describe a strategy to backtest — e.g. 'RELIANCE when RSI drops below 30 over 5 years'"
                  : PLACEHOLDER_TEXT
          }
          className={cn(
            "min-h-[88px] resize-none border-0 bg-transparent text-sm",
            "rounded-2xl rounded-b-none",
            "px-4 pt-4 pb-2",
            "placeholder:text-muted-foreground/70",
            "focus-visible:ring-0 focus-visible:ring-offset-0",
          )}
          disabled={loading}
          data-testid="chat-textarea"
          aria-label="Describe your strategy"
        />
        {/* Hairline divider — pure light/dark border */}
        <div className="h-px bg-border/50" aria-hidden={true} />
        {/* Footer row: attachment + cmd hint left, Send right */}
        <div className="flex items-center justify-between gap-2 px-3 py-2">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-lg",
                "border border-border/50 bg-background/40 text-muted-foreground",
                "hover:bg-background/70 hover:text-foreground transition-colors",
              )}
              aria-label="Attach"
              tabIndex={-1}
            >
              <Paperclip className="h-3.5 w-3.5" aria-hidden={true} />
            </button>
            <span
              className={cn(
                "flex h-8 items-center gap-1 rounded-lg px-2",
                "border border-border/50 bg-background/40 text-[11px]",
                "text-muted-foreground tabular-nums",
              )}
              aria-label="Keyboard shortcut: Cmd+Enter"
            >
              <Command className="h-3 w-3" aria-hidden={true} />
              <span className="hidden sm:inline">Enter</span>
            </span>
          </div>
          <Button
            size="sm"
            onClick={onSubmit}
            disabled={!value.trim() || loading}
            data-testid="chat-submit-btn"
            aria-label="Send"
            className={cn(
              "h-8 rounded-lg px-3 gap-1.5",
              activeMeta &&
                "bg-[rgb(var(--accent))] text-background hover:bg-[rgb(var(--accent)/0.9)]",
            )}
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden={true} />
            ) : (
              <Send className="h-3.5 w-3.5" aria-hidden={true} />
            )}
            <span>Send</span>
          </Button>
        </div>
      </div>

      {/* Mode pills — Automation / Agent / Backtest. Click toggles
          the mode (click again to deselect → auto). The active pill
          gets a colored ring + light fill, matching its accent. */}
      <div className="flex items-center justify-center gap-2">
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
              className={cn(
                "group relative inline-flex items-center gap-1.5 rounded-full px-3 py-1.5",
                "text-[11px] font-medium transition-all duration-200",
                "border bg-card/50 backdrop-blur-md",
                "supports-[backdrop-filter]:bg-card/30",
                isActive
                  ? cn(
                      "ring-1",
                      m.accent,
                      "border-transparent text-foreground",
                    )
                  : cn(
                      "border-border/60 text-muted-foreground",
                      "hover:text-foreground hover:border-border",
                    ),
              )}
            >
              <Icon className="h-3 w-3" aria-hidden={true} />
              <span>{m.label}</span>
              {isActive && (
                <Sparkles
                  className="h-2.5 w-2.5 opacity-70"
                  aria-hidden={true}
                />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
