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
      <body className="min-h-screen bg-background text-foreground antialiased">
        <AppBootstrap>{children}</AppBootstrap>
        <Toaster position="top-right" />
      </body>
    </html>
  );
}
