/**
 * The daily and evergreen stock lists: each one a page people search for by
 * name ("top gainers today", "52 week high stocks", "top 100 companies in
 * India by market cap"), rebuilt from the same end-of-day snapshot as the
 * stock pages.
 *
 * Lists rank within the 1,000 largest companies. Below that a "top gainer"
 * is usually an illiquid name on an upper circuit, which is noise, not news
 * (the Markets tab draws the same line at 500 for the same reason).
 */

import type { Column } from "@/components/seo/CompanyTable";
import type { MarketRow } from "./data";

export type ListDef = {
  slug: string;
  title: (day: string) => string;
  h1: string;
  intro: string;
  columns: Column[];
  pick: (rows: MarketRow[]) => MarketRow[];
};

const LIQUID = 1000;
const liquid = (rows: MarketRow[]): MarketRow[] => rows.filter((r) => r.rank <= LIQUID && r.price != null);

export const LISTS: ListDef[] = [
  {
    slug: "top-gainers",
    title: (d) => `Top Gainers Today on NSE (${d}): Biggest Stock Price Gains`,
    h1: "Top gainers on the NSE",
    intro: "The 50 stocks that rose the most in the last session, among the 1,000 largest NSE companies by market cap.",
    columns: ["change", "ret_1y", "mcap", "pe"],
    pick: (rows) => liquid(rows).filter((r) => (r.change ?? 0) > 0).sort((a, b) => b.change! - a.change!).slice(0, 50),
  },
  {
    slug: "top-losers",
    title: (d) => `Top Losers Today on NSE (${d}): Biggest Stock Price Falls`,
    h1: "Top losers on the NSE",
    intro: "The 50 stocks that fell the most in the last session, among the 1,000 largest NSE companies by market cap.",
    columns: ["change", "ret_1y", "mcap", "pe"],
    pick: (rows) => liquid(rows).filter((r) => (r.change ?? 0) < 0).sort((a, b) => a.change! - b.change!).slice(0, 50),
  },
  {
    slug: "52-week-high",
    title: (d) => `Stocks at 52-Week High on NSE (${d})`,
    h1: "Stocks at or near their 52-week high",
    intro: "Large and mid-sized NSE stocks closing within 2% of their highest price of the past year, nearest first.",
    columns: ["dist_high", "change", "ret_1y", "mcap"],
    pick: (rows) => liquid(rows).filter((r) => r.dist_high != null && r.dist_high >= -2)
      .sort((a, b) => b.dist_high! - a.dist_high!).slice(0, 100),
  },
  {
    slug: "52-week-low",
    title: (d) => `Stocks at 52-Week Low on NSE (${d})`,
    h1: "Stocks at or near their 52-week low",
    intro: "Large and mid-sized NSE stocks closing within 2% of their lowest price of the past year, nearest first.",
    columns: ["dist_low", "change", "ret_1y", "mcap"],
    pick: (rows) => liquid(rows).filter((r) => r.dist_low != null && r.dist_low <= 2)
      .sort((a, b) => a.dist_low! - b.dist_low!).slice(0, 100),
  },
  {
    slug: "largest-companies",
    title: () => "Top 100 Companies in India by Market Cap (NSE)",
    h1: "The 100 largest companies on the NSE",
    intro: "India's most valuable listed companies, ranked by market capitalisation at the last close.",
    columns: ["mcap", "change", "ret_1y", "pe", "roe"],
    pick: (rows) => rows.filter((r) => r.price != null).slice(0, 100),
  },
  {
    slug: "low-pe-stocks",
    title: () => "Low P/E Stocks in India: Profitable NSE Companies Trading Below 15× Earnings",
    h1: "Low P/E stocks with a return on equity above 15%",
    intro: "NSE companies among the 1,000 largest trading below 15 times trailing earnings while earning more than 15% on equity, cheapest first. A low P/E can also mean the market expects earnings to fall.",
    columns: ["pe", "roe", "mcap", "ret_1y"],
    pick: (rows) => liquid(rows).filter((r) => r.pe != null && r.pe > 0 && r.pe < 15 && (r.roe ?? 0) > 15)
      .sort((a, b) => a.pe! - b.pe!).slice(0, 100),
  },
  {
    slug: "high-roe-stocks",
    title: () => "High ROE Stocks in India: NSE Companies With the Best Return on Equity",
    h1: "Stocks with the highest return on equity",
    intro: "The 100 companies among the 1,000 largest on the NSE earning the most on shareholders' equity in their latest fiscal year.",
    columns: ["roe", "pe", "mcap", "ret_1y"],
    pick: (rows) => liquid(rows).filter((r) => r.roe != null && r.roe > 0 && r.roe < 200)
      .sort((a, b) => b.roe! - a.roe!).slice(0, 100),
  },
];

export function listBySlug(slug: string): ListDef | undefined {
  return LISTS.find((l) => l.slug === slug);
}
