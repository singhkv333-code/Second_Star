"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";

type QuickAskProps = {
  placeholder: string;
  contextLabel: string;
  onSubmit: (question: string) => void;
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
  visible = true,
}: QuickAskProps): React.ReactElement {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setValue("");
  }, [contextLabel]);

  useEffect(() => {
    const focus = (): void => inputRef.current?.focus();
    window.addEventListener("pivot:focus-quick-ask", focus);
    return () => window.removeEventListener("pivot:focus-quick-ask", focus);
  }, []);

  const send = (): void => {
    const question = value.trim();
    if (!question) return;
    setValue("");
    onSubmit(question);
  };

  return (
    <section
      className={`copilot-quick-ask ${visible ? "copilot-quick-ask--visible" : "copilot-quick-ask--hidden"}`}
      aria-label="Pivot Copilot quick ask"
      aria-hidden={!visible}
      data-testid="copilot-quick-ask"
    >
      <div className="copilot-quick-pill">
        <span className="copilot-quick-mark" aria-hidden={true} />
        <textarea
          ref={inputRef}
          tabIndex={visible ? 0 : -1}
          rows={1}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            }
          }}
          placeholder={placeholder}
          aria-label={placeholder}
          className="copilot-quick-input"
        />
        <button
          type="button"
          tabIndex={visible ? 0 : -1}
          onClick={send}
          disabled={!value.trim()}
          className="copilot-quick-send"
          aria-label="Ask Pivot"
        >
          <ArrowUp size={16} strokeWidth={2.2} aria-hidden={true} />
        </button>
      </div>
    </section>
  );
}
