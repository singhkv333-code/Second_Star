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
    // Quarters present, Segments absent. The page carries two sections now —
    // annual report, ownership and documents were cut — so this pins that a
    // zero-count section produces no tab, not a disabled or empty one.
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage({ quarters: { count: 175, latest: "2026-06-30", bases: 2 } })),
    } as never);
    vi.spyOn(api, "getStockQuarters").mockResolvedValue({
      data: {
        symbol: "TEST", basis: "consolidated", matched_on: "isin",
        bases_available: ["consolidated"], quarters: [],
      },
    } as never);

    render(<DeepSections symbol="TEST" />);

    await waitFor(() => expect(screen.getByRole("tab", { name: /Quarters/ })).toBeTruthy());
    expect(screen.queryByRole("tab", { name: /Segments/ })).toBeNull();
    // The removed sections must not reappear even when the API still reports
    // coverage for them — the API keeps serving all five.
    expect(screen.queryByRole("tab", { name: /Annual report/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: /Ownership/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: /Documents/ })).toBeNull();
  });

  it("ignores coverage for sections the page no longer renders", async () => {
    // The endpoints still exist and /sections still counts them. A company
    // with ONLY removed-section data must render nothing at all rather than
    // an empty shell.
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage({
        annual_report: { count: 236, tasks: 10, documents: 2, latest_period: "2025-2026" },
        ownership: { count: 1 },
        documents: { count: 127 },
      })),
    } as never);

    const { container } = render(<DeepSections symbol="TEST" />);
    await waitFor(() => expect(container.querySelector("section")).toBeNull());
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
    // first that exists — must open. A fixed default would select a tab that
    // is not on screen and render an empty panel.
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage({ revenue_mix: { count: 1 } })),
    } as never);
    const mix = vi.spyOn(api, "getStockMix").mockResolvedValue({
      data: { symbol: "TEST", available: true, charts: [] },
    } as never);
    const quarters = vi.spyOn(api, "getStockQuarters");

    render(<DeepSections symbol="TEST" />);

    await waitFor(() => expect(mix).toHaveBeenCalled());
    expect(screen.getByRole("tab", { name: /Segments/ }).getAttribute("aria-selected")).toBe("true");
    // Lazy, and correct: Quarters is first in reading order but has no data,
    // so it is neither rendered nor fetched.
    expect(quarters).not.toHaveBeenCalled();
  });
});
