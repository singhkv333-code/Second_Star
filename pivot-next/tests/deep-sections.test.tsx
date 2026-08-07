/**
 * DeepSections — the coverage rule.
 *
 * The whole design rests on one behaviour: a section with no data for this
 * company is not rendered, rather than rendered empty. HDFCBANK genuinely has
 * zero rows in `quarterly_metrics` while carrying 236 annual-report facts, so
 * "every company gets the same tabs" would put a permanently empty Quarters
 * panel on one of the largest listed companies in the country.
 *
 * These tests pin that rule and the lazy-load contract around it.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { DeepSections } from "@/components/stock/DeepSections";
import * as api from "@/lib/api";
import type { SectionCoverage, StockSections } from "@/lib/api";

const coverage = (over: Partial<SectionCoverage> = {}): SectionCoverage => ({
  quarters: { count: 0, latest: null, bases: 0 },
  annual_report: { count: 0, tasks: 0, documents: 0, latest_period: null },
  revenue_mix: { count: 0 },
  ownership: { count: 0 },
  documents: { count: 0 },
  ...over,
});

const sections = (cov: SectionCoverage): StockSections => ({
  symbol: "TEST", isin: "INE000A01000", sc_id: "T1",
  name: "Test Ltd", bse_scripcode: "500000", coverage: cov,
});

beforeEach(() => vi.restoreAllMocks());
afterEach(() => vi.restoreAllMocks());

describe("DeepSections coverage rule", () => {
  it("renders a tab only for sections that have data", async () => {
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage({
        annual_report: { count: 236, tasks: 10, documents: 2, latest_period: "2025-2026" },
        ownership: { count: 1 },
      })),
    } as never);
    vi.spyOn(api, "getStockAnnualReport").mockResolvedValue({
      data: { symbol: "TEST", documents: [], tasks: [], truncated: false },
    } as never);

    render(<DeepSections symbol="TEST" />);

    await waitFor(() => expect(screen.getByRole("tab", { name: /Annual report/ })).toBeTruthy());
    expect(screen.getByRole("tab", { name: /Ownership/ })).toBeTruthy();
    // The three with zero coverage must not exist at all — not disabled, not
    // empty, absent.
    expect(screen.queryByRole("tab", { name: /Quarters/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: /Segments/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: /Documents/ })).toBeNull();
  });

  it("renders nothing at all when the company has none of this data", async () => {
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage()),
    } as never);

    const { container } = render(<DeepSections symbol="TEST" />);
    await waitFor(() => expect(container.querySelector("section")).toBeNull());
  });

  it("renders nothing when coverage itself fails, rather than an error box", async () => {
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      error: { code: "boom", message: "down" },
    } as never);

    const { container } = render(<DeepSections symbol="TEST" />);
    await waitFor(() => expect(container.querySelector("section")).toBeNull());
  });

  it("opens the first AVAILABLE tab, not a fixed default", async () => {
    // Quarters is first in reading order but absent here, so Segments — the
    // first that exists — must be the one that opens. A fixed default would
    // open a tab that is not on screen and show an empty panel.
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage({ revenue_mix: { count: 1 }, documents: { count: 12 } })),
    } as never);
    const mix = vi.spyOn(api, "getStockMix").mockResolvedValue({
      data: { symbol: "TEST", available: true, charts: [] },
    } as never);
    const docs = vi.spyOn(api, "getStockDocuments").mockResolvedValue({
      data: { symbol: "TEST", available: true, types: [], documents: [] },
    } as never);

    render(<DeepSections symbol="TEST" />);

    await waitFor(() => expect(mix).toHaveBeenCalled());
    expect(screen.getByRole("tab", { name: /Segments/ }).getAttribute("aria-selected")).toBe("true");
    // Lazy: the tab that was not opened has not been fetched.
    expect(docs).not.toHaveBeenCalled();
  });
});
