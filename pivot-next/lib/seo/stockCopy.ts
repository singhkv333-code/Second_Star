/**
 * The words on a public stock page, written from its numbers.
 *
 * Every sentence here is a template over one field of the snapshot, and a
 * sentence whose field is missing is left out rather than filled in. That is
 * the rule the whole product runs on (never fabricate), and it is also what
 * makes three thousand pages three thousand different pages: no two
 * companies share the same numbers, so no two pages share the same text.
 *
 * Answer engines quote pages that state a fact, its date and its source in
 * one sentence, so each sentence carries all three where it can.
 */

import type { Bar, MarketRow, Quarter, StockSnapshot } from "./data";
import { capBand, crore, day, fiscalQuarter, fiscalYear, inr, num, ordinal, pct, times } from "./format";

export type Faq = { q: string; a: string };

export type PriceStats = {
  last: number;
  lastDay: string;
  high52: number;
  low52: number;
  high52Day: string;
  low52Day: string;
  first: number;
  firstDay: string;
};

function isoDay(t: number): string {
  // Daily bars are stamped at the session's midnight IST.
  return new Date((t + 19_800) * 1000).toISOString().slice(0, 10);
}

/** The 52-week range from the candles themselves, with the days it was set. */
export function priceStats(bars: Bar[]): PriceStats | null {
  if (bars.length < 2) return null;
  const last = bars[bars.length - 1]!;
  const yearAgo = last.t - 365 * 86_400;
  const year = bars.filter((b) => b.t >= yearAgo);
  if (!year.length) return null;
  let hi = year[0]!;
  let lo = year[0]!;
  for (const b of year) {
    if (b.h > hi.h) hi = b;
    if (b.l < lo.l) lo = b;
  }
  return {
    last: last.c,
    lastDay: isoDay(last.t),
    high52: hi.h,
    low52: lo.l,
    high52Day: isoDay(hi.t),
    low52Day: isoDay(lo.t),
    first: year[0]!.c,
    firstDay: isoDay(year[0]!.t),
  };
}

function latestQuarter(s: StockSnapshot): Quarter | null {
  return s.quarters?.quarters?.[0] ?? null;
}

function moved(v: number): string {
  return v >= 0 ? "risen" : "fallen";
}

/** The opening paragraph: what the company is and where its price stands. */
export function lead(s: StockSnapshot, p: PriceStats | null): string[] {
  const m = s.market;
  const out: string[] = [];
  const price = p?.last ?? m?.price ?? null;
  const asOf = p?.lastDay ?? s.as_of?.eod_day ?? null;
  if (price != null) {
    const change = m?.change != null && (!p || p.lastDay === s.as_of?.eod_day)
      ? `, ${m.change >= 0 ? "up" : "down"} ${pct(Math.abs(m.change), 2)} on the day`
      : "";
    out.push(`${s.name} (NSE: ${s.symbol}) closed at ${inr(price)} on ${day(asOf)}${change}.`);
  }
  if (m?.mcap_cr && m.rank && s.universe_size) {
    const band = capBand(m.rank);
    const sector = s.sector_label ? ` in the ${s.sector_label} sector` : "";
    out.push(
      `With a market capitalisation of ${crore(m.mcap_cr)}, it ranks ${ordinal(m.rank)} by market cap among the ` +
      `${num(s.universe_size, 0)} NSE companies Pivot tracks${band ? `, which makes it a ${band} stock by SEBI's rank bands` : ""}${sector}.`,
    );
  }
  if (s.industry) out.push(`Its industry is ${s.industry}.`);
  return out;
}

