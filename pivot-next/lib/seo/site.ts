/**
 * The public, indexable half of the app: its address and its name.
 *
 * Stock pages, the stock directory, the sector pages and the files crawlers
 * read (robots.txt, the sitemaps, llms.txt) all live on app.pivotnow.in. The
 * same build also answers on the VM's Azure hostnames, so every canonical
 * URL is written against SITE_URL — never the request's host — and
 * robots.txt closes every other host (app/robots.txt/route.ts).
 */

export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL || "https://app.pivotnow.in").replace(/\/$/, "");
export const SITE_HOST = new URL(SITE_URL).host;
/** The marketing site. It links into the stock pages and they link back. */
export const HOME_URL = "https://www.pivotnow.in";
export const BRAND = "Pivot";
export { LEGAL_NAME, CONTACT_EMAIL } from "@/components/company/content";
export const TAGLINE = "AI stock research for India: live charts you can talk to, fundamentals, and strategies you can test";

/** IndexNow proves ownership with a key served from this host. The key is
 *  public by design (the protocol publishes it at /indexnow-key.txt); it only
 *  says "this host may tell Bing and Yandex its pages changed". */
export const INDEXNOW_KEY = "ae55aa1e736e0281a3d048b0bba846d3";

export function absoluteUrl(path: string): string {
  return `${SITE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

/** `M&M` → `/stock/M%26M`. One encoder for every link to a stock page, so
 *  the sitemap, the canonical tag and the in-page links can never disagree. */
export function stockPath(symbol: string): string {
  return `/stock/${encodeURIComponent(symbol.toUpperCase())}`;
}

export function sectorPath(sector: string): string {
  return `/sectors/${encodeURIComponent(sector.replace(/_/g, "-"))}`;
}

/** `/sectors/capital-goods` → `capital_goods`, the key the API uses. */
export function sectorKey(slug: string): string {
  return decodeURIComponent(slug).toLowerCase().replace(/-/g, "_");
}

/** The directory groups names by first character: a–z, and "0-9". */
export function letterOf(name: string): string {
  const c = (name.trim()[0] || "").toLowerCase();
  return /[a-z]/.test(c) ? c : "0-9";
}

export const LETTERS = ["0-9", ..."abcdefghijklmnopqrstuvwxyz".split("")];
