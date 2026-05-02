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
  logiccard?: unknown;
  raw_data?: {
    _render_hint?: string;
    // Workflow draft card payload
    name?: string;
    description?: string;
    steps?: Array<{ step_type: string; label: string | null; config: Record<string, unknown> }>;
    rationale?: string;
    warnings?: string[];
    // Indicator backtest chart payload (typed loose here; the card has
    // its own narrow type and validates at the boundary)
    symbol?: string;
    indicator?: "rsi" | "sma" | "ema";
    indicator_period?: number;
    operator?: string;
    threshold?: number;
    period_label?: string;
    price_curve?: Array<{ t: string; v: number }>;
    equity_curve?: Array<{ t: string; v: number }>;
    indicator_curve?: Array<{ t: string; v: number }>;
    signals?: Array<{ t: string; side: "buy" | "sell"; price: number; indicator_value: number | null }>;
    metrics?: Record<string, number>;
    bench_buy_hold_return_pct?: number;
  } | null;
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
    const text = await res.text();
    throw new Error(`Chat error ${res.status}: ${text.slice(0, 200)}`);
  }

  return res.json() as Promise<ChatApiResponse>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Returns the symbol if the input is a bare NSE ticker (2-12 uppercase letters). */
function extractTicker(text: string): string | null {
  const trimmed = text.trim();
  if (/^[A-Z]{2,12}$/.test(trimmed)) return trimmed;
  return null;
}

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
      if (
        rawData &&
        rawData._render_hint === "workflow_draft_card" &&
        rawData.name &&
        rawData.steps
      ) {
        const draft: WorkflowDraft = {
          name: rawData.name,
          description: rawData.description ?? "",
          steps: (rawData.steps ?? []).map((s) => ({
            step_type: s.step_type,
            label: s.label,
            config: s.config,
          })),
          rationale: rawData.rationale ?? "",
          warnings: rawData.warnings ?? [],
          _render_hint: "workflow_draft_card",
        };
        setMessages((prev) => [...prev, { kind: "draft", draft }]);
      } else if (
        rawData &&
        rawData._render_hint === "indicator_backtest_chart" &&
        rawData.symbol &&
        rawData.indicator &&
        rawData.price_curve &&
        rawData.equity_curve &&
        rawData.indicator_curve &&
        rawData.signals &&
        rawData.metrics
      ) {
        // Indicator backtest result — render the chart card. The
        // assistant text becomes a one-line intro above the card.
        const payload: IndicatorBacktestPayload = {
          symbol: rawData.symbol,
          indicator: rawData.indicator,
          indicator_period: rawData.indicator_period ?? 14,
          operator: rawData.operator ?? "<",
          threshold: rawData.threshold ?? 0,
          period_label: rawData.period_label ?? "",
          price_curve: rawData.price_curve,
          equity_curve: rawData.equity_curve,
          indicator_curve: rawData.indicator_curve,
          signals: rawData.signals,
          metrics: {
            total_return_pct: rawData.metrics.total_return_pct ?? 0,
            cagr_pct: rawData.metrics.cagr_pct ?? 0,
            max_drawdown_pct: rawData.metrics.max_drawdown_pct ?? 0,
            hit_rate_pct: rawData.metrics.hit_rate_pct ?? 0,
            n_trades: rawData.metrics.n_trades ?? 0,
            n_wins: rawData.metrics.n_wins ?? 0,
            starting_capital: rawData.metrics.starting_capital ?? 0,
            ending_value: rawData.metrics.ending_value ?? 0,
          },
          bench_buy_hold_return_pct: rawData.bench_buy_hold_return_pct ?? 0,
        };
        setMessages((prev) => [
          ...prev,
          {
            kind: "indicator_backtest",
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
