"use client";

import { useEffect, useRef } from "react";
import { ArrowUp, Square } from "lucide-react";
import { PromptInput, PromptInputAction, PromptInputActions, PromptInputTextarea } from "@/components/prompt-kit/prompt-input";

/** Shared Prompt Kit composer for the page assistant and collapsed ask bar. */
export function AssistantComposer({ value, onValueChange, onSubmit, placeholder,
  ariaLabel, busy = false, onStop, onFocus, focusEvent, compact = false, visible = true,
}: {
  value: string;
  onValueChange: (value: string) => void;
  onSubmit: () => void;
  placeholder: string;
  ariaLabel: string;
  busy?: boolean;
  onStop?: () => void;
  onFocus?: () => void;
  focusEvent: string;
  compact?: boolean;
  visible?: boolean;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const focus = () => {
      if (visible) wrapperRef.current?.querySelector("textarea")?.focus();
    };
    window.addEventListener(focusEvent, focus);
    return () => window.removeEventListener(focusEvent, focus);
  }, [focusEvent, visible]);

  return (
    <div ref={wrapperRef} className={compact ? "assistant-composer-wrap assistant-composer-wrap--compact" : "assistant-composer-wrap"}>
      <PromptInput value={value} onValueChange={onValueChange} isLoading={busy} maxHeight={160}
        onSubmit={() => { if (visible && !busy && value.trim()) onSubmit(); }}
        className="assistant-composer">
        <div className="assistant-composer-row">
          {compact && <span className="copilot-quick-mark" aria-hidden />}
          <PromptInputTextarea placeholder={placeholder} aria-label={ariaLabel} onFocus={onFocus}
            tabIndex={visible ? 0 : -1} className="assistant-composer-input" />
          <PromptInputActions>
            <PromptInputAction tooltip={busy ? "Stop response" : "Send message"}>
              <button type="button" className="assistant-composer-send" tabIndex={visible ? 0 : -1}
                disabled={!visible || (!busy && !value.trim())}
                onClick={busy ? onStop : onSubmit} aria-label={busy ? "Stop" : compact ? "Ask Pivot" : "Send"}>
                {busy ? <Square size={13} fill="currentColor" aria-hidden /> : <ArrowUp size={18} strokeWidth={2.2} aria-hidden />}
              </button>
            </PromptInputAction>
          </PromptInputActions>
        </div>
      </PromptInput>
    </div>
  );
}
