import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Pivot — Talk to the Charts",
  description: "Pivot is an interactive financial charting terminal you can talk to. Explore markets, setups, and chart structure conversationally.",
};

export default function WaitlistLayout({ children }: { children: React.ReactNode }) {
  return children;
}
