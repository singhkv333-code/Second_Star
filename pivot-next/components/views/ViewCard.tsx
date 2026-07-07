"use client";

/**
 * ViewCard — one belief-market "prediction card" for a ViewSummary, a faithful
 * port of the reference mockup:
 *
 *   ┌────────────────────────────────────────────────┐
 *   │ ┌────┐  Will gold price rise?                   │  thumb · question
 *   │ │IMG │  Timeline  [3M] 6M  1Y                   │           · timeline
 *   │ └────┘                                          │
 *   │ ───────────────────────────────────────────────│  hairline rule
 *   │  95%                         ┌──────────┐       │  hero return  ·  Yes / No
 *   │  Expected return             │   Yes    │       │
 *   │  best strategy if yes true   ├──────────┤       │
 *   │                              │   No     │       │
 *   └────────────────────────────────────────────────┘
 *
 * The ONE difference from a prediction exchange — and it is the whole product:
 * the big number is NOT a fabricated "chance". It is the strategy's OWN best
 * realised run (best_episode_pct), an honest track number, not a priced
 * outcome. The Yes/No buttons are NAVIGATION intents, not wagers: they open the
 * detail page and route to that side's deployable expression.
 *
 * TIMELINE: some events have a hard resolution date → the horizon is fixed, so
 * we show a single duration label. Open/structural views show a presentational
 * 3M/6M/1Y toggle (a reading device — it does not fabricate a per-horizon
 * return; the number stays the honest best run).
 *
 * Colours are the mockup's, routed through theme tokens so light matches the
 * mockup and dark still holds. Green/red stay reserved for the realised return
 * and the stance buttons.
 */

import * as React from "react";
import {
  Cpu,
  Zap,
  Car,
  Globe,
  Landmark,
  Flame,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import type { ViewSummary, StanceIntent } from "@/lib/types";
import { fmtPct } from "./view-format";

const FONT = "var(--font-display)";

// ---------------------------------------------------------------------------
// CategoryGlyph — the mockup's 88×88 thumbnail tile: a filled, rounded square
// keyed off the category's leading word ("AI · Theme" → AI) with a calm
// monochrome glyph. Unknown categories fall back to a trend glyph.
// ---------------------------------------------------------------------------

const CATEGORY_ICON: Record<string, LucideIcon> = {
  ai: Cpu,
  energy: Zap,
  autos: Car,
  auto: Car,
  geopolitics: Globe,
  macro: Landmark,
  index: TrendingUp,
  commodity: Flame,
  crude: Flame,
  oil: Flame,
  gold: Flame,
};

/** Cheap deterministic string hash → stable per-card image lock. */
function hashStr(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h << 5) - h + s.charCodeAt(i);
  return h;
}

function categoryLeadWord(category: string | null | undefined): string {
  return (
    ((category ?? "").split("·")[0] ?? "")
      .trim()
      .toLowerCase()
      .split(/\s+/)[0] ?? ""
  );
}

function categoryIcon(category: string | null | undefined): LucideIcon {
  const lead = categoryLeadWord(category);
  return (lead ? CATEGORY_ICON[lead] : undefined) ?? TrendingUp;
}

