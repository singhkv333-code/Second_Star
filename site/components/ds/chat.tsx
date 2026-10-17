"use client";

/**
 * Pivot design system — chat surface.
 *
 * The conversational vocabulary: user bubbles (soft grey pill, right
 * aligned — exactly the landing phone mock), assistant turns (plain
 * ink text, no bubble), the floating tagged prompt chip from the dark
 * landing panels, the pill input bar, and the three-bar thinking
 * ticker (reuses the wittyBar keyframes from globals.css).
 */

import * as React from "react";
import { cn } from "@/lib/utils";
import { MonoTag, type MonoTagTone } from "./primitives";

/* ────────────────────────────────────────────────────────────────────
 * Bubbles
 * ──────────────────────────────────────────────────────────────────── */

/**
 * One chat turn. Users get the rounded grey bubble; the assistant
 * speaks in plain text — quieter, editorial, no chrome.
 */
export function ChatBubble({
  role,
  className,
  children,
}: {
  role: "user" | "assistant";
  className?: string;
  children: React.ReactNode;
}) {
  if (role === "assistant") {
    return (
      <div
        className={cn("max-w-[85%]", className)}
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 13.5,
          lineHeight: 1.65,
          color: "var(--text-primary)",
        }}
      >
        {children}
      </div>
    );
  }
  return (
    <div className={cn("flex justify-end", className)}>
      <div
        className="max-w-[78%]"
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 13.5,
          lineHeight: 1.55,
          color: "var(--text-primary)",
          background: "var(--surface-active)",
          border: "1px solid var(--glass-border)",
          borderRadius: "16px 16px 4px 16px",
          padding: "9px 14px",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Prompt chip — the landing dark-panel artifact
 * ──────────────────────────────────────────────────────────────────── */

/**
 * Floating tagged prompt — a mono intent tag (ALERT / BACKTEST / RULE)
 * pinned above a small glass message card. Used on the landing dark
 * sections and as suggestion chips in the empty chat state.
 */
export function PromptChip({
  tag,
  tagTone = "ink",
  className,
  children,
  onClick,
}: {
  tag: string;
  tagTone?: MonoTagTone;
  className?: string;
  children: React.ReactNode;
  onClick?: () => void;
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={cn(
        "inline-flex flex-col items-start gap-0 text-left",
        onClick &&
          "cursor-pointer transition-transform duration-200 hover:-translate-y-0.5",
        className,
      )}
      style={{ transitionTimingFunction: "var(--ease-quartr)" }}
    >
      <span style={{ marginLeft: 10, position: "relative", zIndex: 1 }}>
        <MonoTag tone={tagTone} dot>
          {tag}
        </MonoTag>
      </span>
      <span
        style={{
          marginTop: -9,
          padding: "16px 14px 11px",
          maxWidth: 230,
          fontFamily: "var(--font-ui)",
          fontSize: 12.5,
          lineHeight: 1.5,
          color: "var(--text-secondary)",
          background: "var(--surface-hover)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
          display: "block",
        }}
      >
        {children}
      </span>
    </Tag>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Input bar
 * ──────────────────────────────────────────────────────────────────── */

/**
 * The chat input pill — hairline border, placeholder in tertiary,
 * circular ink send button. Presentational: wire value/onChange/onSend
 * from the caller.
 */
export function ChatInputBar({
  placeholder = "Ask Pivot anything…",
  value,
  onChange,
  onSend,
  className,
}: {
  placeholder?: string;
  value?: string;
  onChange?: (v: string) => void;
  onSend?: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn("flex items-center gap-2", className)}
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--glass-border-hover)",
        borderRadius: "var(--radius-pill)",
        padding: "6px 6px 6px 18px",
      }}
    >
      <input
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && onSend?.()}
        placeholder={placeholder}
        className="min-w-0 flex-1 bg-transparent outline-none"
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 13.5,
          color: "var(--text-primary)",
          caretColor: "var(--text-primary)",
        }}
      />
      <button
        type="button"
        onClick={onSend}
        aria-label="Send"
        className="grid shrink-0 place-items-center transition-transform duration-200 hover:scale-105 active:scale-95"
        style={{
          width: 32,
          height: 32,
          borderRadius: "50%",
          background: "var(--text-primary)",
          color: "var(--bg-base)",
          border: "none",
          cursor: "pointer",
          transitionTimingFunction: "var(--ease-quartr)",
        }}
      >
        <svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden>
          <path
            d="M7 12V2M7 2L2.5 6.5M7 2l4.5 4.5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Thinking ticker
 * ──────────────────────────────────────────────────────────────────── */

/**
 * The three-bar "reading the tape" thinking indicator + phrase. Bars
 * animate via the witty-bar classes already defined in globals.css.
 */
export function ThinkingTicker({
  phrase = "Reading the tape…",
  className,
}: {
  phrase?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <span className="flex items-end gap-[3px]" aria-hidden>
        <span className="witty-bar" />
        <span className="witty-bar" />
        <span className="witty-bar" />
      </span>
      <span
        className="witty-phrase"
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 13,
          color: "var(--text-secondary)",
        }}
      >
        {phrase}
      </span>
    </div>
  );
}