/**
 * The Markets tab's data and arithmetic.
 *
 *   /api/markets/explore   the 500 largest companies: every stock's numbers,
 *                          breadth and sectors (pivot/backend/services/market_explore.py)
 *   /api/markets/brief     three sentences written from those numbers only
 *   /quotes, /bars         indices, commodities, crypto and currencies (chart server)
 *   /feeds, /calendar      market news and the events calendar (chart server)
 *
 * The lists, battles and treemap are computed here from the explore payload,
 * so switching a list or a sector never waits on the network. Everything is a
 * pure function of its inputs and is tested in tests/lib/markets.test.ts.
 */

import { request } from "@/lib/api";
import type { ApiResult } from "@/lib/types";

// ── payload ──────────────────────────────────────────────────────────────

export type MarketStock = {
  symbol: string;
  name: string;
  sector: string;
  mcap_cr: number;
  price: number | null;
  change: number | null;
  ret_1w: number | null;
  ret_1m: number | null;
  ret_3m: number | null;
  ret_1y: number | null;
  pe: number | null;
  roe: number | null;
  rsi: number | null;
  vol_ratio: number | null;
  turnover_cr: number | null;
  dist_high: number | null;
  dist_low: number | null;
  streak: number | null;
  bb_width: number | null;
  adx: number | null;
  sma50: number | null;
  sma200: number | null;
  breakout: number | null;
  vp_pos: number | null;
  vp_shift: number | null;
  logo: string | null;
};

export type MarketSector = {
  key: string;
  label: string;
  count: number;
  mcap_cr: number;
  change: number | null;
  ret_1w: number | null;
  ret_1m: number | null;
  ret_1y: number | null;
  advancers: number;
  decliners: number;
  pe_median: number | null;
  leaders: string[];
  best: string | null;
  worst: string | null;
  rs_1m: number | null;
  rs_1w: number | null;
};

export type MarketBreadth = {
  total: number;
  advancers: number;
  decliners: number;
  unchanged: number;
  median_change: number | null;
  above_sma50: number;
  sma50_total: number;
  above_sma200: number;
  sma200_total: number;
  near_high: number;
  near_low: number;
  extremes_total: number;
  histogram: { edges: number[]; counts: number[] };
};

export type MarketExplore = {
  as_of: {
    prices: "live" | "eod";
    price_source: string;
    live_coverage: number;
    eod_day: string | null;
    generated_at: string;
  };
  universe: { basis: string; size: number };
  breadth: MarketBreadth;
  sectors: MarketSector[];
  stocks: MarketStock[];
};

export type MarketBrief = { text: string; source: "model" | "computed"; as_of: MarketExplore["as_of"] };

export function getMarketExplore(): Promise<ApiResult<MarketExplore>> {
  return request<MarketExplore>("/markets/explore");
}

export function getMarketBrief(): Promise<ApiResult<MarketBrief>> {
  return request<MarketBrief>("/markets/brief");
}

// ── periods ──────────────────────────────────────────────────────────────

export type Period = "1D" | "1W" | "1M" | "1Y";
export const PERIODS: Period[] = ["1D", "1W", "1M", "1Y"];

export function stockReturn(s: MarketStock, p: Period): number | null {
  return p === "1D" ? s.change : p === "1W" ? s.ret_1w : p === "1M" ? s.ret_1m : s.ret_1y;
}

export function sectorReturn(s: MarketSector, p: Period): number | null {
  return p === "1D" ? s.change : p === "1W" ? s.ret_1w : p === "1M" ? s.ret_1m : s.ret_1y;
}

// ── lenses: the explorations ─────────────────────────────────────────────

export type LensColumn = {
  label: string;
  value: (s: MarketStock, ctx: LensContext) => string;
  /** Signed values are tinted as gains and losses. */
  signed?: (s: MarketStock) => number | null;
};

export type LensContext = { sectorPe: Map<string, number> };

