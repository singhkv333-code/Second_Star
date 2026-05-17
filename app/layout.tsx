import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://pivotnow.in"),
  title: "Pivot. One message. That's all investing takes.",
  description:
    "Pivot is an agentic investing assistant. Tell it what you want: buy, sell, alerts, automation, strategies. It executes.",
  openGraph: {
    type: "website",
    url: "https://pivotnow.in",
    siteName: "Pivot",
    title: "Pivot. One message. That's all investing takes.",
    description:
      "AI native trade automation and investing platform.",
  },
  twitter: {
    card: "summary_large_image",
    title: "Pivot. One message. That's all investing takes.",
    description:
      "AI native trade automation and investing platform.",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-white text-[#0d0d0e] antialiased">
        {children}
      </body>
    </html>
  );
}
