/**
 * The public stock pages' words and crawler files. The rule under test is the
 * product's: a sentence exists only when the number it states exists.
 */
import { describe, expect, it } from "vitest";
import type { Bar, MarketRow, StockSnapshot } from "@/lib/seo/data";
import { isIndexable } from "@/lib/seo/data";
import { crore, fiscalQuarter, fiscalYear, inr, pct } from "@/lib/seo/format";
import { LISTS, listBySlug } from "@/lib/seo/lists";
import { llmsTxt, stockMarkdown } from "@/lib/seo/markdown";
import { ldJson, stockGraph } from "@/lib/seo/schema";
import { faqs, lead, metaDescription, priceStats, summary } from "@/lib/seo/stockCopy";
import { letterOf, sectorKey, sectorPath, stockPath } from "@/lib/seo/site";
import { GET as robots } from "@/app/robots.txt/route";

const row = (o: Partial<MarketRow>): MarketRow => ({
  symbol: "X", name: "X Co", sector: "energy", mcap_cr: 100, price: 10, change: 1, ret_1w: null, ret_1m: null,
  ret_3m: null, ret_1y: null, pe: null, roe: null, dist_high: null, dist_low: null, rsi: null, rank: 1, ...o,
});

function snapshot(o: Partial<StockSnapshot> = {}): StockSnapshot {
  return {
    symbol: "BIGOIL", name: "Big Oil", sector: "energy", sector_label: "Oil & Gas", industry: "Refining",
    website: null, universe_size: 4000,
    market: row({ symbol: "BIGOIL", name: "Big Oil", mcap_cr: 1639061, price: 1209.6, change: 0.34, pe: 20.3, roe: 8.9, ret_1y: -12.12, rank: 1 }),
    as_of: { prices: "eod", price_source: "x", eod_day: "2026-10-16", generated_at: "2026-10-16T18:00:00+00:00" },
    latest: { roe: { value: 8.93, period_label: "Mar 26", period_end: "2026-03-31", basis: "consolidated" } },
    history: {},
    quarters: { basis: "consolidated", quarters: [{
      period_end: "2026-06-30", period_label: "Jun '26", revenue: 309468, ebitda: null, net_profit: 23001, eps_basic: 15.48,
      operating_margin_pct: null, net_margin_pct: 7.43, revenue_yoy_pct: 27.02, net_profit_yoy_pct: -25.03,
      revenue_qoq_pct: null, net_profit_qoq_pct: null, rev_ttm: null, np_ttm: null, eps_ttm: 55.22,
      gross_npa_pct: null, net_npa_pct: null,
    }] },
    shareholding: { quarter: "2026-06-30", promoter_pct: 50.48, pledge_pct: null, groups: [{ label: "Promoters", pct: 50.48 }], quarters: [] },
    peers: [row({ symbol: "ONGC", name: "ONGC", pe: 8, rank: 9 }), row({ symbol: "IOC", name: "Indian Oil", pe: 10, rank: 12 }), row({ symbol: "BPCL", name: "BPCL", pe: 9, rank: 20 })],
    sources: { prices: "NSE" },
    ...o,
  };
}

const bars: Bar[] = Array.from({ length: 260 }, (_, i) => ({
  t: 1_760_000_000 + i * 86_400, o: 100, h: 100 + (i === 50 ? 50 : 1), l: 100 - (i === 200 ? 40 : 1), c: 100 + i / 10, v: 1,
}));

describe("formats", () => {
  it("uses Indian grouping, crores and fiscal periods", () => {
    expect(inr(1209.6)).toBe("₹1,209.60");
    expect(crore(1639061)).toBe("₹16,39,061 Cr");
    expect(crore(1639061, true)).toBe("₹16.39 lakh Cr");
    expect(pct(-25.03, 1, true)).toBe("−25.0%");
    expect(crore(-11900)).toBe("−₹11,900 Cr");
    expect(inr(-4.1)).toBe("−₹4.10");
    expect(fiscalQuarter("2026-06-30")).toBe("Q1 FY27");
    expect(fiscalQuarter("2026-03-31")).toBe("Q4 FY26");
    expect(fiscalYear("2026-03-31")).toBe("FY26");
  });

  it("encodes symbols and sector slugs one way everywhere", () => {
    expect(stockPath("m&m")).toBe("/stock/M%26M");
    expect(sectorPath("capital_goods")).toBe("/sectors/capital-goods");
    expect(sectorKey("capital-goods")).toBe("capital_goods");
    expect(letterOf("3M India")).toBe("0-9");
  });
});

