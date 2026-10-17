import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./globals.css";
import "./billing.css";
import { Toaster } from "@/components/ui/sonner";
import { AppBootstrap } from "@/components/AppBootstrap";
import { BillingProvider } from "@/components/billing/BillingProvider";
import { SITE_URL } from "@/lib/seo/site";

// Defaults for every page. Most of this app is the signed-in product, which
// has nothing for a search engine, so the default is `noindex`; the public
// pages (stocks, sectors, lists, pricing, the company pages) opt back in.
export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: "Pivot — AI stock research for Indian markets",
  description:
    "Charts you can talk to, fundamentals for every NSE company, and strategies you can backtest honestly. " +
    "Pivot is AI stock research for Indian investors and traders.",
  applicationName: "Pivot",
  robots: { index: false, follow: true },
  openGraph: { siteName: "Pivot", locale: "en_IN", type: "website" },
  twitter: { card: "summary_large_image" },
  // Search Console and Bing Webmaster ownership, set from the environment so
  // a token never has to be committed: GOOGLE_SITE_VERIFICATION, BING_SITE_VERIFICATION.
  verification: {
    ...(process.env.GOOGLE_SITE_VERIFICATION ? { google: process.env.GOOGLE_SITE_VERIFICATION } : {}),
    ...(process.env.BING_SITE_VERIFICATION ? { other: { "msvalidate.01": process.env.BING_SITE_VERIFICATION } } : {}),
  },
  // Favicon + apple-touch-icon are wired via the Next.js App Router
  // file convention: app/icon.svg and app/apple-icon.png. No explicit
  // `icons` field is needed; Next bakes <link rel="icon"> tags into
  // <head> with content-hashed URLs that defeat browser cache.
};

// `viewport-fit=cover` lets the app draw into the notch / Dynamic Island
// region on iPhones AND makes the `env(safe-area-inset-*)` values resolve to
// the real insets (they report 0 without it). globals.css then pads the shell
// so the top bar clears the camera cutout and the composer clears the home
// indicator. Without this the header rendered directly under the front camera.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Synchronous on purpose: it sets the theme before first paint. */}
        {/* eslint-disable-next-line @next/next/no-sync-scripts */}
        <script src="/workspace-theme.js?v=6" data-surface="shell" />
        <link rel="stylesheet" href="/workspace-theme.css?v=6" />
        {/* Load Inter / Newsreader via explicit <link> tags.
            globals.css also @imports the same families, but a CSS @import to a
            remote URL is unreliable under Next + Tailwind/PostCSS (it can be
            reordered or load late), which makes the app fall back to a system
            serif on some loads. The <link> here guarantees the webfonts load. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;550;600;700;800&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;0,6..72,700;1,6..72,400;1,6..72,500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <AppBootstrap>
          {/* The plan, mirrored once for the app, and the one contextual
              paywall any 402 can raise (components/billing). */}
          <BillingProvider>{children}</BillingProvider>
        </AppBootstrap>
        <Toaster position="top-right" closeButton />
        {/* Product analytics and front-end error monitoring: the chart's own
            loader, so the shell and the framed chart are one visitor. It
            fetches the key from the data server and does nothing without one. */}
        <Script src="/chart-app/js/analytics.js?v=2" strategy="afterInteractive" />
      </body>
    </html>
  );
}
