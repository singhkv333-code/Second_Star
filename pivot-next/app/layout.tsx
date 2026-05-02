import type { Metadata } from "next";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";
import { AppBootstrap } from "@/components/AppBootstrap";

export const metadata: Metadata = {
  title: "Pivot — Agent System",
  description: "Build, review, and run autonomous trading agents.",
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
        <Toaster richColors position="top-right" />
      </body>
    </html>
  );
}
