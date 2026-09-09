/**
 * Related-questions block: what it renders, and when it stays out of the way.
 *
 * The hover treatment is deliberately inline style rather than a CSS class, so
 * these assert the rendered style attribute — that is the actual contract with
 * the Perplexity look (row tint on hover, arrow and text to full foreground).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FollowUpSuggestions } from "@/components/chat/FollowUpSuggestions";

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
