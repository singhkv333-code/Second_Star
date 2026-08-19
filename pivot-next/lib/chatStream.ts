/**
 * The chat SSE client — one wire, several surfaces.
 *
 * This was `streamChat` inside ChatDemo, private to the Chat tab, back when
 * the Chat tab was the only thing that talked to the agent. It moved here
 * unchanged the moment a second surface needed it (the stock page's ask bar):
 * two copies of a stream parser is how one of them quietly stops matching the
 * backend's dialect. Every caller now shares the same base-URL resolution, the
 * same 401 handling and the same event union.
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
 * Where a chat turn is posted.
 *
 * DE-WIRED (temporary): when NEXT_PUBLIC_PIVOTTED_BASE is set, chat talks to
 * Pivotted — the research/analysis chat in `pivotted/` — instead of Pivot's
 * own /chat/stream. Pivotted speaks this exact SSE dialect (start /
 * tool_start / tool_done / delta / done{response}), so nothing downstream
 * changes and unsetting the env var restores Pivot.
 *
 * What is deliberately absent when de-wired: logiccard and raw_data, so no
 * card renders and nothing is committable. That is the point of the split —
 * Pivotted researches and does not build, register or deploy anything.
 *
 * Every surface reads it from here, so the two cannot disagree about which
 * brain the product is currently talking to.
 */
export function chatStreamUrl(): string {
  const pivotted =
    typeof process !== "undefined" && process.env.NEXT_PUBLIC_PIVOTTED_BASE;
  const base =
    (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
    "/api";
  const legacyBase = base.replace(/\/api\/?$/, "");
  return pivotted
    ? `${pivotted.replace(/\/$/, "")}/chat/stream`
    : `${legacyBase}/chat/stream`;
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
