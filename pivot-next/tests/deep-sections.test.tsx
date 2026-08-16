/**
 * DeepSections — the coverage rule.
 *
 * The whole design rests on one behaviour: a section with no data for this
 * company is not rendered, rather than rendered empty. HDFCBANK genuinely has
 * zero rows in `quarterly_metrics` while carrying 236 annual-report facts, so
 * "every company gets the same sections" would put a permanently empty
 * Quarters panel on one of the largest listed companies in the country.
 *
 * These tests pin that rule. They query the SECTIONS themselves rather than an
 * index — there is no index rail any more, the sections stand on their own —
 * which is also the more honest assertion: what matters is whether the panel
 * is on the page, not whether something links to it.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/react";

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

/** The sections are addressed by id, the same handle a deep link uses. */
const has = (root: HTMLElement, id: string): boolean =>
  root.querySelector(`#stock-section-${id}`) !== null;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getStockAnnualReport").mockResolvedValue({ data: { symbol: "TEST", documents: [], tasks: [], truncated: false } } as never);
  vi.spyOn(api, "getStockOwnership").mockResolvedValue({ data: { symbol: "TEST", available: false } } as never);
  vi.spyOn(api, "getStockDocuments").mockResolvedValue({ data: { symbol: "TEST", available: false, types: [], documents: [] } } as never);
  vi.spyOn(api, "getStockMix").mockResolvedValue({ data: { symbol: "TEST", available: false, charts: [] } } as never);
  vi.spyOn(api, "getStockPeers").mockResolvedValue({ data: { symbol: "TEST", available: false, sector: null, fields: [], catalog: [], peers: [] } } as never);
  vi.spyOn(api, "getStockQuarters").mockResolvedValue({ data: { symbol: "TEST", basis: "consolidated", matched_on: "isin", bases_available: ["consolidated"], quarters: [] } } as never);
});
afterEach(() => vi.restoreAllMocks());

describe("DeepSections coverage rule", () => {
  it("renders a section only where there is data", async () => {
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage({ revenue_mix: { count: 1 } })),
    } as never);
    vi.spyOn(api, "getStockMix").mockResolvedValue({
      data: { symbol: "TEST", available: true, charts: [] },
    } as never);

    const { container } = render(<DeepSections symbol="TEST" />);

    await waitFor(() => expect(has(container, "revenue_mix")).toBe(true));
  });

  it("does not render quarters — it belongs to the Financial Performance tabs", async () => {
    // Quarters used to be the first section here. It is the same question the
    // annual statements answer over a different period, so it moved up into
    // that panel as a third tab; covered or not, it must not come back as a
    // section, or the page shows the quarterly numbers twice.
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage({ quarters: { count: 175, latest: "2026-06-30", bases: 2 } })),
    } as never);
    const quarters = vi.spyOn(api, "getStockQuarters");

    const { container } = render(<DeepSections symbol="TEST" />);

    await waitFor(() => expect(has(container, "peers")).toBe(true));
    expect(has(container, "quarters")).toBe(false);
    expect(quarters).not.toHaveBeenCalled();
  });

  it("does not render annual-report, ownership or document sections", async () => {
    // All three are covered for this company and none of them are on the page:
    // coverage decides which of the SUPPORTED sections render, it does not
    // decide what the page supports.
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage({
        quarters: { count: 175, latest: "2026-06-30", bases: 2 },
        annual_report: { count: 236, tasks: 10, documents: 2, latest_period: "2025-2026" },
        ownership: { count: 1 },
        documents: { count: 127 },
      })),
    } as never);
    const ownership = vi.spyOn(api, "getStockOwnership");

    const { container } = render(<DeepSections symbol="TEST" />);
    await waitFor(() => expect(has(container, "peers")).toBe(true));
    expect(has(container, "annual_report")).toBe(false);
    expect(has(container, "ownership")).toBe(false);
    expect(has(container, "documents")).toBe(false);
    expect(ownership).not.toHaveBeenCalled();
  });

  it("still renders sector peers when no filing sections are covered", async () => {
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage()),
    } as never);

    const { container } = render(<DeepSections symbol="TEST" />);
    await waitFor(() => expect(has(container, "peers")).toBe(true));
  });

  it("renders nothing when coverage itself fails, rather than an error box", async () => {
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      error: { code: "boom", message: "down" },
    } as never);

    const { container } = render(<DeepSections symbol="TEST" />);
    await waitFor(() => expect(container.querySelector("section")).toBeNull());
  });

  it("fetches only the sections that exist", async () => {
    // An uncovered section must cost nothing — no panel and no request.
    vi.spyOn(api, "getStockSections").mockResolvedValue({
      data: sections(coverage({ revenue_mix: { count: 1 } })),
    } as never);
    const mix = vi.spyOn(api, "getStockMix").mockResolvedValue({
      data: { symbol: "TEST", available: true, charts: [] },
    } as never);
    const quarters = vi.spyOn(api, "getStockQuarters");

    const { container } = render(<DeepSections symbol="TEST" />);

    await waitFor(() => expect(mix).toHaveBeenCalled());
    expect(has(container, "revenue_mix")).toBe(true);
    expect(quarters).not.toHaveBeenCalled();
  });
});
