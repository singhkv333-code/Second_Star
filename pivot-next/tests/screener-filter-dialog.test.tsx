import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AdvancedFilterDialog } from "@/components/screener/AdvancedFilterDialog";

describe("AdvancedFilterDialog", () => {
  it("organises market, fundamental and Charto filters with source context", () => {
    render(<AdvancedFilterDialog value={[]} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /all filters/i }));

    expect(screen.getByText("Equity filters")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /market data/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /quality & balance sheet/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /technical/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /volume profile/i })).toBeInTheDocument();
  });

  it("builds an RSI clause and applies it", () => {
    const onChange = vi.fn();
    render(<AdvancedFilterDialog value={[]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /all filters/i }));
    fireEvent.click(screen.getByRole("button", { name: /technical/i }));
    fireEvent.click(screen.getByRole("button", { name: /rsi \(14\)/i }));
    fireEvent.click(screen.getByRole("button", { name: "Oversold" }));
    fireEvent.click(screen.getByRole("button", { name: "Show results" }));

    expect(onChange).toHaveBeenCalledWith([
      { field: "rsi14", op: "lt", value: 30, value2: undefined },
    ]);
  });

  it("searches descriptions and exposes standardized units", () => {
    render(<AdvancedFilterDialog value={[]} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /all filters/i }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByPlaceholderText(/search for filters/i), {
      target: { value: "accepted value" },
    });
    expect(within(dialog).getByRole("button", { name: /position inside value area/i })).toBeInTheDocument();
  });
});
