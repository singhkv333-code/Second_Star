/**
 * view-format.ts — pure, dependency-free formatting helpers for the Views tab.
 *
 * All color helpers return CSS var() strings so they respond correctly to
 * light/dark theme switches without reading the DOM. Never return raw hex
 * here — that belongs in `use-token-color.ts` (the recharts color hook).
 *
 * Conventions:
 *  - gradeColor / dialColor: returns a CSS var string usable in inline style
 *    color / fill / stroke.
 *  - verdictTone: returns a semantic tone descriptor used by MonoTag and the
 *    verdict chip; callers map tone → CSS var via the DS token table.
 *  - Numeric formatters: return plain strings; callers wrap in <Figure> for
 *    the JetBrains Mono / tabular-nums treatment.
 */

import type { Dial, ExpressionTier, Grade, TrustVerdict } from "@/lib/types";

// ---------------------------------------------------------------------------
// Grade → color (CSS var string)
// A / A-  → profit green
// B / B-  → pivot blue
// C       → amber warn
// D / F   → loss red
// null    → tertiary text (muted)
// ---------------------------------------------------------------------------

export function gradeColor(grade: Grade | string | null | undefined): string {
  if (!grade) return "var(--text-tertiary)";
  switch (grade) {
    case "A":
    case "A-":
      return "var(--color-profit)";
    case "B":
    case "B-":
      return "var(--pivot-blue)";
    case "C":
      return "var(--color-warn)";
    case "D":
    case "F":
      return "var(--color-loss)";
    default:
      return "var(--text-tertiary)";
  }
}

// ---------------------------------------------------------------------------
// Dial → color
// Delegates to gradeColor for letter values; SUPPRESSED → tertiary.
// ---------------------------------------------------------------------------

export function dialColor(dial: Dial | string | null | undefined): string {
  if (!dial || dial === "SUPPRESSED") return "var(--text-tertiary)";
  return gradeColor(dial as Grade);
}

// ---------------------------------------------------------------------------
// Score (0..100) → letter band  (mirrors backend confidence.letter_band)
// A>=85, B>=70, C>=55, D>=40, else F
// ---------------------------------------------------------------------------

export function letterBand(score: number): Grade {
  if (score >= 85) return "A";
  if (score >= 70) return "B";
  if (score >= 55) return "C";
  if (score >= 40) return "D";
  return "F";
}

/** Convert a dial letter to a Dial value (pass-through — just typed). */
export function letterToDial(letter: string | null | undefined): Dial | null {
  if (!letter) return null;
  if (letter === "SUPPRESSED") return "SUPPRESSED";
  return letter as Grade;
}

// ---------------------------------------------------------------------------
// Trust verdict → semantic tone descriptor
// Callers map: "profit" | "warn" | "muted"
// The MonoTag / chip renders these using the DS token for that tone.
// ---------------------------------------------------------------------------

export type VerdictTone = "profit" | "warn" | "muted";

export function verdictTone(
  verdict: TrustVerdict | string | null | undefined,
): VerdictTone {
  switch ((verdict ?? "").toUpperCase()) {
    case "PROMISING":
      return "profit";
    case "UNPROVEN":
      return "warn";
    case "NO_EDGE":
    case "INSUFFICIENT_DATA":
    default:
      return "muted";
  }
}

/** The CSS color var that corresponds to a verdict tone (for inline use). */
export function verdictColor(
  verdict: TrustVerdict | string | null | undefined,
): string {
  switch (verdictTone(verdict)) {
    case "profit":
      return "var(--color-profit)";
    case "warn":
      return "var(--color-warn)";
    default:
      return "var(--text-tertiary)";
  }
}

// ---------------------------------------------------------------------------
// Sign → semantic color. Positive = profit green, negative = loss red,
// zero/null = primary ink. For returns / excess / per-window figures.
// ---------------------------------------------------------------------------

export function signColor(
  value: number | null | undefined,
  neutral = "var(--text-primary)",
): string {
  if (value === null || value === undefined) return "var(--text-tertiary)";
  if (value > 0) return "var(--color-profit)";
  if (value < 0) return "var(--color-loss)";
  return neutral;
}

/** Risk metrics (drawdown, prob-loss) are always rendered in a risk hue.
 *  `kind="dd"` → amber warn, `kind="loss"` → loss red. */
