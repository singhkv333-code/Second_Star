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
  CloudRain,
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
  renewable: Zap,
  nuclear: Zap,
  monsoon: CloudRain,
};

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

// Themed artwork per opinion, served from /public/opinions. Keyed by view id
// first (each opinion gets its own illustration), then by the category's lead
// word so a future view in the same theme inherits one for free. No match →
// the lucide glyph alone, which is why the tile always renders something.
const VIEW_IMAGE: Record<string, string> = {
  renewable: "/opinions/renewable.svg",
  gold: "/opinions/gold.svg",
  monsoon: "/opinions/monsoon.svg",
  ev: "/opinions/ev.svg",
  nuclear: "/opinions/nuclear.svg",
  commodity: "/opinions/gold.svg",
  autos: "/opinions/ev.svg",
  auto: "/opinions/ev.svg",
  energy: "/opinions/renewable.svg",
};

/** Illustration for a card: exact view id wins, else the category lead word. */
function viewImage(
  id: string,
  category: string | null | undefined,
): string | null {
  const lead = categoryLeadWord(category);
  return VIEW_IMAGE[id] ?? (lead ? VIEW_IMAGE[lead] : undefined) ?? null;
}

export function CategoryGlyph({
  category,
  seed,
}: {
  category: string | null | undefined;
  seed: string;
}): React.ReactElement {
  const Icon = categoryIcon(category);
  const [imgOk, setImgOk] = React.useState(true);
  const src = viewImage(seed, category);
  return (
    <div
      aria-hidden
      className="view-card-thumb"
      style={{
        position: "relative",
        width: 68,
        height: 68,
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        borderRadius: 0,
        overflow: "hidden",
        background: "var(--bg-elevated)",
      }}
    >
      <Icon size={28} strokeWidth={1.75} color="var(--text-secondary)" />
      {src && imgOk && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt=""
          onError={() => setImgOk(false)}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      )}
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

type HorizonConfig =
  | { mode: "fixed"; label: string }
  | { mode: "toggle"; options: readonly string[]; defaultIdx: number };

/** Decide how to render a view's timeline.
 *
 * The timeline is the belief's EXPECTED RESOLUTION HORIZON — how long until the
 * event is expected to happen / the card expires — NOT the strategy's holding
 * period and NOT a promise of gains within that window. It is a single fixed
 * label. A 3M/6M/1Y *chooser* is only ever shown when a view genuinely carries
 * DIFFERENT strategies for different horizons (available_horizons.length > 1) —
 * we never render a chooser that changes nothing. */
function horizonConfig(view: ViewSummary): HorizonConfig {
  const th = (view.time_horizon ?? "").trim();
  const avail = view.available_horizons?.filter(Boolean) ?? null;
  // Only a real multi-horizon strategy set earns an interactive toggle.
  if (avail && avail.length > 1) {
    return { mode: "toggle", options: avail, defaultIdx: 0 };
  }
  // Everything else: one fixed resolution-horizon label, no fake chooser.
  const label = (avail && avail[0]) || th || "Open-ended";
  return { mode: "fixed", label };
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
      className="view-card-stance"
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
// HeroFigure — the big honest return with a slightly smaller, baseline-aligned
// % so it reads as "+64.8%" cleanly (no superscript float).
// fmtPct gives e.g. "+64.8%"; we render the trailing % a touch smaller.
// ---------------------------------------------------------------------------

function HeroFigure({
  pct,
  color,
  sans = false,
}: {
  pct: number | null;
  color: string;
  /** Render the figure in the sans display face instead of the serif — used
   *  by the Home dashboard's teaser cards; the Views tab/detail keep serif. */
  sans?: boolean;
}): React.ReactElement {
  const s = fmtPct(pct);
  const hasPct = s.endsWith("%");
  const body = hasPct ? s.slice(0, -1) : s;
  return (
    <div
      className="view-card-hero"
      style={{
        fontFamily: sans ? "var(--font-display)" : "var(--font-serif)",
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
        <span style={{ fontSize: 24, fontWeight: 600, verticalAlign: "baseline", marginLeft: "0.02em" }}>
          %
        </span>
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
  /** Renders the whole card in the sans display face (title + hero figure)
   *  instead of the serif — used by the Home dashboard's teaser cards; the
   *  Views tab grid + detail page keep their serif hero untouched. */
  sans?: boolean;
};

export function ViewCard({
  view,
  onOpen,
  sans = false,
}: ViewCardProps): React.ReactElement {
  const [hover, setHover] = React.useState(false);

  // Teaser card: the question ships before its basket does. Inert by design —
  // there is no detail record to open, so the whole card stops being a button.
  const soon = view.coming_soon === true;

  const handleKey = (e: React.KeyboardEvent<HTMLElement>): void => {
    if (soon) return;
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

  // ── The hero number — the AVERAGE GAIN (mean of the winning occurrences) ──
  // This is the "average of all gaining turns" metric, NOT the single best run
  // and NOT a fabricated chance. Falls back to the realised total return, then
  // the best occurrence, when no gain/loss split exists.
  const avgGain =
    typeof be?.gain_loss?.avg_gain_pct === "number"
      ? be.gain_loss.avg_gain_pct
      : null;
  const bestRun =
    typeof view.best_episode_pct === "number" ? view.best_episode_pct : null;
  const heroPct = avgGain ?? be?.total_return_pct ?? bestRun ?? null;
  const heroColor = "var(--text-primary)";
  // Honest context for the average-gain headline: how often it actually won.
  const hitPct =
    typeof be?.pct_positive === "number" ? Math.round(be.pct_positive) : null;

  const stopAnd = (intent: StanceIntent) => (e: React.MouseEvent) => {
    e.stopPropagation();
    onOpen(view.id, intent);
  };

  return (
    <article
      role={soon ? "group" : "button"}
      tabIndex={soon ? -1 : 0}
      aria-label={
        soon
          ? `Coming soon: ${view.plain_one_liner ?? view.title}`
          : `Open view: ${view.plain_one_liner ?? view.title}`
      }
      onClick={soon ? undefined : () => onOpen(view.id)}
      onKeyDown={handleKey}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      data-testid={`view-card-${view.id}`}
      data-coming-soon={soon ? "true" : undefined}
      className={`view-card focus:outline-none ${
        soon
          ? ""
          : "cursor-pointer focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      }`}
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        // Every card the same height, across rows as well as within one.
        // `items-stretch` only equalises siblings in the SAME grid row, and a
        // teaser card (no hero figure, no stance column) is shorter than a live
        // one — so the floor is set here. Home's teasers opt out: their sizing
        // is vh-clamped by .home-views-grid to fit the dashboard cell.
        minHeight: sans ? undefined : 212,
        background: sans ? "transparent" : "var(--bg-secondary)",
        border: sans
          ? `1px solid ${hover ? "var(--glass-border-hover)" : "var(--glass-border)"}`
          : "none",
        borderRadius: 12,
        padding: "20px 22px",
        boxShadow: "none",
        transition: "border-color 180ms var(--ease-quartr)",
      }}
    >
      {/* ── (a) header — thumb · (question + timeline) ────────────────────── */}
      <div className="view-card-head" style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <CategoryGlyph category={view.category} seed={view.id} />

        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            className="view-card-title line-clamp-2"
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

      {/* ── (b) body ─────────────────────────────────────────────────────────
          Live: hero return (left) · Yes/No stance (right).
          Coming soon: just the words, in the muted body style — no number to
          quote and no stance to take. */}
      {soon ? (
        <div
          className="view-card-body"
          style={{ marginTop: "auto", paddingTop: 24 }}
        >
          <div
            style={{
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 500,
              lineHeight: 1.45,
              color: "var(--text-tertiary)",
            }}
          >
            Coming soon
          </div>
        </div>
      ) : (
      <div
        className="view-card-body flex items-center justify-between"
        style={{ gap: 24, marginTop: "auto", paddingTop: 24 }}
      >
        <div style={{ minWidth: 0 }}>
          <HeroFigure pct={heroPct} color={heroColor} sans={sans} />
          <div
            className="view-card-caption"
            style={{
              marginTop: 5,
              fontFamily: FONT,
              fontSize: 11.5,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              lineHeight: 1.4,
            }}
          >
            Average gain
            <span style={{ display: "block", color: "var(--text-disabled)" }}>
              {hitPct != null
                ? `per winning turn · ${hitPct}% of turns won`
                : "best strategy if yes is true"}
            </span>
          </div>
        </div>

        {/* Yes / No stance — navigation intents, not wagers */}
        <div
          className="view-card-stance-col shrink-0 flex flex-col"
          style={{ gap: 8 }}
          onClick={(e) => e.stopPropagation()}
        >
          <StanceButton label="Yes" tone="yes" onClick={stopAnd("yes")} />
          <StanceButton label="No" tone="no" onClick={stopAnd("no")} />
        </div>
      </div>
      )}
    </article>
  );
}
