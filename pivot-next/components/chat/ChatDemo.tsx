"use client";

/**
 * ChatDemo — real chat surface wired to POST /chat.
 *
 * Messages → POST /chat (legacy router, no /api prefix).
 * When the response includes a tool_call/raw_data with _render_hint:
 *   "workflow_draft_card" → renders WorkflowDraftCard inline.
 * Bare NSE tickers → renders StockSnapshotCard.
 *
 * Conversation ID is derived per-user from the backend (u{user_id} format).
 * Client carries rolling history so backend has context.
 * Conversations sidebar is wired in AppShell via GET /api/conversations.
 */

import { useEffect, useRef, useState } from "react";
import { Bot, Send } from "lucide-react";
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
// Backend chat types (POST /chat — legacy router at /chat, no /api prefix)
// ---------------------------------------------------------------------------

type ChatHistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

type ChatApiResponse = {
  response: string;
  tools_called?: string[];
  /** Backend builds a structured LogicCard for ~30 tools (orders, GTT,
   * SL, OCO, dip-buy, basket, squareoff, SIP create…). When set, the
   * frontend renders LogicCardChip and the user can confirm-register. */
  logiccard?: LogicCard | null;
  /**
   * Loosely typed bag — every chat tool ships its own payload shape and
   * we narrow inside the dispatch via `_render_hint`. Payload types
   * (WorkflowDraft, IndicatorBacktestPayload, FinancialBacktestPayload,
   * LogicCard) live with their respective card components.
   */
  raw_data?:
    | (Record<string, unknown> & { _render_hint?: string })
    | null;
};

/**
 * Call POST /chat — the legacy chat router (no /api prefix).
 * Carries rolling history so the backend has conversation context.
 */
async function callChat(
  userMessage: string,
  history: ChatHistoryMessage[],
  token: string | null,
): Promise<ChatApiResponse> {
  const base =
    (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
    "/api";
  const legacyBase = base.replace(/\/api\/?$/, "");
  const url = `${legacyBase}/chat`;

  const messages: ChatHistoryMessage[] = [
    ...history,
    { role: "user", content: userMessage },
  ];

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify({
      messages,
      include_portfolio_context: true,
    }),
    cache: "no-store",
  });

  if (!res.ok) {
    // Bounce stale tokens back through the auth gate, same as
    // lib/api.ts:_doRequest — without this the user just sees
    // "Chat error 401: ..." over and over after the JWT expires.
    if (res.status === 401 && typeof window !== "undefined") {
      try {
        window.localStorage.removeItem("pivot_jwt");
      } catch {
        /* embed-locked storage; safe to ignore */
      }
      window.location.reload();
    }
    const text = await res.text();
    throw new Error(`Chat error ${res.status}: ${text.slice(0, 200)}`);
  }

  return res.json() as Promise<ChatApiResponse>;
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
  if (dollarMatch) return dollarMatch[1];

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
      const candidate = m[1].toUpperCase();
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

type Message =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
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

  const submit = async (text: string): Promise<void> => {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    setMessages((prev) => [...prev, { kind: "user", text: trimmed }]);
    setIntent("");

    // If input is a bare ticker symbol, show snapshot card instead (no API call).
    const ticker = extractTicker(trimmed);
    if (ticker) {
      setMessages((prev) => [...prev, { kind: "snapshot", symbol: ticker }]);
      return;
    }

    setLoading(true);

    try {
      const token = getToken();
      const data = await callChat(trimmed, historyRef.current, token);

      // Update rolling history
      historyRef.current = [
        ...historyRef.current,
        { role: "user" as const, content: trimmed },
        { role: "assistant" as const, content: data.response ?? "" },
      ].slice(-20); // cap at 20 messages to avoid huge payloads

      // Check for workflow draft render hint in raw_data
      const rawData = data.raw_data;
      const hint = rawData?._render_hint;

      if (hint === "workflow_draft_card" && rawData) {
        // Workflow draft proposal — propose_workflow tool result.
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
          setMessages((prev) => [...prev, { kind: "draft", draft }]);
        } else if (data.response) {
          setMessages((prev) => [...prev, { kind: "assistant", text: data.response }]);
        }
      } else if (hint === "logic_card" && data.logiccard) {
        // Generic LogicCard path — covers ~30 chat tools (orders, GTT,
        // SL, OCO, dip-buy, basket, squareoff, SIP create, etc.). The
        // assistant text becomes the intro bubble; the card carries
        // the structured details + confirm payload.
        setMessages((prev) => [
          ...prev,
          {
            kind: "logic_card",
            card: data.logiccard as LogicCard,
            intro: data.response ?? "",
          },
        ]);
      } else if (hint === "indicator_backtest_chart" && rawData) {
        // Indicator backtest result (yfinance + technical indicator).
        const payload = rawData as unknown as IndicatorBacktestPayload;
        setMessages((prev) => [
          ...prev,
          {
            kind: "indicator_backtest",
            payload,
            intro: data.response ?? "",
          },
        ]);
      } else if (hint === "financial_backtest_chart" && rawData) {
        // Fundamentals (SQL DB) backtest — same render hint emits this
        // shape from backend/routers/chat.py::_run_expr_backtest.
        const payload = rawData as unknown as FinancialBacktestPayload;
        setMessages((prev) => [
          ...prev,
          {
            kind: "financial_backtest",
            payload,
            intro: data.response ?? "",
          },
        ]);
      } else if (data.response) {
        setMessages((prev) => [...prev, { kind: "assistant", text: data.response }]);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Network error";
      setMessages((prev) => [...prev, { kind: "error", message: msg }]);
    } finally {
      setLoading(false);
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

          {/* Loading state — bot is "typing" */}
          {loading && (
            <div
              className="flex justify-start"
              data-testid="chat-loading"
              aria-live="polite"
              aria-label="Generating workflow draft"
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
