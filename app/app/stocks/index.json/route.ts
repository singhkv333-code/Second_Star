import { getCompanies, listed } from "@/lib/seo/data";

/** [symbol, name] for every priced company, largest first: the public
 *  search box's whole index (components/seo/StockSearch.tsx). */

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const rows = listed(await getCompanies().catch(() => null));
  return Response.json(rows.map((r) => [r.symbol, r.name]), {
    headers: { "Cache-Control": "public, max-age=900, stale-while-revalidate=3600" },
  });
}
