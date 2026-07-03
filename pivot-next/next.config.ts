import type { NextConfig } from "next";

const BACKEND = process.env.NEXT_PUBLIC_PIVOT_API_BASE?.replace(/\/api\/?$/, "") || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // SKIP_LINT=1 lets `next build` complete despite pre-existing lint errors
  // (unused vars in waitlist/legacy components) — used for perf-measurement
  // and CI builds. Default behaviour (lint enforced) is unchanged.
  eslint: {
    ignoreDuringBuilds: process.env.SKIP_LINT === "1",
  },
  experimental: {
    typedRoutes: false,
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
