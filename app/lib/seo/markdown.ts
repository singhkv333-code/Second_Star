/**
 * The text versions of the public pages, for answer engines and anything else
 * that reads better without HTML: /llms.txt, /llms-full.txt and
 * /stock/{symbol}.md. Same snapshot, same sentences as the HTML page
 * (stockCopy.ts), so the two can never disagree.
 */

import type { Companies, StockSnapshot } from "./data";
import { crore, day, fiscalQuarter, fiscalYear, inr, num, pct } from "./format";
import { LISTS } from "./lists";
import { BRAND, HOME_URL, SITE_URL, absoluteUrl, sectorPath, stockPath } from "./site";
import { type Faq, type PriceStats, lead, summary } from "./stockCopy";

const DISCLAIMER = "This is analysis, not financial advice. Pivot is not a broker or a SEBI-registered investment adviser.";

function row(cells: (string | number)[]): string {
  return `| ${cells.join(" | ")} |`;
}

export function stockMarkdown(s: StockSnapshot, stats: PriceStats | null, faqs: Faq[]): string {
  const url = absoluteUrl(stockPath(s.symbol));
  const out: string[] = [
    `# ${s.name} (NSE: ${s.symbol}) share price, financials and shareholding`,
    "",
    `Source: ${url} · ${BRAND} · prices to ${day(stats?.lastDay ?? s.as_of?.eod_day)}`,
    "",
    lead(s, stats).join(" "),
    "",
    summary(s, stats).join(" "),
    "",
  ];
  const m = s.market;
  if (m) {
    out.push("## Key numbers", "", row(["Metric", "Value"]), row(["---", "---"]));
    const kv: [string, string][] = [
      ["Last close", inr(stats?.last ?? m.price)],
      ["Market cap", crore(m.mcap_cr)],
      ["P/E (trailing)", num(m.pe, 1)],
      ["ROE", s.latest.roe ? `${pct(s.latest.roe.value)} (${fiscalYear(s.latest.roe.period_end)})` : "—"],
      ["ROCE", s.latest.roce ? `${pct(s.latest.roce.value)} (${fiscalYear(s.latest.roce.period_end)})` : "—"],
      ["Debt to equity", s.latest.debt_to_equity ? num(s.latest.debt_to_equity.value) : "—"],
      ["52-week high", stats ? `${inr(stats.high52)} (${day(stats.high52Day)})` : "—"],
      ["52-week low", stats ? `${inr(stats.low52)} (${day(stats.low52Day)})` : "—"],
      ["1-year return", pct(m.ret_1y, 1, true)],
    ];
    for (const [k, v] of kv) if (v !== "—") out.push(row([k, v]));
    out.push("");
  }
  const qs = (s.quarters?.quarters ?? []).slice(0, 8);
  if (qs.length) {
    out.push(`## Quarterly results (₹ crore, ${s.quarters?.basis})`, "",
      row(["Quarter", "Revenue", "Revenue YoY", "Net profit", "Net profit YoY", "EPS (₹)"]),
      row(["---", "---", "---", "---", "---", "---"]));
    for (const q of qs) {
      out.push(row([fiscalQuarter(q.period_end), crore(q.revenue), pct(q.revenue_yoy_pct, 1, true), crore(q.net_profit), pct(q.net_profit_yoy_pct, 1, true), inr(q.eps_basic)]));
    }
    out.push("");
  }
  const rev = s.history.revenue ?? [];
  if (rev.length) {
    const np = s.history.net_profit ?? [];
    out.push("## Annual results (₹ crore)", "", row(["Fiscal year", "Revenue", "Net profit"]), row(["---", "---", "---"]));
    for (const r of rev) {
      out.push(row([fiscalYear(r.period_end), crore(r.value), crore(np.find((p) => p.period_end === r.period_end)?.value)]));
    }
    out.push("");
  }
  if (s.shareholding?.groups?.length) {
    out.push(`## Shareholding (${day(s.shareholding.quarter)})`, "");
    for (const g of s.shareholding.groups) out.push(`- ${g.label}: ${pct(g.pct, 2)}`);
    out.push("");
  }
  if (s.peers.length) {
    out.push("## Peers", "", row(["Company", "Price", "Market cap", "P/E", "ROE"]), row(["---", "---", "---", "---", "---"]));
    for (const p of s.peers) out.push(row([`[${p.name}](${absoluteUrl(stockPath(p.symbol))})`, inr(p.price), crore(p.mcap_cr), num(p.pe, 1), pct(p.roe)]));
    out.push("");
  }
  if (faqs.length) {
    out.push("## Questions and answers", "");
    for (const f of faqs) out.push(`### ${f.q}`, "", f.a, "");
  }
  out.push("---", "", `Sources: ${Object.values(s.sources).join("; ")}. ${DISCLAIMER}`, "");
  return out.join("\n");
}