export type Lens = {
  id: string;
  group: "Movers" | "Trend" | "Value" | "Setups";
  label: string;
  /** One line, shown on hover: what qualifies and how it is ranked. */
  hint: string;
  column: LensColumn;
  pick: (stocks: MarketStock[], ctx: LensContext) => MarketStock[];
};

const has = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);
/** A comparator: largest first, or smallest first with `asc`. */
const by = (f: (s: MarketStock) => number | null, asc = false) =>
  (a: MarketStock, b: MarketStock): number => (asc ? 1 : -1) * ((f(a) ?? 0) - (f(b) ?? 0));

export const fmtPct = (v: number | null | undefined, digits = 2): string =>
  has(v) ? `${v > 0 ? "+" : ""}${v.toFixed(digits)}%` : "—";
const fmtX = (v: number | null): string => (has(v) ? `${v.toFixed(1)}×` : "—");

export const LENSES: Lens[] = [
  {
    id: "gainers", group: "Movers", label: "Top gainers",
    hint: "Largest rise in the session",
    column: { label: "1M", value: (s) => fmtPct(s.ret_1m, 1), signed: (s) => s.ret_1m },
    pick: (xs) => xs.filter((s) => has(s.change) && s.change > 0).sort(by((s) => s.change)),
  },
  {
    id: "losers", group: "Movers", label: "Top losers",
    hint: "Largest fall in the session",
    column: { label: "1M", value: (s) => fmtPct(s.ret_1m, 1), signed: (s) => s.ret_1m },
    pick: (xs) => xs.filter((s) => has(s.change) && s.change < 0).sort(by((s) => s.change, true)),
  },
  {
    id: "active", group: "Movers", label: "Most traded",
    hint: "Highest average daily value traded over 20 sessions",
    column: { label: "Daily value", value: (s) => (has(s.turnover_cr) ? `₹${fmtCr(s.turnover_cr)}` : "—") },
    pick: (xs) => xs.filter((s) => has(s.turnover_cr)).sort(by((s) => s.turnover_cr)),
  },
  {
    id: "volume", group: "Movers", label: "Volume surge",
    hint: "Session volume at least twice its 20-day average",
    column: { label: "Volume", value: (s) => fmtX(s.vol_ratio) },
    pick: (xs) => xs.filter((s) => has(s.vol_ratio) && s.vol_ratio >= 2).sort(by((s) => s.vol_ratio)),
  },
  {
    id: "highs", group: "Trend", label: "52W highs",
    hint: "Within 1% of the 52-week high, strongest year first",
    column: { label: "1Y", value: (s) => fmtPct(s.ret_1y, 1), signed: (s) => s.ret_1y },
    pick: (xs) => xs.filter((s) => has(s.dist_high) && s.dist_high >= -1).sort(by((s) => s.ret_1y)),
  },
  {
    id: "lows", group: "Trend", label: "52W lows",
    hint: "Within 1% of the 52-week low, weakest year first",
    column: { label: "1Y", value: (s) => fmtPct(s.ret_1y, 1), signed: (s) => s.ret_1y },
    pick: (xs) => xs.filter((s) => has(s.dist_low) && s.dist_low <= 1).sort(by((s) => s.ret_1y, true)),
  },
  {
    id: "breakouts", group: "Trend", label: "Breakouts",
    hint: "Closed above its 20-day high, heaviest volume first",
    column: { label: "Volume", value: (s) => fmtX(s.vol_ratio) },
    pick: (xs) => xs.filter((s) => has(s.breakout) && s.breakout > 0).sort(by((s) => s.vol_ratio)),
  },
  {
    id: "streaks", group: "Trend", label: "Streaks",
    hint: "Longest runs of higher or lower closes",
    column: {
      label: "Run",
      value: (s) => (has(s.streak) ? `${Math.abs(s.streak)} ${s.streak > 0 ? "up" : "down"}` : "—"),
      signed: (s) => s.streak,
    },
    pick: (xs) => xs.filter((s) => has(s.streak) && Math.abs(s.streak) >= 4)
      .sort((a, b) => Math.abs(b.streak ?? 0) - Math.abs(a.streak ?? 0)),
  },
  {
    id: "cheap", group: "Value", label: "Cheaper than sector",
    hint: "P/E under 70% of its sector's median, with ROE of 15% or more",
    column: {
      label: "P/E · sector",
      value: (s, c) => (has(s.pe) ? `${s.pe.toFixed(1)} · ${c.sectorPe.get(s.sector)?.toFixed(1) ?? "—"}` : "—"),
    },
    pick: (xs, c) => xs
      .filter((s) => {
        const med = c.sectorPe.get(s.sector);
        return has(s.pe) && has(s.roe) && has(med) && s.pe < 0.7 * med && s.roe >= 15;
      })
      .sort((a, b) => (a.pe! / c.sectorPe.get(a.sector)!) - (b.pe! / c.sectorPe.get(b.sector)!)),
  },
  {
    id: "oversold", group: "Value", label: "Oversold quality",
    hint: "RSI under 35 with ROE of 15% or more",
    column: { label: "RSI", value: (s) => (has(s.rsi) ? s.rsi.toFixed(0) : "—") },
    pick: (xs) => xs.filter((s) => has(s.rsi) && s.rsi < 35 && has(s.roe) && s.roe >= 15)
      .sort(by((s) => s.rsi, true)),
  },
  {
    id: "coiled", group: "Setups", label: "Coiled",
    hint: "Tightest Bollinger Bands while the trend is quiet (ADX under 20)",
    column: { label: "Band width", value: (s) => (has(s.bb_width) ? `${s.bb_width.toFixed(1)}%` : "—") },
    pick: (xs) => xs.filter((s) => has(s.bb_width) && has(s.adx) && s.adx < 20)
      .sort(by((s) => s.bb_width, true)),
  },
  {
    id: "value-area", group: "Setups", label: "Above value",
    hint: "Closed above its 20-session value area while value moved higher",
    column: { label: "Value shift", value: (s) => fmtPct(s.vp_shift, 1), signed: (s) => s.vp_shift },
    pick: (xs) => xs.filter((s) => has(s.vp_pos) && s.vp_pos > 100 && has(s.vp_shift) && s.vp_shift > 0)
      .sort(by((s) => s.vp_shift)),
  },
  {
    id: "trend", group: "Setups", label: "Strong trends",
    hint: "Above its 50- and 200-day averages with ADX over 25",
    column: { label: "ADX", value: (s) => (has(s.adx) ? s.adx.toFixed(0) : "—") },
    pick: (xs) => xs.filter((s) => has(s.sma50) && s.sma50 > 0 && has(s.sma200) && s.sma200 > 0 && has(s.adx) && s.adx > 25)
      .sort(by((s) => s.adx)),
  },
];

