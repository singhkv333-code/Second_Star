/** The landing page's questions. Plain data, so the server-rendered layout can turn the questions into
 *  FAQPage markup while the client page renders them. */

export const LANDING_FAQS: { q: string; a: string; link?: { href: string; label: string } }[] = [
  { q: "What is Pivot?", a: "Pivot is a complete workspace for charting, market research, strategy testing and paper trading. Ask a question in plain English, inspect the evidence on the chart, and turn an idea into rules you can test." },
  { q: "Who is Pivot for?", a: "Both active traders and long-term investors. Traders can investigate setups, set alerts and test rules; investors can examine companies, filings, fundamentals and comparisons in the same workspace." },
  { q: "What can I ask Pivot to do on a chart?", a: "Explain a move, mark levels and patterns, compare setups, add indicators, check multiple timeframes at once, mark earnings and their implications on the chart, or watch for conditions. You can inspect the market data behind its drawings." },
  { q: "Does Pivot place real trades?", a: "No. Strategies can be armed and followed in a simulated paper book. Pivot does not send live orders to a broker or hold your funds." },
  { q: "Which markets does Pivot cover?", a: "Indian equities and indices, NSE options and MCX commodities, with crypto available on charts." },
  { q: "Is Pivot giving me investment advice?", a: "No. Pivot provides market data, analysis and testing frameworks. It does not give personalised buy, sell or hold recommendations. This is analysis, not financial advice." },
  { q: "How do I get access, and what does it cost?", a: "You can start for free. Premium features are available on paid plans.", link: { href: "/pricing", label: "Check out the pricing." } },
];
