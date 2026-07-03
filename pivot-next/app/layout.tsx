import type { Metadata } from "next";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";
import { AppBootstrap } from "@/components/AppBootstrap";

export const metadata: Metadata = {
  title: "Pivot — Agent System",
  description: "Build, review, and run autonomous trading agents.",
  // Favicon + apple-touch-icon are wired via the Next.js App Router
  // file convention: app/icon.svg and app/apple-icon.png. No explicit
  // `icons` field is needed; Next bakes <link rel="icon"> tags into
  // <head> with content-hashed URLs that defeat browser cache.
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Load Inter / JetBrains Mono / Newsreader via explicit <link> tags.
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
          href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Inter:wght@400;500;550;600;700&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;0,6..72,700;1,6..72,400;1,6..72,500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <AppBootstrap>{children}</AppBootstrap>
        <Toaster position="top-right" closeButton />
      </body>
    </html>
  );
}
