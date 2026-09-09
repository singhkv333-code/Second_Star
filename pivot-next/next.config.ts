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
const CHART = process.env.CHART_UPSTREAM || "http://127.0.0.1:5173";

const nextConfig: NextConfig = {
  reactStrictMode: true,
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
  async rewrites() {
    return [
      {
        source: "/pivot-chat/:path*",
        destination: `${BACKEND}/:path*`,
      },
      {
        // The chart app, proxied so it is same-origin with the shell. Two
        // rules because `/chart-app` with no trailing path must resolve too —
        // it is what the iframe asks for first.
        source: "/chart-app",
        destination: `${CHART}/`,
      },
      {
        source: "/chart-app/:path*",
        destination: `${CHART}/:path*`,
      },
      {
        // The Agent System client (lib/api.ts `request()`) targets the
        // `/api` base; when NEXT_PUBLIC_PIVOT_API_BASE isn't inlined it
        // falls back to the RELATIVE `/api/*`, so proxy that to the backend
        // (which serves the Agent System under /api). Without this, calls
        // like /api/workflows hit Next's 404 -> "Failed to fetch" in the
        // Active Agents rail. Mirrors the legacy /chat,/paper,... rewrites.
        source: "/api/:path*",
        destination: `${BACKEND}/api/:path*`,
      },
      {
        source: "/chat/:path*",
        destination: `${BACKEND}/chat/:path*`,
      },
      {
        source: "/auth/:path*",
        destination: `${BACKEND}/auth/:path*`,
      },
      {
        source: "/orders/:path*",
        destination: `${BACKEND}/orders/:path*`,
      },
      {
        source: "/workflows/:path*",
        destination: `${BACKEND}/workflows/:path*`,
      },
      {
        source: "/runs/:path*",
        destination: `${BACKEND}/runs/:path*`,
      },
      {
        source: "/markets/:path*",
        destination: `${BACKEND}/markets/:path*`,
      },
      {
        source: "/paper/:path*",
        destination: `${BACKEND}/paper/:path*`,
      },
      {
        // Voice input — bare-mounted like /paper; without this the relative
        // fallback base would 404 on Next instead of reaching the backend.
        source: "/audio/:path*",
        destination: `${BACKEND}/audio/:path*`,
      },
      {
        source: "/health",
        destination: `${BACKEND}/health`,
      },
    ];
  },
};

export default nextConfig;
