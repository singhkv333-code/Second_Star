"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  buildRefSuggestions,
  validateRefsInString,
  type RefSuggestion,
} from "@/lib/refs";
import type { Step } from "@/lib/types";

export type RefChipPickerProps = {
  id?: string;
  value: string;
  onChange: (next: string) => void;
  onBlur?: () => void;
  placeholder?: string;
  /** Render as a textarea instead of a single-line input. */
  multiline?: boolean;
  /** Steps that come BEFORE the step being edited — drives suggestions. */
  priorSteps: Step[];
  hasWebhookTrigger: boolean;
  /** Set true when the parent's API submit returned an error for this field. */
  hasFieldError?: boolean;
  /** aria-invalid forwarding. */
  "aria-invalid"?: boolean | "false" | "true";
};

/**
 * Single-line / multi-line text input with a `{{` autocomplete popover for
 * inter-step references. Suggestions are derived from the workflow's prior
 * steps (and webhook flag) so users can never insert a ref the engine will
 * reject at run time.
 */
export function RefChipPicker({
  id,
  value,
  onChange,
  onBlur,
  placeholder,
  multiline = false,
  priorSteps,
  hasWebhookTrigger,
  hasFieldError,
  "aria-invalid": ariaInvalid,
}: RefChipPickerProps): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  const suggestions = useMemo(
    () => buildRefSuggestions(priorSteps, hasWebhookTrigger),
    [priorSteps, hasWebhookTrigger],
  );

  // Live validation of any refs already typed.
  const validationErrors = useMemo(
    () => validateRefsInString(value, priorSteps, hasWebhookTrigger),
    [value, priorSteps, hasWebhookTrigger],
  );

  // Open the picker whenever the user just typed `{{`.
  useEffect(() => {
    if (!value) {
      setOpen(false);
      return;
    }
    const tail = value.slice(-2);
    if (tail === "{{") {
      setOpen(true);
      setActiveIdx(0);
    } else if (tail === "}}") {
      setOpen(false);
    }
  }, [value]);

  const insertSuggestion = (s: RefSuggestion): void => {
    const lastOpen = value.lastIndexOf("{{");
    if (lastOpen < 0) {
      // Fallback — append.
      onChange(`${value}{{ ${s.value} }}`);
    } else {
      const before = value.slice(0, lastOpen);
      const wrapped = `{{ ${s.value} }}`;
      onChange(`${before}${wrapped}`);
    }
    setOpen(false);
    inputRef.current?.focus();
  };

  const onKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
  ): void => {
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      const target = suggestions[activeIdx];
      if (target) {
        e.preventDefault();
        insertSuggestion(target);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  const inputClass = cn(
    "font-mono text-[12px]",
    (hasFieldError || validationErrors.length > 0) &&
      "border-destructive focus-visible:ring-destructive",
  );

  return (
    <div className="relative" data-testid="ref-chip-picker">
      {multiline ? (
        <Textarea
          id={id}
          ref={inputRef as React.Ref<HTMLTextAreaElement>}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          rows={3}
          aria-invalid={ariaInvalid}
          className={inputClass}
        />
      ) : (
        <Input
          id={id}
          ref={inputRef as React.Ref<HTMLInputElement>}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          aria-invalid={ariaInvalid}
          className={inputClass}
        />
      )}

      {open && suggestions.length > 0 && (
        <div
          role="listbox"
          aria-label="Reference suggestions"
          className="absolute z-50 mt-1 w-full overflow-hidden rounded-md border bg-popover shadow-md"
        >
          <ul className="max-h-56 overflow-y-auto py-1 text-xs">
            {suggestions.map((s, idx) => (
              <li key={s.value}>
                <button
                  type="button"
                  role="option"
                  aria-selected={idx === activeIdx}
                  onMouseDown={(e) => {
                    // Prevent input blur before click resolves.
                    e.preventDefault();
                    insertSuggestion(s);
                  }}
                  onMouseEnter={() => setActiveIdx(idx)}
                  className={cn(
                    "flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left",
                    idx === activeIdx
                      ? "bg-accent text-accent-foreground"
                      : "hover:bg-accent/60",
                  )}
                >
                  <span className="truncate font-mono">{s.label}</span>
                  <Badge variant="muted" className="shrink-0">
                    {s.category}
                  </Badge>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {validationErrors.length > 0 && (
        <p className="mt-1 text-[11px] font-medium text-destructive">
          Bad ref{validationErrors.length === 1 ? "" : "s"}:{" "}
          {validationErrors.map((e) => e.ref).join(", ")} —{" "}
          {validationErrors[0]?.reason}
        </p>
      )}
    </div>
  );
}
