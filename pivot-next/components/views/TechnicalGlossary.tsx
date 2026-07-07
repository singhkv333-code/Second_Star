"use client";

/**
 * TechnicalGlossary — the "Technical details, simplified" section at the
 * bottom of a View detail page (the /view-detail glossary, made honest for
 * real views): short plain-English explanations of the MECHANICS behind the
 * strategies this view actually contains. Items are picked by the strategy
 * types present in the expressions — a view with no options never explains a
 * call option.
 *
 * Borderless: whitespace + a hairline, no box (the caller supplies the
 * hairline-topped section wrapper).
 */

import * as React from "react";
import type { ExpressionDetail } from "@/lib/types";

const FONT = "var(--font-display)";

interface Item {
  term: string;
  gloss: string;
  body: string;
}

// ── static plain-English copy, keyed by mechanic ────────────────────────────

const BASKET_ITEM: Item = {
  term: "Basket / bundle",
  gloss: "one trade that buys several stocks at once",
  body: "Instead of betting on a single company, you buy a small set of related companies in one go, usually equal-weighted. If the idea plays out, most of the basket benefits — and one bad stock can't sink the whole position the way a single-stock bet can.",
};

const PAIR_ITEM: Item = {
  term: "Pair trade (market-neutral)",
  gloss: "long the idea, short the market",
  body: "You buy the basket and simultaneously take an offsetting short position on the index. The position profits only if the basket beats the market — whether the market rises or falls. It cushions market crashes, but it also gives up the easy gains of a general rally.",
};

const CALL_ITEM: Item = {
  term: "Call option",
  gloss: "the right to buy at a fixed price later",
  body: "A call gives you the right (not the obligation) to buy at a set 'strike' price before an expiry date. If the market rises above that strike, the call gains value fast. If it doesn't, the call can expire worth nothing — you only lose what you paid for it.",
};

const DEFINED_RISK_ITEM: Item = {
  term: "Defined-risk structure",
  gloss: "a known maximum loss",
  body: "Structures like spreads pair an option you buy with one you sell. Selling the second option reduces the cost, but caps the gain — the result is a bet with a known maximum loss (what you paid) and a known maximum gain, fixed at entry.",
};

const LOT_ITEM: Item = {
  term: "Lot size & minimums",
  gloss: "options trade in fixed bundles",
  body: "Options don't trade one unit at a time — they trade in a fixed 'lot' set by the exchange. You must buy at least one whole lot, so every options strategy has a real minimum ticket, unlike a basket of stocks you can size almost freely.",
};

const PRICED_AT_DEPLOY_ITEM: Item = {
  term: "Priced at deploy",
  gloss: "numbers set by the live market",
  body: "Options here have no offline history to backtest, so we never show a made-up return for them. The real numbers — max loss, breakevens, probability — are priced from the live option chain at the moment you arm the strategy.",
};

const AVG_ITEM: Item = {
  term: "Avg profit",
  gloss: "the average past occurrence, not a promise",
  body: "The average return each time this setup happened before. Some occurrences did better, some lost money — that's why the worst and best outcomes are always shown alongside, never a single guaranteed figure.",
};

const WORST_ITEM: Item = {
  term: "Worst seen (max drop)",
  gloss: "the deepest past loss",
  body: "The single worst outcome across the past occurrences — the honest 'how bad has it gotten' number. The future can always be worse than the past; treat it as a floor-so-far, not a floor.",
};

// ── pick items by what the view's strategies actually are ───────────────────

function hasType(exprs: ExpressionDetail[], needle: string): boolean {
  return exprs.some((e) =>
    `${e.strategy_type ?? ""} ${e.expression_kind ?? ""}`
      .toLowerCase()
      .includes(needle),
  );
}

function hasOptions(exprs: ExpressionDetail[]): boolean {
  return exprs.some(
    (e) =>
      e.expression_kind === "option_strategy" ||
      (e.option_legs != null && e.option_legs.length > 0) ||
      (e.strategy_type ?? "").toLowerCase().includes("option"),
  );
}

export function buildGlossary(exprs: ExpressionDetail[]): Item[] {
  const items: Item[] = [];
  if (hasType(exprs, "basket")) items.push(BASKET_ITEM);
  if (hasType(exprs, "pair") || hasType(exprs, "neutral"))
    items.push(PAIR_ITEM);
  if (hasOptions(exprs)) {
    items.push(CALL_ITEM, DEFINED_RISK_ITEM, LOT_ITEM, PRICED_AT_DEPLOY_ITEM);
  }
  items.push(AVG_ITEM);
  if (exprs.some((e) => e.worst_drop_pct != null)) items.push(WORST_ITEM);
  return items;
}

export function TechnicalGlossary({
  expressions,
}: {
  expressions: ExpressionDetail[];
}): React.ReactElement | null {
  const items = expressions.length > 0 ? buildGlossary(expressions) : [];
  if (items.length === 0) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 17,
          fontWeight: 650,
          letterSpacing: "-0.01em",
          color: "var(--text-primary)",
        }}
      >
        Technical details, simplified
      </span>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 13,
          color: "var(--text-tertiary)",
          lineHeight: 1.45,
          marginBottom: 12,
        }}
      >
        The mechanics behind these strategies, in plain English.
      </span>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: "24px 40px",
        }}
      >
        {items.map((it) => (
          <div
            key={it.term}
            style={{ display: "flex", flexDirection: "column", gap: 5 }}
          >
            <span
              style={{
                fontFamily: FONT,
                fontSize: 14.5,
                fontWeight: 700,
                color: "var(--text-primary)",
              }}
            >
              {it.term}{" "}
              <span
                style={{
                  fontWeight: 500,
                  fontStyle: "italic",
                  color: "var(--text-tertiary)",
                }}
              >
                — {it.gloss}
              </span>
            </span>
            <p
              style={{
                margin: 0,
                fontFamily: FONT,
                fontSize: 13.5,
                lineHeight: 1.6,
                color: "var(--text-secondary)",
              }}
            >
              {it.body}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default TechnicalGlossary;
