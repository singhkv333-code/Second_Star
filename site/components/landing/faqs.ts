/** The landing page's questions. Plain data, so the server-rendered layout can turn the questions into
 *  FAQPage markup while the client page renders them. */

export const LANDING_FAQS: { q: string; a: string; link?: { href: string; label: string } }[] = [
  { q: "What is Pivot?", a: "Pivot is a complete workspace for charting, market research, strategy testing and paper trading. Ask a question in plain English, inspect the evidence on the chart, and turn an idea into rules you can test." },
  { q: "Who is Pivot for?", a: "Both active traders and long-term investors. Traders can investigate setups, set alerts and test rules; investors can examine companies, filings, fundamentals and comparisons in the same workspace." },
  { q: "What can I ask Pivot to do on a chart?", a: "Explain a move, mark levels and patterns, compare setups, add indicators, check multiple timeframes at once, mark earnings and their implications on the chart, or watch for conditions. You can inspect the market data behind its drawings." },
  { q: "What is a play?", a: "A rule you describe in plain English that watches the market for you: a price level, an NSE filing, a headline, a schedule or a limit on your own trading. Pivot turns the sentence into triggers and actions you can read and change, shows how often the rule would have fired on past bars, and runs it on paper." },
  { q: "What happens when a play fires?", a: "It does what you set it to do: write an alert to your Inbox with the value that decided it, propose an order for you to confirm, or place a simulated order in your paper book. A play only reaches a broker if you switch it to live, and then only an order you confirm." },
  { q: "Does Pivot place real trades?", a: "Only when you choose to. Plays fill in a simulated paper book by default. A play you switch to live sends one-stock orders to your own connected broker, and only after you confirm each order. Pivot never places an order on its own and never holds your funds." },
  { q: "Which markets does Pivot cover?", a: "Indian equities and indices, NSE options and MCX commodities, with crypto available on charts." },
  { q: "Is Pivot giving me investment advice?", a: "No. Pivot provides market data, analysis and testing frameworks. It does not give personalised buy, sell or hold recommendations. This is analysis, not financial advice." },
  { q: "How do I get access, and what does it cost?", a: "You can start for free. Premium features are available on paid plans.", link: { href: "/pricing", label: "Check out the pricing." } },
];
