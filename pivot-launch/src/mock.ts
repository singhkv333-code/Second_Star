export const USER_PROMPT =
  "Buy ₹10k of Reliance every Friday at 3:55 PM if it's down 1% or more";

export const AI_RESPONSE =
  "Done. I've drafted this agent for you — review and activate when ready.";

export const AGENT = {
  name: "RELIANCE Weekday Dip-Buy",
  trigger: "Every Friday, 3:55 PM IST",
  condition: "RELIANCE price ↓ ≥ 1% intraday",
  action: "Market buy ₹10,000",
  product: "CNC (Delivery)",
} as const;

export const BACKTEST_STATS = {
  returnPct: "+12.4%",
  winRate: "68%",
  maxDrawdown: "−3.2%",
  // 9 portfolio-value samples — generally up-right with a small wiggle.
  series: [150, 145, 140, 138, 128, 132, 120, 105, 95] as const,
} as const;

export const PORTFOLIO = {
  value: "₹77,945",
  dayPnl: "+₹294",
  totalPnl: "+₹3,355",
  totalPnlPct: "+4.50%",
} as const;

export const EXISTING_AGENTS = [
  { name: "INFY weekly dip-buy", status: "Idle" },
  { name: "TCS monthly SIP", status: "Idle" },
  { name: "RELIANCE 3:55 PM weekday buy", status: "Idle" },
] as const;
