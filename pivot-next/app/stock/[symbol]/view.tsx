"use client";

import { AppShell } from "@/components/AppShell";
import { StockDetailPage } from "@/components/StockDetailPage";

/**
 * Client wrapper that mounts the stock detail page inside the same
 * AppShell (topbar + sidebar) the rest of the product uses, so the
 * route keeps the global navigation chrome instead of replacing it.
 *
 * AppBootstrap (auth gate + token provider) is wired once in
 * app/layout.tsx — no need to nest it here.
 *
 * Quick Ask and the expanded Copilot are mounted once by AppShell. Keeping
 * them out of StockDetailPage prevents a second thread and duplicate stream.
 */
export function StockSymbolView({ symbol }: { symbol: string }): React.ReactElement {
  return (
    <AppShell>
      <StockDetailPage symbol={symbol} />
      <div aria-hidden style={{ height: 96 }} />
    </AppShell>
  );
}