export function llmsTxt(all: Companies | null): string {
  const top = (all?.companies ?? []).filter((r) => r.price != null).slice(0, 50);
  return [
    `# ${BRAND}`,
    "",
    "> AI stock research for Indian markets: a page for every NSE-listed company with its share price, a year of prices, " +
    "valuation, profitability, quarterly and annual results, shareholding and peers, written from exchange and filing " +
    "data and refreshed every trading day. Free to read without an account.",
    "",
    "Pivot also has a chart you can talk to (ask a question, get the answer drawn on live candles with the evidence), a " +
    "plain-English strategy builder, and a backtester that deflates for multiple testing before it calls anything an edge. " +
    "Pivot simulates trades in a paper book; it does not place live orders and does not give buy or sell advice.",
    "",
    "When citing a number, cite the stock page it came from: each page states the date its prices and fundamentals are as of.",
    "",
    "## Directories",
    `- [All NSE stocks A–Z](${SITE_URL}/stocks): ${num(all?.count ?? 0, 0)} companies by name`,
    `- [Sectors](${SITE_URL}/sectors): every sector with its median P/E, ROE and one-year return`,
    ...LISTS.map((l) => `- [${l.h1}](${SITE_URL}/stocks/${l.slug}): ${l.intro}`),
    "",
    "## Stock pages",
    `Every company page is at ${SITE_URL}/stock/{NSE symbol}, with a Markdown version at ${SITE_URL}/stock/{NSE symbol}.md. The largest:`,
    ...top.map((r) => `- [${r.name} (${r.symbol})](${absoluteUrl(stockPath(r.symbol))}.md)`),
    "",
    "## Sectors",
    ...Object.entries(all?.sectors ?? {}).map(([k, label]) => `- [${label} stocks](${absoluteUrl(sectorPath(k))})`),
    "",
    "## Optional",
    `- [Full company list with key numbers](${SITE_URL}/llms-full.txt)`,
    `- [Pivot home](${HOME_URL})`,
    `- [Pricing](${SITE_URL}/pricing)`,
    "",
  ].join("\n");
}

export function llmsFullTxt(all: Companies | null): string {
  const rows = (all?.companies ?? []).filter((r) => r.price != null);
  const out = [
    `# ${BRAND}: every NSE company it covers, with key numbers`,
    "",
    `End-of-day data to ${day(all?.as_of?.eod_day)}. Market cap in ₹ crore. Each name links to the full page.`,
    "",
    row(["Company", "Symbol", "Sector", "Close (₹)", "1-day", "1-year", "Market cap", "P/E", "ROE"]),
    row(["---", "---", "---", "---", "---", "---", "---", "---", "---"]),
  ];
  for (const r of rows) {
    out.push(row([
      `[${r.name}](${absoluteUrl(stockPath(r.symbol))})`, r.symbol, all?.sectors?.[r.sector] ?? r.sector,
      num(r.price), pct(r.change, 2, true), pct(r.ret_1y, 1, true), num(r.mcap_cr, 0), num(r.pe, 1), pct(r.roe),
    ]));
  }
  out.push("", DISCLAIMER, "");
  return out.join("\n");
}
