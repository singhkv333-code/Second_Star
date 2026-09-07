"use client";

/**
 * ClarifyCard — dynamic clarifying-questions card with local paging.
 *
 * LOCAL-PAGING MODE (questions.length > 1 — all questions delivered up-front):
 *   The card pages through questions entirely in local state. On each answer:
 *     1. Record the answer ({slot, prompt, value, label}) locally.
 *     2. Call onSendMessage immediately so the backend slot-state stays in sync.
 *     3. If NOT the last question: animate a slide transition and advance the
 *        local index — NO new chat message is emitted.
 *     4. If it IS the last question: call onSendMessage, then call
 *        onFlowComplete(answers) so the parent can render a summary.
 *
 * ONE-AT-A-TIME FALLBACK (questions.length === 1 while total > 1):
 *   The backend is sending one question per card (legacy adaptive path).
 *   Behaves identically to the old per-card model: answer → backend emits the
 *   next card → ChatDemo appends it. `onFlowComplete` is NOT called (the
 *   backend drives the last-question detection in this path).
 *
 * Reuses the DS v2 visual language: rounded-3xl card, sky "Question" chip,
 * soft amber footer strip, `draftCardIn-quartr` entrance animation.
 */

import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ChevronLeft,
  HelpCircle,
  Pencil,
  SkipForward,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  ClarifyCard as ClarifyCardData,
  ClarifyAnswerRecord,
  ClarifyQuestion,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type ClarifyCardProps = {
  card: ClarifyCardData;
  /**
   * Send the answer back as the next chat message. Called for EVERY answer so
   * the backend's per-answer slot-state stays in sync regardless of paging mode.
   */
  onSendMessage: (text: string) => void;
  /**
   * Called after the LAST question is answered in local-paging mode.
   * The parent uses this to append a `clarify_summary` message instead of
   * waiting for the backend's next clarify card to do it.
   * NOT called in one-at-a-time fallback mode.
   */
  onFlowComplete?: (answers: ClarifyAnswerRecord[]) => void;
  /** Disable inputs while a turn is in flight. */
  disabled?: boolean;
};

// ---------------------------------------------------------------------------
// Slide direction helper
// ---------------------------------------------------------------------------

type SlideDir = "none" | "slide-out-left" | "slide-in-right";

// ---------------------------------------------------------------------------
// ClarifyCard
// ---------------------------------------------------------------------------

