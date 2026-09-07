import { StockSymbolView } from "./view";

// Next.js 15: route params are now async.
type Params = Promise<{ symbol: string }>;

export default async function Page({ params }: { params: Params }) {
  const { symbol } = await params;
  // Next hands dynamic segments over STILL percent-encoded. Without this
  // decode, a symbol containing a reserved character arrived as its literal
  // encoding ("%5ENSEI"), got encoded a second time by the API helper, and
  // 404'd with "no quote available for %5ENSEI.NSE". Index symbols also carry
  // a space ("NIFTY 50" → "NIFTY%2050"), so this is the common path now.
  let decoded = symbol;
  try {
    decoded = decodeURIComponent(symbol);
  } catch {
    // Malformed escape sequence — fall back to the raw segment rather than
    // throwing a 500 out of the route.
  }
  return <StockSymbolView symbol={decoded.toUpperCase()} />;
}
