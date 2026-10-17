import type { MetadataRoute } from "next";

/** The marketing site's own pages. Company pages are in app.pivotnow.in's
 *  sitemap, which robots.txt also names. */
export default function sitemap(): MetadataRoute.Sitemap {
  const site = "https://www.pivotnow.in";
  const pages: [string, number][] = [["", 1], ["/pricing", 0.8], ["/about", 0.6], ["/contact", 0.4], ["/terms", 0.2], ["/privacy", 0.2], ["/refunds", 0.2]];
  return pages.map(([path, priority]) => ({ url: `${site}${path}`, priority, changeFrequency: path ? "monthly" : "weekly" }));
}
