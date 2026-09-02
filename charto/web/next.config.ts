import type { NextConfig } from "next";

// Where the rewrites below send API traffic in LOCAL DEV. In production none
// of them fire: nginx routes /paper and /paper/ here and everything else to the
// dataserver, so a rewrite is only ever the local answer to "the Next app and
// the backend are on different ports".
//
// The default was Pivot's :8000. This is Charto's app talking to Charto's
// dataserver, so :5174 is the only value that can be right — with :8000 a
// locally-run paper page asked a different product for the book and got a 404
// it could not explain. `NEXT_PUBLIC_PIVOT_API_BASE` is relative in .env.local
// (deliberately — it is inlined into the browser bundle), which strips to an
// empty string, so the fallback is what is actually in force.
const BACKEND =
  process.env.CHARTO_BACKEND ||
  process.env.NEXT_PUBLIC_PIVOT_API_BASE?.replace(/\/api\/?$/, "") ||
  "http://127.0.0.1:5174";

const nextConfig: NextConfig = {
  reactStrictMode: true,
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
  typedRoutes: false,
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
        source: "/:path*",
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
        // `:path+`, not `:path*`. A `*` matches zero segments, so it would
        // capture `/paper` itself and rewrite the PAGE to the backend — the
        // dashboard would never render, and the 404 would come from a route
        // that looks like it exists. `+` requires at least one segment, so the
        // page stays with Next and only its data crosses over.
        source: "/paper/:path+",
        destination: `${BACKEND}/paper/:path+`,
      },
      {
        source: "/strategies/:path*",
        destination: `${BACKEND}/strategies/:path*`,
      },
      {
        source: "/strategies",
        destination: `${BACKEND}/strategies`,
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
