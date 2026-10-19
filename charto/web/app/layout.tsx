import type { Metadata, Viewport } from "next";
import "./globals.css";
import "@/components/landing/PivotLanding.css";
import "@/components/landing/film/ProductFilm.css";
import "@/components/landing/features/Features.css";
import "@/components/landing/v3/v3.css";
import { Toaster } from "@/components/ui/sonner";
import { AppBootstrap } from "@/components/AppBootstrap";
import { LANDING_FAQS } from "@/components/landing/faqs";

const SITE = "https://www.pivotnow.in";

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: "Pivot: AI Stock Market Research & Charts for India | Talk to the Charts",
  description:
    "Pivot is AI stock research for Indian markets: charts you can talk to, fundamentals for every NSE company, " +
    "auto-detected levels and patterns, and strategies you can backtest honestly. Start free.",
  applicationName: "Pivot",
  keywords: ["AI stock analysis India", "stock market charts", "NSE stocks", "stock screener India", "backtesting", "technical analysis", "fundamental analysis"],
  alternates: { canonical: SITE },
  openGraph: {
    type: "website", url: SITE, siteName: "Pivot", locale: "en_IN",
    title: "Pivot: Superintelligent charts for trading",
    description: "Ask a question, get the answer drawn on live NSE charts with the evidence. Fundamentals, patterns and honest backtests in one workspace.",
    // The landing page itself, so a pasted link unfurls into what the visitor will see.
    images: [{ url: "/og-landing.jpg", width: 2400, height: 1260, alt: "Pivot: Superintelligent charts for trading" }],
  },
  twitter: {
    card: "summary_large_image", site: "@pivotnow00",
    images: [{ url: "/og-landing.jpg", width: 2400, height: 1260, alt: "Pivot: Superintelligent charts for trading" }],
  },
  verification: {
    ...(process.env.GOOGLE_SITE_VERIFICATION ? { google: process.env.GOOGLE_SITE_VERIFICATION } : {}),
    ...(process.env.BING_SITE_VERIFICATION ? { other: { "msvalidate.01": process.env.BING_SITE_VERIFICATION } } : {}),
  },
  // Favicon + apple-touch-icon are wired via the Next.js App Router
  // file convention: app/icon.svg and app/apple-icon.png. No explicit
  // `icons` field is needed; Next bakes <link rel="icon"> tags into
  // <head> with content-hashed URLs that defeat browser cache.
};

const SITE_GRAPH = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${SITE}/#organization`,
      name: "Pivot",
      legalName: "Pivot Tech",
      url: SITE,
      logo: `${SITE}/pivot-icon.png`,
      email: "contact@pivotnow.in",
      areaServed: "IN",
      sameAs: ["https://x.com/pivotnow00", "https://www.instagram.com/pivotnow00/"],
    },
    { "@type": "WebSite", "@id": `${SITE}/#website`, url: SITE, name: "Pivot", inLanguage: "en-IN", publisher: { "@id": `${SITE}/#organization` } },
    {
      "@type": "SoftwareApplication",
      name: "Pivot",
      url: "https://app.pivotnow.in",
      applicationCategory: "FinanceApplication",
      operatingSystem: "Web",
      description: "AI stock research for Indian markets: charts you can talk to, company fundamentals, pattern and level detection, strategy backtests and a paper book.",
      offers: { "@type": "Offer", price: "0", priceCurrency: "INR", description: "Free plan; paid plans for deeper history and more AI questions." },
      publisher: { "@id": `${SITE}/#organization` },
    },
    {
      "@type": "FAQPage",
      mainEntity: LANDING_FAQS.map(({ q, a }) => ({ "@type": "Question", name: q, acceptedAnswer: { "@type": "Answer", text: a } })),
    },
  ],
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
        <script src="/workspace-theme.js?v=6" data-surface="shell" />
        <link rel="stylesheet" href="/workspace-theme.css?v=6" />
        {/* Load Inter / Newsreader via explicit <link> tags. globals.css also
            @imports the same families, but a CSS @import to a remote URL is
            unreliable under Next + Tailwind/PostCSS (it can be reordered or
            load late), which makes the app fall back to a system serif on some
            loads. The <link> here guarantees the webfonts load.
            JetBrains Mono was dropped with the numeral face: every figure is
            Inter-tabular now, and nothing in the app asked for it. */}
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

        {/* Who Pivot is, for search and answer engines (schema.org). */}
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(SITE_GRAPH).replace(/</g, "\\u003c") }}
        />
      </head>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <AppBootstrap>{children}</AppBootstrap>
        <Toaster position="top-right" closeButton />
      </body>
    </html>
  );
}
