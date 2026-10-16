import { SITE_HOST, SITE_URL } from "@/lib/seo/site";

/**
 * robots.txt, per host.
 *
 * On app.pivotnow.in: the public research pages are open to every search
 * and answer engine, by name, so a blanket rule somewhere upstream can never
 * shut one out; the signed-in app, the APIs and the auth pages are not
 * crawled. AI crawlers are allowed on purpose: the goal is for ChatGPT,
 * Claude, Perplexity, Gemini and Copilot to read and cite these pages.
 *
 * On any other host this build answers on (the VM's Azure names), the whole
 * site is closed, so the same pages are never indexed twice.
 */

export const dynamic = "force-dynamic";

const PRIVATE = [
  "/api/", "/pv/", "/pivot-chat/", "/research/", "/chart-app/", "/_next/data/",
  "/settings", "/checkout", "/paper", "/strategies", "/brokers", "/affiliate/admin",
  "/login", "/signup", "/reset-password", "/verify-email", "/r/", "/design", "/dev",
  "/demo-widgets", "/anim-preview", "/ui-layout-check", "/paywall-gallery", "/waitlist",
  "/stock/*/financials",
];

// Search engines, and the crawlers behind AI answers and AI search.
const NAMED = [
  "Googlebot", "Bingbot", "DuckDuckBot", "Applebot", "YandexBot",
  "OAI-SearchBot", "ChatGPT-User", "GPTBot",
  "Claude-SearchBot", "Claude-User", "ClaudeBot",
  "PerplexityBot", "Perplexity-User",
  "Google-Extended", "Applebot-Extended", "meta-externalagent", "Amazonbot", "CCBot",
];

export function GET(request: Request): Response {
  const host = (request.headers.get("x-forwarded-host") || request.headers.get("host") || "").split(":")[0];
  const local = host === "localhost" || host === "127.0.0.1";
  const body = host && host !== SITE_HOST && !local
    ? "User-agent: *\nDisallow: /\n"
    : [
      ...NAMED.flatMap((ua) => [`User-agent: ${ua}`]),
      "User-agent: *",
      "Allow: /stock/",
      "Allow: /stocks",
      "Allow: /sectors",
      ...PRIVATE.map((p) => `Disallow: ${p}`),
      "",
      `Sitemap: ${SITE_URL}/sitemap.xml`,
      "",
    ].join("\n");
  return new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "public, max-age=3600" },
  });
}
