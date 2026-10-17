/**
 * Words, not numbers. Everything a person reads about a plan or a feature
 * that is not a value from the catalog lives here, so a copy edit never
 * touches a limit and a limit change never needs a copy edit.
 *
 * Copy rules (CLAUDE.md §4): calm, specific, no urgency, no emoji, and never
 * a capability the product does not have. A feature the catalog marks
 * `pending` is shown as "Coming soon" or not at all, never as a perk.
 */

import { fmtValue } from "./format";
import type { CatalogFeature, FeatureValue, PlanId, PublicCatalog } from "./types";

export type PlanCopy = {
  tagline: string;
  /** Who it is for, one line, used in the recommendation label. */
  forWho: string;
  /** Catalog keys the card leads with, in order. */
  highlights: string[];
};

export const PLAN_COPY: Record<Exclude<PlanId, "anonymous">, PlanCopy> = {
  free: {
    tagline: "Chart, screen and ask Pivot about the market, on the house.",
    forWho: "For learning the platform",
    highlights: ["ai.credits", "chart.indicators", "chart.panes", "alerts.price", "alerts.technical", "screens.saved"],
  },
  pro: {
    tagline: "More questions, more alerts and a larger desk for the active trader.",
    forWho: "For daily traders and researchers",
    highlights: ["ai.credits", "chart.indicators", "chart.panes", "alerts.price", "alerts.technical", "alerts.expiry_days"],
  },
  pro_plus: {
    tagline: "The highest limits across AI, alerts and parallel charts.",
    forWho: "For running many setups at once",
    highlights: ["ai.credits", "chart.indicators", "chart.parallel", "alerts.price", "alerts.technical", "alerts.expiry_days"],
  },
};

/** The plan the pricing page marks as recommended. */
export const RECOMMENDED: PlanId = "pro";

/**
 * Keys the pricing surfaces leave out entirely. `ads.free`: Pivot shows no
 * ads, so "ad-free" would sell the absence of something that is not there.
 */
export const HIDDEN_FEATURES = new Set(["ads.free"]);

/**
 * `pending` in the catalog means EITHER "no product surface yet" OR "the
 * number is unsettled". These keys are the second kind: live and enforced,
 * so they are never labelled "Coming soon".
 */
export const LIVE_DESPITE_PENDING = new Set(["chart.history_bars"]);

/** Unbuilt: show as "Coming soon", never as a live perk, never as a loss. */
export function isUnbuilt(key: string, f: CatalogFeature | undefined): boolean {
  return !!f?.pending && !LIVE_DESPITE_PENDING.has(key);
}

export type FeatureGroup = { title: string; keys: string[] };

export const FEATURE_GROUPS: FeatureGroup[] = [
  { title: "AI analyst", keys: ["ai.credits", "ai.summaries"] },
  {
    title: "Charting",
    keys: ["chart.indicators", "chart.panes", "chart.parallel", "chart.history_bars", "chart.custom_timeframes"],
  },
  {
    title: "Alerts",
    keys: [
      "alerts.price",
      "alerts.technical",
      "alerts.multi_condition",
      "alerts.expiry_days",
      "alerts.watchlist",
      "alerts.screen",
      "alerts.fundamental",
    ],
  },
  { title: "Screening & watchlists", keys: ["screens.saved", "watchlists"] },
];

/** Notes under a comparison row where the label alone would mislead. */
export const FEATURE_NOTES: Record<string, string> = {
  "ai.credits": "One credit is one question you send. Follow-up suggestions and failed answers are free.",
  "chart.history_bars": "Intraday charts only. Daily and longer charts show full history on every plan.",
  "chart.parallel": "Chart tabs open at the same time. A grid layout in one tab counts once.",
  "alerts.technical": "Indicator, drawing, channel and percentage-move alerts.",
};

/** What a contextual paywall says about the feature that triggered it. */
export type FeatureCopy = {
  title: string;
  /** The outcome, not the mechanism. One or two sentences. */
  benefit: string;
  /** Short noun for "N more ___". */
  noun: string;
};

