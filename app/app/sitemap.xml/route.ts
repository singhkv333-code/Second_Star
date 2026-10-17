import { SITE_URL } from "@/lib/seo/site";
import { getCompanies } from "@/lib/seo/data";

/** The sitemap index: the hub pages and the stock pages, in separate files
 *  so Search Console reports indexing for each kind on its own. */

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const all = await getCompanies().catch(() => null);
  const lastmod = all?.as_of?.eod_day ?? new Date().toISOString().slice(0, 10);
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${["pages", "stocks"].map((f) => `  <sitemap><loc>${SITE_URL}/sitemaps/${f}.xml</loc><lastmod>${lastmod}</lastmod></sitemap>`).join("\n")}
</sitemapindex>
`;
  return new Response(body, {
    headers: { "Content-Type": "application/xml; charset=utf-8", "Cache-Control": "public, max-age=3600" },
  });
}
