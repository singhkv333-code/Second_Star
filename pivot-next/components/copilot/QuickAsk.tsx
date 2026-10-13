"use client";

import { useEffect, useState } from "react";
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
      className={`copilot-quick-ask ${visible ? "copilot-quick-ask--visible" : "copilot-quick-ask--hidden"}`}
      aria-label="Pivot Copilot quick ask"
      aria-hidden={!visible}
      data-testid="copilot-quick-ask"
    >
      <AssistantComposer value={value} onValueChange={setValue} onSubmit={send}
        placeholder={placeholder} ariaLabel={placeholder} onFocus={onFocus}
        focusEvent="pivot:focus-quick-ask" compact visible={visible} />
    </section>
  );
}
