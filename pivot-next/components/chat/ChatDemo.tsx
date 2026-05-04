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
import { Bot, Loader2, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
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
import { InlineRunCard } from "@/components/chat/InlineRunCard";
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
async function* streamChat(
  userMessage: string,
  history: ChatHistoryMessage[],
  token: string | null,
  signal: AbortSignal,
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
    body: JSON.stringify({ messages, include_portfolio_context: true }),
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
 * Quick "show me X" ticker shortcut. Three accepted shapes:
 *   1. bare ticker: "RELIANCE"
 *   2. snapshot phrase: "show me reliance", "what about TCS?", "INFY snapshot"
 *   3. with leading $: "$RELIANCE"
 *
 * If no phrase pattern matches we fall through to the LLM. The earlier
 * regex-only path missed everything except #1 and forced users to type
 * exactly the ticker — too narrow.
 */
function extractTicker(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  // Bare ticker (the original case).
  if (/^[A-Z]{2,12}$/.test(trimmed)) return trimmed;

  // $RELIANCE
  const dollarMatch = /^\$([A-Z]{2,12})\b/.exec(trimmed);
  if (dollarMatch) return dollarMatch[1] ?? null;

  // Phrase patterns: require an explicit verb cue AND a short total
  // message length. Snapshot intents are phrases ("show me INFY",
  // "TCS quote"), workflow descriptions are sentences. Without this
  // length gate, a phrase like "...sells if price decreases..." inside
  // a long workflow description would match `/(\w)\s+price/` and
  // mis-route to the snapshot card with the conjunction as the
  // ticker (e.g. "no quote available for IF.NSE").
  if (trimmed.length > 40) return null;

  const lower = trimmed.toLowerCase();
  const phrasePatterns = [
    /^(?:show|show me|what about|how(?:'s| is| about)|tell me about|price of|quote for|snapshot of|chart for)\s+([a-z]{2,12})\b/,
    /^([a-z]{2,12})\s+(?:snapshot|quote|price|chart)\s*\??\s*$/,
  ];
  for (const re of phrasePatterns) {
    const m = re.exec(lower);
    if (m) {
      const raw = m[1];
      if (!raw) continue;
      const candidate = raw.toUpperCase();
      if (!STOPWORDS.has(candidate)) return candidate;
    }
  }
  return null;
}

/** Words that look like tickers but are clearly conversational filler. */
const STOPWORDS = new Set([
  // Greetings / acknowledgements
  "HI", "HELLO", "HEY", "OK", "OKAY", "YES", "NO", "PLEASE", "THANKS", "BYE",
  "SURE", "MAYBE",
  // Question words
  "WHAT", "WHEN", "WHY", "WHO", "HOW",
  // Conjunctions / prepositions (caught when message slips past length gate)
  "IF", "AT", "ON", "IN", "BY", "AS", "IS", "OF", "TO", "OR", "AND", "BUT",
  "FOR", "WITH", "FROM", "THE", "AN", "BE",
  // Verb-ish words that look like tickers
  "SHOW", "TELL", "BUY", "SELL", "RUN", "GET", "SET", "ADD",
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
  | { kind: "draft"; draft: WorkflowDraft }
  | { kind: "snapshot"; symbol: string }
  | { kind: "indicator_backtest"; payload: IndicatorBacktestPayload; intro: string }
  | { kind: "financial_backtest"; payload: FinancialBacktestPayload; intro: string }
  | { kind: "logic_card"; card: LogicCard; intro: string }
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

export function ChatDemo({ onOpenEditor, prefill, onPrefillConsumed }: ChatDemoProps): React.ReactElement {
  const [intent, setIntent] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  // Rolling history for the backend's conversation context
  const historyRef = useRef<ChatHistoryMessage[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Consume prefill once when it arrives
  useEffect(() => {
    if (prefill) {
      setIntent(prefill);
      onPrefillConsumed?.();
      textareaRef.current?.focus();
    }
  }, [prefill, onPrefillConsumed]);

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
        finalMessage = { kind: "draft", draft };
      } else {
        finalMessage = { kind: "assistant", text: data.response ?? "" };
      }
    } else if (hint === "logic_card" && data.logiccard) {
      finalMessage = {
        kind: "logic_card",
        card: data.logiccard,
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

    // Bare ticker shortcut — no API call.
    const ticker = extractTicker(trimmed);
    if (ticker) {
      setMessages((prev) => [...prev, { kind: "snapshot", symbol: ticker }]);
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

      const gen = streamChat(trimmed, historyRef.current, token, abortCtrl.signal);

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
    <div className="flex flex-col gap-4" data-testid="chat-demo">
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
        <div className="flex flex-col gap-3" data-testid="chat-messages">
          {messages.map((msg, idx) => {
            if (msg.kind === "user") {
              return (
                <div key={idx} className="flex justify-end">
                  <div className="max-w-sm rounded-xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground">
                    {msg.text}
                  </div>
                </div>
              );
            }
            if (msg.kind === "streaming") {
              return (
                <div key={idx} className="flex justify-start">
                  <div className="flex items-start gap-2 max-w-sm">
                    <Bot
                      className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
                      aria-hidden={true}
                    />
                    <div className="rounded-xl rounded-bl-sm border bg-card px-4 py-2.5 text-sm text-foreground">
                      {/* Single-line status row replaces the previous
                          tool-pill row. Shows what the model is doing
                          + an elapsed counter so the user can track
                          time themselves. */}
                      <StreamingStatusBar
                        startedAt={msg.startedAt}
                        tools={msg.tools}
                        hasText={msg.text.length > 0}
                      />
                      {msg.text ? (
                        <span className="mt-2 block whitespace-pre-wrap">
                          {msg.text}
                        </span>
                      ) : (
                        /* No text yet — show skeleton while first delta arrives */
                        <div className="mt-2 flex flex-col gap-1.5 py-0.5">
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
                <div key={idx} className="flex justify-start">
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
                  <div className="flex items-start gap-2 max-w-sm">
                    <Bot className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden={true} />
                    <div className="rounded-xl rounded-bl-sm border bg-card px-4 py-2.5 text-sm text-foreground">
                      {msg.text}
                    </div>
                  </div>
                </div>
              );
            }
            if (msg.kind === "snapshot") {
              return (
                <div key={idx} className="flex justify-start">
                  <StockSnapshotCard symbol={msg.symbol} />
                </div>
              );
            }
            if (msg.kind === "indicator_backtest") {
              return (
                <div key={idx} className="flex flex-col gap-2">
                  {msg.intro && (
                    <div className="flex justify-start">
                      <div className="flex items-start gap-2 max-w-md">
                        <Bot className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden={true} />
                        <div className="rounded-xl rounded-bl-sm border bg-card px-4 py-2.5 text-sm text-foreground">
                          {msg.intro}
                        </div>
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
                      <div className="flex items-start gap-2 max-w-md">
                        <Bot className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden={true} />
                        <div className="rounded-xl rounded-bl-sm border bg-card px-4 py-2.5 text-sm text-foreground">
                          {msg.intro}
                        </div>
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
                      <div className="flex items-start gap-2 max-w-md">
                        <Bot className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden={true} />
                        <div className="rounded-xl rounded-bl-sm border bg-card px-4 py-2.5 text-sm text-foreground">
                          {msg.intro}
                        </div>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-start">
                    <LogicCardChip card={msg.card} />
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

      {/* Input area */}
      <div className="rounded-xl border bg-card shadow-sm">
        <Textarea
          ref={textareaRef}
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={PLACEHOLDER_TEXT}
          className="min-h-[80px] resize-none rounded-b-none border-0 border-b focus-visible:ring-0 focus-visible:ring-offset-0 text-sm"
          disabled={loading}
          data-testid="chat-textarea"
          aria-label="Describe your strategy"
        />
        <div className="flex items-center justify-between px-3 py-2">
          <p className="text-[11px] text-muted-foreground">
            Cmd+Enter to send
          </p>
          <Button
            size="sm"
            onClick={() => void submit(intent)}
            disabled={!intent.trim() || loading}
            data-testid="chat-submit-btn"
            aria-label="Send"
          >
            <Send className="mr-1.5 h-3.5 w-3.5" aria-hidden={true} />
            Send
          </Button>
        </div>
      </div>
    </div>
  );
}