export function riskColor(kind: "dd" | "loss" | "neutral"): string {
  switch (kind) {
    case "dd":
      return "var(--color-warn)";
    case "loss":
      return "var(--color-loss)";
    default:
      return "var(--text-primary)";
  }
}

// ---------------------------------------------------------------------------
// Trust ladder — the 4-step verdict stepper ordering + helpers.
// ---------------------------------------------------------------------------

export const TRUST_STEPS: TrustVerdict[] = [
  "INSUFFICIENT_DATA",
  "NO_EDGE",
  "UNPROVEN",
  "PROMISING",
];

/** Short uppercase label for a trust verdict (for the stepper chip). */
export function verdictLabel(
  verdict: TrustVerdict | string | null | undefined,
): string {
  switch ((verdict ?? "").toUpperCase()) {
    case "INSUFFICIENT_DATA":
      return "Insufficient data";
    case "NO_EDGE":
      return "No edge";
    case "UNPROVEN":
      return "Unproven";
    case "PROMISING":
      return "Promising";
    default:
      return "Not evaluated";
  }
}

/** Index (0..3) of a verdict in TRUST_STEPS, or -1 when unknown/null. */
export function verdictStepIndex(
  verdict: TrustVerdict | string | null | undefined,
): number {
  if (!verdict) return -1;
  return TRUST_STEPS.indexOf(verdict.toUpperCase() as TrustVerdict);
}

// ---------------------------------------------------------------------------
// isSuppressed — true when the dial must render as "—" + tooltip
// ---------------------------------------------------------------------------

export function isSuppressed(dial: Dial | string | null | undefined): boolean {
  return dial === "SUPPRESSED" || dial === null || dial === undefined;
}

// ---------------------------------------------------------------------------
// Numeric formatters (return strings; wrap in <Figure> at call site)
// ---------------------------------------------------------------------------

/**
 * Format a percentage with an explicit sign.
 * fmtPct(14.2)    → "+14.2%"
 * fmtPct(-6.0)    → "−6.0%"   (uses proper minus sign U+2212)
 * fmtPct(null)    → "—"
 */
export function fmtPct(
  value: number | null | undefined,
  dp = 1,
): string {
  if (value === null || value === undefined) return "—";
  const sign = value >= 0 ? "+" : "−";
  return `${sign}${Math.abs(value).toFixed(dp)}%`;
}

/**
 * Format an INR value using Intl en-IN (lakhs / crores).
 * fmtInr(1200000) → "₹12L"
 * fmtInr(null)    → "—"
 */
export function fmtInr(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

/**
 * Format an INR value with full digits (no compact suffix) — for tooltips
 * and detailed fields.
 */
export function fmtInrFull(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(value);
}

/**
 * Format a decimal ratio (PSR, DSR, etc.) to 2 dp.
 * fmtRatio(0.71)  → "0.71"
 * fmtRatio(null)  → "—"
 */
export function fmtRatio(value: number | null | undefined, dp = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(dp);
}

/**
 * Format a score (0..100) as an integer string.
 * fmtScore(72)  → "72"
 * fmtScore(null)→ "—"
 */
export function fmtScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Math.round(value).toString();
}

/**
 * Format a transmission edge strength (0..1) to 2 dp.
 * fmtStrength(0.8) → "0.80"
 */
export function fmtStrength(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(2);
}

/**
 * Format an ISO date string using the en-IN locale (day + short month).
 * fmtDate("2026-06-18T00:00:00Z") → "18 Jun"
 */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-IN", {
      day: "numeric",
      month: "short",
    }).format(new Date(iso));
  } catch {
    return "—";
  }
}

// ---------------------------------------------------------------------------
// Label helpers
// ---------------------------------------------------------------------------

/** Human-readable tier label with title casing. */
export function tierLabel(tier: ExpressionTier | string | null | undefined): string {
  if (!tier) return "";
  switch (tier) {
    case "conservative":
      return "Conservative";
    case "balanced":
      return "Balanced";
    case "aggressive":
      return "Aggressive";
    default:
      return tier.charAt(0).toUpperCase() + tier.slice(1);
  }
}

/**
 * Humanize an expression_kind slug.
 * "option_strategy"    → "Option strategy"
 * "basket"             → "Basket"
 * "relative_pair"      → "Relative pair"
 */
