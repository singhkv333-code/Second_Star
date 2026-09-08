/**
 * Related next questions, offered under a finished answer.
 *
 * The Perplexity affordance, and deliberately its geometry: a hairline above
 * the block, one question per row, a small turn-down arrow in the left gutter,
 * hairlines between rows, and the whole row — not just the text — as the hit
 * target. Hover lifts the row onto a faint tint and brings the arrow and text
 * up to full foreground.
 *
 * Fetched AFTER the answer has rendered (see the hook below), so it never
 * delays the reply. Empty, still loading, or failed all render nothing: the
 * answer is already complete without it.
 */
"use client";

import { CornerDownRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { getAccessTokenSync } from "@/lib/authToken";

export function FollowUpSuggestions({
  suggestions,
  onPick,
}: {
  suggestions: string[];
  onPick: (q: string) => void;
}): React.ReactElement | null {
  if (suggestions.length === 0) return null;

  return (
    <div
      className="mt-6 w-full"
      data-testid="followup-suggestions"
      aria-label="Related questions"
    >
      <div style={{ height: 1, background: "var(--glass-border)" }} />
      {suggestions.map((q, i) => (
        <FollowUpRow
          key={`${i}-${q}`}
          question={q}
          last={i === suggestions.length - 1}
          onPick={onPick}
        />
      ))}
    </div>
  );
}

function FollowUpRow({
  question,
  last,
  onPick,
}: {
  question: string;
  last: boolean;
  onPick: (q: string) => void;
}): React.ReactElement {
  const [hovered, setHovered] = useState(false);

  return (
    <button
      type="button"
      onClick={() => onPick(question)}
      data-testid="followup-item"
      className="flex w-full items-start gap-3 py-3.5 text-left"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        // The hairline sits on every row but the last, so the block reads as
        // one list rather than a stack of separate buttons.
        borderBottom: last ? "none" : "1px solid var(--glass-border)",
        background: hovered ? "var(--surface-hover)" : "transparent",
        cursor: "pointer",
        transition:
          "background-color 0.15s var(--ease-quartr), color 0.15s var(--ease-quartr)",
      }}
    >
      <CornerDownRight
        className="mt-0.5 shrink-0"
        size={17}
        strokeWidth={1.75}
        aria-hidden={true}
        style={{
          color: hovered ? "var(--text-primary)" : "var(--text-tertiary)",
          transition: "color 0.15s var(--ease-quartr)",
        }}
      />
      <span
        className="text-[15px] leading-6"
        style={{
          color: hovered ? "var(--text-primary)" : "var(--text-secondary)",
          transition: "color 0.15s var(--ease-quartr)",
        }}
      >
        {question}
      </span>
    </button>
  );
}

/**
 * Ask the backend what to suggest, once, after `answer` settles.
 *
 * Keyed on the question+answer pair: a retry or an edit produces a different
 * answer and therefore a fresh set, while a re-render of the same message does
 * not re-fetch. An in-flight request is aborted when the key changes, so a
 * fast retry cannot land the previous answer's suggestions under the new one.
 */
export function useFollowUps(
  question: string,
  answer: string,
  enabled: boolean,
): string[] {
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const keyRef = useRef<string>("");

  useEffect(() => {
    // Short answers ("hi", a price) have no natural next question that isn't
    // a product tour, which is exactly what this should not become.
    if (!enabled || !answer || answer.length < 200) {
      setSuggestions([]);
      return;
    }
    const key = `${question} ${answer}`;
    if (keyRef.current === key) return;
    keyRef.current = key;
    setSuggestions([]);

    const ac = new AbortController();
    // Same base as the chat stream itself (chatStreamUrl) — the followups
    // route sits beside /chat/stream on the same backend, and reading it from
    // one place is what stops the two from disagreeing about which brain the
    // product is talking to.
    const base = (
      (typeof process !== "undefined" &&
        process.env.NEXT_PUBLIC_PIVOT_CHAT_BASE) ||
      "/pivot-chat"
    ).replace(/\/$/, "");
    void (async () => {
      try {
        const token = getAccessTokenSync();
        const res = await fetch(`${base}/chat/followups`, {
          method: "POST",
          signal: ac.signal,
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ question, answer }),
        });
        if (!res.ok) return;
        const data: { suggestions?: unknown } = await res.json();
        if (Array.isArray(data.suggestions)) {
          setSuggestions(
            data.suggestions.filter((s): s is string => typeof s === "string"),
          );
        }
      } catch {
        // Best-effort by design — the answer stands without suggestions.
      }
    })();
    return () => ac.abort();
  }, [question, answer, enabled]);

  return suggestions;
}
