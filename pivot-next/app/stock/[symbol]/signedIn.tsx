"use client";

import { AppShell } from "@/components/AppShell";
import { StockDetailPage } from "@/components/StockDetailPage";

/** The full research page inside the AppShell (topbar + sidebar), for a
 *  signed-in reader. Its own module so it is its own chunk (view.tsx). */
export function SignedInStockPage({ symbol }: { symbol: string }): React.ReactElement {
  return (
    <AppShell>
      <StockDetailPage symbol={symbol} />
      <div aria-hidden style={{ height: 96 }} />
    </AppShell>
  );
}
