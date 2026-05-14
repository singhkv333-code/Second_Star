import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pivot — One message. That's all investing takes.",
  description:
    "Pivot is an agentic investing assistant. Tell it what you want — buy, sell, alerts, automation, strategies — and it executes.",
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
