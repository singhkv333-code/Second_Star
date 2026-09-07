/**
 * FeaturesSection — what the product actually does, told as one story.
 *
 * Eight capabilities in six layout slots. Each one is a claim of a few words
 * and a single line under it; the argument is carried by what sits beside or
 * beneath it. Most of those are transcriptions of the real thing rather than
 * illustrations of it. That division is the whole design: the words say what
 * you get, the frame shows the vocabulary you get it in.
 *
 * Two slots are FIGURES instead, and deliberately. A claim about how much fits
 * on screen at once, and a claim that three things live in one place, are the
 * two that a single pane cannot make — quoting them as panes shrinks the
 * workspace until it argues against itself, and splits the journal into the
 * three separate surfaces the feature exists to join. Those two are drawn,
 * using the app's own 40-unit tile illustrations (`feature-figures`).
 *
 * The order is the product's own, from the chart outward:
 *
 *   01  STRUCTURE     what the detectors found      ── the anchor, largest slot
 *   02  WORKSPACE     the room it all happens in    ── figures, full measure
 *   03  CONTEXT       every timeframe at once       ┐ paired: both widen the
 *   04  REACH         the whole universe            ┘ question past this chart
 *   05  MEMORY        trades, notes and charts      ── figures, full measure
 *   06  FUNDAMENTALS  the company behind the ticker ┐ paired, uneven: the
 *   07  PATIENCE      it watches while you are away ┘ tiles need the wider half
 *   08  CONTROL       the chart does what you said  ── now the section heading
 *
 * Every slot has a different geometry so the eye never learns a template and
 * stops reading, and weight follows importance: 01 and 02 take roughly twice
 * the vertical space of the paired middle.
 *
 * Motion is entrance-only and demonstrative: `landing-scroll` batches the
 * page's `[data-reveal]` blocks so the ones that cross the line together enter
 * together, the halftone plates behind the section drift a little slower than
 * the page, and the level bands on 01 draw onto the candles a beat apart —
 * cause and effect, once. Nothing loops, nothing floats, nothing hijacks the
 * scroll.
 */
"use client";

import * as React from "react";
import {
  DEMO_ALERT,
  DEMO_JOURNAL_FIGURES,
  DEMO_SCREEN,
  DEMO_WORKSPACE_FIGURES,
} from "./feature-data";
import { FeatureDemo } from "./feature-demo";
import { FigureRow } from "./feature-figures";
import {
  AlertPanel,
  AskedLine,
  FundamentalsPanel,
  ProductFrame,
  RungsPanel,
  ScreenPanel,
} from "./feature-visuals";

/**
 * A feature's words: one claim and one line about it.
 */
