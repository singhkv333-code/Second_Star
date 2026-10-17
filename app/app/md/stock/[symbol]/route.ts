import { getDailyBars, getStock } from "@/lib/seo/data";
import { stockMarkdown } from "@/lib/seo/markdown";
import { faqs, priceStats } from "@/lib/seo/stockCopy";
import { absoluteUrl, stockPath } from "@/lib/seo/site";

/** /stock/{symbol}.md, rewritten here by middleware.ts: the stock page as
 *  Markdown. The HTML page is canonical (the Link header says so). */

export const revalidate = 900;

export async function GET(_req: Request, { params }: { params: Promise<{ symbol: string }> }): Promise<Response> {
  let symbol = (await params).symbol;
  try { symbol = decodeURIComponent(symbol); } catch { /* raw */ }
  symbol = symbol.toUpperCase();
  const [s, bars] = await Promise.all([getStock(symbol), getDailyBars(symbol)]);
  if (!s) return new Response("not found", { status: 404 });
  const stats = priceStats(bars);
  return new Response(stockMarkdown(s, stats, faqs(s, stats)), {
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Cache-Control": "public, max-age=900",
      Link: `<${absoluteUrl(stockPath(symbol))}>; rel="canonical"`,
    },
  });
}
