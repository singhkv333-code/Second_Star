/**
 * Losses render in brackets, not with a minus sign.
 *
 * The colour already carries the direction; a lone "-" at the left edge of a
 * right-aligned column is easy to miss, and brackets are the convention a
 * finance reader expects. Positives keep their explicit "+".
 *
 * The model still WRITES "-₹8,966" — this is a render-layer transform, so it
 * applies to prose and table cells alike without the model being reminded.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { colorizeGainLoss } from "@/components/chat/AssistantMessage";

const show = (t: string) => render(<div>{colorizeGainLoss(t, "t")}</div>);

describe("accounting negatives", () => {
  it("brackets a negative rupee amount", () => {
    show("Unrealised P&L is -₹8,966 today.");
    expect(screen.getByText("(₹8,966)")).toBeTruthy();
  });

  it("brackets a negative percentage", () => {
    show("The return is -34.21% over the year.");
    expect(screen.getByText("(34.21%)")).toBeTruthy();
  });

  it("brackets a minus written inside the currency symbol", () => {
    show("It shows ₹-500 on the book.");
    expect(screen.getByText("(₹500)")).toBeTruthy();
  });

  it("brackets a unicode minus too", () => {
    show("Down −3.1% on the day.");
    expect(screen.getByText("(3.1%)")).toBeTruthy();
  });

  it("leaves positives alone, sign and all", () => {
    show("SBIN is +22.57% and +₹2,776.");
    expect(screen.getByText("+22.57%")).toBeTruthy();
    expect(screen.getByText("+₹2,776")).toBeTruthy();
  });

  it("colours a bracketed loss as a loss, not a gain", () => {
    const { container } = show("TCS is -₹8,966.");
    const span = Array.from(container.querySelectorAll("span")).find(
      (e) => e.textContent === "(₹8,966)",
    );
    expect(span?.getAttribute("style")).toContain("--color-loss");
  });

  it("does not touch an unsigned number", () => {
    const { container } = show("P/E is 15.36 and ROE 12.97%.");
    expect(container.querySelectorAll("span").length).toBe(0);
  });
});
