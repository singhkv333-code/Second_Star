/**
 * Every instrument Pivot charts, not only listed companies: stocks, indices,
 * crypto, MCX commodities and currency pairs.
 *
 * The company search (`/api/companies/search`) and the company quote
 * (`/api/markets/quote`) only know listed securities — they have no crypto,
 * and "GOLD" there resolves to a gold ETF, not MCX gold. The chart server
 * (the dataserver, same origin) holds the whole universe: `/symbols` names and logos
 * every instrument, `/quotes` prices any of them, `/bars` gives their history.
 */

import { searchCompanies } from "@/lib/api";
import { isError } from "@/lib/types";

export type InstrumentKind = "stock" | "index" | "crypto" | "commodity" | "currency";

export type Instrument = {
  symbol: string;
  /** What a person calls it: "RELIANCE", "Nifty 50", "Bitcoin", "Gold". */
  title: string;
  /** One quiet line under the title: the company, or the kind and venue. */
  sub: string;
  kind: InstrumentKind;
  logo: string | null;
};

const MCX = new Set(["GOLD", "GOLDM", "SILVER", "SILVERM", "COPPER", "ZINC", "ALUMINIUM", "CRUDEOIL", "CRUDEOILM", "NATURALGAS", "NATGASMINI", "LEAD", "NICKEL"]);
const INDEX = /^(NIFTY\b|NIFTY$|BANKNIFTY$|FINNIFTY$|MIDCPNIFTY$|SENSEX$|BANKEX$|INDIA VIX$)/;

export function kindOf(symbol: string): InstrumentKind {
  const s = symbol.toUpperCase();
  if (/(USDT|-USD)$/.test(s)) return "crypto";
  if (MCX.has(s)) return "commodity";
  if (/^(USD|EUR|GBP|JPY)INR$/.test(s)) return "currency";
  if (INDEX.test(s)) return "index";
  return "stock";
}

/** Stocks open their company page; everything else opens in the chart. */
export function opensInChart(symbol: string): boolean {
  return kindOf(symbol) !== "stock";
}

type SymbolsPayload = {
  symbols?: string[];
  names?: Record<string, string>;
  long?: Record<string, string>;
  alias?: Record<string, string>;
  logos?: Record<string, string>;
};

// The chart's own instrument art lives under the chart app's path.
const logoUrl = (u?: string | null): string | null =>
  !u ? null : u.startsWith("/assets/") ? `/chart-app${u}` : u;

function describe(symbol: string, p: SymbolsPayload): Instrument {
  const kind = kindOf(symbol);
  const short = p.alias?.[symbol] || p.names?.[symbol] || symbol;
  const long = p.long?.[symbol] || p.names?.[symbol] || "";
  const logo = logoUrl(p.logos?.[symbol]);
  if (kind === "crypto") {
    const base = symbol.replace(/(USDT|-USD)$/, "");
    const venue = symbol.endsWith("USDT") ? "Bybit" : "Coinbase";
    return { symbol, kind, logo, title: short, sub: `${base} · ${venue}` };
  }
  if (kind === "index") return { symbol, kind, logo, title: p.names?.[symbol] || symbol, sub: "Index" };
  if (kind === "commodity") return { symbol, kind, logo, title: p.names?.[symbol] || symbol, sub: "MCX" };
  if (kind === "currency") return { symbol, kind, logo, title: p.names?.[symbol] || symbol, sub: "Currency" };
  return { symbol, kind, logo, title: symbol, sub: long || short };
}

let universe: Promise<Map<string, Instrument>> | null = null;

/** The whole chartable universe, fetched once per page. */
export function loadUniverse(): Promise<Map<string, Instrument>> {
  universe ??= fetch("/symbols")
    .then((r) => r.json() as Promise<SymbolsPayload>)
    .then((p) => {
      const all = new Set([...(p.symbols ?? []), ...Object.keys(p.names ?? {})]);
      return new Map([...all].map((s) => [s, describe(s, p)]));
    })
    .catch(() => { universe = null; return new Map(); });
  return universe;
}

/** Any symbol, described even when the universe has not loaded. */
export function fallback(symbol: string): Instrument {
  return describe(symbol, {});
}

/**
 * Search everything: crypto, indices, commodities and currencies from the
 * chart's universe, stocks from the company search, ranked so an exact or
 * leading match comes first.
 */
export async function searchInstruments(query: string, limit = 10): Promise<Instrument[]> {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const [map, companies] = await Promise.all([
    loadUniverse(),
    searchCompanies(query.trim(), 8).catch(() => null),
  ]);
  const score = (i: Instrument): number => {
    const hay = [i.symbol, i.title, i.sub].map((x) => x.toLowerCase());
    if (hay.some((h) => h === q)) return 0;
    if (hay.some((h) => h.startsWith(q))) return 1;
    if (hay.some((h) => h.split(/[\s/·&-]+/).some((w) => w.startsWith(q)))) return 2;
    return hay.some((h) => h.includes(q)) ? 3 : 9;
  };
  const local = [...map.values()].map((i) => ({ i, s: score(i) })).filter((x) => x.s < 9);
  const fromApi: Instrument[] = companies && !isError(companies)
    ? companies.data.results.map((r) => {
        if (map.has(r.symbol)) return map.get(r.symbol)!;
        const kind = kindOf(r.symbol);
        const listed = kind === "stock";
        return {
          ...fallback(r.symbol),
          title: listed ? r.symbol : r.name,
          sub: listed ? (r.instrument_type === "ETF" ? `${r.name} · ETF` : r.name) : fallback(r.symbol).sub,
          logo: r.logo_url ?? null,
        };
      })
    : [];
  const ranked = [
    ...local.filter((x) => x.i.kind !== "stock").sort((a, b) => a.s - b.s).map((x) => x.i),
    ...fromApi,
    ...local.filter((x) => x.i.kind === "stock").sort((a, b) => a.s - b.s).map((x) => x.i),
  ];
  const seen = new Set<string>();
  return ranked.filter((i) => !seen.has(i.symbol) && seen.add(i.symbol)).slice(0, limit);
}

export type ChartQuote = { symbol: string; last: number | null; change_pct?: number | null; currency?: string };

/** Live prices for any instruments, in one call to the chart server. */
export async function chartQuotes(symbols: string[]): Promise<Map<string, ChartQuote>> {
  if (!symbols.length) return new Map();
  try {
    const r = await fetch(`/quotes?symbols=${encodeURIComponent(symbols.join(","))}`);
    const d = (await r.json()) as { quotes?: ChartQuote[] };
    return new Map((d.quotes ?? []).map((q) => [q.symbol, q]));
  } catch {
    return new Map();
  }
}

/** About a month of daily closes from the chart server, for a sparkline. */
export async function chartSpark(symbol: string): Promise<number[] | null> {
  try {
    const r = await fetch(`/bars?symbol=${encodeURIComponent(symbol)}&interval=1d&limit=30`);
    const d = (await r.json()) as { bars?: { c: number }[] };
    const closes = (d.bars ?? []).map((b) => b.c);
    return closes.length >= 2 ? closes : null;
  } catch {
    return null;
  }
}