/** Valuation, returns, results and ownership, one sentence each. */
export function summary(s: StockSnapshot, p: PriceStats | null): string[] {
  const m = s.market;
  const out: string[] = [];
  const roe = s.latest.roe;
  if (m?.pe) {
    out.push(
      `At its last close the stock traded at ${times(m.pe)} trailing earnings` +
      (roe ? `, against a return on equity of ${pct(roe.value)} in ${fiscalYear(roe.period_end)}` : "") + ".",
    );
  } else if (roe) {
    out.push(`Its return on equity was ${pct(roe.value)} in ${fiscalYear(roe.period_end)}.`);
  }
  if (m?.ret_1y != null) {
    out.push(`Over the past year the share price has ${moved(m.ret_1y)} ${pct(Math.abs(m.ret_1y))}.`);
  }
  if (p) {
    const fromHigh = ((p.last - p.high52) / p.high52) * 100;
    out.push(
      `Its 52-week range is ${inr(p.low52)} (${day(p.low52Day)}) to ${inr(p.high52)} (${day(p.high52Day)}); ` +
      `the last close is ${pct(Math.abs(fromHigh))} ${fromHigh < 0 ? "below" : "above"} the high.`,
    );
  }
  const q = latestQuarter(s);
  if (q && q.revenue != null && q.net_profit != null) {
    const ry = q.revenue_yoy_pct != null ? ` (${pct(q.revenue_yoy_pct, 1, true)} year on year)` : "";
    const ny = q.net_profit_yoy_pct != null ? ` (${pct(q.net_profit_yoy_pct, 1, true)})` : "";
    out.push(
      `In ${fiscalQuarter(q.period_end)}, the quarter ended ${day(q.period_end)}, revenue was ${crore(q.revenue)}${ry} ` +
      `and net profit ${crore(q.net_profit)}${ny}, on a ${s.quarters?.basis ?? "reported"} basis.`,
    );
  }
  const sh = s.shareholding;
  if (sh?.promoter_pct != null) {
    const pledge = sh.pledge_pct ? `; ${pct(sh.pledge_pct, 2)} of promoter shares were pledged` : "";
    out.push(`Promoters held ${pct(sh.promoter_pct, 2)} of the company at the end of ${day(sh.quarter)}${pledge}.`);
  }
  return out;
}

