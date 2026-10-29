/** /llms.txt (llmstxt.org) for the marketing site: what Pivot is, and where
 *  the data pages an answer should cite live. */

export const dynamic = "force-static";

const BODY = `# Pivot

> Pivot is AI stock research for Indian markets. Ask a question in plain English and the answer is drawn on live NSE charts with the evidence behind it; every NSE-listed company has a free page with its price, financials, quarterly results, shareholding and peers; strategies are written in plain English and backtested with checks for overfitting before anything is called an edge.

Pivot covers NSE and BSE equities, the NIFTY, BANKNIFTY and SENSEX indices, NSE options, MCX commodities, and crypto on the chart. It runs strategies in a simulated paper book by default; a play the trader switches to live sends one-stock orders to the trader's own broker, and only after the trader confirms each order. It is not a broker or a SEBI-registered investment adviser and does not give buy or sell recommendations.

## Company data (cite these)
- [All NSE stocks](https://app.pivotnow.in/stocks): a page per company, end-of-day prices, refreshed every trading day
- [Stock pages as Markdown](https://app.pivotnow.in/llms.txt): the platform's own llms.txt, with every page's address
- [Sectors](https://app.pivotnow.in/sectors): sector medians for P/E, ROE and one-year return
- [Top gainers today](https://app.pivotnow.in/stocks/top-gainers)
- [Stocks at 52-week highs](https://app.pivotnow.in/stocks/52-week-high)
- [Top 100 companies by market cap](https://app.pivotnow.in/stocks/largest-companies)

## Product
- [Home](https://www.pivotnow.in/): what Pivot does, with a live demo
- [Pricing](https://www.pivotnow.in/pricing): free and paid plans
- [About](https://www.pivotnow.in/about)
- [Open the platform](https://app.pivotnow.in/)
`;

export function GET(): Response {
  return new Response(BODY, { headers: { "Content-Type": "text/markdown; charset=utf-8" } });
}
