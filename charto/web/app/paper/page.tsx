"use client";

/**
 * /paper — the Portfolio page.
 *
 * This is Pivot's `PortfolioTab`, unedited: the value header, the range
 * selector, the equity curve, the holdings table with its sort and its live
 * marks, the Orders and History views, and the score panel underneath.
 *
 * It needed no changes because the payloads were matched to it rather than the
 * other way round. `lib/api` already branches every portfolio read to the
 * paper book when the trading mode is paper, and Charto serves those paths;
 * the two it could not get that way — `/portfolio/scores` and
 * `/api/portfolio/performance` — are computed from the same book in
 * `charto/data/paper.py`.
 */

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
