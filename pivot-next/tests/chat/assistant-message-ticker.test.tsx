/**
 * Tests for the ticker-detection logic in AssistantMessage:
 *   - extractTickerSymbol identifies NSE/BSE symbols vs real code tokens
 *   - Inline code renderer uses body-font Link for tickers, monospace for code
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { extractTickerSymbol } from "@/components/chat/AssistantMessage";
import AssistantMessage from "@/components/chat/AssistantMessage";

// ── extractTickerSymbol unit tests ────────────────────────────────────────

describe("extractTickerSymbol", () => {
  it("returns symbol for plain NSE tickers", () => {
    expect(extractTickerSymbol("RELIANCE")).toBe("RELIANCE");
    expect(extractTickerSymbol("INFY")).toBe("INFY");
    expect(extractTickerSymbol("TCS")).toBe("TCS");
    expect(extractTickerSymbol("ONGC")).toBe("ONGC");
    expect(extractTickerSymbol("BPCL")).toBe("BPCL");
    expect(extractTickerSymbol("IOC")).toBe("IOC");
  });

  it("returns symbol for special-char tickers (M&M, L&T)", () => {
    expect(extractTickerSymbol("M&M")).toBe("M&M");
    expect(extractTickerSymbol("LT")).toBe("LT");
  });

  it("strips NSE: prefix and returns core symbol", () => {
    expect(extractTickerSymbol("NSE:INFY")).toBe("INFY");
    expect(extractTickerSymbol("BSE:RELIANCE")).toBe("RELIANCE");
  });

  it("returns null for pure numeric BSE codes (no letter start)", () => {
    // BSE numeric codes like 500325 start with a digit — the regex requires
    // the core symbol to start with a letter (all NSE symbols do), so these
    // don't match and stay as monospace code.
    expect(extractTickerSymbol("BSE:500325")).toBeNull();
  });

  it("strips .NS / .BO suffix and returns core symbol", () => {
    expect(extractTickerSymbol("INFY.NS")).toBe("INFY");
    expect(extractTickerSymbol("RELIANCE.BO")).toBe("RELIANCE");
  });

  it("returns null for code identifiers with underscores", () => {
    expect(extractTickerSymbol("revenue_growth")).toBeNull();
    expect(extractTickerSymbol("pe_ratio")).toBeNull();
    expect(extractTickerSymbol("cagr_pct")).toBeNull();
  });

  it("returns null for mixed-case identifiers", () => {
    expect(extractTickerSymbol("cagrPct")).toBeNull();
    expect(extractTickerSymbol("someFunc")).toBeNull();
    expect(extractTickerSymbol("getValue")).toBeNull();
  });

  it("returns null for empty or whitespace-only strings", () => {
    expect(extractTickerSymbol("")).toBeNull();
    expect(extractTickerSymbol("  ")).toBeNull();
  });

  it("returns null for single-char strings (too short for a ticker)", () => {
    // core must be at least 2 chars: [A-Z][A-Z0-9&-]{1,14}
    expect(extractTickerSymbol("A")).toBeNull();
  });

  it("returns null for strings exceeding 15 core chars", () => {
    // 16 uppercase chars — outside the 2-15 range
    expect(extractTickerSymbol("ABCDEFGHIJKLMNOP")).toBeNull();
  });
});

// ── Rendering integration tests ───────────────────────────────────────────

describe("AssistantMessage inline code rendering", () => {
  it("renders ticker backtick as a stock-page link, not monospace", () => {
    render(<AssistantMessage text="Sector leaders include `ONGC` and `IOC`." />);
    const ongcLink = screen.getByRole("link", { name: "ONGC" });
    expect(ongcLink).toBeDefined();
    expect(ongcLink.getAttribute("href")).toBe("/stock/ONGC");
    // No font-mono on the link element
    expect(ongcLink.className).not.toContain("font-mono");

    const iocLink = screen.getByRole("link", { name: "IOC" });
    expect(iocLink.getAttribute("href")).toBe("/stock/IOC");
  });

  it("preserves monospace styling for non-ticker code", () => {
    render(<AssistantMessage text="Filter by `pe_ratio` less than 20." />);
    // No link for pe_ratio
    expect(screen.queryByRole("link", { name: "pe_ratio" })).toBeNull();
    // Should still render as <code> text
    const codeEl = screen.getByText("pe_ratio");
    expect(codeEl.tagName.toLowerCase()).toBe("code");
  });

  it("encodes special characters in the href (M&M)", () => {
    render(<AssistantMessage text="Check `M&M` performance." />);
    const link = screen.getByRole("link", { name: "M&M" });
    expect(link.getAttribute("href")).toBe("/stock/M%26M");
  });
});

// ── Gain/loss number coloring ─────────────────────────────────────────────

describe("AssistantMessage gain/loss coloring", () => {
  it("colors a signed positive percentage green (profit token)", () => {
    render(<AssistantMessage text="RELIANCE is up +2.4% today." />);
    const span = screen.getByText("+2.4%");
    expect(span.style.color).toBe("var(--color-profit)");
  });

  it("colors a signed negative percentage red (loss token)", () => {
    render(<AssistantMessage text="The position is down -3.1% since entry." />);
    const span = screen.getByText("-3.1%");
    expect(span.style.color).toBe("var(--color-loss)");
  });

  it("colors signed currency amounts", () => {
    render(<AssistantMessage text="P&L today: +₹1,240.50 on the basket." />);
    const span = screen.getByText("+₹1,240.50");
    expect(span.style.color).toBe("var(--color-profit)");
  });

  it("colors the U+2212 minus sign as a loss", () => {
    render(<AssistantMessage text="Return over the week: −5.6%." />);
    const span = screen.getByText("−5.6%");
    expect(span.style.color).toBe("var(--color-loss)");
  });

  it("does not color an unsigned percentage", () => {
    render(<AssistantMessage text="Expense ratio is 8.2% for this fund." />);
    // Plain text, not wrapped in a colored span.
    const el = screen.getByText(/Expense ratio is 8\.2% for this fund\./);
    expect(el.querySelector("span")).toBeNull();
  });

  it("colors gain/loss inside a list item too", () => {
    render(<AssistantMessage text={"- INFY: +1.8%\n- TCS: -0.9%"} />);
    expect(screen.getByText("+1.8%").style.color).toBe("var(--color-profit)");
    expect(screen.getByText("-0.9%").style.color).toBe("var(--color-loss)");
  });
});

// ── Internal stock-page links (company name mentions) ────────────────────

describe("AssistantMessage internal link rendering", () => {
  it("renders a markdown link to /stock/ as a bold, same-tab link", () => {
    render(
      <AssistantMessage text="[Reliance Industries](/stock/RELIANCE) reported strong Q3 numbers." />,
    );
    const link = screen.getByRole("link", { name: "Reliance Industries" });
    expect(link.getAttribute("href")).toBe("/stock/RELIANCE");
    expect(link.getAttribute("target")).not.toBe("_blank");
    expect(link.className).toContain("font-semibold");
  });

  it("keeps external links opening in a new tab", () => {
    render(<AssistantMessage text="[NSE circular](https://nseindia.com/notice) has details." />);
    const link = screen.getByRole("link", { name: "NSE circular" });
    expect(link.getAttribute("target")).toBe("_blank");
  });
});
