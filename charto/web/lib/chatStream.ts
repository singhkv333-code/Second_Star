/**
 * The chat SSE client — Pivot's, carried over unedited except for its address.
 *
 * charto's company page asks questions about the company it is showing, and
 * the thing that answers them is Pivotted (`pivotted/`): charto's own read
 * tools plus fundamentals, ratios, filings and screens over every listed
 * company rather than the ~500 whose bars we store. Pivotted deliberately
 * speaks Pivot's SSE dialect — `start` / `tool_start` / `tool_done` / `delta`
 * / `done{response}` — precisely so this parser could move across without a
 * line of translation. Rewriting it here would be writing a second parser
 * against the same wire, which is how one of them stops matching it.
 *
 * Two things did change, and both are addresses rather than behaviour:
 * where a turn is posted, and what a 401 means (below).
 */

import type { LogicCard } from "@/components/chat/LogicCardChip";
import type { WorkflowDraft } from "@/components/chat/WorkflowDraftCard";

export type ChatHistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

/** Shape of the `done` event payload — identical to POST /chat response. */
export type ChatDonePayload = {
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

export type SseStart = { type: "start" };
export type SseToolStart = { type: "tool_start"; name: string };
export type SseToolDone = { type: "tool_done"; name: string; ok: boolean; error: string | null };
export type SseDelta = { type: "delta"; text: string };
export type SseReplace = { type: "replace"; text: string };
export type SseError = { type: "error"; message: string };
export type SseDone = { type: "done" } & ChatDonePayload;

export type SseEvent =
  | SseStart
  | SseToolStart
  | SseToolDone
  | SseDelta
  | SseReplace
  | SseError
  | SseDone;

/** Optional mode hint the FE attaches to a chat request. The backend
 * uses this to deterministically route tool selection — picking
 * Automation forces the immediate-order family, Agent forces
 * propose_workflow, Backtest forces the backtester paths.  When
 * `null` the backend falls back to its inferred classifier. */
export type ChatMode = "automation" | "agent" | "backtest" | null;

/**
 * Where a research turn is posted.
 *
 * Relative by default, and that is the whole design: the chart, this company
 * app and the data server are one origin already (serve.py in dev, nginx on
 * the VM), and `/research/` joins them by the same route. An absolute
 * `http://localhost:5176` would be a second origin — no shared session, a
 * preflight on every turn, and a link that works on exactly one laptop.
 *
 * Every surface reads it from here, so two of them cannot disagree about
 * which brain the page is talking to.
 */
export function chatStreamUrl(): string {
  const base =
    (typeof process !== "undefined" && process.env.NEXT_PUBLIC_RESEARCH_BASE) ||
    "/research";
  return `${base.replace(/\/$/, "")}/chat/stream`;
}

/**
 * Connects to POST /chat/stream and yields parsed SseEvent objects.
 * The caller owns the AbortController so it can cancel mid-stream.
 * On 401 this function wipes the JWT and reloads (same guard as callChat).
 */
export async function* streamChat(
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
  /**
   * The unsaved draft currently open in the editor (if any). Sent to
   * the backend so it amends exactly what the user sees, not its own
   * Redis copy. Absent when the editor is closed or showing a saved
   * workflow — in that case the backend falls back to its Redis state.
   */
  editorDraft?: WorkflowDraft | null,
  /**
   * Composer context attachments (the "+" menu / "@" mentions) — the
   * securities, positions and agents the user tagged. The backend weaves
   * them into the prompt as a grounding block. Empty/absent = none.
   */
  attachments?: Array<Record<string, unknown>> | null,
): AsyncGenerator<SseEvent> {
  const url = chatStreamUrl();

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
      // Per-session conversation_id — generated once per surface mount in
      // the React tree. The backend keys its Redis-stored active draft /
      // pending clarification under this id, so a fresh session id ensures
      // we never inherit yesterday's draft.
      conversation_id: conversationId,
      // Optional mode hint. When the user clicks Automation / Agent /
      // Backtest below the composer, we pass that intent to the
      // backend deterministically. Null = classifier decides.
      mode,
      // Reply-by-selecting: the highlighted excerpt the user is
      // replying to. Omitted entirely when there's no active quote.
      ...(quotedText ? { quoted_text: quotedText } : {}),
      // Editor-draft sync: when the editor is open on an unsaved draft,
      // send it so the backend amends exactly what the user sees.
      ...(editorDraft ? { editor_draft: editorDraft } : {}),
      // Composer context attachments — omitted entirely when none.
      ...(attachments && attachments.length ? { attachments } : {}),
    }),
    cache: "no-store",
    signal,
  });

  if (!res.ok) {
    // Pivot wiped its JWT and reloaded the page on a 401, because there a 401
    // means the session died and the reload lands on the login screen. Nothing
    // of that holds here: the research server reads no session at all, so a
    // 401 could only come from something in front of it, and a page that
    // reloads itself in response would take the reader's question with it and
    // arrive back in the same state. It is reported like any other status.
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
