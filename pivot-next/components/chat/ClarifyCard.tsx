"use client";

/**
 * ClarifyCard — the dynamic clarifying-questions card (Workstream A, plan §2e).
 *
 * Renders ONE VOI-ranked question at a time, paginated "N of M":
 *   - the question prompt
 *   - 4-5 one-click numbered option chips/rows
 *   - a pencil "Something else" row that expands a free-text input (free_text)
 *   - a "Skip" button (skippable)
 *   - an "index+1 of total" pager with a back affordance
 *   - footer microcopy "…or reply directly"
 *
 * Answers travel back IN-BAND through the normal chat turn (plan §2d/§2f): the
 * card calls `onSendMessage(text)` which the parent wires to the chat `submit`.
 * The backend (`chat_service._try_resume_clarify` →
 * `clarify_engine.normalize_answer_into_slots`) parses the next user message:
 *   - a one-click pick sends the chip's `id` (canonical for the enum match);
 *   - "Something else" sends the free text the user typed;
 *   - "Skip" sends the literal "skip";
 *   - the user may also ignore the chips and type a sentence directly.
 * The travelling `session_slot_state` is round-tripped by the backend (it is
 * persisted in Redis keyed by conversation), so the FE only needs to send the
 * answer string — no slot-state echo required from the client.
 *
 * Reuses the DS v2 visual language of WorkflowDraftCard / OptionStrategyCard
 * (rounded-3xl card, sky "Question" chip, soft amber footer strip).
 */

import { useState } from "react";
import {
  ArrowRight,
  ChevronLeft,
  HelpCircle,
  Pencil,
  SkipForward,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { ClarifyCard as ClarifyCardData, ClarifyQuestion } from "@/lib/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type ClarifyCardProps = {
  card: ClarifyCardData;
  /**
   * Send the answer back as the next chat message. The parent wires this to
   * the chat `submit`; the backend resumes the N-of-M flow in-band.
   */
  onSendMessage: (text: string) => void;
  /** Disable inputs while a turn is in flight. */
  disabled?: boolean;
};

// ---------------------------------------------------------------------------
// ClarifyCard
// ---------------------------------------------------------------------------