export function lensContext(sectors: MarketSector[]): LensContext {
  const sectorPe = new Map<string, number>();
  for (const s of sectors) if (has(s.pe_median)) sectorPe.set(s.key, s.pe_median);
  return { sectorPe };
}

// ── formatting ───────────────────────────────────────────────────────────

const IN2 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const IN0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function fmtPrice(v: number | null | undefined): string {
  if (!has(v)) return "—";
  if (Math.abs(v) >= 100_000) return IN0.format(v);
  if (Math.abs(v) < 1) return v.toPrecision(3);
  return IN2.format(v);
}

/** ₹ crore, compact: 12,340 → "12.3K Cr", 1,234,567 → "12.3L Cr". */
export function fmtCr(v: number | null | undefined): string {
  if (!has(v)) return "—";
  if (v >= 100_000) return `${(v / 100_000).toFixed(1)}L Cr`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K Cr`;
  return `${v.toFixed(0)} Cr`;
}

/** "2026-07-22" → "22 Jul". */
export function fmtDay(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/** Where the price sits in its 52-week range, 0..1, from the two distances. */
export function rangePosition(s: Pick<MarketStock, "price" | "dist_high" | "dist_low">): number | null {
  if (!has(s.price) || !has(s.dist_high) || !has(s.dist_low)) return null;
  const low = s.price / (1 + s.dist_low / 100);
  const high = s.price / (1 + s.dist_high / 100);
  if (!(high > low)) return null;
  return Math.min(1, Math.max(0, (s.price - low) / (high - low)));
}

// ── battles ──────────────────────────────────────────────────────────────

export type Battle = { a: string; b: string; title: string };

/** Rivals that share a market. Shown only when both are in the 500. */
export const BATTLES: Battle[] = [
  { a: "TCS", b: "INFY", title: "IT bellwethers" },
  { a: "HDFCBANK", b: "ICICIBANK", title: "Private banks" },
  { a: "ETERNAL", b: "SWIGGY", title: "Food delivery" },
  { a: "MARUTI", b: "M&M", title: "Passenger cars" },
  { a: "ASIANPAINT", b: "BERGEPAINT", title: "Paints" },
  { a: "TATASTEEL", b: "JSWSTEEL", title: "Steel" },
  { a: "DMART", b: "TRENT", title: "Retail" },
  { a: "HAL", b: "BEL", title: "Defence" },
  { a: "BAJFINANCE", b: "CHOLAFIN", title: "Lenders" },
  { a: "TITAN", b: "KALYANKJIL", title: "Jewellery" },
  { a: "SBIN", b: "BANKBARODA", title: "State banks" },
  { a: "HINDUNILVR", b: "NESTLEIND", title: "FMCG giants" },
  { a: "ULTRACEMCO", b: "AMBUJACEM", title: "Cement" },
  { a: "NTPC", b: "POWERGRID", title: "Power utilities" },
  { a: "BAJAJ-AUTO", b: "HEROMOTOCO", title: "Two-wheelers" },
  { a: "SUNPHARMA", b: "CIPLA", title: "Pharma" },
  { a: "HDFCLIFE", b: "SBILIFE", title: "Life insurers" },
  { a: "BHARTIARTL", b: "IDEA", title: "Telecom" },
];

export type Round = {
  label: string;
  a: number | null;
  b: number | null;
  fmt: (v: number | null) => string;
  /** "a", "b", or null when either side has no number or they tie. */
  winner: "a" | "b" | null;
};

const ROUNDS: { label: string; get: (s: MarketStock) => number | null; lower?: boolean; fmt: (v: number | null) => string }[] = [
  { label: "Today", get: (s) => s.change, fmt: (v) => fmtPct(v) },
  { label: "1 month", get: (s) => s.ret_1m, fmt: (v) => fmtPct(v, 1) },
  { label: "1 year", get: (s) => s.ret_1y, fmt: (v) => fmtPct(v, 1) },
  { label: "Valuation", get: (s) => s.pe, lower: true, fmt: (v) => (has(v) ? `${v.toFixed(1)} P/E` : "—") },
  { label: "Quality", get: (s) => s.roe, fmt: (v) => (has(v) ? `${v.toFixed(1)}% ROE` : "—") },
  { label: "Trend", get: (s) => s.sma200, fmt: (v) => (has(v) ? `${v > 0 ? "+" : ""}${v.toFixed(1)}% vs 200D` : "—") },
];

export function scoreBattle(a: MarketStock, b: MarketStock): { rounds: Round[]; a: number; b: number } {
  const rounds = ROUNDS.map((r) => {
    const va = r.get(a);
    const vb = r.get(b);
    let winner: Round["winner"] = null;
    if (has(va) && has(vb) && va !== vb) winner = (r.lower ? va < vb : va > vb) ? "a" : "b";
    return { label: r.label, a: va, b: vb, fmt: r.fmt, winner };
  });
  return {
    rounds,
    a: rounds.filter((r) => r.winner === "a").length,
    b: rounds.filter((r) => r.winner === "b").length,
  };
}

/** Two close series rebased to 100 on their first common day. */
export function rebase(a: { t: number; c: number }[], b: { t: number; c: number }[]): { t: number; a: number; b: number }[] {
  const mb = new Map(b.map((x) => [x.t, x.c]));
  const both = a.filter((x) => mb.has(x.t));
  const start = both[0];
  if (both.length < 2 || !start) return [];
  const a0 = start.c;
  const b0 = mb.get(start.t)!;
  if (!a0 || !b0) return [];
  return both.map((x) => ({ t: x.t, a: (x.c / a0) * 100, b: (mb.get(x.t)! / b0) * 100 }));
}

// ── news: which stocks a headline is about ───────────────────────────────

export type NewsItem = {
  title: string;
  link: string;
  source: string;
  summary?: string | null;
  ts: number;
  image?: string | null;
};

const STOP_FIRST = new Set([
  "india", "indian", "bharat", "national", "hindustan", "state", "bank", "power",
  "the", "new", "global", "general", "united", "central", "punjab", "great",
  "max", "life", "first", "union", "eastern", "western", "southern", "northern",
  "star", "jindal", "adani", "tata", "bajaj", "mahindra", "aditya", "birla", "godrej",
  "jsw", "hdfc", "icici", "sbi", "larsen", "oil", "gas", "steel", "capital",
]);

/** A few names the papers use that the filings do not. */
const ALIASES: Record<string, string[]> = {
  SBIN: ["SBI"], HINDUNILVR: ["HUL"], BHARTIARTL: ["Airtel"], LT: ["L&T"],
  "M&M": ["Mahindra & Mahindra", "M&M"], ETERNAL: ["Zomato", "Eternal"],
  RELIANCE: ["Reliance Industries", "RIL"], INFY: ["Infosys"], TCS: ["TCS"],
  HDFCBANK: ["HDFC Bank"], ICICIBANK: ["ICICI Bank"], KOTAKBANK: ["Kotak Mahindra Bank", "Kotak Bank"],
  BAJFINANCE: ["Bajaj Finance"], TMPV: ["Tata Motors"], JIOFIN: ["Jio Financial"],
  DMART: ["DMart", "Avenue Supermarts"], LICI: ["LIC"], INDIGO: ["IndiGo", "InterGlobe"],
  NAUKRI: ["Info Edge", "Naukri"], POLICYBZR: ["PB Fintech", "Policybazaar"],
  TATASTEEL: ["Tata Steel"], TATAPOWER: ["Tata Power"], ADANIENT: ["Adani Enterprises"],
  ADANIPORTS: ["Adani Ports"], ADANIGREEN: ["Adani Green"], ADANIPOWER: ["Adani Power"],
  JSWSTEEL: ["JSW Steel"], ONGC: ["ONGC"], NTPC: ["NTPC"], ITC: ["ITC"],
};

const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export type Tagger = (text: string) => string[];

/**
 * Builds a matcher from the 500: aliases, the company name without its
 * suffix, and the name's first word when it is distinctive and no other
 * company in the 500 starts with it. Case-sensitive on purpose: "Titan" the
 * company is capitalised, the word in a sentence usually is not.
 */
export function newsTagger(stocks: MarketStock[]): Tagger {
  const firstCount = new Map<string, number>();
  for (const s of stocks) {
    const w = s.name.split(/\s+/)[0]?.toLowerCase() ?? "";
    firstCount.set(w, (firstCount.get(w) ?? 0) + 1);
  }
  const pats: { sym: string; re: RegExp }[] = [];
  for (const s of stocks) {
    const names = new Set<string>(ALIASES[s.symbol] ?? []);
    const clean = s.name.replace(/\s+(Industries|Company|Corporation|India)$/i, "").trim();
    if (clean.split(/\s+/).length >= 2) names.add(clean);
    const first = s.name.split(/\s+/)[0] ?? "";
    if (first.length >= 5 && !STOP_FIRST.has(first.toLowerCase()) && firstCount.get(first.toLowerCase()) === 1) {
      names.add(first);
    }
    for (const n of names) {
      if (n.length < 3) continue;
      pats.push({ sym: s.symbol, re: new RegExp(`(^|[^A-Za-z0-9&])${escapeRe(n)}(?![A-Za-z0-9])`) });
    }
  }
  return (text: string) => {
    const out: string[] = [];
    for (const p of pats) if (!out.includes(p.sym) && p.re.test(text)) out.push(p.sym);
    return out.slice(0, 3);
  };
}

export async function fetchNews(limit = 40): Promise<NewsItem[]> {
  const r = await fetch(`/feeds?limit=${limit}`);
  if (!r.ok) throw new Error(`news ${r.status}`);
  const d = (await r.json()) as { items?: NewsItem[] };
  return (d.items ?? []).filter((i) => i.title && i.link);
}

// ── calendar ─────────────────────────────────────────────────────────────

export type CalendarItem = {
  date: string;
  time?: string | null;
  kind: string;
  action?: string;
  region?: string;
  symbol?: string | null;
  title: string;
  detail?: string | null;
  /** Results: the quarter, "Q2 FY27". */
  period?: string | null;
  purpose?: string | null;
  source?: string;
};

export async function fetchCalendar(days = 10): Promise<CalendarItem[]> {
  const r = await fetch(`/calendar?days=${days}`);
  if (!r.ok) throw new Error(`calendar ${r.status}`);
  const d = (await r.json()) as { items?: CalendarItem[] };
  return d.items ?? [];
}

// ── other asset classes ──────────────────────────────────────────────────

export type AssetGroup = { title: string; symbols: string[] };
export type AssetClass = "indices" | "commodities" | "crypto" | "currencies";

export const ASSET_BOARDS: Record<AssetClass, AssetGroup[]> = {
  indices: [
    { title: "Benchmarks", symbols: ["NIFTY 50", "SENSEX", "NIFTY NEXT 50", "NIFTY 500", "NIFTY MIDCAP 100", "NIFTY SMLCAP 100", "INDIA VIX"] },
    {
      title: "Sectors",
      symbols: ["NIFTY BANK", "NIFTY PVT BANK", "NIFTY PSU BANK", "NIFTY FIN SERVICE", "NIFTY IT", "NIFTY AUTO",
        "NIFTY PHARMA", "NIFTY HEALTHCARE", "NIFTY FMCG", "NIFTY METAL", "NIFTY ENERGY", "NIFTY OIL AND GAS",
        "NIFTY REALTY", "NIFTY INFRA", "NIFTY MEDIA", "NIFTY CONSR DURBL", "NIFTY COMMODITIES"],
    },
  ],
  commodities: [
    { title: "Precious metals", symbols: ["GOLD", "GOLDM", "SILVER", "SILVERM"] },
    { title: "Energy", symbols: ["CRUDEOIL", "CRUDEOILM", "NATURALGAS", "NATGASMINI"] },
    { title: "Base metals", symbols: ["COPPER", "ZINC", "ALUMINIUM", "LEAD", "NICKEL"] },
  ],
  crypto: [
    {
      title: "Largest coins",
      symbols: ["BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT", "TRXUSDT",
        "LINKUSDT", "AVAXUSDT", "SUIUSDT", "LTCUSDT", "DOTUSDT", "XLMUSDT", "BCHUSDT", "HBARUSDT"],
    },
  ],
  currencies: [{ title: "Rupee pairs", symbols: ["USDINR", "EURINR", "GBPINR", "JPYINR"] }],
};

export type AssetQuote = {
  symbol: string;
  last: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  prev_close?: number | null;
  change?: number | null;
  change_pct?: number | null;
  as_of?: number | null;
  currency?: string;
};

export async function fetchAssetQuotes(symbols: string[]): Promise<Map<string, AssetQuote>> {
  const out = new Map<string, AssetQuote>();
  for (let i = 0; i < symbols.length; i += 100) {
    const chunk = symbols.slice(i, i + 100);
    try {
      const r = await fetch(`/quotes?symbols=${encodeURIComponent(chunk.join(","))}`);
      const d = (await r.json()) as { quotes?: AssetQuote[] };
      for (const q of d.quotes ?? []) if (q.last != null) out.set(q.symbol, q);
    } catch {
      /* a failed chunk leaves its symbols unpriced, drawn as unavailable */
    }
  }
  return out;
}

const barCache = new Map<string, Promise<{ t: number; c: number }[]>>();

/** Daily closes from the chart server, oldest first. */
export function fetchDailyCloses(symbol: string, limit = 30): Promise<{ t: number; c: number }[]> {
  const key = `${symbol}:${limit}`;
  let p = barCache.get(key);
  if (!p) {
    p = fetch(`/bars?symbol=${encodeURIComponent(symbol)}&interval=1d&limit=${limit}`)
      .then((r) => r.json() as Promise<{ bars?: { t: number; c: number }[] }>)
      .then((d) => (d.bars ?? []).filter((b) => Number.isFinite(b.c)).map((b) => ({ t: b.t, c: b.c })))
      .catch(() => {
        barCache.delete(key);
        return [];
      });
    barCache.set(key, p);
  }
  return p;
}

// ── treemap ──────────────────────────────────────────────────────────────

export type Rect = { x: number; y: number; w: number; h: number };
export type Placed<T> = Rect & { item: T };

/**
 * Squarified treemap (Bruls, Huizing & van Wijk, 2000): rows are laid along
 * the shorter side and grown while the worst aspect ratio improves.
 */
export function squarify<T>(items: T[], weight: (t: T) => number, rect: Rect): Placed<T>[] {
  const data = items.map((item) => ({ item, v: Math.max(0, weight(item)) })).filter((d) => d.v > 0)
    .sort((a, b) => b.v - a.v);
  const total = data.reduce((s, d) => s + d.v, 0);
  if (!total || rect.w <= 0 || rect.h <= 0) return [];
  const scale = (rect.w * rect.h) / total;
  const areas = data.map((d) => ({ item: d.item, a: d.v * scale }));
  const out: Placed<T>[] = [];
  let { x, y, w, h } = rect;

  const worst = (row: number[], side: number): number => {
    const s = row.reduce((a, b) => a + b, 0);
    const max = Math.max(...row);
    const min = Math.min(...row);
    return Math.max((side * side * max) / (s * s), (s * s) / (side * side * min));
  };

  let i = 0;
  while (i < areas.length) {
    const side = Math.min(w, h);
    const row: number[] = [areas[i]!.a];
    let j = i + 1;
    while (j < areas.length && worst([...row, areas[j]!.a], side) <= worst(row, side)) {
      row.push(areas[j]!.a);
      j++;
    }
    const sum = row.reduce((a, b) => a + b, 0);
    if (w >= h) {
      const rw = sum / h;
      let yy = y;
      for (let k = 0; k < row.length; k++) {
        const rh = row[k]! / rw;
        out.push({ item: areas[i + k]!.item, x, y: yy, w: rw, h: rh });
        yy += rh;
      }
      x += rw;
      w -= rw;
    } else {
      const rh = sum / w;
      let xx = x;
      for (let k = 0; k < row.length; k++) {
        const rw = row[k]! / rh;
        out.push({ item: areas[i + k]!.item, x: xx, y, w: rw, h: rh });
        xx += rw;
      }
      y += rh;
      h -= rh;
    }
    i = j;
  }
  return out;
}

/**
 * Tile colour for a move: a muted wash of the gain or loss colour over the
 * card surface, saturating at ±cap percent. Never a full-strength fill.
 */
export function moveTint(v: number | null | undefined, cap = 3): string {
  if (!has(v) || v === 0) return "var(--bg-secondary)";
  const t = Math.min(1, Math.abs(v) / cap);
  const pct = Math.round(10 + t * 40);
  const tone = v > 0 ? "var(--color-profit)" : "var(--color-loss)";
  return `color-mix(in srgb, ${tone} ${pct}%, var(--bg-secondary))`;
}