export function kindLabel(kind: string | null | undefined): string {
  if (!kind) return "";
  return kind
    .replace(/_/g, " ")
    .replace(/^./, (c) => c.toUpperCase());
}

// ===========================================================================
// Humanizers — NO raw enum / slug / jargon token may ever reach the screen.
// Every helper here returns a humanized fallback for unknown input, never the
// raw token. Route EVERY on-screen label through one of these.
// ===========================================================================

/**
 * View type → display word.
 * "event" → "Event", "theme" → "Theme", "relative" → "Relative"
 * (case-insensitive; backend ViewType is uppercase "EVENT"/"THEME").
 */
export function viewTypeLabel(t: string | null | undefined): string {
  switch ((t ?? "").toLowerCase()) {
    case "event":
      return "Event";
    case "theme":
      return "Theme";
    case "relative":
      return "Relative";
    default:
      if (!t) return "View";
      // Humanized fallback — never echo a raw token.
      return t.charAt(0).toUpperCase() + t.slice(1).toLowerCase();
  }
}

/**
 * Category slug → Title Case words, with a few curated overrides.
 * "macro_commodity"  → "Macro · Commodity"
 * "equity_rotation"  → "Equity rotation"
 * "seasonal_macro"   → "Seasonal"
 */
export function categoryLabel(c: string | null | undefined): string {
  if (!c) return "General";
  const trimmed = c.trim();
  // Already display-formatted (e.g. "AI · Theme", "Geopolitics · Event") — the
  // backend/pack sends these pre-cased. Trust them; lowercasing would mangle
  // acronyms ("AI" → "Ai") and drop the intended capitalisation.
  if (trimmed.includes("·")) return trimmed;
  const slug = trimmed.toLowerCase();
  const OVERRIDES: Record<string, string> = {
    macro_commodity: "Macro · Commodity",
    equity_rotation: "Equity rotation",
    seasonal_macro: "Seasonal",
  };
  if (OVERRIDES[slug]) return OVERRIDES[slug];
  // Generic humanized fallback: first word capitalized, rest lower.
  const words = slug.replace(/[_-]+/g, " ").trim().split(/\s+/);
  if (words.length === 0 || words[0] === "") return "General";
  return words
    .map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/**
 * Category → its leading THEME word — the token before the "·" separator, in
 * its original casing. "AI · Theme" → "AI", "Energy · Theme" → "Energy",
 * "Index · Price target" → "Index". This is the Polymarket-style category chip
 * label (a broad bucket, not the full compound category). Empty input → "".
 */
export function categoryLead(c: string | null | undefined): string {
  const head = (c ?? "").split("·")[0]?.trim() ?? "";
  return head.split(/\s+/)[0] ?? "";
}

/**
 * Lifecycle status → display word.
 * draft → "Developing", published → "Open" (both map sensibly).
 */
export function statusLabel(s: string | null | undefined): string {
  switch ((s ?? "").toLowerCase()) {
    case "open":
      return "Open";
    case "developing":
    case "draft":
      return "Developing";
    case "consensus":
      return "Consensus";
    case "published":
      return "Open";
    case "resolved":
      return "Resolved";
    case "archived":
      return "Archived";
    default:
      return "Developing";
  }
}

/** Status → the lifecycle dot color (CSS var string). */
export function statusDotColor(s: string | null | undefined): string {
  switch ((s ?? "").toLowerCase()) {
    case "open":
    case "published":
      return "var(--color-profit)";
    case "developing":
    case "draft":
      return "var(--pivot-blue)";
    case "consensus":
      return "var(--color-warn)";
    case "resolved":
      return "var(--text-secondary)";
    case "archived":
      return "var(--text-tertiary)";
    default:
      return "var(--text-tertiary)";
  }
}

/**
 * Trust verdict → plain badge text (the de-jargoned trust word shown on cards).
 * insufficient_data → "Not enough data", no_edge → "No edge yet",
 * unproven → "Unproven", promising → "Promising".
 */
export function trustBadge(
  verdict: TrustVerdict | string | null | undefined,
): string {
  switch ((verdict ?? "").toLowerCase()) {
    case "insufficient_data":
      return "Not enough data";
    case "no_edge":
      return "No edge yet";
    case "unproven":
      return "Unproven";
    case "promising":
      return "Promising";
    default:
      return "Not enough data";
  }
}

/**
 * Transmission strength (0..1) → plain link word.
 * <0.34 → "weak link", <0.67 → "moderate link", else "strong link".
 * null/undefined → "moderate link" (neutral fallback, never a raw number).
 */
export function strengthLabel(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "moderate link";
  }
  if (value < 0.34) return "weak link";
  if (value < 0.67) return "moderate link";
  return "strong link";
}

