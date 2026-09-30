"use client";

/**
 * The page assistant's client: the stream, the warm-up, and the registry of
 * what each page is showing.
 *
 * The server fetches a page's data itself (indices, portfolio, the company's
 * quote and fundamentals…). What only the browser holds, the screen the user
 * just ran or their watchlist, each page publishes here as plain lines, and
 * the assistant sends the current page's lines with the question.
 */

import { chatStreamUrl } from "@/lib/chatStream";
import type { ScreenResultsPayload } from "@/components/chat/ScreenResultsCard";

export type AssistPage =
  | "home" | "portfolio" | "screener" | "agents" | "brokers" | "stock" | "chat";

export type AssistEvent =
  | { type: "start" }
  | { type: "thought"; part: string; delta: string }
  | { type: "tool_start"; name: string; label: string }
  | { type: "tool_done"; name: string; ok: boolean }
  | { type: "card"; card: ScreenResultsPayload }
  | { type: "delta"; text: string }
  | { type: "replace"; text: string }
  | { type: "done"; response: string; timing?: Record<string, number> }
  | { type: "error"; message: string };

// ── What each page shows ────────────────────────────────────────────────────

const visible = new Map<AssistPage, string[]>();

/** A page reports what it is displaying (≤40 short lines). */
export function publishVisible(page: AssistPage, lines: string[]): void {
  visible.set(page, lines.filter(Boolean).slice(0, 40));
}

export function readVisible(page: AssistPage): string[] {
  return visible.get(page) ?? [];
}

// ── Transport ───────────────────────────────────────────────────────────────

function base(): string {
  return chatStreamUrl().replace(/\/chat\/stream$/, "");
}

function headers(token: string | null): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

/** A user-facing sentence for any failure before the stream starts. The
 *  status text of a failed request never reaches the screen. */
export function failureText(status?: number): string {
  if (status === 402 || status === 429) {
    return "You've reached your limit for now. Please try again shortly, or check your plan in Settings.";
  }
  return "Pivot couldn't answer that just now. Please try again in a moment.";
}

const warmed = new Map<string, number>();

/** Fetch the page's data on the server ahead of the question (debounced to
 *  once per page per 45s; the server caches it for a minute). */
export function warmAssist(page: AssistPage, symbol: string | undefined, token: string | null): void {
  const key = `${page}:${symbol ?? ""}`;
  const now = Date.now();
  if ((warmed.get(key) ?? 0) > now - 45_000) return;
  warmed.set(key, now);
  void fetch(`${base()}/chat/assist/warm`, {
    method: "POST",
    headers: headers(token),
    body: JSON.stringify({ page, symbol }),
    cache: "no-store",
  }).catch(() => undefined);
}

export class AssistHttpError extends Error {
  constructor(readonly status: number) {
    super(`assist ${status}`);
  }
}

export async function* streamAssist(args: {
  message: string;
  history: { role: "user" | "assistant"; content: string }[];
  page: AssistPage;
  symbol?: string;
  threadId: string;
  token: string | null;
  signal: AbortSignal;
}): AsyncGenerator<AssistEvent> {
  const res = await fetch(`${base()}/chat/assist`, {
    method: "POST",
    headers: { ...headers(args.token), Accept: "text/event-stream" },
    body: JSON.stringify({
      message: args.message,
      history: args.history.slice(-8),
      page: args.page,
      symbol: args.symbol,
      visible: readVisible(args.page),
      thread_id: args.threadId,
    }),
    cache: "no-store",
    signal: args.signal,
  });
  if (!res.ok || !res.body) {
    if (res.status === 401 && typeof window !== "undefined") {
      try { window.localStorage.removeItem("pivot_jwt"); } catch { /* ignore */ }
      window.location.reload();
    }
    throw new AssistHttpError(res.status);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        try {
          yield JSON.parse(line.slice(5).trim()) as AssistEvent;
        } catch {
          /* a malformed chunk is skipped */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
