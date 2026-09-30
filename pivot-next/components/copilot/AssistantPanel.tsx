"use client";

/**
 * AssistantPanel — the quick assistant beside every page.
 *
 * The prompt bar under Home, Portfolio, Screener, Strategy and the company
 * page opens this panel. It talks to /chat/assist, which answers from the
 * page's own data (fetched on the server while the user types) and reaches
 * for a tool only when that data falls short. Depth lives in the full Chat;
 * "Open in Chat" carries the question there.
 *
 * What the user sees while it works is what is actually happening: reading
 * the page, then each lookup by name, then the answer. When the model
 * reasons, its summaries stream under the steps and fold into "Thought for
 * Ns" afterwards. A failure is one plain sentence from the server, never a
 * status code or a trace.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Check, ChevronDown, Loader2, MessageSquarePlus, Square, X } from "lucide-react";
import AssistantMessage from "@/components/chat/AssistantMessage";
import { ScreenResultsCard, type ScreenResultsPayload } from "@/components/chat/ScreenResultsCard";
import { getAccessToken } from "@/lib/authToken";
import {
  AssistHttpError, failureText, streamAssist, warmAssist, type AssistPage,
} from "@/lib/assist";

type Step = { label: string; name?: string; done: boolean; ok: boolean };

type Turn = {
  id: string;
  question: string;
  steps: Step[];
  thought: string;
  thoughtMs?: number;
  answer: string;
  card?: ScreenResultsPayload;
  error?: string;
  status: "working" | "done" | "error";
  ms?: number;
};

const THREAD_KEY = "pivot:assist-thread";

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function loadThread(): { id: string; turns: Turn[] } {
  try {
    const raw = sessionStorage.getItem(THREAD_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as { id: string; turns: Turn[] };
      // A turn interrupted by a reload is shown as ended, not spinning.
      return {
        id: parsed.id,
        turns: parsed.turns.map((t) => (t.status === "working"
          ? { ...t, status: "error", error: "This answer was interrupted. Please ask again." }
          : t)),
      };
    }
  } catch { /* unavailable */ }
  return { id: newId(), turns: [] };
}

function suggestions(page: AssistPage, symbol?: string): string[] {
  switch (page) {
    case "stock":
      return [`Is ${symbol} expensive right now?`, `How has ${symbol} done this year?`,
        `What's the latest news on ${symbol}?`];
    case "portfolio":
      return ["How is my portfolio doing today?", "What's my biggest loser?",
        "How concentrated am I by sector?"];
    case "screener":
      return ["Summarise this screen", "Which of these looks cheapest?",
        "Find IT stocks above their 200-day average"];
    case "agents":
      return ["Which of my strategies are running?", "Where do I backtest a strategy?"];
    case "brokers":
      return ["How do I connect a broker?", "Are my orders live or simulated?"];
    default:
      return ["How are markets doing today?", "How is my portfolio doing?",
        "Where do I build a strategy?"];
  }
}