export function ClarifyCard({
  card,
  onSendMessage,
  disabled = false,
}: ClarifyCardProps): React.ReactElement {
  // The backend re-surfaces a fresh single-question card on every advance, so
  // `index` is authoritative. We still clamp defensively.
  const index = Math.max(0, Math.min(card.index, Math.max(0, card.questions.length - 1)));
  const question: ClarifyQuestion | undefined = card.questions[index];

  const [freeTextOpen, setFreeTextOpen] = useState(false);
  const [freeText, setFreeText] = useState("");
  // Once an answer is dispatched the card freezes (the next turn renders its
  // own fresh card or the built strategy), avoiding a double-send.
  const [answered, setAnswered] = useState(false);

  const isLocked = disabled || answered;

  if (!question) {
    return (
      <div
        data-testid="clarify-card-empty"
        className="my-2 w-full max-w-[420px] rounded-3xl border border-border/50 bg-card px-5 py-4 text-[12px] text-muted-foreground"
      >
        No further questions — ask me to build it whenever you&apos;re ready.
      </div>
    );
  }

  function send(text: string): void {
    if (isLocked) return;
    const trimmed = text.trim();
    if (!trimmed) return;
    setAnswered(true);
    onSendMessage(trimmed);
  }

  function handlePick(optionId: string): void {
    // Send the chip's canonical id — the backend prefers the id for the enum
    // match and falls back to the label's synonyms.
    send(optionId);
  }

  function handleFreeTextSubmit(): void {
    if (!freeText.trim()) return;
    send(freeText);
  }

  function handleSkip(): void {
    // Literal "skip" matches the backend's _CLARIFY_SKIP_RE; the slot keeps its
    // default and stays flagged "(assumed …)".
    send("skip");
  }

  function handleBack(): void {
    if (isLocked || index === 0) return;
    // A back affordance to revise: re-surface the previous question by asking
    // the backend to step back one. Sent as plain text the resume parser reads
    // as a non-answer is risky, so instead we just nudge with "back" which the
    // user can also type; if the backend doesn't recognise it the LLM handles
    // it gracefully. Kept minimal — pagination is backend-driven.
    send("go back");
  }

  return (
    <div
      data-testid="clarify-card"
      role="region"
      aria-label={`Clarifying question ${index + 1} of ${card.total}`}
      className={cn(
        "my-2 w-full max-w-[420px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
      )}
      style={{
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      {/* HEADER — chip + pager */}
      <div className="flex flex-col gap-2.5 px-5 pt-4 pb-3">
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center gap-1 rounded-md bg-sky-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
            <HelpCircle className="h-3 w-3" aria-hidden="true" />
            Quick question
          </span>
          <div className="flex items-center gap-1.5">
            {index > 0 && (
              <button
                type="button"
                onClick={handleBack}
                disabled={isLocked}
                aria-label="Back to previous question"
                data-testid="clarify-back"
                className={cn(
                  "inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground",
                  "hover:bg-muted hover:text-foreground transition-colors",
                  "disabled:cursor-not-allowed disabled:opacity-40",
                )}
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            )}
            <span
              className="text-[10.5px] font-medium tabular-nums text-muted-foreground"
              data-testid="clarify-pager"
            >
              {index + 1} of {card.total}
            </span>
          </div>
        </div>

        {/* QUESTION PROMPT — the hero line */}
        <h3 className="text-[14.5px] leading-snug font-semibold tracking-tight text-foreground">
          {question.prompt}
        </h3>
      </div>

      {/* OPTION ROWS — numbered, one-click */}
      {question.options.length > 0 && (
        <div className="flex flex-col gap-1.5 px-4 pb-1">
          {question.options.map((opt, i) => (
            <button
              key={opt.id}
              type="button"
              onClick={() => handlePick(opt.id)}
              disabled={isLocked}
              data-testid={`clarify-option-${opt.id}`}
              className={cn(
                "group flex items-center gap-3 rounded-xl border border-border/50 bg-background px-3 py-2.5 text-left",
                "transition-colors hover:border-border hover:bg-muted",
                "focus:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              <span
                aria-hidden="true"
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-muted/70 text-[10.5px] font-semibold tabular-nums text-muted-foreground group-hover:bg-background"
              >
                {i + 1}
              </span>
              <span className="flex-1 text-[12.5px] leading-snug text-foreground">
                {opt.label}
              </span>
              <ArrowRight
                className="h-3.5 w-3.5 shrink-0 text-muted-foreground/0 transition-colors group-hover:text-muted-foreground"
                aria-hidden="true"
              />
            </button>
          ))}
        </div>
      )}

      {/* "SOMETHING ELSE" — pencil row that expands a free-text input */}
      {question.free_text && (
        <div className="px-4 pt-1">
          {!freeTextOpen ? (
            <button
              type="button"
              onClick={() => setFreeTextOpen(true)}
              disabled={isLocked}
              data-testid="clarify-something-else"
              className={cn(
                "flex w-full items-center gap-3 rounded-xl border border-dashed border-border/60 bg-background px-3 py-2.5 text-left",
                "transition-colors hover:border-border hover:bg-muted",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              <span
                aria-hidden="true"
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-muted/70 text-muted-foreground"
              >
                <Pencil className="h-3 w-3" aria-hidden="true" />
              </span>
              <span className="flex-1 text-[12.5px] text-muted-foreground">
                Something else…
              </span>
            </button>
          ) : (
            <div
              className="flex flex-col gap-2 rounded-xl border border-border/60 bg-background px-3 py-2.5"
              style={{
                animation: "draftCardIn-quartr 220ms cubic-bezier(0.22, 1, 0.36, 1) both",
              }}
            >
              <textarea
                autoFocus
                rows={2}
                value={freeText}
                disabled={isLocked}
                onChange={(e) => setFreeText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleFreeTextSubmit();
                  }
                }}
                placeholder="Type your answer…"
                data-testid="clarify-free-text-input"
                className={cn(
                  "w-full resize-none bg-transparent text-[12.5px] leading-snug text-foreground placeholder:text-muted-foreground/60",
                  "focus:outline-none",
                )}
                aria-label="Your answer"
              />
              <div className="flex items-center justify-end gap-1.5">
                <button
                  type="button"
                  onClick={() => {
                    setFreeTextOpen(false);
                    setFreeText("");
                  }}
                  disabled={isLocked}
                  className="rounded-md px-2.5 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleFreeTextSubmit}
                  disabled={isLocked || !freeText.trim()}
                  data-testid="clarify-free-text-submit"
                  className={cn(
                    "inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground transition-all",
                    "hover:bg-primary/90 active:scale-[0.98]",
                    "disabled:cursor-not-allowed disabled:opacity-60",
                  )}
                >
                  Send
                  <ArrowRight className="h-3 w-3" aria-hidden="true" />
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* SKIP — advances without answering (slot keeps its default) */}
      {question.skippable && (
        <div className="px-4 pt-2 pb-1">
          <button
            type="button"
            onClick={handleSkip}
            disabled={isLocked}
            data-testid="clarify-skip"
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] font-medium text-muted-foreground",
              "transition-colors hover:bg-muted/60 hover:text-foreground",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            <SkipForward className="h-3 w-3" aria-hidden="true" />
            Skip — I&apos;ll let you assume a sensible default
          </button>
        </div>
      )}

      {/* FOOTER — "…or reply directly" microcopy */}
      <div className="flex items-center gap-1.5 border-t border-border/40 bg-amber-50/40 px-5 py-2 dark:bg-amber-500/[0.04]">
        <Sparkles
          className="h-3 w-3 shrink-0 text-amber-600/80 dark:text-amber-400/80"
          aria-hidden="true"
        />
        <p className="text-[10.5px] leading-snug text-amber-700/90 dark:text-amber-300/90">
          …or reply directly in the chat — say &ldquo;just build it&rdquo; to skip the rest.
        </p>
      </div>
    </div>
  );
}
