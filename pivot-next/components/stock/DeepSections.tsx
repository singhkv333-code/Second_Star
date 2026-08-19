"use client";

/**
 * The deep sections, below the existing overview / metrics / financial summary.
 *
 * Everything above this component is untouched. This is purely additive: the
 * chart, the overview card, the key-metrics strip and the financials panel
 * keep their layout and their data path.
 *
 * The organising rule is that COVERAGE DECIDES THE SECTIONS: the page asks
 * `/sections` what this company actually has and renders only those. HDFCBANK,
 * for instance, genuinely has no quarterly metrics — so it gets no Quarters
 * section rather than an empty one. An empty panel reads as a broken product;
 * a missing section reads as a page that knows what it holds.
 *
 * This page is a research document, not a tabbed settings screen. Available
 * sections render in one vertical reading order at full width, each standing
 * on its own with a single title. There is no index rail and no wrapper
 * heading: an index over three sections is furniture, and a "Company research"
 * title above three titled sections only says again what they already say.
 */

import * as React from "react";

import {
  getCompanyScores, getDeals, getFlows, getShareholding, getStockMix, getStockSections,
  type CompanyScores, type DealsResponse, type FlowsResponse, type MixResponse,
  type ShareholdingResponse, type StockSections,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { DealsPanel } from "./DealsPanel";
import { FlowsPanel } from "./FlowsPanel";
import { MixPanel } from "./MixPanel";
import { PeerComparisonPanel } from "./PeerComparisonPanel";
import { ShareholdingPanel } from "./ShareholdingPanel";
import { SolvencyValuePanel } from "./SolvencyValuePanel";
import { PanelSkeleton } from "./chrome";

type SectionId = "scores" | "revenue_mix" | "peers" | "shareholding" | "flows" | "deals";

// Reading order, not coverage order: a person works down from the numbers to
// the composition to the comparison.
//
// Quarters is NOT here any more. It is the same question the Financial
// Performance panel above already answers, asked over three months instead of
// twelve, so it is a third tab up there rather than a fourth section down
// here with its own heading and its own chart language.
// Shareholding, flows and deals are appended BELOW the peer table rather than
// slotted next to the mix chart. They are not variations on "what is this
// company made of" — they are the market around it, which is a later question
// than the business itself.
//
// None of the three is announced by /sections: shareholding lives in shp.*
// and the flows pair in charto's store, neither of which the coverage call
// counts. So each one loads unconditionally and reports its own emptiness by
// returning `available: false`, and the section is dropped on that instead.
// Scores sit after the peer table. They are arithmetic over the statements,
// so they could open this run — but a score is a JUDGEMENT, and a judgement
// reads better once the reader has seen what the company sells and how it
// stands against the companies it competes with. Mix, then peers, then the
// verdict those two have been building toward.
const SECTION_ORDER: SectionId[] = [
  "revenue_mix", "peers", "scores", "shareholding", "flows", "deals",
];

export function DeepSections({ symbol, price }: { symbol: string; price?: number | null }): React.ReactElement | null {
  const [sections, setSections] = React.useState<StockSections | null>(null);
  const [failed, setFailed] = React.useState(false);

  const [scores, setScores] = React.useState<CompanyScores | null>(null);
  const [mix, setMix] = React.useState<MixResponse | null>(null);
  const [shp, setShp] = React.useState<ShareholdingResponse | null>(null);
  const [flows, setFlows] = React.useState<FlowsResponse | null>(null);
  const [deals, setDeals] = React.useState<DealsResponse | null>(null);

  // ── coverage ─────────────────────────────────────────────────────────────
  React.useEffect(() => {
    let dead = false;
    setSections(null); setFailed(false);
    setScores(null); setMix(null); setShp(null); setFlows(null); setDeals(null);
    getStockSections(symbol)
      .then((r) => {
        if (dead) return;
        if (isError(r)) { setFailed(true); return; }
        setSections(r.data);
      })
      .catch(() => { if (!dead) setFailed(true); });
    return () => { dead = true; };
  }, [symbol]);

  const available = React.useMemo<SectionId[]>(() => {
    if (!sections) return [];
    const c = sections.coverage;
    return SECTION_ORDER.filter((t) => {
      if (t === "peers") return true;
      // Self-reporting sections: drawn once their own fetch says it has data.
      if (t === "scores") return scores?.available ?? false;
      if (t === "shareholding") return shp?.available ?? false;
      if (t === "flows") return flows?.available ?? false;
      if (t === "deals") return deals?.available ?? false;
      return (c[t]?.count ?? 0) > 0;
    });
  }, [sections, scores, shp, flows, deals]);

  // ── section loads ───────────────────────────────────────────────────────
  React.useEffect(() => {
    if (!available.includes("revenue_mix") || mix) return;
    let dead = false;
    getStockMix(symbol)
      .then((r) => { if (!dead && !isError(r)) setMix(r.data); })
      .catch(() => {});
    return () => { dead = true; };
  }, [available, symbol, mix]);

  // These three do not wait on coverage — coverage does not know about them.
  React.useEffect(() => {
    let dead = false;
    getCompanyScores(symbol)
      .then((r) => { if (!dead && !isError(r)) setScores(r.data); })
      .catch(() => {});
    getShareholding(symbol)
      .then((r) => { if (!dead && !isError(r)) setShp(r.data); })
      .catch(() => {});
    getFlows(symbol, 180)
      .then((r) => { if (!dead && !isError(r)) setFlows(r.data); })
      .catch(() => {});
    getDeals(symbol, 80)
      .then((r) => { if (!dead && !isError(r)) setDeals(r.data); })
      .catch(() => {});
    return () => { dead = true; };
  }, [symbol]);

  // A company with none of this data gets nothing rather than an empty shell —
  // the page above still stands on its own.
  if (failed || (sections && available.length === 0)) return null;

  return (
    <section
      aria-label="Company detail"
      style={{
        marginTop: 28,
        display: "flex",
        flexDirection: "column",
        gap: 40,
        // Financial Performance above pads its header 20px in. These sections
        // started at 0, so every heading below it began a fifth of an inch to
        // the left of the one above — the misalignment was between SECTIONS,
        // not inside them.
        padding: "0 20px",
      }}
    >
      {sections === null ? <PanelSkeleton rows={7} /> : null}

      {available.includes("revenue_mix") ? <ResearchSection id="revenue_mix" label="Segment mix">
        {mix ? <MixPanel data={mix} /> : <PanelSkeleton rows={6} />}
      </ResearchSection> : null}

      <ResearchSection id="peers" label="Peer comparison">
        <PeerComparisonPanel symbol={symbol} />
      </ResearchSection>

      {available.includes("scores") && scores ? (
        <ResearchSection id="scores" label="Solvency and value">
          <SolvencyValuePanel data={scores} price={price ?? null} />
        </ResearchSection>
      ) : null}

      {available.includes("shareholding") && shp ? (
        <ResearchSection id="shareholding" label="Shareholding">
          <ShareholdingPanel data={shp} />
        </ResearchSection>
      ) : null}

      {available.includes("flows") && flows ? (
        <ResearchSection id="flows" label="Delivery and open interest">
          <FlowsPanel data={flows} />
        </ResearchSection>
      ) : null}

      {available.includes("deals") && deals ? (
        <ResearchSection id="deals" label="Bulk and block deals">
          <DealsPanel data={deals} />
        </ResearchSection>
      ) : null}
    </section>
  );
}

/** One section, full width, no chrome of its own. The label is only the
 *  accessible name — each panel prints its own title, and printing it twice
 *  was the "extra text" the page was carrying. */
function ResearchSection({ id, label, children }: { id: SectionId; label: string; children: React.ReactNode }): React.ReactElement {
  return (
    <section id={`stock-section-${id}`} aria-label={label} style={{ scrollMarginTop: 72 }}>
      {children}
    </section>
  );
}