export function AssistantPanel({
  page,
  symbol,
  contextLabel,
  seed,
  onSeedConsumed,
  onOpenFullChat,
  onClose,
}: {
  page: AssistPage;
  symbol?: string;
  contextLabel: string;
  seed?: string;
  onSeedConsumed?: () => void;
  onOpenFullChat: (question?: string) => void;
  onClose: () => void;
}): React.ReactElement {
  const [thread, setThread] = useState<{ id: string; turns: Turn[] }>(() => ({ id: "", turns: [] }));
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => setThread(loadThread()), []);
  useEffect(() => {
    if (!thread.id) return;
    try { sessionStorage.setItem(THREAD_KEY, JSON.stringify(thread)); } catch { /* full */ }
  }, [thread]);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread.turns]);

  // Warm the page's data the moment the panel shows for it.
  useEffect(() => {
    void getAccessToken().then((t) => warmAssist(page, symbol, t));
  }, [page, symbol]);

  const patch = useCallback((id: string, fn: (t: Turn) => Turn): void => {
    setThread((th) => ({ ...th, turns: th.turns.map((t) => (t.id === id ? fn(t) : t)) }));
  }, []);

  const ask = useCallback(async (question: string): Promise<void> => {
    const q = question.trim();
    if (!q || busy) return;
    const id = newId();
    const started = performance.now();
    let thoughtStart: number | undefined;
    const history = thread.turns
      .filter((t) => t.status === "done")
      .flatMap((t) => [
        { role: "user" as const, content: t.question },
        { role: "assistant" as const, content: t.answer },
      ]);
    setThread((th) => ({
      ...th,
      turns: [...th.turns, {
        id, question: q, thought: "", answer: "", status: "working",
        steps: [{ label: page === "stock" ? `Reading ${symbol}` : "Reading this page", done: false, ok: true }],
      }],
    }));
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const settleFirst = (t: Turn): Turn => ({
      ...t, steps: t.steps.map((s, i) => (i === 0 ? { ...s, done: true } : s)),
    });
    try {
      const token = await getAccessToken();
      for await (const ev of streamAssist({
        message: q, history, page, symbol, threadId: thread.id || id, token, signal: ctrl.signal,
      })) {
        switch (ev.type) {
          case "thought":
            thoughtStart ??= performance.now();
            patch(id, (t) => settleFirst({ ...t, thought: t.thought + ev.delta }));
            break;
          case "tool_start":
            patch(id, (t) => settleFirst({
              ...t, steps: [...t.steps, { label: ev.label, name: ev.name, done: false, ok: true }],
            }));
            break;
          case "tool_done":
            patch(id, (t) => {
              const i = t.steps.findIndex((s) => s.name === ev.name && !s.done);
              if (i < 0) return t;
              const steps = t.steps.slice();
              steps[i] = { ...steps[i]!, done: true, ok: ev.ok };
              return { ...t, steps };
            });
            break;
          case "card":
            patch(id, (t) => ({ ...t, card: ev.card }));
            break;
          case "delta":
            patch(id, (t) => settleFirst({
              ...t,
              answer: t.answer + ev.text,
              thoughtMs: t.thoughtMs ?? (thoughtStart ? performance.now() - thoughtStart : undefined),
            }));
            break;
          case "replace":
            patch(id, (t) => ({ ...t, answer: ev.text }));
            break;
          case "done":
            patch(id, (t) => ({
              ...settleFirst(t), answer: ev.response || t.answer, status: "done",
              ms: performance.now() - started,
            }));
            break;
          case "error":
            patch(id, (t) => ({ ...settleFirst(t), status: "error", error: ev.message }));
            break;
        }
      }
    } catch (err) {
      const aborted = ctrl.signal.aborted;
      patch(id, (t) => ({
        ...settleFirst(t),
        status: aborted && t.answer ? "done" : "error",
        error: aborted
          ? (t.answer ? undefined : "Stopped.")
          : failureText(err instanceof AssistHttpError ? err.status : undefined),
      }));
    } finally {
      // A stream that closed without `done` or `error` still ends the turn.
      patch(id, (t) => (t.status === "working"
        ? (t.answer ? { ...t, status: "done", ms: performance.now() - started }
          : { ...t, status: "error", error: failureText() })
        : t));
      setBusy(false);
      abortRef.current = null;
    }
  }, [busy, page, patch, symbol, thread.id, thread.turns]);

  // A question typed into the page's prompt bar lands here.
  useEffect(() => {
    if (!seed || !thread.id) return;
    onSeedConsumed?.();
    void ask(seed);
  }, [seed, thread.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const focus = (): void => inputRef.current?.focus();
    window.addEventListener("pivot:focus-composer", focus);
    return () => window.removeEventListener("pivot:focus-composer", focus);
  }, []);

  const send = (): void => {
    const q = value;
    setValue("");
    void ask(q);
  };
  const lastQuestion = thread.turns[thread.turns.length - 1]?.question;
  const ideas = useMemo(() => suggestions(page, symbol), [page, symbol]);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="assistant-panel">
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold text-foreground">Pivot Assistant</div>
          <div className="truncate text-[12px] text-muted-foreground">{contextLabel}</div>
        </div>
        <button
          type="button"
          className="copilot-panel-action"
          onClick={() => onOpenFullChat(lastQuestion)}
          title="Open in Chat for a deeper answer"
          aria-label="Open in Chat"
        >
          <MessageSquarePlus size={16} aria-hidden />
        </button>
        <button
          type="button"
          className="copilot-panel-action"
          onClick={() => { abortRef.current?.abort(); setThread({ id: newId(), turns: [] }); }}
          title="New conversation"
          aria-label="New conversation"
          disabled={thread.turns.length === 0}
        >
          <span className="text-[12px] font-medium">New</span>
        </button>
        <button type="button" className="copilot-panel-action" onClick={onClose} aria-label="Close assistant">
          <X size={17} aria-hidden />
        </button>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {thread.turns.length === 0 ? (
          <div className="flex flex-col gap-2">
            <p className="text-[13px] text-muted-foreground">
              Ask anything about this page, the market or where to find something in Pivot.
            </p>
            {ideas.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => void ask(s)}
                className="rounded-lg border border-border px-3 py-2 text-left text-[13px] text-foreground hover:bg-muted/50"
              >
                {s}
              </button>
            ))}
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            {thread.turns.map((t) => <TurnView key={t.id} turn={t} onRetry={() => void ask(t.question)} />)}
          </div>
        )}
      </div>

      <div className="border-t border-border/60 p-3">
        <div className="flex items-end gap-2 rounded-xl border border-border bg-background px-3 py-2">
          <textarea
            ref={inputRef}
            rows={1}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={page === "stock" ? `Ask about ${symbol}…` : "Ask Pivot…"}
            aria-label="Ask the assistant"
            className="max-h-32 min-h-[24px] flex-1 resize-none bg-transparent text-[14px] leading-6 outline-none"
          />
          {busy ? (
            <button
              type="button"
              onClick={() => abortRef.current?.abort()}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-foreground text-background"
              aria-label="Stop"
            >
              <Square size={12} fill="currentColor" aria-hidden />
            </button>
          ) : (
            <button
              type="button"
              onClick={send}
              disabled={!value.trim()}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-foreground text-background disabled:opacity-30"
              aria-label="Send"
            >
              <ArrowUp size={16} strokeWidth={2.2} aria-hidden />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function TurnView({ turn, onRetry }: { turn: Turn; onRetry: () => void }): React.ReactElement {
  const [open, setOpen] = useState(false);
  const working = turn.status === "working";
  const lookups = turn.steps.length - 1;
  const showSteps = working || open;
  const summary = [
    turn.thoughtMs ? `Thought for ${Math.max(1, Math.round(turn.thoughtMs / 1000))}s` : null,
    lookups > 0 ? `${lookups} lookup${lookups === 1 ? "" : "s"}` : "From this page",
    turn.ms ? `${(turn.ms / 1000).toFixed(1)}s` : null,
  ].filter(Boolean).join(" · ");

  return (
    <div className="flex flex-col gap-2">
      <div className="self-end rounded-2xl bg-muted px-3.5 py-2 text-[14px] text-foreground">{turn.question}</div>

      {!working && turn.status === "done" && (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="inline-flex items-center gap-1 self-start text-[12px] text-muted-foreground hover:text-foreground"
          aria-expanded={open}
        >
          {summary}
          <ChevronDown size={12} className={open ? "rotate-180" : ""} aria-hidden />
        </button>
      )}

      {showSteps && (
        <ul className="flex flex-col gap-1" aria-live="polite">
          {turn.steps.map((s, i) => (
            <li key={i} className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
              {s.done ? (
                s.ok ? <Check size={12} className="text-[var(--color-profit)]" aria-hidden />
                  : <X size={12} className="text-[var(--color-loss)]" aria-hidden />
              ) : (
                <Loader2 size={12} className="animate-spin" aria-hidden />
              )}
              <span>{s.label}{s.done && !s.ok ? " (unavailable)" : ""}</span>
            </li>
          ))}
          {turn.thought && (
            <li className="whitespace-pre-wrap border-l-2 border-border pl-3 text-[12.5px] leading-5 text-muted-foreground">
              {turn.thought.replace(/\*\*(.+?)\*\*/g, "$1\n").trim()}
            </li>
          )}
        </ul>
      )}

      {turn.card && <ScreenResultsCard payload={turn.card} />}
      {turn.answer && <AssistantMessage text={turn.answer} className="text-[14px] leading-6" />}

      {turn.status === "error" && turn.error && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-[13px] text-muted-foreground">
          <span>{turn.error}</span>
          {turn.error !== "Stopped." && (
            <button type="button" onClick={onRetry} className="shrink-0 font-medium text-foreground hover:underline">
              Try again
            </button>
          )}
        </div>
      )}
    </div>
  );
}
