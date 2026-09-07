"use client";

import { AppShell } from "@/components/AppShell";
import { StockDetailPage } from "@/components/StockDetailPage";
import { StockAskBar } from "@/components/stock/StockAskBar";

/**
 * Client wrapper that mounts the stock detail page inside the same
 * AppShell (topbar + sidebar) the rest of the product uses, so the
 * route keeps the global navigation chrome instead of replacing it.
 *
 * AppBootstrap (auth gate + token provider) is wired once in
 * app/layout.tsx — no need to nest it here.
 *
 * The ask bar is mounted here rather than inside StockDetailPage because the
 * page has two layouts (desktop and phone) and the bar belongs to the ROUTE:
 * one instance, present whenever this page is open, whichever layout is
 * drawn. It floats over the content, so the spacer below it reserves the
 * height it would otherwise cover at the bottom of the scroll.
 */
export function StockSymbolView({ symbol }: { symbol: string }): React.ReactElement {
  return (
    <AppShell>
      <StockDetailPage symbol={symbol} />
      <div aria-hidden style={{ height: 96 }} />
      <StockAskBar symbol={symbol} />
    </AppShell>
  );
}
