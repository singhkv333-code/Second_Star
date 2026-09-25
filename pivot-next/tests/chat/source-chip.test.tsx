import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import AssistantMessage from "@/components/chat/AssistantMessage";

describe("source chips", () => {
  it("renders a web citation as a logo pill with the outlet's name", () => {
    render(
      <AssistantMessage text="Exports rose 51%. ([bajajauto.com](https://www.bajajauto.com/pr-aug-2026.pdf))" />,
    );
    const chip = screen.getByTestId("source-chip");
    expect(chip).toHaveTextContent("Bajaj Auto");
    expect(chip.querySelector("img")?.getAttribute("src")).toContain("domain=bajajauto.com");
    expect(chip.textContent).not.toContain("(");
  });

  it("a report citation carries its page", () => {
    render(
      <AssistantMessage text="Dahej ran at 99%. ([Annual report 2024-2025, p.56](https://nsearchives.nseindia.com/ar.pdf#page=56))" />,
    );
    expect(screen.getByTestId("source-chip")).toHaveTextContent("NSE · p.56");
    expect(screen.getByTestId("source-chip").getAttribute("title")).toBe(
      "Annual report 2024-2025, p.56",
    );
  });
});
