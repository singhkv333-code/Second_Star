import { StockSymbolView } from "./view";

// Next.js 15: route params are now async.
type Params = Promise<{ symbol: string }>;

export default async function Page({ params }: { params: Params }) {
  const { symbol } = await params;
  return <StockSymbolView symbol={symbol.toUpperCase()} />;
}
