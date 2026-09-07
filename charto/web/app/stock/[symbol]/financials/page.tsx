import { StatementsView } from "./view";

// Next.js 15: route params are async. Same decode as the overview route —
// dynamic segments arrive still percent-encoded.
type Params = Promise<{ symbol: string }>;

export default async function Page({ params }: { params: Params }) {
  const { symbol } = await params;
  let decoded = symbol;
  try {
    decoded = decodeURIComponent(symbol);
  } catch {
    // Malformed escape sequence — the raw segment beats a 500.
  }
  return <StatementsView symbol={decoded.toUpperCase()} />;
}
