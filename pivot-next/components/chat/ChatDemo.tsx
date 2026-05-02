"use client";

/**
 * ChatDemo — demo-grade chat surface for the Chat tab.
 *
 * Task #39 (Day 6). Replaces the static ChatPlaceholder with a working
 * demo: textarea → POST /api/propose-workflow → renders WorkflowDraftCard.
 * "Open in editor →" calls onOpenEditor so AppShell mounts AgentPanel
 * pre-filled with the draft.
 *
 * Intentionally minimal — this is a demo surface, not a full chat UI.
 * The real chatbot lives in the legacy frontend/ Vite app.
 */

import { useRef, useState } from "react";
import { Bot, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import {
  WorkflowDraftCard,
  draftToWorkflow,
  type WorkflowDraft,
} from "@/components/chat/WorkflowDraftCard";
import { proposeWorkflow } from "@/lib/api";
import { isError } from "@/lib/types";
import type { Workflow } from "@/lib/types";

const PLACEHOLDER_TEXT =
  "Describe your strategy, e.g. \"Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email.\"";

const EXAMPLE_PROMPT =
  "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email.";

type Message =
  | { kind: "user"; text: string }
  | { kind: "draft"; draft: WorkflowDraft }
  | { kind: "error"; message: string };

type ChatDemoProps = {
  /** Called when user clicks "Open in editor →" on a draft card. */
  onOpenEditor: (workflow: Workflow) => void;
};

export function ChatDemo({ onOpenEditor }: ChatDemoProps): React.ReactElement {
  const [intent, setIntent] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const submit = async (text: string): Promise<void> => {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    setMessages((prev) => [...prev, { kind: "user", text: trimmed }]);
    setIntent("");
    setLoading(true);

    try {
      const result = await proposeWorkflow(trimmed);
      if (isError(result)) {
        setMessages((prev) => [
          ...prev,
          { kind: "error", message: result.error.message },
        ]);
      } else {
        const draft: WorkflowDraft = {
          name: result.data.name,
          description: result.data.description ?? "",
          steps: result.data.steps.map((s) => ({
            step_type: s.step_type,
            label: s.label,
            config: s.config,
          })),
          rationale: result.data.rationale ?? "",
          warnings: result.data.warnings,
          _render_hint: "workflow_draft_card",
        };
        setMessages((prev) => [...prev, { kind: "draft", draft }]);
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