export function ClarifyCard({
  card,
  onSendMessage,
  onFlowComplete,
  disabled = false,
}: ClarifyCardProps): React.ReactElement {
  // Determine paging mode.
  // LOCAL_PAGING: all questions arrive at once (questions.length > 1).
  // ONE_AT_A_TIME: backend drives one question per card.
  const isLocalPaging = card.questions.length > 1;

  // Local state for paged flow.
  const [localIndex, setLocalIndex] = useState<number>(0);
  const [answers, setAnswers] = useState<ClarifyAnswerRecord[]>([]);
  const [slideDir, setSlideDir] = useState<SlideDir>("none");
  const [freeTextOpen, setFreeTextOpen] = useState(false);
  const [freeText, setFreeText] = useState("");
  // Once the final answer is dispatched the card freezes.
  const [done, setDone] = useState(false);

  // In one-at-a-time mode the authoritative index is card.index (backend-driven).
  const displayIndex = isLocalPaging ? localIndex : Math.max(0, Math.min(card.index, Math.max(0, card.questions.length - 1)));
  const question: ClarifyQuestion | undefined = card.questions[isLocalPaging ? localIndex : 0] ?? card.questions[displayIndex];

  // Track the previous localIndex so we can detect a question change and
  // reset the free-text panel.
  const prevLocalIndexRef = useRef(localIndex);
  useEffect(() => {
    if (prevLocalIndexRef.current !== localIndex) {
      prevLocalIndexRef.current = localIndex;
      setFreeTextOpen(false);
      setFreeText("");
    }
  }, [localIndex]);

  // Clear slide animation class once CSS finishes (~320ms).
  useEffect(() => {
    if (slideDir === "none") return;
    const t = window.setTimeout(() => setSlideDir("none"), 360);
    return () => window.clearTimeout(t);
  }, [slideDir]);

  const isLocked = disabled || done;

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

  // ── helpers ──────────────────────────────────────────────────────────────
  // `question` is guaranteed non-undefined here (guarded above), but TypeScript
  // does not narrow across nested function declarations — hence the assertion.
  const q = question;

  function recordAndAdvance(value: string, label: string): void {
    if (isLocked) return;
    const trimmed = value.trim();
    if (!trimmed) return;

    const newAnswer: ClarifyAnswerRecord = {
      slot: q.slot,
      prompt: q.prompt,
      value: trimmed,
      label,
    };
    const newAnswers = [...answers, newAnswer];

    const isLastQuestion = isLocalPaging
      ? localIndex >= card.questions.length - 1
      : true; // one-at-a-time: always treat as "last" (backend drives)

    if (!isLocalPaging) {
      // Legacy one-at-a-time: the backend drives — send the answer and it
      // re-surfaces the next card as its own message.
      onSendMessage(trimmed);
      return;
    }
    // Local paging: record locally and slide. We deliberately do NOT round-trip
    // per answer — that races the backend cursor and litters the chat with raw
    // chip ids. ALL answers are submitted once as a batch on completion
    // (onFlowComplete), which keeps the chat to one sliding card + a summary and
    // lets Back work purely on local state.

    if (isLastQuestion) {
      // Last question: freeze card and tell parent to render summary.
      setAnswers(newAnswers);
      setDone(true);
      onFlowComplete?.(newAnswers);
    } else {
      // More questions remain: slide to the next one.
      setAnswers(newAnswers);
      setSlideDir("slide-out-left");
      // Advance after a short delay so the exit animation starts first.
      window.setTimeout(() => {
        setLocalIndex((i) => i + 1);
        setSlideDir("slide-in-right");
      }, 180);
    }
  }

  function handlePick(optionId: string, optionLabel: string): void {
    recordAndAdvance(optionId, optionLabel);
  }

  function handleFreeTextSubmit(): void {
    if (!freeText.trim()) return;
    recordAndAdvance(freeText, freeText);
  }

  function handleSkip(): void {
    recordAndAdvance("skip", "Skipped");
  }

  function handleBack(): void {
    if (isLocked || !isLocalPaging || localIndex === 0) return;
    // Remove the last recorded answer and step back.
    setAnswers((prev) => prev.slice(0, -1));
    setSlideDir("slide-out-left");
    window.setTimeout(() => {
      setLocalIndex((i) => Math.max(0, i - 1));
      setSlideDir("slide-in-right");
    }, 180);
  }

  // In one-at-a-time mode keep the old "go back" text send for the backend.
  function handleBackLegacy(): void {
    if (isLocked || displayIndex === 0) return;
    onSendMessage("go back");
  }

  // ── pager values ─────────────────────────────────────────────────────────

  const pagerCurrent = isLocalPaging ? localIndex + 1 : displayIndex + 1;
  const pagerTotal = card.total;
  // Back is safe in local-paging mode now: answers live only in local state
  // until the batched submit on completion, so stepping back and re-answering
  // simply rewrites local state — nothing has been sent to the backend yet.
  const canGoBack = isLocalPaging ? localIndex > 0 : displayIndex > 0;

  // ── slide-direction CSS ───────────────────────────────────────────────────

  const slideStyle: React.CSSProperties = slideDir === "slide-out-left"
    ? { animation: "clarifySlideOut 180ms cubic-bezier(0.4, 0, 1, 1) both" }
    : slideDir === "slide-in-right"
      ? { animation: "clarifySlideIn 200ms cubic-bezier(0.22, 1, 0.36, 1) both" }
      : {};

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div
      data-testid="clarify-card"
      role="region"
      aria-label={`Clarifying question ${pagerCurrent} of ${pagerTotal}`}
      className={cn(
        "my-2 w-full max-w-[420px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
        done && "opacity-60 pointer-events-none",
      )}
      style={{
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      {/* Inline keyframes for slide transitions */}
      <style>{`
        @keyframes clarifySlideOut {
          from { opacity: 1; transform: translateX(0); }
          to   { opacity: 0; transform: translateX(-18px); }
        }
        @keyframes clarifySlideIn {
          from { opacity: 0; transform: translateX(18px); }
          to   { opacity: 1; transform: translateX(0); }
        }
      `}</style>

      {/* Question body — wrapped in a div that receives the slide animation */}
      <div style={slideStyle}>
        {/* HEADER — chip + pager */}
        <div className="flex flex-col gap-2.5 px-5 pt-4 pb-3">
          <div className="flex items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1 rounded-md bg-sky-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
              <HelpCircle className="h-3 w-3" aria-hidden="true" />
              Quick question
            </span>
            <div className="flex items-center gap-1.5">
              {canGoBack && !done && (
                <button
                  type="button"
                  onClick={isLocalPaging ? handleBack : handleBackLegacy}
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
                {pagerCurrent} of {pagerTotal}
              </span>
            </div>
          </div>

          {/* QUESTION PROMPT */}
          <h3 className="text-[14.5px] leading-snug font-semibold tracking-tight text-foreground">
            {question.prompt}
          </h3>
        </div>

        {/* OPTION ROWS */}
        {question.options.length > 0 && (
          <div className="flex flex-col gap-1.5 px-4 pb-1">
            {question.options.map((opt, i) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => handlePick(opt.id, opt.label)}
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

        {/* "SOMETHING ELSE" free-text */}
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

        {/* SKIP */}
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
      </div>

      {/* FOOTER */}
      <div className="flex items-center gap-1.5 border-t border-border/40 bg-amber-50/40 px-5 py-2 dark:bg-amber-500/[0.04]">
        <Sparkles
          className="h-3 w-3 shrink-0 text-amber-600/80 dark:text-amber-400/80"
          aria-hidden="true"
        />
        <p className="text-[10.5px] leading-snug text-amber-700/90 dark:text-amber-300/90">
          {done
            ? "All set — building your strategy now…"
            : "…or reply directly in the chat — say “just build it” to skip the rest."}
        </p>
      </div>
    </div>
  );
}