export function CategoryGlyph({
  category,
  seed,
}: {
  category: string | null | undefined;
  seed: string;
}): React.ReactElement {
  const Icon = categoryIcon(category);
  // A calm, self-contained tile: the category glyph on a faint tinted ground
  // keyed off the seed hue. No external imagery (CSP-safe, no network, no
  // preview-only placeholder photos).
  const hue = Math.abs(hashStr(seed)) % 360;
  return (
    <div
      aria-hidden
      style={{
        position: "relative",
        width: 68,
        height: 68,
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        borderRadius: 12,
        overflow: "hidden",
        background: `hsl(${hue} 30% 50% / 0.10)`,
        border: "1px solid var(--glass-border)",
      }}
    >
      <Icon
        size={28}
        strokeWidth={1.75}
        color={`hsl(${hue} 42% 45%)`}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Timeline — the horizon affordance. Two modes:
//   fixed  → a single non-interactive duration label (event with a hard date /
//            an episodic horizon).
//   toggle → the mockup's 3M / 6M / 1Y raised-pill segmented control on a grey
//            track. Presentational only: switching does NOT change the honest
//            best-run number (we don't fabricate per-horizon returns yet).
// ---------------------------------------------------------------------------

const TOGGLE_HORIZONS = ["3M", "6M", "1Y"] as const;

type HorizonConfig =
  | { mode: "fixed"; label: string }
  | { mode: "toggle"; options: readonly string[]; defaultIdx: number };

/** Decide whether a view's timeline is fixed (single label) or flexible (toggle). */
function horizonConfig(view: ViewSummary): HorizonConfig {
  const th = (view.time_horizon ?? "").trim();
  const episodic = /episode|day|week|by\s|until|expir/i.test(th);
  // A hard resolution date, or an episodic/dated horizon, means the window is
  // pinned — show it as one duration, not a chooser.
  if (view.resolution_date || (episodic && th)) {
    return { mode: "fixed", label: th || "Fixed window" };
  }
  // Otherwise the belief is open / structural → a flexible timeline chooser.
  const s = th.toLowerCase();
  let defaultIdx = 2; // default to 1Y (matches the mockup)
  if (/\b3\b|quarter|3m|90/.test(s)) defaultIdx = 0;
  else if (/\b6\b|half|6m|180/.test(s)) defaultIdx = 1;
  return { mode: "toggle", options: TOGGLE_HORIZONS, defaultIdx };
}

function TimelineLabel(): React.ReactElement {
  return (
    <span
      style={{
        fontFamily: FONT,
        fontSize: 11.5,
        fontWeight: 600,
        color: "var(--text-tertiary)",
        whiteSpace: "nowrap",
      }}
    >
      Timeline
    </span>
  );
}

function Timeline({
  config,
  selected,
  onSelect,
}: {
  config: HorizonConfig;
  selected: number;
  onSelect: (idx: number) => void;
}): React.ReactElement {
  if (config.mode === "fixed") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
        <TimelineLabel />
        <span
          className="truncate"
          title={config.label}
          style={{
            fontFamily: FONT,
            fontSize: 13,
            fontWeight: 700,
            color: "var(--text-primary)",
            fontVariantNumeric: "tabular-nums",
            minWidth: 0,
          }}
        >
          {config.label}
        </span>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <TimelineLabel />
      <div
        role="tablist"
        aria-label="Timeline"
        style={{ display: "inline-flex", gap: 2 }}
        onClick={(e) => e.stopPropagation()}
      >
        {config.options.map((opt, i) => {
          const active = i === selected;
          return (
            <button
              key={opt}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(i);
              }}
              style={{
                appearance: "none",
                border: "none",
                cursor: "pointer",
                padding: "3px 9px",
                borderRadius: "var(--radius-xs)",
                fontFamily: "var(--font-ui)",
                fontSize: 10.5,
                fontWeight: 500,
                fontVariantNumeric: "tabular-nums",
                lineHeight: 1.2,
                color: active ? "var(--bg-primary)" : "var(--text-secondary)",
                background: active ? "var(--text-primary)" : "transparent",
                transition:
                  "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
              }}
            >
              {opt}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// StanceButton — Polymarket-style outcome button: a soft tinted fill (green for
// Yes, red for No) with matching colored ink and a small corner radius, no glow.
// NOT a wager: it opens the detail page pre-scrolled to that side's expression.
// ---------------------------------------------------------------------------

function StanceButton({
  label,
  tone,
  onClick,
}: {
  label: string;
  tone: "yes" | "no";
  onClick: (e: React.MouseEvent) => void;
}): React.ReactElement {
  const [hover, setHover] = React.useState(false);
  const accent = tone === "yes" ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        appearance: "none",
        cursor: "pointer",
        width: 112,
        padding: "7px 0",
        borderRadius: 6,
        border: "none",
        // Soft tinted fill with colored ink (Polymarket outcome-button style).
        background: `color-mix(in srgb, ${accent} ${hover ? 18 : 12}%, transparent)`,
        color: accent,
        fontFamily: FONT,
        fontSize: 14,
        fontWeight: 700,
        letterSpacing: "-0.01em",
        transition: "background 140ms var(--ease-quartr)",
      }}
    >
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// HeroFigure — the big honest return with a superscript % (mockup grammar).
// fmtPct gives e.g. "+64.8%"; we split the trailing % into a <sup>.
// ---------------------------------------------------------------------------

function HeroFigure({
  pct,
  color,
}: {
  pct: number | null;
  color: string;
}): React.ReactElement {
  const s = fmtPct(pct);
  const hasPct = s.endsWith("%");
  const body = hasPct ? s.slice(0, -1) : s;
  return (
    <div
      style={{
        fontFamily: "var(--font-serif)",
        fontVariantNumeric: "tabular-nums",
        fontSize: 40,
        fontWeight: 600,
        letterSpacing: "-0.02em",
        lineHeight: 1,
        color,
      }}
    >
      {body}
      {hasPct && (
        <sup style={{ fontSize: 20, fontWeight: 600, top: "-0.7em" }}>%</sup>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ViewCard
// ---------------------------------------------------------------------------

type ViewCardProps = {
  view: ViewSummary;
  /** intent lets a Yes/No press open the detail on that side + its strategy. */
  onOpen: (id: string, intent?: StanceIntent) => void;
  onFollowChange?: (
    id: string,
    next: { is_following: boolean; follower_count: number },
  ) => void;
};

export function ViewCard({
  view,
  onOpen,
}: ViewCardProps): React.ReactElement {
  const [hover, setHover] = React.useState(false);

  const handleKey = (e: React.KeyboardEvent<HTMLElement>): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen(view.id);
    }
  };

  // ── Timeline state (presentational when a toggle) ─────────────────────────
  const config = React.useMemo(() => horizonConfig(view), [view]);
  const [horizon, setHorizon] = React.useState(
    config.mode === "toggle" ? config.defaultIdx : 0,
  );

  const be = view.best_expression;
  const title = view.short_title ?? view.plain_one_liner ?? view.title;

  // ── The hero number — honest by construction ────────────────────────────
  // The single best past occurrence of the headline strategy (best_episode_pct),
  // falling back to the expression's total realised return. Never a fabricated
  // "chance" — a real, realised track number.
  const bestRun =
    typeof view.best_episode_pct === "number" ? view.best_episode_pct : null;
  const heroPct = bestRun ?? be?.total_return_pct ?? null;
  const heroColor = "var(--text-primary)";

  const stopAnd = (intent: StanceIntent) => (e: React.MouseEvent) => {
    e.stopPropagation();
    onOpen(view.id, intent);
  };

  return (
    <article
      role="button"
      tabIndex={0}
      aria-label={`Open view: ${view.plain_one_liner ?? view.title}`}
      onClick={() => onOpen(view.id)}
      onKeyDown={handleKey}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      data-testid={`view-card-${view.id}`}
      className="cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "transparent",
        border: `1px solid ${hover ? "var(--glass-border-hover)" : "var(--glass-border)"}`,
        borderRadius: 12,
        padding: "20px 22px",
        boxShadow: "none",
        transition: "border-color 180ms var(--ease-quartr)",
      }}
    >
      {/* ── (a) header — thumb · (question + timeline) ────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <CategoryGlyph category={view.category} seed={view.id} />

        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            className="line-clamp-2"
            style={{
              fontFamily: FONT,
              fontSize: 16,
              fontWeight: 700,
              lineHeight: 1.18,
              letterSpacing: "-0.02em",
              color: "var(--text-primary)",
              marginBottom: 9,
              minHeight: "2.36em",
            }}
          >
            {title}
          </div>
          <Timeline config={config} selected={horizon} onSelect={setHorizon} />
        </div>
      </div>

      {/* ── (b) body — hero return (left) · Yes/No stance (right) ─────────── */}
      <div
        className="flex items-center justify-between"
        style={{ gap: 24, marginTop: "auto", paddingTop: 24 }}
      >
        <div style={{ minWidth: 0 }}>
          <HeroFigure pct={heroPct} color={heroColor} />
          <div
            style={{
              marginTop: 5,
              fontFamily: FONT,
              fontSize: 11.5,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              lineHeight: 1.4,
            }}
          >
            Expected return
            <span style={{ display: "block", color: "var(--text-disabled)" }}>
              best strategy if yes is true
            </span>
          </div>
        </div>

        {/* Yes / No stance — navigation intents, not wagers */}
        <div
          className="shrink-0 flex flex-col"
          style={{ gap: 8 }}
          onClick={(e) => e.stopPropagation()}
        >
          <StanceButton label="Yes" tone="yes" onClick={stopAnd("yes")} />
          <StanceButton label="No" tone="no" onClick={stopAnd("no")} />
        </div>
      </div>
    </article>
  );
}
