import type { NextConfig } from "next";

// The origin Next's own rewrites proxy to. Read from its OWN variable first,
// because NEXT_PUBLIC_PIVOT_API_BASE is now a browser-facing PATH in
// production (`/pv/api`, routed to :8000 by nginx) rather than an origin —
// deriving the upstream from it would rewrite `/api/x` to the relative
// `/pv/api/x`, which is a route Next does not have.
const BACKEND =
  process.env.PIVOT_BACKEND_ORIGIN ||
  (process.env.NEXT_PUBLIC_PIVOT_API_BASE?.startsWith("http")
    ? process.env.NEXT_PUBLIC_PIVOT_API_BASE.replace(/\/api\/?$/, "")
    : "") ||
  "http://127.0.0.1:8000";

// The charting engine. It is a static app (charto/preview) served by its own
// tiny no-cache server in development and by nginx in production; either way
// the shell proxies it so the browser only ever sees ONE origin. That is not
// cosmetic — the chart keeps its auth token, workspace and saved layouts in
// localStorage, and localStorage is per-origin, so a chart on a second port
// is a chart the signed-in user is signed out of.
// The data server behind the chart (charto/data/dataserver.py). Every path
// that is not a page of this app, a /pv/ Pivot API call or a chart file falls
// through to it — the same fall-through nginx has on the VM, so one origin
// (this port) is the whole product locally exactly as it is in production.
const CHARTO = process.env.CHARTO_UPSTREAM || "http://127.0.0.1:5174";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Keep `next build` from overwriting a running development server's chunks.
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  // Mounted under a prefix in production so it can sit beside Charto's chart,
  // which owns `/` on the same host. Set from the environment rather than
  // hardcoded: `next dev` and every test run leave it unset and keep serving
  // from the root, so nothing local changes. Both the router and the asset
  // URLs follow it, which is why this has to be a build-time config and not
  // an nginx rewrite — a rewrite would strip the prefix off requests while
  // the HTML kept asking for `/_next/...` at the root.
  basePath: process.env.NEXT_BASE_PATH || undefined,
  // Standalone output bundles a minimal server + only the deps actually used
  // into .next/standalone — the standard shape for a containerized deploy
  // (small image, no full node_modules copy). Purely a build-output change,
  // no runtime behavior difference for `next dev`.
  output: "standalone",
  // SKIP_LINT=1 lets `next build` complete despite pre-existing lint errors
  // (unused vars in waitlist/legacy components) — used for perf-measurement
  // and CI builds. Default behaviour (lint enforced) is unchanged.
  eslint: {
    ignoreDuringBuilds: process.env.SKIP_LINT === "1",
  },
  experimental: {
    typedRoutes: false,
  },
  // Security headers on every response. The headline fix is clickjacking:
  // `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` stop the app being
  // iframed to overlay a "Confirm & place" click. The CSP is deliberately
  // scoped to directives that don't govern resource loading (frame-ancestors /
  // object-src / base-uri / form-action) so it can't break the app's inline
  // styles or the cross-origin API base; a full script/style CSP with nonces
  // is a separate follow-up.
  async headers() {
    const csp = [
      "frame-ancestors 'none'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; ");
    return [
      {
        // Everything EXCEPT the chart. `frame-ancestors 'none'` and
        // `X-Frame-Options: DENY` are what stop the app being iframed to
        // overlay a "Confirm & place" click — and they applied to `/:path*`,
        // which includes the chart the shell frames itself. The Chart tab
        // therefore rendered "localhost refused to connect": the shell was
        // refusing its own frame. Excluded by path rather than relaxed
        // globally, so the clickjacking guard still covers every surface that
        // can place an order.
        source: "/:path((?!chart-app).*)",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            // Voice input needs the mic; nothing needs camera/geolocation.
            value: "camera=(), geolocation=(), microphone=(self)",
          },
        ],
      },
    ];
  },
  /* ONE routing table, the same one nginx serves in production
   * (charto/deploy/nginx-charto.conf). Earlier this file sent /auth, /chat,
   * /paper and /api to Pivot's API while nginx sent the same paths to Charto,
   * so signing in, the chat and the paper book reached a different backend
   * on a laptop than on the site. Now:
   *   /pv/*           Pivot's API, prefix stripped (NEXT_PUBLIC_PIVOT_API_BASE=/pv/api)
   *   /pivot-chat/*   Pivot's chat, same API
   *   /api/pivot/*    Pivot's company data and logos (unprefixed on the API)
   *   /research/*     the research chat, mounted on Pivot's API
   *   /chart-app/*    the chart, served by app/chart-app (this app)
   *   anything else   Charto's data server, AFTER this app's own pages —
   *                   including /billing/*, where plans, subscriptions and
   *                   usage live (charto_users.db) */
  async rewrites() {
    return {
      beforeFiles: [],
      afterFiles: [
        { source: "/pv/:path*", destination: `${BACKEND}/:path*` },
        { source: "/pivot-chat/:path*", destination: `${BACKEND}/:path*` },
        { source: "/api/pivot/:path*", destination: `${BACKEND}/api/pivot/:path*` },
        { source: "/research/:path*", destination: `${BACKEND}/research/:path*` },
      ],
      fallback: [
        { source: "/:path*", destination: `${CHARTO}/:path*` },
      ],
    };
  },
};

export default nextConfig;
