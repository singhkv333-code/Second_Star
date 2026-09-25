"use client";

/**
 * The per-tab grounding block the composer sends with every question.
 *
 * The prompt bar floats over whatever tab the user is on, so "how am I doing"
 * on Portfolio and "how am I doing" on Strategy are different questions. What
 * the model was getting was the route and the document title, which tells it
 * the URL changed and nothing about what is on the screen — so it either asked
 * which surface the user meant or answered for the wrong one.
 *
 * TWO FIELDS DO THE WORK, and the split matters:
 *
 *   `section` is the handful of facts already on screen. It exists so a
 *   glanceable question ("what's my P&L") is answered from the block in one
 *   hop instead of a tool call, and so a follow-up pronoun resolves. It is
 *   deliberately a SUMMARY, never the underlying rows: the holdings table is
 *   a tool call away and copying it here would spend hundreds of tokens per
 *   turn to pre-empt a question the user may not ask.
 *
 *   `available_data` is the reach, not the payload. It names what this surface
 *   can pull so the model asks for it rather than declaring it cannot see it.
 *   The failure it fixes is the expensive one: a model that says "I don't have
 *   your holdings" while sitting one tool call away from them teaches the user
 *   the product is blind.
 *
 * BUDGET. The backend truncates every field at 240 chars and takes the first
 * 16 capability entries (`_MAX_PAGE_FIELD` / `_MAX_PAGE_CAPABILITIES` in
 * backend/routers/chat.py), so the ceiling is roughly 150 tokens even if this
 * file misbehaves. The target is well under that: one summary line and a short
 * noun list per tab. Everything here is client-supplied data rendered through
 * the backend's allowlist, which is why it carries facts and never phrasing
 * that could read as an instruction.
 */

import type { ChatPageContext } from "@/lib/chatStream";
import type { PortfolioSummary } from "@/lib/api";

/** Tabs that own a copilot surface. `chart` is absent on purpose: the chart
 *  ask is routed to Charto, which builds its own, much richer, chart envelope. */
export type ContextTab =
  | "home"
  | "portfolio"
  | "agents"
  | "screener"
  | "brokers"
  | "chat";

export type PageFacts = {
  /** Live portfolio summary, when the metric strip has one. */
  summary?: PortfolioSummary;
  /** "paper" or "real" — changes what an order question even means. */
  tradingMode?: string;
  /** Focused security, on the company page. */
  symbol?: string;
  name?: string;
  /** Brokers the user has actually connected. */
  brokersConnected?: string[];
  /** Whether live order placement is armed server-side. */
  brokersLiveArmed?: boolean;
};

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

function money(n: number | undefined): string {
  return typeof n === "number" && Number.isFinite(n) ? INR.format(n) : "—";
}

function signedPct(n: number | undefined): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

/** One line of portfolio state. Numbers only — the reading is the model's job. */
function portfolioLine(s: PortfolioSummary, mode?: string): string {
  const bits = [
    `value ${money(s.total_value)}`,
    `invested ${money(s.invested_value)}`,
    `P&L ${money(s.total_pnl)} (${signedPct(s.total_pnl_pct)})`,
    `today ${money(s.day_pnl)}`,
    `${s.num_holdings} holdings`,
  ];
  if (typeof s.cash_available === "number") bits.push(`cash ${money(s.cash_available)}`);
  if (mode) bits.push(`${mode} book`);
  return bits.join(", ");
}

/**
 * What each tab can reach. These are NOUNS the model can ask for, phrased the
 * way the tools are named, so naming one is a step toward calling it.
 */
const REACH: Record<ContextTab, string[]> = {
  home: ["portfolio summary", "watchlist", "market movers", "saved strategies"],
  portfolio: [
    "holdings", "positions", "open orders", "trade history",
    "per-holding P&L", "sector allocation", "portfolio value history",
  ],
  agents: [
    "saved strategies", "strategy drafts", "run history",
    "backtest results", "trust-ladder verdicts", "armed/paused state",
  ],
  screener: ["screen filters", "current results", "sector universe", "fundamental fields"],
  brokers: ["connected brokers", "token expiry", "live-order toggle", "broker order log"],
  chat: ["portfolio summary", "holdings", "saved strategies", "market data"],
};

const LABEL: Record<ContextTab, string> = {
  home: "Home",
  portfolio: "Portfolio",
  agents: "Strategy",
  screener: "Screener",
  brokers: "Brokers",
  chat: "Chat",
};

/**
 * Build the block for the tab the composer is floating over.
 *
 * Returns undefined for a tab with nothing worth saying, so the request omits
 * `page_context` entirely rather than sending an empty envelope.
 */
export function buildPageContext(
  tab: ContextTab,
  facts: PageFacts = {},
): ChatPageContext | undefined {
  const ctx: ChatPageContext = {
    surface: LABEL[tab],
    available_data: REACH[tab],
  };

  // A focused security outranks the tab it is being viewed from: on the
  // company page the question is about the company, wherever the user came
  // from. Its own reach replaces the tab's.
  if (facts.symbol) {
    ctx.surface = "Company page";
    ctx.entity = { kind: "security", symbol: facts.symbol, name: facts.name ?? facts.symbol };
    ctx.available_data = [
      "quote", "price history", "fundamentals", "quarterly results",
      "shareholding", "filings", "peers", "news",
    ];
    return ctx;
  }

  if ((tab === "portfolio" || tab === "home") && facts.summary) {
    ctx.section = portfolioLine(facts.summary, facts.tradingMode);
  }

  if (tab === "brokers") {
    const connected = facts.brokersConnected ?? [];
    // Said plainly in both directions. "None connected" is the fact that stops
    // the model explaining how to place an order through a broker that isn't
    // there, and it is the fact a bare capability list cannot carry.
    ctx.section = connected.length
      ? `connected: ${connected.join(", ")}; live orders ${facts.brokersLiveArmed ? "armed" : "off"}`
      : "no brokers connected yet";
  }

  if (tab === "agents" && facts.tradingMode) {
    ctx.section = `${facts.tradingMode} book`;
  }

  return ctx;
}
