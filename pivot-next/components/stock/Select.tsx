"use client";

/**
 * The product's one dropdown, restated from the chart page.
 *
 * This replaces four native `<select>`s. A dressed `<select>` can only ever be
 * half the control: the browser draws the option list itself, so the page got
 * an OS popup with a blue-filled selected row sitting under a trigger styled to
 * look like ours. The chart page (`charto/preview`) solved this the same way —
 * `select.is-dressed { display: none }` and a real button beside it — and its
 * menu is what this one copies:
 *
 *   · the trigger is a GHOST button — 30px, transparent fill AND border, 6px
 *     radius, hover fills with the hover wash. Not a bordered input box: these
 *     sit in a panel header next to a heading, where a boxed field reads as
 *     something you type into.
 *   · the menu marks its live row with INK AND A TICK, never a fill. The fill
 *     is left to say "under the pointer". A menu that paints the current row
 *     reads as a selection just made rather than the state already in effect.
 *   · optional group labels — the uppercase micro-label over a full-bleed
 *     hairline, pulled out to the sheet's edges by negative margins.
 *
 * Keyboard: Escape closes, Enter/Space opens, arrows move, Home/End jump. The
 * listbox owns focus while open so a screen reader reads it as one control.
 */

import * as React from "react";
import { Check, ChevronDown } from "lucide-react";

export type SelectOption = {
  value: string;
  label: string;
  /** Rows sharing a group render under one uppercase label, in first-seen order. */
  group?: string;
};

export function Select({
  value,
  options,
  onChange,
  ariaLabel,
  align = "right",
  minMenuWidth,
}: {
  value: string;
  options: SelectOption[];
  onChange: (v: string) => void;
  ariaLabel: string;
  /** Which edge the menu hangs from. A control at a panel's right edge opens
   *  leftward or the sheet runs off the page. */
  align?: "left" | "right";
  minMenuWidth?: number;
}): React.ReactElement {
  const [open, setOpen] = React.useState(false);
  const [activeIdx, setActiveIdx] = React.useState(0);
  const wrapRef = React.useRef<HTMLDivElement | null>(null);
  const listRef = React.useRef<HTMLUListElement | null>(null);

  const selectedIdx = Math.max(0, options.findIndex((o) => o.value === value));
  const current = options[selectedIdx];

  // Opening always starts on the live row, not on whatever the pointer left
  // behind the last time the menu was open.
  React.useEffect(() => {
    if (open) setActiveIdx(selectedIdx);
  }, [open, selectedIdx]);

  React.useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent): void => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, [open]);

  React.useEffect(() => {
    if (open) listRef.current?.focus();
  }, [open]);

  const commit = (i: number): void => {
    const opt = options[i];
    if (opt) onChange(opt.value);
    setOpen(false);
  };

  const onListKeyDown = (e: React.KeyboardEvent): void => {
    if (e.key === "Escape") { e.preventDefault(); setOpen(false); return; }
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); commit(activeIdx); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setActiveIdx((i) => Math.min(options.length - 1, i + 1)); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); setActiveIdx((i) => Math.max(0, i - 1)); return; }
    if (e.key === "Home") { e.preventDefault(); setActiveIdx(0); return; }
    if (e.key === "End") { e.preventDefault(); setActiveIdx(options.length - 1); }
  };

  // Group labels print once, above the first row carrying that group.
  let lastGroup: string | undefined;

  return (
    <div className="pv-select" ref={wrapRef}>
      <button
        type="button"
        className="pv-select-btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) { e.preventDefault(); setOpen(true); }
        }}
      >
        <span className="val">{current?.label ?? value}</span>
        <ChevronDown size={14} strokeWidth={2} aria-hidden="true" />
      </button>

      {open && (
        <ul
          ref={listRef}
          role="listbox"
          tabIndex={-1}
          aria-label={ariaLabel}
          aria-activedescendant={`${ariaLabel}-opt-${activeIdx}`}
          className={`pv-select-menu${align === "left" ? " is-left" : ""}`}
          style={minMenuWidth ? { minWidth: minMenuWidth } : undefined}
          onKeyDown={onListKeyDown}
        >
          {options.map((o, i) => {
            const head = o.group && o.group !== lastGroup ? o.group : null;
            lastGroup = o.group;
            const on = o.value === value;
            return (
              <React.Fragment key={o.value}>
                {head ? <li className="head" role="presentation">{head}</li> : null}
                <li
                  id={`${ariaLabel}-opt-${i}`}
                  role="option"
                  aria-selected={on}
                  className={`item${on ? " on" : ""}${i === activeIdx ? " active" : ""}`}
                  onClick={() => commit(i)}
                  onMouseEnter={() => setActiveIdx(i)}
                >
                  <span>{o.label}</span>
                  {on ? <Check size={14} strokeWidth={2.2} aria-hidden="true" /> : null}
                </li>
              </React.Fragment>
            );
          })}
        </ul>
      )}
    </div>
  );
}
