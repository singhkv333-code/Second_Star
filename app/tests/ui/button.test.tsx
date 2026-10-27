import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui/button";

describe("Button (sanity)", () => {
  it("renders children", () => {
    render(<Button>Save workflow</Button>);
    expect(
      screen.getByRole("button", { name: /save workflow/i }),
    ).toBeInTheDocument();
  });

  it("applies the destructive variant classes", () => {
    render(<Button variant="destructive">Archive</Button>);
    const btn = screen.getByRole("button", { name: /archive/i });
    expect(btn.className).toContain("bg-destructive");
  });
});
