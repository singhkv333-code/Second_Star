"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AssistantComposer } from "@/components/copilot/AssistantComposer";
import "./quick-ask-glass.css";

type QuickAskProps = {
  placeholder: string;
  contextLabel: string;
  onSubmit: (question: string) => void;
  /** The bar gained focus: the page's data can be fetched while they type. */
  onFocus?: () => void;
  visible?: boolean;
};

/**
 * The collapsed presentation of Copilot. It intentionally owns no message or
 * conversation state: submitting hands the text to the one mounted Copilot
 * workspace in AppShell (or to Charto's own chat bridge on the chart page).
 */
export function QuickAsk({
  placeholder,
  contextLabel,
  onSubmit,
  onFocus,
  visible = true,
}: QuickAskProps): React.ReactElement {
  const [value, setValue] = useState("");
  const barRef = useRef<HTMLElement>(null);
  const drag = useRef<{ id: number; x: number; y: number; left: number; top: number } | null>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const positionKey = "pivot:quick-ask-position";

  const clamp = useCallback((left: number, top: number) => {
    const rect = barRef.current?.getBoundingClientRect();
    return {
      left: Math.max(12, Math.min(left, window.innerWidth - (rect?.width || 440) - 12)),
      // Leave the chart's time scale and clock unobstructed at the bottom.
      top: Math.max(12, Math.min(top, window.innerHeight - (rect?.height || 64) - 64)),
    };
  }, []);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(positionKey) || "null");
      if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.top)) {
        setPosition(clamp(saved.left, saved.top));
      }
    } catch { /* Storage can be unavailable. */ }
    const resize = () => setPosition(current => current ? clamp(current.left, current.top) : null);
    window.addEventListener("resize", resize);
    const observer = new ResizeObserver(resize);
    if (barRef.current) observer.observe(barRef.current);
    return () => { window.removeEventListener("resize", resize); observer.disconnect(); };
  }, [clamp]);

  const savePosition = (next: typeof position) => {
    setPosition(next);
    try {
      if (next) localStorage.setItem(positionKey, JSON.stringify(next));
      else localStorage.removeItem(positionKey);
    } catch { /* Moving the bar works without storage. */ }
  };

  useEffect(() => {
    setValue("");
  }, [contextLabel]);

  const send = (): void => {
    const question = value.trim();
    if (!question) return;
    setValue("");
    onSubmit(question);
  };

  return (
    <section
      ref={barRef}
      style={position ? { left: position.left + (barRef.current?.offsetWidth || 440) / 2, top: position.top, bottom: "auto" } : undefined}
      className={`copilot-quick-ask ${visible ? "copilot-quick-ask--visible" : "copilot-quick-ask--hidden"}`}
      aria-label="Pivot Copilot quick ask"
      aria-hidden={!visible}
      data-testid="copilot-quick-ask"
    >
      <AssistantComposer value={value} onValueChange={setValue} onSubmit={send}
        placeholder={placeholder} ariaLabel={placeholder} onFocus={onFocus}
        focusEvent="pivot:focus-quick-ask" compact visible={visible}
        dragHandle={{
          onClick: event => event.stopPropagation(),
          onPointerDown: event => {
            if (event.button !== 0) return;
            event.preventDefault();
            const rect = barRef.current!.getBoundingClientRect();
            drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
            event.currentTarget.setPointerCapture(event.pointerId);
          },
          onPointerMove: event => {
            const start = drag.current;
            if (!start || start.id !== event.pointerId) return;
            setPosition(clamp(start.left + event.clientX - start.x, start.top + event.clientY - start.y));
          },
          onPointerUp: event => {
            if (!drag.current || drag.current.id !== event.pointerId) return;
            const start = drag.current;
            drag.current = null;
            savePosition(clamp(start.left + event.clientX - start.x, start.top + event.clientY - start.y));
            event.currentTarget.releasePointerCapture(event.pointerId);
          },
          onLostPointerCapture: () => { drag.current = null; },
          onPointerCancel: () => { drag.current = null; },
          onDoubleClick: () => savePosition(null),
          onKeyDown: event => {
            if (event.key === "Home") { event.preventDefault(); savePosition(null); return; }
            const delta: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
            const step = delta[event.key];
            if (!step) return;
            event.preventDefault();
            const rect = barRef.current!.getBoundingClientRect();
            const distance = event.shiftKey ? 32 : 8;
            savePosition(clamp(rect.left + step[0] * distance, rect.top + step[1] * distance));
          },
        }} />
    </section>
  );
}
