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
  getStockMix, getStockSections,
  type MixResponse, type StockSections,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { MixPanel } from "./MixPanel";
import { PeerComparisonPanel } from "./PeerComparisonPanel";
import { PanelSkeleton } from "./chrome";

type SectionId = "revenue_mix" | "peers";

// Reading order, not coverage order: a person works down from the numbers to
// the composition to the comparison.
//
// Quarters is NOT here any more. It is the same question the Financial
// Performance panel above already answers, asked over three months instead of
// twelve, so it is a third tab up there rather than a fourth section down
// here with its own heading and its own chart language.
const SECTION_ORDER: SectionId[] = [
  "revenue_mix", "peers",
];

export function DeepSections({ symbol }: { symbol: string }): React.ReactElement | null {
  const [sections, setSections] = React.useState<StockSections | null>(null);
  const [failed, setFailed] = React.useState(false);

  const [mix, setMix] = React.useState<MixResponse | null>(null);

  // ── coverage ─────────────────────────────────────────────────────────────
  React.useEffect(() => {
    let dead = false;
    setSections(null); setFailed(false);
    setMix(null);
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
    return SECTION_ORDER.filter((t) => t === "peers" || (c[t]?.count ?? 0) > 0);
  }, [sections]);

  // ── section loads ───────────────────────────────────────────────────────
  React.useEffect(() => {
    if (!available.includes("revenue_mix") || mix) return;
    let dead = false;
    getStockMix(symbol)
      .then((r) => { if (!dead && !isError(r)) setMix(r.data); })
      .catch(() => {});
    return () => { dead = true; };
  }, [available, symbol, mix]);

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
