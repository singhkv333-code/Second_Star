import type { NextConfig } from "next";

const BACKEND = process.env.NEXT_PUBLIC_PIVOT_API_BASE?.replace(/\/api\/?$/, "") || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  experimental: {
    typedRoutes: false,
  },
  async rewrites() {
    return [
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
        source: "/health",
        destination: `${BACKEND}/health`,
      },
    ];
  },
};

export default nextConfig;
