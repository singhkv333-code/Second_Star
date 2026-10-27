"use client";

import { useEffect, useRef, type ButtonHTMLAttributes } from "react";
import { ArrowUp, Square } from "lucide-react";
import { PromptInput, PromptInputAction, PromptInputActions, PromptInputTextarea } from "@/components/prompt-kit/prompt-input";

/** Shared Prompt Kit composer for the page assistant and collapsed ask bar. */
export function AssistantComposer({ value, onValueChange, onSubmit, placeholder,
  ariaLabel, busy = false, onStop, onFocus, focusEvent, compact = false, visible = true, dragHandle,
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
  dragHandle?: ButtonHTMLAttributes<HTMLButtonElement>;
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
          {compact && (dragHandle
            ? <button type="button" {...dragHandle} className="copilot-quick-mark copilot-quick-drag"
                tabIndex={visible ? 0 : -1} aria-label="Move prompt bar"
                title="Drag to move · Double-click to reset" />
            : <span className="copilot-quick-mark" aria-hidden />)}
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
