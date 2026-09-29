import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/hooks/useCompanyLogos", () => ({ useCompanyLogos: () => ({}) }));
vi.mock("@/lib/api", () => ({ searchCompanies: vi.fn(() => new Promise(() => {})) }));

import { SmartMarkdownTable } from "@/components/chat/SmartMarkdownTable";

const cell = (tag: string, text: string) => ({
  type: "element", tagName: tag, children: [{ type: "text", value: text }],
});
const tr = (tag: string, cells: string[]) => ({
  type: "element", tagName: "tr", children: cells.map((c) => cell(tag, c)),
});
const table = (header: string[], rows: string[][]) => ({
  type: "element", tagName: "table", children: [
    { type: "element", tagName: "thead", children: [tr("th", header)] },
    { type: "element", tagName: "tbody", children: rows.map((r) => tr("td", r)) },
  ],
});

describe("SmartMarkdownTable", () => {
  it("draws a company table as the screen card does: rank, name with ticker, median", () => {
    render(<SmartMarkdownTable node={table(
      ["Company", "P/E", "Read"],
      [["Infosys (INFY)", "22.3x", "Cheaper than the pack on earnings, with slower growth"],
       ["TCS (TCS)", "24.1x", "Premium"],
       ["Wipro (WIPRO)", "19.0x", "Cheapest"]],
    )} />);
    expect(screen.getByText("#")).toBeInTheDocument();
    expect(screen.getByText("Infosys")).toBeInTheDocument();
    expect(screen.getByText("INFY")).toBeInTheDocument();
    expect(screen.getByText("Median of 3")).toBeInTheDocument();
    expect(screen.getByText("22.3x", { selector: "tfoot td" })).toBeInTheDocument();
    // A column of sentences wraps in a bounded box rather than widening the table.
    expect(screen.getByText(/slower growth/).className).toContain("max-w-[280px]");
  });

  it("leaves a non-company table without rank or median", () => {
    render(<SmartMarkdownTable node={table(["Metric", "Value"], [["P/E", "24.1"], ["ROE", "51%"]])} />);
    expect(screen.queryByText("#")).toBeNull();
    expect(screen.queryByText(/Median of/)).toBeNull();
  });
});