export function peerNames(peers: MarketRow[], n = 4): string {
  const names = peers.slice(0, n).map((p) => p.name);
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The questions people type about a stock, answered from the page. Only
 *  questions the data can answer are asked. */
export function faqs(s: StockSnapshot, p: PriceStats | null): Faq[] {
  const m = s.market;
  const out: Faq[] = [];
  const n = s.name;
  const price = p?.last ?? m?.price;
  if (price != null) {
    out.push({
      q: `What is the share price of ${n} today?`,
      a: `${n} (NSE: ${s.symbol}) last closed at ${inr(price)} on ${day(p?.lastDay ?? s.as_of?.eod_day)}.` +
        (p ? ` Its 52-week high is ${inr(p.high52)} and its 52-week low is ${inr(p.low52)}.` : ""),
    });
  }
  if (m?.mcap_cr) {
    out.push({
      q: `What is the market cap of ${n}?`,
      a: `${n}'s market capitalisation is ${crore(m.mcap_cr)} (${crore(m.mcap_cr, true)}) at the ${day(s.as_of?.eod_day)} close, ` +
        `ranking ${ordinal(m.rank)} among the NSE companies Pivot tracks.`,
    });
  }
  if (m?.pe) {
    const peers = s.peers.filter((x) => x.pe);
    const median = peers.length >= 3 ? [...peers.map((x) => x.pe!)].sort((a, b) => a - b)[Math.floor(peers.length / 2)]! : null;
    out.push({
      q: `What is the P/E ratio of ${n}?`,
      a: `${n} trades at a trailing P/E of ${num(m.pe, 1)}.` +
        (median ? ` The median P/E of the ${peers.length} closest ${s.sector_label ?? "sector"} peers by market cap is ${num(median, 1)}.` : ""),
    });
  }
  const roe = s.latest.roe;
  const roce = s.latest.roce;
  if (roe || roce) {
    out.push({
      q: `What is the ROE and ROCE of ${n}?`,
      a: [
        roe ? `Return on equity was ${pct(roe.value)} in ${fiscalYear(roe.period_end)}` : null,
        roce ? `return on capital employed was ${pct(roce.value)} in ${fiscalYear(roce.period_end)}` : null,
      ].filter(Boolean).join(" and ") + ` (${roe?.basis ?? roce?.basis ?? "reported"} figures).`,
    });
  }
  const de = s.latest.debt_to_equity;
  // A lender's balance sheet is mostly deposits and borrowings by design, so
  // "debt-free" is not a question that means anything for one.
  if (de && s.sector !== "banks" && s.sector !== "financials") {
    out.push({
      q: `Is ${n} a debt-free company?`,
      a: de.value <= 0.05
        ? `${n} is close to debt-free: its debt-to-equity ratio was ${num(de.value)} in ${fiscalYear(de.period_end)}.`
        : `No. ${n}'s debt-to-equity ratio was ${num(de.value)} in ${fiscalYear(de.period_end)}` +
          (s.latest.total_debt ? `, with total debt of ${crore(s.latest.total_debt.value)}` : "") + ".",
    });
  }
  const q = latestQuarter(s);
  if (q && q.revenue != null && q.net_profit != null) {
    out.push({
      q: `What were ${n}'s latest quarterly results?`,
      a: `For ${fiscalQuarter(q.period_end)} (quarter ended ${day(q.period_end)}), ${n} reported revenue of ${crore(q.revenue)}` +
        (q.revenue_yoy_pct != null ? `, ${pct(q.revenue_yoy_pct, 1, true)} year on year,` : "") +
        ` and net profit of ${crore(q.net_profit)}` +
        (q.net_profit_yoy_pct != null ? `, ${pct(q.net_profit_yoy_pct, 1, true)} year on year` : "") + ".",
    });
  }
  if (m?.ret_1y != null) {
    out.push({
      q: `How has ${n} stock performed over the last year?`,
      a: `${n}'s share price has ${moved(m.ret_1y)} ${pct(Math.abs(m.ret_1y))} over the past year` +
        (m.ret_3m != null ? `, ${pct(m.ret_3m, 1, true)} over three months` : "") +
        (m.ret_1m != null ? ` and ${pct(m.ret_1m, 1, true)} over one month` : "") + `, to the ${day(s.as_of?.eod_day)} close.`,
    });
  }
  const sh = s.shareholding;
  if (sh?.groups?.length) {
    out.push({
      q: `What is the shareholding pattern of ${n}?`,
      a: `At the end of ${day(sh.quarter)}: ` + sh.groups.map((g) => `${g.label.toLowerCase()} ${pct(g.pct, 2)}`).join(", ") +
        (sh.pledge_pct ? `. ${pct(sh.pledge_pct, 2)} of promoter shares were pledged.` : "."),
    });
  }
  if (s.peers.length) {
    out.push({
      q: `Who are ${n}'s competitors?`,
      a: `The listed ${s.sector_label ?? "sector"} companies closest to ${n} by market cap are ${peerNames(s.peers, 6)}.`,
    });
  }
  out.push({
    q: `Is ${n} a good stock to buy?`,
    a: `Pivot does not give buy or sell recommendations. This page sets out ${n}'s price history, valuation, ` +
      `profitability, quarterly results and ownership so you can judge it yourself, and Pivot's chart lets you test ` +
      `a strategy on ${s.symbol} against its own history before risking money. This is analysis, not financial advice.`,
  });
  return out;
}

/** The meta description: the numbers a searcher wants, under ~160 characters
 *  where the data allows. */
export function metaDescription(s: StockSnapshot, p: PriceStats | null): string {
  const m = s.market;
  const parts: string[] = [];
  const price = p?.last ?? m?.price;
  if (price != null) parts.push(`${s.name} (${s.symbol}) share price ${inr(price)} on ${day(p?.lastDay ?? s.as_of?.eod_day)}`);
  else parts.push(`${s.name} (${s.symbol}) share price`);
  const facts = [
    m?.mcap_cr ? `market cap ${crore(m.mcap_cr, true)}` : null,
    m?.pe ? `P/E ${num(m.pe, 1)}` : null,
    s.latest.roe ? `ROE ${pct(s.latest.roe.value)}` : null,
  ].filter(Boolean);
  if (facts.length) parts.push(facts.join(", "));
  parts.push("Live chart, financials, quarterly results, shareholding and peers");
  return `${parts.join(". ")}.`;
}
