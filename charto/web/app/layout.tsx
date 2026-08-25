import type { Metadata, Viewport } from "next";
import "./globals.css";
import "@/components/landing/PivotLanding.css";
import "@/components/landing/film/ProductFilm.css";
import "@/components/landing/features/Features.css";
import { Toaster } from "@/components/ui/sonner";
import { AppBootstrap } from "@/components/AppBootstrap";

export const metadata: Metadata = {
  title: "Charto — company",
  description: "Company page for a charto chart symbol.",
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
        {/* charto: apply the theme BEFORE first paint. Without this the page
            paints light, then flips to dark a frame later — a white flash
            every time you open a company from a dark chart. Same source of
            truth as view.tsx: ?theme= from the chart, else the remembered
            choice, else the OS. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{var u=new URLSearchParams(location.search).get('theme');" +
              "var m=(u==='dark'||u==='light')?u:localStorage.getItem('charto_theme');" +
              "if(m!=='dark'&&m!=='light')m=matchMedia('(prefers-color-scheme: dark)')" +
              ".matches?'dark':'light';" +
              "document.documentElement.classList.toggle('dark',m==='dark');}catch(e){}",
          }}
        />
      </head>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <AppBootstrap>{children}</AppBootstrap>
        <Toaster position="top-right" closeButton />
      </body>
    </html>
  );
}
