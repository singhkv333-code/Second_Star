"use client";

/** The simulated paper portfolio page. */

import { useEffect } from "react";

import { PortfolioTab } from "@/components/agent-panel/PortfolioTab";
import { BookShell } from "@/components/paper/BookShell";
import { setTradingMode } from "@/lib/trading-mode";

export default function PaperPage(): React.ReactElement {
  // Paper is the only mode Charto has, and the reads branch on it. Setting it
  // here rather than trusting a stored value means a browser that once held
  // "live" — from Pivot, on a shared key — cannot point this page at endpoints
  // that do not exist on this deployment.
  useEffect(() => setTradingMode("paper"), []);
  return (
    <BookShell active="/paper">
      <PortfolioTab />
    </BookShell>
  );
}
