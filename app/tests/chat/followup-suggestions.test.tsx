/**
 * Related-questions block: what it renders, and when it stays out of the way.
 *
 * The hover treatment is deliberately inline style rather than a CSS class, so
 * these assert the rendered style attribute — that is the actual contract with
 * the Perplexity look (row tint on hover, arrow and text to full foreground).
 */
import { fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FollowUpSuggestions, useFollowUps } from "@/components/chat/FollowUpSuggestions";

const QS = [
  "How does HDFC Bank compare with ICICI Bank on valuation?",
  "What could improve HDFC Bank's ROE from here?",
  "Which risks would make this a value trap?",
];

describe("FollowUpSuggestions", () => {
  it("renders one row per suggestion", () => {
    render(<FollowUpSuggestions suggestions={QS} onPick={() => {}} />);
    expect(screen.getAllByTestId("followup-item")).toHaveLength(3);
    expect(screen.getByText(QS[0]!)).toBeTruthy();
  });

  it("renders nothing at all when there are no suggestions", () => {
    const { container } = render(
      <FollowUpSuggestions suggestions={[]} onPick={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("sends the question when a row is clicked", () => {
    const onPick = vi.fn();
    render(<FollowUpSuggestions suggestions={QS} onPick={onPick} />);
    fireEvent.click(screen.getAllByTestId("followup-item")[1]!);
    expect(onPick).toHaveBeenCalledWith(QS[1]);
  });

  it("tints the whole row on hover and clears it on leave", () => {
    render(<FollowUpSuggestions suggestions={QS} onPick={() => {}} />);
    const row = screen.getAllByTestId("followup-item")[0]!;
    expect(row.style.background).toBe("transparent");
    fireEvent.mouseEnter(row);
    expect(row.style.background).toContain("--surface-hover");
    fireEvent.mouseLeave(row);
    expect(row.style.background).toBe("transparent");
  });

  it("separates rows with a hairline, except the last", () => {
    render(<FollowUpSuggestions suggestions={QS} onPick={() => {}} />);
    const rows = screen.getAllByTestId("followup-item");
    expect(rows[0]!.style.borderBottom).toContain("--glass-border");
    expect(rows[1]!.style.borderBottom).toContain("--glass-border");
    // jsdom serialises `border-bottom: none` to "", so assert the thing
    // that actually matters: the last row carries no hairline.
    expect(rows[2]!.style.borderBottom).not.toContain("--glass-border");
  });

  it("makes the row itself the hit target, not just the text", () => {
    render(<FollowUpSuggestions suggestions={QS} onPick={() => {}} />);
    const row = screen.getAllByTestId("followup-item")[0]!;
    expect(row.tagName).toBe("BUTTON");
    expect(row.className).toContain("w-full");
  });
});

describe("useFollowUps", () => {
  const A = "An answer long enough to earn follow-ups. ".repeat(8);

  it("an aborted request does not leave the answer without suggestions", async () => {
    // Coming back to the chat restores the thread in steps; each step re-runs
    // the hook and aborts the request before it. The pair used to be marked
    // done when the request STARTED, so the last run skipped it: no block.
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () => new Response(JSON.stringify({ suggestions: QS })),
    );
    const { result, rerender } = renderHook(
      ({ on }: { on: boolean }) => useFollowUps("q-abort", A, on),
      { initialProps: { on: true } },
    );
    rerender({ on: false }); // aborts the first request
    rerender({ on: true });
    await waitFor(() => expect(result.current).toEqual(QS));
    expect(f).toHaveBeenCalledTimes(2);
    f.mockRestore();
  });

  it("a remount shows the settled set at once, without asking again", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () => new Response(JSON.stringify({ suggestions: QS })),
    );
    const first = renderHook(() => useFollowUps("q-remount", A, true));
    await waitFor(() => expect(first.result.current).toEqual(QS));
    first.unmount();
    const again = renderHook(() => useFollowUps("q-remount", A, true));
    expect(again.result.current).toEqual(QS);
    expect(f).toHaveBeenCalledTimes(1);
    f.mockRestore();
  });
});