export const FEATURE_COPY: Record<string, FeatureCopy> = {
  "ai.credits": {
    title: "Keep asking Pivot",
    benefit:
      "Each credit is one question: a chart drawn with evidence you can check, a strategy built and backtested, or a filing read for you.",
    noun: "AI credits",
  },
  "ai.summaries": {
    title: "Summaries of financial documents",
    benefit: "Read the substance of an annual report or results filing in minutes.",
    noun: "AI summaries",
  },
  "alerts.price": {
    title: "Watch more levels",
    benefit: "Set price alerts across more instruments, so the move finds you instead of the other way round.",
    noun: "price alerts",
  },
  "alerts.technical": {
    title: "More technical alerts",
    benefit: "Alert on indicators, drawings, channels and percentage moves across a wider watchlist.",
    noun: "technical alerts",
  },
  "alerts.multi_condition": {
    title: "Multi-condition alerts",
    benefit: "Fire only when every condition you care about lines up, not on the first one.",
    noun: "multi-condition alerts",
  },
  "alerts.expiry_days": {
    title: "Alerts that last longer",
    benefit: "Keep long-horizon alerts armed for months, or indefinitely, without re-creating them.",
    noun: "alert lifetime",
  },
  "chart.indicators": {
    title: "More indicators on one chart",
    benefit: "Layer trend, momentum and volume studies together instead of switching between them.",
    noun: "indicators per chart",
  },
  "chart.panes": {
    title: "A larger chart grid",
    benefit: "Watch more synchronised charts side by side in a single tab.",
    noun: "charts per tab",
  },
  "chart.parallel": {
    title: "More charts open at once",
    benefit: "Keep more chart tabs live at the same time, each with its own feed.",
    noun: "parallel charts",
  },
  "chart.history_bars": {
    title: "Deeper intraday history",
    benefit: "Scroll and backtest further back on minute and hourly charts.",
    noun: "intraday bars",
  },
  "chart.custom_timeframes": {
    title: "Custom timeframes",
    benefit: "Chart any interval, not only the preset ones.",
    noun: "custom timeframes",
  },
  "screens.saved": {
    title: "Save more screens",
    benefit: "Keep more screener setups ready to rerun in one click.",
    noun: "saved screens",
  },
  "alerts.screen": {
    title: "Screener alerts",
    benefit: "Be told when a stock enters one of your screens.",
    noun: "screener alerts",
  },
  "alerts.watchlist": {
    title: "Watchlist alerts",
    benefit: "One alert across a whole watchlist.",
    noun: "watchlist alerts",
  },
};

export function featureCopy(cat: PublicCatalog, key: string): FeatureCopy {
  const label = cat.features[key]?.label ?? key;
  return FEATURE_COPY[key] ?? { title: label, benefit: "", noun: label.toLowerCase() };
}

/** "200 AI credits a month", "Unlimited indicators per chart", "Alerts never expire". */
export function featureLine(key: string, f: CatalogFeature, v: FeatureValue): string {
  const label = f.label;
  const lower = /^.[A-Z]/.test(label) ? label : label.charAt(0).toLowerCase() + label.slice(1);
  if (f.kind === "flag") return label;
  if (key === "alerts.expiry_days") return v === null ? "Alerts never expire" : `Alerts last ${fmtValue(key, f, v)}`;
  if (key === "chart.history_bars") return v === null ? "Full intraday history" : `${fmtValue(key, f, v)} of intraday history`;
  if (f.kind === "quota") return v === null ? `Unlimited ${lower}` : `${fmtValue(key, f, v).replace(" / ", ` ${lower} a `)}`;
  if (v === null) return `Unlimited ${lower}`;
  return `${fmtValue(key, f, v)} ${lower}`;
}

/** Whether a row is worth showing on pricing at all. */
export function isShown(cat: PublicCatalog, key: string): boolean {
  if (HIDDEN_FEATURES.has(key)) return false;
  // A feature every public plan has at zero / off is a promise of nothing.
  return cat.plans.some((p) => {
    const v = p.features[key];
    return v === null || v === true || (typeof v === "number" && v > 0);
  });
}
