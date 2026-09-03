import type { NextConfig } from "next";

const BACKEND = process.env.NEXT_PUBLIC_PIVOT_API_BASE?.replace(/\/api\/?$/, "") || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The Charto VM also holds the 29 GB mmap-backed market store and the live
  // data service. Parallel Next compiler workers can push the box into an OOM
  // kill during deployment even though the same source builds successfully on
  // a workstation. One worker is slower but keeps peak build memory bounded.
  experimental: {
    cpus: 1,
  },
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