function FeatureCopy({
  title,
  reveal = false,
  children,
}: {
  title: React.ReactNode;
  /** Reveal the words on their own, for slots whose article is not the
      reveal target — the figure rows, where the article holds a background
      plate that must not fade in with the copy. */
  reveal?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="cf-copy" {...(reveal ? { "data-reveal": "" } : {})}>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}

export function FeaturesSection(): React.ReactElement {
  return (
    <section id="features" className="cf">
      <div className="pl-shell">
        <div className="pl-heading" data-reveal>
          <h2>
            Control the chart
            <br />
            <em>with words.</em>
          </h2>
        </div>

        {/* ── 01 · the anchor ─────────────────────────────────────────────
            Copy left, product right, and the product is the larger column: the
            claim is about what is IN the panel, so the panel has to be legible
            at a glance rather than illustrative. */}
        <article className="cf-story cf-wide cf-structure" data-reveal>
          <FeatureCopy title={<>Find the structure</>}>
            Patterns, levels, and key zones drawn automatically.
          </FeatureCopy>

          <div className="cf-visual">
            <FeatureDemo />
          </div>
        </article>

        {/* ── 02 · the first figure row ───────────────────────────────────
            The one claim in the section that a pane cannot make: it is about
            how much fits on screen AT ONCE, and a four-pane workspace shrunk
            into a landing card demonstrates the opposite. Told as drawings
            instead — the app's own 40-unit tiles — one per noun in the
            sentence above them. */}
        <article className="cf-story cf-wide cf-figs cf-plate cf-plate-orbit">
          <FeatureCopy reveal title={<>A complete trading workspace</>}>
            Indicators, layouts, fundamentals, and multi-chart views.
          </FeatureCopy>

          <FigureRow figures={DEMO_WORKSPACE_FIGURES} />
        </article>

        {/* ── 03 + 04 · the pair ──────────────────────────────────────────
            Grouped because they are one idea at two radii: widen the question
            past the interval you happen to be on, then past the symbol. Half
            the vertical weight of the two above, on purpose. */}
        <div className="cf-story cf-pair">
          <article data-reveal>
            <FeatureCopy title={<>See every timeframe</>}>
              Understand the higher and lower timeframe context in one view.
            </FeatureCopy>
            <div className="cf-visual">
              <ProductFrame
                head={
                  <>
                    <span className="cf-panel-title">Timeframes</span>
                    <span className="cf-head-note">6 rungs · 300 bars each</span>
                  </>
                }
              >
                <RungsPanel />
              </ProductFrame>
            </div>
          </article>

          <article data-reveal>
            <FeatureCopy title={<>Screen the whole market</>}>
              Find setups across your universe with technical conditions.
            </FeatureCopy>
            <div className="cf-visual">
              <ProductFrame
                flush
                head={
                  <>
                    <span className="cf-panel-title">Screener</span>
                    <span className="cf-head-note">{DEMO_SCREEN.asOf}</span>
                  </>
                }
              >
                <div className="cf-inset cf-ask">
                  <AskedLine>{DEMO_SCREEN.prompt}</AskedLine>
                </div>
                <div className="cf-inset">
                  <ScreenPanel />
                </div>
              </ProductFrame>
            </div>
          </article>
        </div>

        {/* ── 05 · the second figure row ──────────────────────────────────
            The claim is that three things sit in one place, so it is made by
            putting three of them in one row rather than by saying
            "integrated". Three panes said it too, but said it as three
            separate surfaces — which is the argument the feature is against. */}
        <article className="cf-story cf-figs">
          <FeatureCopy reveal title={<>Journal in context</>}>
            Keep trades, notes, and charts together.
          </FeatureCopy>

          <FigureRow figures={DEMO_JOURNAL_FIGURES} />
        </article>

        {/* ── 06 + 07 · the uneven pair ───────────────────────────────────
            Paired but not mirrored: a tile grid needs the wider half and an
            alert row does not, so the columns are 54/46 and the rhythm of the
            first pair does not repeat as a template. */}
        <div className="cf-story cf-pair uneven">
          <article data-reveal>
            <FeatureCopy title={<>Fundamentals, built in</>}>
              Research and screen without leaving the platform.
            </FeatureCopy>
            <div className="cf-visual">
              <ProductFrame
                head={
                  <>
                    <span className="cf-panel-title">Key Metrics</span>
                    <span className="cf-head-note">As of FY25</span>
                  </>
                }
              >
                <FundamentalsPanel />
              </ProductFrame>
            </div>
          </article>

          <article data-reveal>
            <FeatureCopy title={<>Alerts by conversation</>}>
              Describe what you want to watch. Charto handles the setup.
            </FeatureCopy>
            <div className="cf-visual">
              <ProductFrame
                flush
                head={
                  <>
                    <span className="cf-panel-title">Alerts</span>
                    <span className="cf-head-note">server-side · on bar close</span>
                  </>
                }
              >
                <div className="cf-inset cf-ask">
                  <AskedLine>{DEMO_ALERT.prompt}</AskedLine>
                </div>
                <div className="cf-inset">
                  <AlertPanel />
                  <p className="cf-reading">{DEMO_ALERT.reading}</p>
                </div>
              </ProductFrame>
            </div>
          </article>
        </div>

      </div>
    </section>
  );
}
