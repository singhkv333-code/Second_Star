import type { MetadataRoute } from "next";

/** www.pivotnow.in is the marketing site: all of it may be crawled, by search
 *  engines and by the crawlers behind AI answers alike. The stock pages live on
 *  app.pivotnow.in, which has its own robots.txt and sitemap. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      { userAgent: ["Googlebot", "Bingbot", "OAI-SearchBot", "ChatGPT-User", "GPTBot", "Claude-SearchBot", "Claude-User", "ClaudeBot", "PerplexityBot", "Perplexity-User", "Google-Extended", "Applebot", "Applebot-Extended"], allow: "/" },
      { userAgent: "*", allow: "/", disallow: ["/api/", "/checkout", "/paper", "/strategies", "/design", "/anim-preview", "/demo-widgets"] },
    ],
    sitemap: ["https://www.pivotnow.in/sitemap.xml", "https://app.pivotnow.in/sitemap.xml"],
    host: "https://www.pivotnow.in",
  };
}
