import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { StockSymbolView } from "./view";
import { PublicFrame } from "@/components/seo/PublicFrame";
import { StockPublicPage } from "@/components/seo/StockPublicPage";
import { getDailyBars, getStock, isIndexable } from "@/lib/seo/data";
import { faqs, metaDescription, priceStats } from "@/lib/seo/stockCopy";
import { ldJson, stockGraph } from "@/lib/seo/schema";
import { BRAND, absoluteUrl, stockPath } from "@/lib/seo/site";

// Next.js 15: route params are now async.
type Params = Promise<{ symbol: string }>;

// Rendered on first request and cached for a quarter of an hour (ISR): the
// page is a function of end-of-day data, so every crawler and reader in that
// window is served the same cached HTML.
export const revalidate = 900;

function symbolOf(raw: string): string {
  // Next hands dynamic segments over STILL percent-encoded. Without this
  // decode, a symbol containing a reserved character arrived as its literal
  // encoding ("%5ENSEI"), got encoded a second time by the API helper, and
  // 404'd with "no quote available for %5ENSEI.NSE". Index symbols also carry
  // a space ("NIFTY 50" → "NIFTY%2050"), so this is the common path now.
  try {
    return decodeURIComponent(raw).toUpperCase();
  } catch {
    // Malformed escape sequence — fall back to the raw segment rather than
    // throwing a 500 out of the route.
    return raw.toUpperCase();
  }
}

/** One snapshot + one year of candles, shared by the metadata and the page
 *  (Next de-duplicates the identical fetches within a render). */
async function load(symbol: string) {
  const [s, bars] = await Promise.all([getStock(symbol), getDailyBars(symbol)]);
  return { s, bars, stats: priceStats(bars) };
}

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const symbol = symbolOf((await params).symbol);
  const { s, stats } = await load(symbol);
  const canonical = absoluteUrl(stockPath(symbol));
  if (!s) {
    // Indices, crypto and anything the public snapshot does not cover still
    // open the full page for a signed-in reader; they are not indexed.
    return { title: `${symbol} | ${BRAND}`, robots: { index: false, follow: true }, alternates: { canonical } };
  }
  const title = `${s.name} Share Price Today, Chart & Financials (NSE: ${s.symbol}) | Pivot`;
  const description = metaDescription(s, stats);
  return {
    title,
    description,
    alternates: { canonical, types: { "text/markdown": `${canonical}.md` } },
    robots: isIndexable(s) ? { index: true, follow: true, "max-snippet": -1, "max-image-preview": "large" } : { index: false, follow: true },
    openGraph: {
      type: "website",
      url: canonical,
      title: `${s.name} (${s.symbol}) share price, chart and fundamentals`,
      description,
      siteName: BRAND,
      locale: "en_IN",
    },
    twitter: { card: "summary_large_image", title: `${s.name} (${s.symbol}) share price`, description },
  };
}

export default async function Page({ params }: { params: Params }) {
  const symbol = symbolOf((await params).symbol);
  const { s, bars, stats } = await load(symbol);

  // Not in the public snapshot (an index, a crypto pair, an unlisted name):
  // the research page still serves a signed-in reader; a signed-out one is
  // sent to sign in, as before.
  if (!s) {
    if (!/^[A-Z0-9&_.\- ^]{1,32}$/.test(symbol)) notFound();
    return <StockSymbolView symbol={symbol} publicPage={null} />;
  }

  const qa = faqs(s, stats);
  const modified = s.as_of?.generated_at ?? null;
  const graph = stockGraph(s, qa, metaDescription(s, stats), modified);
  const publicPage = (
    <PublicFrame signupNext={`/?symbol=${s.symbol}`}>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: ldJson(graph) }} />
      <StockPublicPage s={s} bars={bars} stats={stats} faqs={qa} />
    </PublicFrame>
  );
  return <StockSymbolView symbol={symbol} publicPage={publicPage} />;
}
