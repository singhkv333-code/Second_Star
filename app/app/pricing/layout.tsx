import type { Metadata } from "next";
import { absoluteUrl } from "@/lib/seo/site";

// The page itself is a client component, so its metadata lives here.
export const metadata: Metadata = {
  title: "Pricing: Free, Pro and Pro+ Plans | Pivot",
  description:
    "Pivot's plans: start free with stock pages, charts and AI answers; upgrade for deeper history, more AI questions " +
    "and strategy backtests. Prices in rupees, GST included.",
  robots: { index: true, follow: true },
  alternates: { canonical: absoluteUrl("/pricing") },
};

export default function Layout({ children }: { children: React.ReactNode }): React.ReactElement {
  return <>{children}</>;
}
