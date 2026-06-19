"use client";

/**
 * StrategyBuilderCard — the editable, register-not-execute basket card
 * rendered when the backend's build_strategy tool returns
 * `_render_hint: "strategy_builder_card"` (plan §3b).
 *
 * Shows:
 *   - the NAMED weighting scheme + the NAMED selection gate (anti-bland: the
 *     card "shows its work", never bare equal-weight / top-mcap alone)
 *   - the enforced sector cap
 *   - constituents + weights with per-constituent key fundamentals (gate_metrics)
 *   - the gold sleeve % (SGB / ETF instruments)
 *   - "(assumed …)" lines for every defaulted/skipped slot + honest-boundary
 *     fallbacks (e.g. covariance too thin → equal-weight)
 *   - a one-paragraph rationale tying back to {view × risk × horizon × capital}
 *   - the not-advice disclaimer
 *
 * Editable-card language mirrors WorkflowDraftCard (DS v2: rounded-3xl surface,
 * sky "Strategy" chip, soft amber disclaimer footer). Register-not-execute:
 * the card frames a basket the user confirms in their own broker app; nothing
 * here auto-executes. Edits are surfaced back as a chat amendment (the user
 * types "make it equal-weight", "drop ITC", etc.) — consistent with the rest of
 * Pivot's editable cards, no dedicated endpoint this phase.
 */

import { useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Coins,
  GitBranch,
  Info,
  Layers,
  PieChart,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  GoldInstrument,
  SelectionGate,
  Sleeve,
  StrategyAlternative,
  StrategyBuilderCard as StrategyBuilderCardData,
  StrategyConstituent,
  WeightingScheme,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type StrategyBuilderCardProps = {
  card: StrategyBuilderCardData;
};

// ---------------------------------------------------------------------------
// Label maps — humanize the closed enums (never leave raw snake_case on screen)
// ---------------------------------------------------------------------------

const SCHEME_LABEL: Record<WeightingScheme, string> = {
  equal: "Equal-weight",
  mcap: "Market-cap weighted",
  risk_parity: "Risk-parity (ERC)",
  min_variance: "Minimum-variance",
  black_litterman: "Black-Litterman",
  factor: "Factor-weighted",
};

const GATE_LABEL: Record<SelectionGate, string> = {
  fscore: "Piotroski F-score",
  magic_formula: "Magic Formula",
  multifactor: "Multi-factor score",
  none: "Price / technical (no fundamental gate)",
};

/** Pretty-print a gate_metrics key: "earnings_yield" → "Earnings yield". */
function humanizeMetricKey(k: string): string {
  const upper = new Set(["pe", "pb", "roe", "roce", "de", "roic", "fscore", "pcr", "iv"]);
  if (upper.has(k.toLowerCase())) return k.toUpperCase();
  const words = k.split("_");
  return words
    .map((w, i) =>
      i === 0 ? (w.length > 0 ? w[0]!.toUpperCase() + w.slice(1) : w) : w,
    )
    .join(" ");
}

/** Format a fundamental value compactly — ratios to 1-2dp, yields as %. */
function fmtMetric(key: string, v: number): string {
  const k = key.toLowerCase();
  if (k.includes("yield") || k.includes("payout") || k.includes("margin")) {
    // Heuristic: a fraction < 1.5 reads as a ratio → show as %.
    if (Math.abs(v) <= 1.5) return `${(v * 100).toFixed(1)}%`;
    return `${v.toFixed(1)}%`;
  }
  if (k === "fscore") return v.toFixed(0);
  if (Number.isInteger(v)) return v.toString();
  return v.toFixed(Math.abs(v) >= 100 ? 0 : 1);
}

function fmtPct(v: number): string {
  return `${v.toFixed(v >= 10 || Number.isInteger(v) ? 0 : 1)}%`;
}

// ---------------------------------------------------------------------------
// Gold sleeve instrument kind labels
// ---------------------------------------------------------------------------

function goldKindLabel(k: GoldInstrument["kind"]): string {
  return k === "sgb" ? "SGB" : "Gold ETF";
}

// ---------------------------------------------------------------------------
// Constituent row
// ---------------------------------------------------------------------------

function ConstituentRow({
  c,
  index,
}: {
  c: StrategyConstituent;
  index: number;
}): React.ReactElement {
  const metricEntries = Object.entries(c.gate_metrics ?? {});
  return (
    <li
      className="flex flex-col gap-1.5 rounded-xl border border-border/50 bg-card px-3 py-2.5 transition-colors hover:border-border"
      style={{
        animation: `stepIn-quartr 320ms cubic-bezier(0.22, 1, 0.36, 1) both`,
        animationDelay: `${index * 45}ms`,
        listStyle: "none",
      }}
    >
      <div className="flex items-center gap-2">
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-[12.5px] font-semibold tracking-tight text-foreground">
            {c.symbol}
          </span>
          <span className="truncate text-[10.5px] text-muted-foreground">
            {c.name} · {c.sector}
          </span>
        </div>
        <span className="shrink-0 rounded-md bg-muted/70 px-2 py-0.5 text-[12px] font-semibold tabular-nums text-foreground">
          {fmtPct(c.weight_pct)}
        </span>
      </div>
      {/* Weight bar */}
      <div className="h-1 w-full overflow-hidden rounded-full bg-muted/60">
        <div
          className="h-full rounded-full bg-sky-500/70 dark:bg-sky-400/70"
          style={{ width: `${Math.min(100, Math.max(0, c.weight_pct))}%` }}
        />
      </div>
      {/* Gate metrics — the fundamentals that earned the slot */}
      {metricEntries.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5">
          {metricEntries.map(([k, v]) => (
            <span
              key={k}
              className="text-[10px] tabular-nums text-muted-foreground"
            >
              <span className="text-muted-foreground/70">{humanizeMetricKey(k)}</span>{" "}
              <span className="font-medium text-foreground/85">{fmtMetric(k, v)}</span>
            </span>
          ))}
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Sleeve row (gold this phase; options/hedge reserved)
// ---------------------------------------------------------------------------

function SleeveBlock({ sleeve }: { sleeve: Sleeve }): React.ReactElement {
  const isGold = sleeve.kind === "gold";
  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-amber-200/60 bg-amber-50/40 px-3 py-2.5 dark:border-amber-500/20 dark:bg-amber-500/[0.05]">
      <div className="flex items-center gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
          {isGold ? (
            <Coins className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <Layers className="h-3.5 w-3.5" aria-hidden="true" />
          )}
        </span>
        <span className="flex-1 text-[12px] font-semibold capitalize tracking-tight text-foreground">
          {sleeve.kind} sleeve
        </span>
        <span className="shrink-0 rounded-md bg-amber-100 px-2 py-0.5 text-[12px] font-semibold tabular-nums text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
          {fmtPct(sleeve.pct)}
        </span>
      </div>
      {sleeve.note && (
        <p className="text-[10.5px] leading-snug text-muted-foreground">{sleeve.note}</p>
      )}
      {sleeve.instruments.length > 0 && (
        <ul className="m-0 flex flex-col gap-1 pl-0.5">
          {sleeve.instruments.map((inst) => (
            <li
              key={inst.symbol}
              className="flex items-center gap-2 text-[11px]"
              style={{ listStyle: "none" }}
            >
              <span className="inline-flex items-center rounded bg-amber-100/80 px-1.5 py-px text-[9.5px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
                {goldKindLabel(inst.kind)}
              </span>
              <span className="min-w-0 flex-1 truncate text-foreground/85">
                {inst.symbol} · {inst.name}
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {fmtPct(inst.weight_pct)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Alternatives block — "you might prefer this instead" pivots
//
// Mirrors the option-card ALTERNATIVES section (subtle, set off by a top
// border + uppercase eyebrow), but rendered as readable explained text rather
// than chips: each alternative is a titled line with its 1-2 sentence "when
// you'd choose this" detail beneath. Suggestions only — nothing here is
// selectable or registered until the user re-asks for one.
// ---------------------------------------------------------------------------

function AlternativeItem({
  alt,
  index,
}: {
  alt: StrategyAlternative;
  index: number;
}): React.ReactElement {
  return (
    <li
      className="flex gap-2.5"
      style={{
        listStyle: "none",
        animation: "stepIn-quartr 320ms cubic-bezier(0.22, 1, 0.36, 1) both",
        animationDelay: `${index * 55}ms`,
      }}
    >
      <span
        aria-hidden="true"
        className="mt-[3px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300"
      >
        <GitBranch className="h-2.5 w-2.5" />
      </span>
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-[12px] font-semibold leading-snug tracking-tight text-foreground">
          {alt.title}
        </span>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {alt.detail}
        </p>
      </div>
    </li>
  );
}

function AlternativesBlock({
  alternatives,
}: {
  alternatives: StrategyAlternative[];
}): React.ReactElement {
  return (
    <div className="border-t border-border/30 px-5 py-3" data-testid="strategy-alternatives">
      <p className="mb-2.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/70">
        <GitBranch className="h-3 w-3 text-muted-foreground/60" aria-hidden="true" />
        Alternative strategies
      </p>
      <ul className="m-0 flex flex-col gap-2.5">
        {alternatives.map((alt, i) => (
          <AlternativeItem key={`${alt.title}-${i}`} alt={alt} index={i} />
        ))}
      </ul>
      <p className="mt-2.5 text-[10px] leading-snug text-muted-foreground/70">
        Suggestions only — reply with one (e.g. &ldquo;{alternatives[0]?.title ?? "the value tilt"}
        &rdquo;) to rebuild the basket that way.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// StrategyBuilderCard
// ---------------------------------------------------------------------------

export function StrategyBuilderCard({
  card,
}: StrategyBuilderCardProps): React.ReactElement {
  const [showWhy, setShowWhy] = useState(false);

  const schemeLabel = SCHEME_LABEL[card.weighting_scheme] ?? card.weighting_scheme;
  const gateLabel = GATE_LABEL[card.selection_gate] ?? card.selection_gate;

  return (
    <div
      data-testid="strategy-builder-card"
      role="region"
      aria-label={`Strategy basket: ${card.title}`}
      className={cn(
        "my-2 w-full max-w-[440px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
      )}
      style={{
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      {/* HEADER */}
      <div className="flex flex-col gap-2.5 px-5 pt-4 pb-3">
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center gap-1 rounded-md bg-sky-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
            <PieChart className="h-3 w-3" aria-hidden="true" />
            Strategy basket
          </span>
          <span className="inline-flex items-center gap-1.5 text-[10.5px] font-medium text-muted-foreground">
            <span
              aria-hidden="true"
              className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50"
            />
            Draft
          </span>
        </div>

        <h3 className="text-[15px] leading-[1.25] font-semibold tracking-tight text-foreground">
          {card.title}
        </h3>

        {/* SCHEME + GATE — the named decisions (anti-bland) */}
        <div className="flex flex-wrap gap-1.5">
          <span
            className="inline-flex items-center gap-1 rounded-md border border-border/50 bg-muted/40 px-2 py-0.5 text-[10.5px] font-medium text-foreground/85"
            data-testid="strategy-scheme"
          >
            <Layers className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
            {schemeLabel}
          </span>
          <span
            className="inline-flex items-center gap-1 rounded-md border border-border/50 bg-muted/40 px-2 py-0.5 text-[10.5px] font-medium text-foreground/85"
            data-testid="strategy-gate"
          >
            <Sparkles className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
            Gate: {gateLabel}
          </span>
          <span className="inline-flex items-center gap-1 rounded-md border border-border/50 bg-muted/40 px-2 py-0.5 text-[10.5px] font-medium text-foreground/85">
            Sector cap {fmtPct(card.sector_cap)}
          </span>
        </div>

        {/* RATIONALE — collapsible "Why this?" like WorkflowDraftCard */}
        {card.rationale && (
          <div className="flex flex-col gap-1.5">
            <button
              type="button"
              onClick={() => setShowWhy((v) => !v)}
              className="inline-flex w-fit items-center gap-1 text-[11px] font-medium text-muted-foreground/80 transition-colors hover:text-foreground"
              aria-expanded={showWhy}
              data-testid="strategy-why-toggle"
            >
              {showWhy ? (
                <ChevronUp className="h-3 w-3 shrink-0" aria-hidden="true" />
              ) : (
                <ChevronDown className="h-3 w-3 shrink-0" aria-hidden="true" />
              )}
              {showWhy ? "Hide reasoning" : "Why this structure?"}
            </button>
            {showWhy && (
              <p
                className="rounded-xl bg-muted/60 px-3 py-2 text-[11.5px] leading-relaxed text-muted-foreground"
                style={{
                  animation:
                    "draftCardIn-quartr 240ms cubic-bezier(0.22, 1, 0.36, 1) both",
                }}
                data-testid="strategy-rationale"
              >
                {card.rationale}
              </p>
            )}
          </div>
        )}
      </div>

      {/* CONSTITUENTS */}
      {card.constituents.length > 0 && (
        <div className="flex flex-col gap-2 px-5 pb-3">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">
            Equity constituents
          </p>
          <ol className="m-0 flex flex-col gap-1.5">
            {card.constituents.map((c, i) => (
              <ConstituentRow key={c.symbol} c={c} index={i} />
            ))}
          </ol>
        </div>
      )}

      {/* SLEEVES — gold this phase */}
      {card.sleeves.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-border/30 px-5 py-3">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">
            Sleeves
          </p>
          <div className="flex flex-col gap-2">
            {card.sleeves.map((s, i) => (
              <SleeveBlock key={`${s.kind}-${i}`} sleeve={s} />
            ))}
          </div>
        </div>
      )}

      {/* ASSUMPTIONS — "(assumed …)" lines */}
      {card.assumptions.length > 0 && (
        <div className="border-t border-border/30 px-5 py-3">
          <p className="mb-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/70">
            Assumptions
          </p>
          <ul className="m-0 flex flex-col gap-1" data-testid="strategy-assumptions">
            {card.assumptions.map((a, i) => (
              <li key={i} className="flex items-start gap-1.5" style={{ listStyle: "none" }}>
                <Info
                  className="mt-px h-3 w-3 shrink-0 text-muted-foreground/60"
                  aria-hidden="true"
                />
                <span className="text-[11px] leading-snug text-muted-foreground">{a}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ALTERNATIVES — "you might prefer this instead" pivots, as explained text */}
      {card.alternatives.length > 0 && (
        <AlternativesBlock alternatives={card.alternatives} />
      )}

      {/* EDIT HINT — register-not-execute, amend-via-chat language */}
      <div className="border-t border-border/30 px-5 py-2.5">
        <p className="text-[10.5px] leading-snug text-muted-foreground">
          This is a draft you can edit — reply to re-weight, swap a name, change
          the scheme, or resize the gold sleeve. Pivot registers the idea; you
          confirm and place orders in your own broker app.
        </p>
      </div>

      {/* DISCLAIMER — not-advice footer */}
      <div className="flex items-start gap-1.5 border-t border-border/40 bg-amber-50/40 px-5 py-2 dark:bg-amber-500/[0.04]">
        <ShieldAlert
          className="mt-px h-3 w-3 shrink-0 text-amber-600/80 dark:text-amber-400/80"
          aria-hidden="true"
        />
        <p className="text-[10.5px] leading-snug text-amber-700/90 dark:text-amber-300/90">
          {card.disclaimer}
        </p>
      </div>
    </div>
  );
}
