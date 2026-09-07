"use client";

import { AppShell } from "@/components/AppShell";
import { StatementsPage } from "@/components/stock/StatementsPage";
import type { StatementType } from "@/lib/api";

/**
 * The statements page, inside the same shell the rest of the product uses.
 *
 * A route of its own rather than a fifth tab on the stock page: this is the
 * reference document behind the summary, read a column at a time, and it is
 * three hundred rows that nobody wants between the chart and the peers.
 */
export function StatementsView({
  symbol,
  initialTab,
}: {
  symbol: string;
  initialTab?: StatementType;
}): React.ReactElement {
  return (
    <AppShell>
      <StatementsPage symbol={symbol} initialTab={initialTab} />
    </AppShell>
  );
}