describe("stock copy", () => {
  it("states each fact with its date", () => {
    const s = snapshot();
    const p = priceStats(bars);
    expect(p?.high52).toBe(150);
    expect(p?.low52).toBe(60);
    const text = [...lead(s, p), ...summary(s, p)].join(" ");
    expect(text).toContain("Big Oil (NSE: BIGOIL) closed at");
    expect(text).toContain("ranks 1st by market cap");
    expect(text).toContain("(a large-cap company by SEBI's rank bands)");
    expect(text).toContain("In Q1 FY27, the quarter ended 30 Jun 2026, revenue was ₹3,09,468 Cr (+27.0% year on year)");
    expect(text).toContain("Promoters held 50.48%");
  });

  it("leaves out every sentence whose number is missing", () => {
    const s = snapshot({ market: null, latest: {}, quarters: null, shareholding: null, peers: [], industry: null, sector_label: null });
    expect(lead(s, null)).toEqual([]);
    expect(summary(s, null)).toEqual([]);
    const qs = faqs(s, null).map((f) => f.q);
    // Only the honest-boundary question survives with no data at all.
    expect(qs).toEqual(["Is Big Oil a good stock to buy?"]);
  });

  it("answers 'should I buy' without advice and asks no question for lenders it cannot answer", () => {
    const qa = faqs(snapshot(), priceStats(bars));
    const buy = qa.find((f) => f.q.startsWith("Is Big Oil a good stock"));
    expect(buy?.a).toContain("does not give buy or sell recommendations");
    expect(buy?.a).toContain("This is analysis, not financial advice.");
    const bank = faqs(snapshot({ sector: "banks", latest: { debt_to_equity: { value: 9, period_label: null, period_end: "2026-03-31", basis: "standalone" } } }), null);
    expect(bank.some((f) => f.q.includes("debt-free"))).toBe(false);
  });

  it("writes a description with the searched-for numbers", () => {
    const d = metaDescription(snapshot(), priceStats(bars));
    expect(d).toMatch(/^Big Oil \(BIGOIL\) share price ₹/);
    expect(d).toContain("Market cap ₹16.39 lakh Cr");
    expect(d).toContain("P/E 20.3");
  });

  it("indexes only pages with a price and a body of numbers", () => {
    expect(isIndexable(snapshot())).toBe(true);
    expect(isIndexable(snapshot({ market: null }))).toBe(false);
    expect(isIndexable(snapshot({ quarters: null, shareholding: null, peers: [], history: {} }))).toBe(false);
  });
});

describe("structured data and text versions", () => {
  it("describes the company by ticker and escapes markup", () => {
    const s = snapshot({ name: "</script><b>x" });
    const json = ldJson(stockGraph(s, faqs(s, null), "d", null));
    expect(json).not.toContain("</script>");
    const g = JSON.parse(json) as { "@graph": { "@type": string; tickerSymbol?: string }[] };
    expect(g["@graph"].find((n) => n["@type"] === "Corporation")?.tickerSymbol).toBe("NSE:BIGOIL");
    expect(g["@graph"].some((n) => n["@type"] === "FAQPage")).toBe(true);
  });

  it("renders the Markdown page and llms.txt", () => {
    const s = snapshot();
    const md = stockMarkdown(s, priceStats(bars), faqs(s, null));
    expect(md).toContain("# Big Oil (NSE: BIGOIL)");
    expect(md).toContain("| Q1 FY27 | ₹3,09,468 Cr | +27.0% |");
    const llms = llmsTxt({ as_of: s.as_of!, count: 1, sectors: { energy: "Oil & Gas" }, companies: [s.market!] });
    expect(llms).toMatch(/^# Pivot\n\n> /);
    expect(llms).toContain("/stock/BIGOIL.md");
  });
});

describe("lists", () => {
  it("ranks gainers within the liquid universe only", () => {
    const rows = [row({ symbol: "A", change: 5, rank: 10 }), row({ symbol: "B", change: 19, rank: 3000 }), row({ symbol: "C", change: -2, rank: 5 })];
    expect(listBySlug("top-gainers")!.pick(rows).map((r) => r.symbol)).toEqual(["A"]);
    expect(listBySlug("top-losers")!.pick(rows).map((r) => r.symbol)).toEqual(["C"]);
    expect(new Set(LISTS.map((l) => l.slug)).size).toBe(LISTS.length);
  });
});

describe("robots.txt", () => {
  it("opens the public pages to search and AI crawlers on the site's host", async () => {
    const res = robots(new Request("https://app.pivotnow.in/robots.txt", { headers: { host: "app.pivotnow.in" } }));
    const body = await res.text();
    for (const ua of ["OAI-SearchBot", "Claude-SearchBot", "PerplexityBot", "Googlebot", "Bingbot"]) {
      expect(body).toContain(`User-agent: ${ua}`);
    }
    expect(body).toContain("Allow: /stock/");
    expect(body).toContain("Disallow: /settings");
    expect(body).toContain("Sitemap: https://app.pivotnow.in/sitemap.xml");
  });

  it("closes every other host", async () => {
    const res = robots(new Request("https://pivot-india.example/robots.txt", { headers: { host: "pivot-india.example" } }));
    expect(await res.text()).toBe("User-agent: *\nDisallow: /\n");
  });
});
