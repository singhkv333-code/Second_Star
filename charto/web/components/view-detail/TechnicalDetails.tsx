"use client";

/**
 * TechnicalDetails — full-width "Technical details (simplified)" section at the
 * bottom of the View-detail page. Explains the mechanics in beginner-friendly
 * terms: what a call spread is, why lot sizes create a minimum, what "expected
 * return" means here. Short paragraphs; every bit of jargon gets a one-line
 * plain-English gloss.
 */

import * as React from "react";

interface Item {
  term: string;
  gloss: string;
  body: string;
}

const ITEMS: Item[] = [
  {
    term: "Index fund / ETF",
    gloss: "a basket that holds the whole market",
    body: "Instead of picking stocks, you buy one fund that owns all 50 Nifty companies in the index's proportions. Your return tracks the Nifty minus a tiny yearly fee. It is the simplest, most diversified way to be invested — the baseline everything else is compared against.",
  },
  {
    term: "Call option",
    gloss: "the right to buy at a fixed price later",
    body: "A call gives you the right (not the obligation) to buy the Nifty at a set 'strike' price before an expiry date. If the market rises above that strike, the call gains value fast. If it doesn't, the call can expire worth nothing — you only lose what you paid for it.",
  },
  {
    term: "Call spread",
    gloss: "buy one call, sell a higher one",
    body: "You buy a call and simultaneously sell another call at a higher strike. Selling the second call reduces your cost, but it also caps how much you can make. The result is a defined-risk bet: a known maximum loss (what you paid) and a known maximum gain (the gap between the two strikes). It pays if the Nifty climbs toward your target.",
  },
  {
    term: "Far out-of-the-money call",
    gloss: "a cheap long-shot option",
    body: "This is a call whose strike sits well above today's level, so the market has to move a lot for it to pay. Because that's unlikely, it's very cheap — a few rupees. Most of the time it expires worthless; occasionally, if the move happens, it pays many times over. That's the 'lottery ticket' shape.",
  },
  {
    term: "Lot size & minimums",
    gloss: "options trade in fixed bundles",
    body: "Options don't trade one unit at a time — they trade in a fixed 'lot' (a bundle of contracts set by the exchange). You have to buy at least one whole lot, so every options strategy has a real minimum ticket. An index fund has almost none — you can start with ₹500 — which is why the minimums in the table differ so much.",
  },
  {
    term: "Expected return",
    gloss: "the probability-weighted average outcome",
    body: "'Expected' doesn't mean 'what will happen' — it means the average across all the ways things could play out, weighted by how likely each is. A lottery bet can have a slightly negative expected return (it usually loses a little) even though its best case is huge. That's why we always show a low→high range next to the expected number, never a single guaranteed figure.",
  },
];

export function TechnicalDetails(): React.ReactElement {
  return (
    // Borderless glossary section — whitespace and hairlines, no box.
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-display)",
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
          fontFamily: "var(--font-display)",
          fontSize: 13,
          color: "var(--text-tertiary)",
          lineHeight: 1.45,
          marginBottom: 12,
        }}
      >
        The mechanics behind the three strategies, in plain English.
      </span>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: "24px 40px",
        }}
      >
        {ITEMS.map((it) => (
          <div key={it.term} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            <span
              style={{
                fontFamily: "var(--font-display)",
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
                fontFamily: "var(--font-display)",
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

export default TechnicalDetails;
