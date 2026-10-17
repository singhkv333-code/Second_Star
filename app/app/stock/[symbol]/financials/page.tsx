import { StatementsView } from "./view";
import type { StatementType } from "@/lib/api";

// Next.js 15: route params and search params are async.
type Params = Promise<{ symbol: string }>;
type Search = Promise<{ tab?: string }>;

const TABS = new Set<StatementType>([
  "balance_sheet", "profit_loss", "cash_flow", "ratios",
]);

export default async function Page(
  { params, searchParams }: { params: Params; searchParams: Search },
) {
  const { symbol } = await params;
  const { tab } = await searchParams;

  // Dynamic segments arrive still percent-encoded — the same decode the
  // symbol route does, and for the same reason.
  let decoded = symbol;
  try {
    decoded = decodeURIComponent(symbol);
  } catch {
    // Malformed escape — fall back to the raw segment rather than 500ing.
  }

  // `?tab=` is what the three "See detail" buttons carry, so a reader lands on
  // the statement they were already looking at rather than on the first one.
  const initialTab = tab && TABS.has(tab as StatementType)
    ? (tab as StatementType)
    : undefined;

  return <StatementsView symbol={decoded.toUpperCase()} initialTab={initialTab} />;
}
