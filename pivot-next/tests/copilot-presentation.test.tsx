import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { QuickAsk } from "@/components/copilot/QuickAsk";
import { attachmentKey, toWireAttachment } from "@/components/chat/ComposerContext";

describe("global Copilot presentations", () => {
  it("Quick Ask hands one submitted question to the shared host", () => {
    const submit = vi.fn();
    render(
      <QuickAsk
        placeholder="Ask about my portfolio…"
        contextLabel="My portfolio"
        onSubmit={submit}
      />,
    );

    const input = screen.getByLabelText("Ask about my portfolio…");
    fireEvent.change(input, { target: { value: "Where is my concentration risk?" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(submit).toHaveBeenCalledOnce();
    expect(submit).toHaveBeenCalledWith("Where is my concentration risk?");
    expect(input).toHaveValue("");
    expect(screen.queryByText("My portfolio")).not.toBeInTheDocument();
  });

  it("restores keyboard focus when the side panel collapses", () => {
    render(
      <QuickAsk
        placeholder="Screen stocks…"
        contextLabel="Screener"
        onSubmit={() => undefined}
      />,
    );
    window.dispatchEvent(new Event("pivot:focus-quick-ask"));
    expect(screen.getByLabelText("Screen stocks…")).toHaveFocus();
  });

  it("keeps stock grounding in the ordinary attachment pipeline", () => {
    const context = { kind: "security", symbol: "RELIANCE", name: "Reliance Industries" } as const;
    expect(attachmentKey(context)).toBe("security:RELIANCE");
    expect(toWireAttachment(context)).toEqual({
      kind: "security",
      symbol: "RELIANCE",
      name: "Reliance Industries",
    });
  });
});