/**
 * Expectation source → plain label (CLOSED map). Anything unknown maps to the
 * honest fallback "Market estimate" — a raw source slug never reaches screen.
 */
export function sourceLabel(src: string | null | undefined): string {
  switch ((src ?? "").toLowerCase()) {
    case "option_implied":
    case "options":
    case "option_chain":
      return "Options market";
    case "polymarket":
      return "Polymarket";
    case "kalshi":
      return "Kalshi";
    case "prediction_market":
      return "Prediction market";
    case "analyst":
    case "consensus":
      return "Analyst consensus";
    case "macro_calendar":
    case "calendar":
      return "Economic calendar";
    default:
      return "Market estimate";
  }
}

/**
 * Win-rate sentence from the episode counts.
 * winRateLabel(75, 4) → "Beat Nifty 3 of 4 times"
 * (pct is the % of episodes that beat the benchmark; we derive the count.)
 * Missing data → "Not enough history yet".
 */
export function winRateLabel(
  pctBeat: number | null | undefined,
  nEpisodes: number | null | undefined,
  benchmark = "Nifty",
): string {
  if (
    pctBeat === null ||
    pctBeat === undefined ||
    nEpisodes === null ||
    nEpisodes === undefined ||
    nEpisodes <= 0
  ) {
    return "Not enough history yet";
  }
  const wins = Math.round((pctBeat / 100) * nEpisodes);
  return `Beat ${benchmark} ${wins} of ${nEpisodes} times`;
}

/**
 * Positive-outcome sentence from the episode counts — the benchmark-free
 * replacement for winRateLabel(). Never mentions Nifty/beating a benchmark;
 * counts occurrences where the strategy's OWN return was positive.
 * positiveHitLabel(75, 32) → "Positive in 24 of 32 occurrences"
 * Missing data → "Not enough history yet".
 */
export function positiveHitLabel(
  pctPositive: number | null | undefined,
  nEpisodes: number | null | undefined,
): string {
  if (
    pctPositive === null ||
    pctPositive === undefined ||
    nEpisodes === null ||
    nEpisodes === undefined ||
    nEpisodes <= 0
  ) {
    return "Not enough history yet";
  }
  const wins = Math.round((pctPositive / 100) * nEpisodes);
  return `Positive in ${wins} of ${nEpisodes} occurrences`;
}

/**
 * Growth-of-investment helper for the "₹1,00,000 → ₹X" story.
 * growthOfInvestment(45.55) → { base: 100000, final: 145550,
 *   label: "₹1,00,000 → ₹1,45,550" }
 * pct null → final null, label uses an em dash for the final.
 */
export function growthOfInvestment(
  pct: number | null | undefined,
  base = 100000,
): { base: number; final: number | null; label: string } {
  const grp = (n: number) =>
    new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 0,
    }).format(n);
  if (pct === null || pct === undefined || Number.isNaN(pct)) {
    return { base, final: null, label: `${grp(base)} → —` };
  }
  const final = Math.round(base * (1 + pct / 100));
  return { base, final, label: `${grp(base)} → ${grp(final)}` };
}

/**
 * Capital-intensity passthrough — accepts the already-plain backend
 * `capital_label` ("Low" / "Low-medium" / "Medium") and guarantees a clean
 * word out (never a raw enum, never a rupee figure).
 */
export function capitalLabel(c: string | null | undefined): string {
  if (!c) return "—";
  const slug = c.trim().toLowerCase();
  const MAP: Record<string, string> = {
    low: "Low",
    "low-medium": "Low-medium",
    low_medium: "Low-medium",
    medium: "Medium",
    "medium-high": "Medium-high",
    medium_high: "Medium-high",
    high: "High",
  };
  if (MAP[slug]) return MAP[slug];
  // Humanized fallback for any other plain word the backend sends.
  return c.charAt(0).toUpperCase() + c.slice(1).toLowerCase();
}
