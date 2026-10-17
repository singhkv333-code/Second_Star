/**
 * Server-side reads for the public pages. Runs inside the Next server only.
 *
 * Two upstreams, both on the box, both reached directly rather than through
 * nginx (so a crawler's page render never spends the visitor rate limit):
 *
 *   Pivot API  /api/public/*   companies and one company's snapshot, built
 *                              from caches; never reaches a price feed
 *   Pivot     /bars           a year of daily candles for the price chart
 *
 * Every read is cached by Next for REVALIDATE seconds, so a page a crawler
 * and a reader both open in the same quarter-hour costs one upstream call.
 * A failed read returns null and the page renders without that section.
 *
 * Locally, point both at production with
 *   PIVOT_BACKEND_ORIGIN=https://app.pivotnow.in/pv PIVOT_DATASERVER_UPSTREAM=https://app.pivotnow.in
 */

export const REVALIDATE = 900;

const API = (process.env.PIVOT_BACKEND_ORIGIN || "http://127.0.0.1:8000").replace(/\/$/, "");
const DATASERVER = (process.env.PIVOT_DATASERVER_UPSTREAM || "http://127.0.0.1:5174").replace(/\/$/, "");

export type MarketRow = {
  symbol: string;
  name: string;
  sector: string;
  mcap_cr: number | null;
  price: number | null;
  change: number | null;
  ret_1w: number | null;
  ret_1m: number | null;
  ret_3m: number | null;
  ret_1y: number | null;
  pe: number | null;
  roe: number | null;
  dist_high: number | null;
  dist_low: number | null;
  rsi: number | null;
  rank: number;
};

export type AsOf = {
  prices: "eod" | "live";
  price_source: string;
  eod_day: string | null;
  generated_at: string;
};

export type Companies = {
  as_of: AsOf;
  count: number;
  sectors: Record<string, string>;
  companies: MarketRow[];
};

export type Latest = {
  value: number;
  period_label: string | null;
  period_end: string | null;
  basis: string | null;
};

export type HistoryPoint = { period_label: string | null; period_end: string | null; value: number };

export type Quarter = {
  period_end: string;
  period_label: string;
  revenue: number | null;
  ebitda: number | null;
  net_profit: number | null;
  eps_basic: number | null;
  operating_margin_pct: number | null;
  net_margin_pct: number | null;
  revenue_yoy_pct: number | null;
  net_profit_yoy_pct: number | null;
  revenue_qoq_pct: number | null;
  net_profit_qoq_pct: number | null;
  rev_ttm: number | null;
  np_ttm: number | null;
  eps_ttm: number | null;
  gross_npa_pct: number | null;
  net_npa_pct: number | null;
};

export type Shareholding = {
  quarter: string;
  promoter_pct: number | null;
  pledge_pct: number | null;
  groups: { label: string; pct: number }[];
  quarters: ({ quarter: string; pledge_pct: number | null } & Record<string, number | string | null>)[];
};

export type StockSnapshot = {
  symbol: string;
  name: string;
  sector: string | null;
  sector_label: string | null;
  industry: string | null;
  website: string | null;
  universe_size: number | null;
  market: MarketRow | null;
  as_of: AsOf | null;
  latest: Record<string, Latest>;
  history: Record<string, HistoryPoint[]>;
  quarters: { basis: string; quarters: Quarter[] } | null;
  shareholding: Shareholding | null;
  peers: MarketRow[];
  sources: Record<string, string>;
};

export type Bar = { t: number; o: number; h: number; l: number; c: number; v: number };

/** null for "there is no such thing" (404); a THROW for "could not ask".
 *  The difference matters to a cached page: a throw during revalidation
 *  keeps the last good page, where a null would replace it with an empty,
 *  unindexed one for a quarter of an hour, possibly in front of a crawler. */
async function getJson<T>(url: string, revalidate = REVALIDATE): Promise<T | null> {
  const res = await fetch(url, {
    next: { revalidate },
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(15_000),
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${url} answered ${res.status}`);
  return (await res.json()) as T;
}

// The company list is ~1 MB and every directory, list, sector, sitemap and
// search-index request reads it, so the process keeps one copy for five
// minutes on top of Next's data cache. A failed refresh keeps serving the
// last good copy.
let companiesMemo: { at: number; body: Companies } | null = null;
let companiesInflight: Promise<Companies | null> | null = null;

export async function getCompanies(): Promise<Companies | null> {
  if (companiesMemo && Date.now() - companiesMemo.at < 300_000) return companiesMemo.body;
  companiesInflight ??= getJson<Companies>(`${API}/api/public/companies`)
    .then((body) => {
      if (body?.companies?.length) companiesMemo = { at: Date.now(), body };
      return body;
    })
    .catch((err: unknown) => {
      if (companiesMemo) return companiesMemo.body;
      throw err;
    })
    .finally(() => { companiesInflight = null; });
  return companiesInflight;
}

export function getStock(symbol: string): Promise<StockSnapshot | null> {
  return getJson<StockSnapshot>(`${API}/api/public/stock/${encodeURIComponent(symbol)}`);
}

/** About a year of daily candles, oldest first. */
export async function getDailyBars(symbol: string): Promise<Bar[]> {
  // The chart is an extra: without it the page still has its numbers.
  const got = await getJson<{ bars?: Bar[] }>(
    `${DATASERVER}/bars?symbol=${encodeURIComponent(symbol)}&interval=1d&limit=260`,
  ).catch(() => null);
  return (got?.bars ?? []).filter((b) => Number.isFinite(b.c) && b.c > 0);
}

/** A page is worth indexing when it has a price and at least one more body
 *  of numbers (results, ownership or a peer table). Below that it is a name and a dash, which is a thin page
 *  to a search engine; it still renders for a reader, with `noindex`. */
export function isIndexable(s: Pick<StockSnapshot, "market" | "history" | "quarters" | "shareholding" | "peers">): boolean {
  return Boolean(s.market?.price) && (
    Boolean(s.history?.revenue?.length) || Boolean(s.quarters?.quarters?.length)
    || Boolean(s.shareholding?.groups?.length) || (s.peers?.length ?? 0) >= 3
  );
}

/** Rows a directory or sitemap should list: priced companies only. */
export function listed(c: Companies | null): MarketRow[] {
  return (c?.companies ?? []).filter((r) => r.price != null);
}
