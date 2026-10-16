import { getCompanies, listed } from "@/lib/seo/data";
import { LISTS } from "@/lib/seo/lists";
import { LETTERS, SITE_URL, sectorPath, stockPath } from "@/lib/seo/site";

/**
 * /sitemaps/pages.xml   the directory, the lists, the sectors and the
 *                       company pages a visitor reads before signing up
 * /sitemaps/stocks.xml  every priced company's page
 *
 * `lastmod` is the last trading day: that is when the numbers on these pages
 * last changed, and a lastmod that moves without the content moving teaches
 * a search engine to ignore it.
 */

export const dynamic = "force-dynamic";

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function urlset(urls: { loc: string; lastmod: string; priority?: string; changefreq?: string }[]): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.map((u) => `  <url><loc>${esc(u.loc)}</loc><lastmod>${u.lastmod}</lastmod>${u.changefreq ? `<changefreq>${u.changefreq}</changefreq>` : ""}${u.priority ? `<priority>${u.priority}</priority>` : ""}</url>`).join("\n")}
</urlset>
`;
}

export async function GET(_req: Request, { params }: { params: Promise<{ file: string }> }): Promise<Response> {
  const { file } = await params;
  const all = await getCompanies();
  if (!all) return new Response("not found", { status: 404 });
  const day = all.as_of?.eod_day ?? new Date().toISOString().slice(0, 10);
  let xml: string;
  if (file === "pages.xml") {
    const sectors = Object.keys(all.sectors ?? {});
    xml = urlset([
      { loc: `${SITE_URL}/stocks`, lastmod: day, changefreq: "daily", priority: "0.9" },
      { loc: `${SITE_URL}/sectors`, lastmod: day, changefreq: "daily", priority: "0.8" },
      ...LISTS.map((l) => ({ loc: `${SITE_URL}/stocks/${l.slug}`, lastmod: day, changefreq: "daily", priority: "0.8" })),
      ...sectors.map((k) => ({ loc: `${SITE_URL}${sectorPath(k)}`, lastmod: day, changefreq: "daily", priority: "0.7" })),
      ...LETTERS.map((l) => ({ loc: `${SITE_URL}/stocks/${l}`, lastmod: day, changefreq: "weekly", priority: "0.5" })),
      ...["/pricing", "/about", "/contact", "/terms", "/privacy", "/refunds"].map((p) => ({ loc: `${SITE_URL}${p}`, lastmod: day, priority: "0.3" })),
    ]);
  } else if (file === "stocks.xml") {
    xml = urlset(listed(all).map((r) => ({
      loc: `${SITE_URL}${stockPath(r.symbol)}`,
      lastmod: day,
      changefreq: "daily",
      // Search demand follows size: the largest companies are searched most.
      priority: r.rank <= 100 ? "0.9" : r.rank <= 500 ? "0.8" : "0.6",
    })));
  } else {
    return new Response("not found", { status: 404 });
  }
  return new Response(xml, {
    headers: { "Content-Type": "application/xml; charset=utf-8", "Cache-Control": "public, max-age=3600" },
  });
}
