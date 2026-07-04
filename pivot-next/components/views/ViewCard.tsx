"use client";

/**
 * ViewCard — one gallery card for a ViewSummary.
 *
 * Visual structure adopted from the collaborator's 943e782 redesign:
 *   ┌──────────────────────────────────────────────────┐
 *   │ ┌────┐  Will gold prices rise?                    │
 *   │ │IMG │  Timeline  Ends Dec 2025 / [3M] [6M] [1Y]  │
 *   │ └────┘                                            │
 *   │  ~~~ sparkline (flex-grow) ~~~                    │
 *   │  +64.8%            ┌──────────┐                   │
 *   │  best past run     │   Yes    │                   │
 *   │                    ├──────────┤                   │
 *   │                    │   No     │                   │
 *   │ ─────────────────────────────────────────────────│
 *   │ ● Promising · Positive in 24 of 32  [From ₹808]  Follow │
 *   └──────────────────────────────────────────────────┘
 *
 * Functional bits preserved from our version:
 *   - `endsLabel` integrated into the Timeline fixed-label (resolution_date → "Ends …")
 *   - positive-rate line ("Positive in X of Y") in footer via footerTrack
 *   - onOpen(id, intent?) callback + Yes/No stance buttons that route intent
 *   - trust badge + FollowButton + From ₹808 chip
 *   - BestExpressionWithPositive type extension
 *   - Sparkline in flex-grow spacer region
 *   - ViewSurface for consistent border-only theming
 *
 * Stance buttons use the collaborator's soft tinted fill (color-mix transparent),
 * not the solid fill in our old version. Yes/No colors are profit/loss for
 * maximum legibility at low opacity.
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
  ArrowRight,
  type LucideIcon,
} from "lucide-react";
import type { ViewSummary, StanceIntent } from "@/lib/types";
import { ViewSurface, Hairline } from "./ViewSurface";
import { FollowButton } from "./FollowButton";
import {
  fmtPct,
  signColor,
  trustBadge,
  verdictColor,
  endsLabel,
} from "./view-format";

const FONT = "var(--font-display)";

// ---------------------------------------------------------------------------
// BestExpressionWithPositive — local structural extension that adds the
// positive-outcome count fields ahead of them landing in lib/types.ts.
// ---------------------------------------------------------------------------

type BestExpressionWithPositive = NonNullable<ViewSummary["best_expression"]> & {
  pct_positive?: number | null;
  n_positive?: number | null;
};

// ---------------------------------------------------------------------------
// CategoryGlyph — 68×68 image tile with an icon fallback.
// Photo keyed by category keyword via loremflickr; icon covers load errors.
// Rounded corners (radius 8) instead of square — matches our design language.
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

const CATEGORY_KEYWORD: Record<string, string> = {
  ai: "microchip",
  energy: "oil-refinery",
  autos: "car-factory",
  auto: "car-factory",
  geopolitics: "world-map",
  macro: "central-bank",
  index: "stock-market",
  commodity: "commodities",
  crude: "oil-barrel",
  oil: "oil-barrel",
  gold: "gold-bars",
  monsoon: "monsoon-farm",
  it: "software",
};

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

function CategoryGlyph({
  category,
  seed,
}: {
  category: string | null | undefined;
  seed: string;
}): React.ReactElement {
  const Icon = categoryIcon(category);
  const [imgOk, setImgOk] = React.useState(true);
  const lead = categoryLeadWord(category);
  const keyword = (lead && CATEGORY_KEYWORD[lead]) || "finance";
  const lock = Math.abs(hashStr(seed)) % 1000;
  const src = `https://loremflickr.com/200/200/${keyword}?lock=${lock}`;
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
        borderRadius: 8,
        overflow: "hidden",
        background: "var(--bg-elevated)",
      }}
    >
      <Icon size={28} strokeWidth={1.75} color="var(--text-secondary)" />
      {imgOk && (
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
// Timeline — shows how far out a view's horizon is.
//
//   fixed  → a single non-interactive label. When resolution_date is present,
//            we use endsLabel("2025-12-01") → "Ends Dec 2025" (our feature).
//            Episodic / dated time_horizons also become fixed labels.
//   toggle → the 3M / 6M / 1Y segmented control for open / structural views.
//            Presentational only: the honest best-run number does NOT change.
// ---------------------------------------------------------------------------

const TOGGLE_HORIZONS = ["3M", "6M", "1Y"] as const;

type HorizonConfig =
  | { mode: "fixed"; label: string }
  | { mode: "toggle"; options: readonly string[]; defaultIdx: number };

function horizonConfig(view: ViewSummary): HorizonConfig {
  // Prefer the formatted resolution date ("Ends Dec 2025") when available.
  const ends = endsLabel(view.resolution_date);
  if (ends) return { mode: "fixed", label: ends };

  const th = (view.time_horizon ?? "").trim();
  const episodic = /episode|day|week|by\s|until|expir/i.test(th);
  if (episodic && th) return { mode: "fixed", label: th };

  // Open / structural view → the presentational toggle.
  const s = th.toLowerCase();
  let defaultIdx = 2; // default 1Y
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
// StanceButton — the collaborator's soft tinted fill (Polymarket style).
// accent color at 12% (18% on hover) over transparent — never a solid fill.
// Yes = profit green tint, No = loss red tint, Muted = tertiary grey tint.
// Our content structure (word + secondary line) is preserved.
// ---------------------------------------------------------------------------

function StanceButton({
  word,
  secondary,
  tone,
  ariaLabel,
  onOpen,
}: {
  word: string;
  secondary: string;
  tone: "yes" | "no" | "muted";
  ariaLabel: string;
  onOpen: (e: React.MouseEvent) => void;
}): React.ReactElement {
  const [hover, setHover] = React.useState(false);
  const accent =
    tone === "yes"
      ? "var(--color-profit)"
      : tone === "no"
        ? "var(--color-loss)"
        : "var(--text-tertiary)";
  return (
    <button
      type="button"
      aria-label={ariaLabel}
      onClick={(e) => {
        e.stopPropagation();
        onOpen(e);
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        flex: 1,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        gap: 2,
        textAlign: "left",
        padding: "9px 12px",
        borderRadius: 6,
        border: "none",
        background: `color-mix(in srgb, ${accent} ${hover ? 18 : 12}%, transparent)`,
        color: accent,
        cursor: "pointer",
        transition: "background 140ms var(--ease-quartr)",
      }}
    >
      <span
        style={{
          fontFamily: FONT,
          fontSize: 13,
          fontWeight: 700,
          letterSpacing: "0.03em",
          lineHeight: 1,
        }}
      >
        {word}
      </span>
      <span
        style={{
          fontFamily: FONT,
          fontSize: tone === "yes" ? 16 : 13,
          fontWeight: tone === "yes" ? 700 : 600,
          lineHeight: 1.2,
          letterSpacing: tone === "yes" ? "-0.01em" : "0",
          fontVariantNumeric: "tabular-nums",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          maxWidth: "100%",
        }}
      >
        {secondary}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// HeroFigure — the big honest return number with a superscript %.
// fmtPct gives "+64.8%" → we split the trailing % into a <sup>.
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
        fontFamily: FONT,
        fontVariantNumeric: "tabular-nums",
        fontSize: 38,
        fontWeight: 800,
        letterSpacing: "-0.03em",
        lineHeight: 1,
        color,
      }}
    >
      {body}
      {hasPct && (
        <sup style={{ fontSize: 19, fontWeight: 700, top: "-0.7em" }}>%</sup>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sparkline — the belief's own return path, sign-tinted.
// Preserved from our version; lives in the flex-grow spacer between the
// header and the hero/stance section.
// ---------------------------------------------------------------------------

function Sparkline({
  values,
  height = 48,
}: {
  values: number[];
  height?: number;
}): React.ReactElement | null {
  if (values.length < 2) return null;
  const W = 100;
  const H = height;
  const pad = 3;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const n = values.length;
  const pts = values.map((v, i) => {
    const x = (i / (n - 1)) * W;
    const y = pad + (1 - (v - min) / span) * (H - pad * 2);
    return [x, y] as const;
  });
  const line = pts
    .map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(2)},${p[1].toFixed(2)}`)
    .join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  const first = values[0] ?? 0;
  const last = values[values.length - 1] ?? first;
  const up = last >= first;
  const color = up ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      preserveAspectRatio="none"
      style={{ display: "block", overflow: "visible" }}
      aria-hidden
    >
      <path d={area} fill={color} fillOpacity={0.07} stroke="none" />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeOpacity={0.85}
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// footerTrack — "Positive in 24 of 32" from the own-return distribution.
// ---------------------------------------------------------------------------

function footerTrack(be: BestExpressionWithPositive): string {
  if (be.n_positive != null && be.n_episodes != null && be.n_episodes > 0) {
    return `Positive in ${be.n_positive} of ${be.n_episodes}`;
  }
  if (be.pct_positive != null && be.n_episodes != null && be.n_episodes > 0) {
    const wins = Math.round((be.pct_positive / 100) * be.n_episodes);
    return `Positive in ${wins} of ${be.n_episodes}`;
  }
  return "";
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
  onFollowChange,
}: ViewCardProps): React.ReactElement {
  const handleKey = (e: React.KeyboardEvent<HTMLElement>): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen(view.id);
    }
  };

  const config = React.useMemo(() => horizonConfig(view), [view]);
  const [horizon, setHorizon] = React.useState(
    config.mode === "toggle" ? config.defaultIdx : 0,
  );

  const be = view.best_expression as BestExpressionWithPositive | null;
  const hasReturn = be != null && be.total_return_pct != null;
  const forwardNet = view.forward_expected_net_pct;
  const hasForwardOnly = !hasReturn && forwardNet != null;
  const curve = be?.equity_curve;
  const curveValues =
    Array.isArray(curve) && curve.length >= 2
      ? curve.map((p) => p.strategy)
      : [];
  const stance = view.stance ?? null;
  const noHasTrade = stance?.no.has_trade === true;
  const title = view.short_title ?? view.plain_one_liner ?? view.title;

  // Hero number — honest by construction.
  // Prefers best_episode_pct (a single real occurrence), then total_return_pct,
  // then forward model (labelled "modeled"). Never fabricated.
  const bestRun =
    typeof view.best_episode_pct === "number" ? view.best_episode_pct : null;
  const heroPct = bestRun ?? be?.total_return_pct ?? null;
  const heroIsForward = heroPct == null && hasForwardOnly;
  const displayPct = heroIsForward ? (forwardNet ?? null) : heroPct;
  const heroColor = signColor(displayPct, "var(--color-profit)");

  const heroLabel =
    bestRun != null
      ? "best past run"
      : hasForwardOnly
        ? "modeled"
        : hasReturn
          ? "total return"
          : "—";

  const stopAnd = (intent: StanceIntent) => (e: React.MouseEvent) => {
    e.stopPropagation();
    onOpen(view.id, intent);
  };

  return (
    <ViewSurface
      as="div"
      interactive
      role="button"
      tabIndex={0}
      aria-label={`Open view: ${view.plain_one_liner ?? view.title}`}
      onClick={() => onOpen(view.id)}
      onKeyDown={handleKey}
      data-testid={`view-card-${view.id}`}
      className="cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        padding: "20px 22px",
      }}
    >
      {/* ── (a) header: CategoryGlyph + title + Timeline ─────────────────── */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 16 }}>
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

      {/* ── (b) sparkline in flex-grow spacer ────────────────────────────── */}
      {curveValues.length >= 2 ? (
        <div
          style={{
            flex: 1,
            minHeight: 48,
            marginTop: 16,
            display: "flex",
            alignItems: "center",
          }}
        >
          <div style={{ width: "100%" }}>
            <Sparkline values={curveValues} height={48} />
          </div>
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 20 }} />
      )}

      {/* ── (c) hero return + stance buttons ─────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          gap: 16,
          marginTop: 20,
        }}
      >
        {/* Hero number */}
        <div style={{ minWidth: 0 }}>
          <HeroFigure pct={displayPct} color={heroColor} />
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
            {heroLabel}
          </div>
        </div>

        {/* Yes / No stance buttons */}
        {stance ? (
          <div
            className="shrink-0 flex flex-col"
            style={{ gap: 6, minWidth: 104 }}
            onClick={(e) => e.stopPropagation()}
          >
            <StanceButton
              tone="yes"
              word="Yes"
              secondary={
                hasReturn
                  ? fmtPct(be!.total_return_pct)
                  : hasForwardOnly
                    ? `${fmtPct(forwardNet)} modeled`
                    : "See the basket"
              }
              ariaLabel={`Yes — ${stance.yes.verdict}. Open the view.`}
              onOpen={stopAnd("yes")}
            />
            <StanceButton
              tone={noHasTrade ? "no" : "muted"}
              word="No"
              secondary={stance.no.verdict}
              ariaLabel={`No — ${stance.no.verdict}. Open the view.`}
              onOpen={stopAnd("no")}
            />
          </div>
        ) : hasReturn ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpen(view.id);
            }}
            style={{
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "10px 14px",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--glass-border)",
              background: "var(--bg-base)",
              cursor: "pointer",
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-secondary)",
            }}
          >
            View details
            <ArrowRight size={13} aria-hidden />
          </button>
        ) : (
          <span
            style={{
              flexShrink: 0,
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 500,
              color: "var(--text-tertiary)",
            }}
          >
            Still developing
          </span>
        )}
      </div>

      <Hairline style={{ marginTop: 16, marginBottom: 12 }} />

      {/* ── (d) footer: trust + track record + min entry + Follow ────────── */}
      <div className="flex items-center justify-between gap-3">
        <span
          className="inline-flex items-center gap-1.5"
          style={{
            minWidth: 0,
            fontFamily: FONT,
            fontSize: 13,
            fontWeight: 500,
            lineHeight: 1.3,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {be ? (
            <>
              <span
                aria-hidden
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: verdictColor(be.trust_verdict),
                  flexShrink: 0,
                }}
              />
              <span style={{ color: verdictColor(be.trust_verdict) }}>
                {trustBadge(be.trust_verdict)}
              </span>
              {footerTrack(be) && (
                <span style={{ color: "var(--text-tertiary)" }}>
                  {" · "}
                  {footerTrack(be)}
                </span>
              )}
            </>
          ) : (
            <span style={{ color: "var(--text-tertiary)" }}>Recent idea</span>
          )}
          {typeof view.min_entry_inr === "number" && (
            <span
              style={{
                marginLeft: 4,
                fontFamily: FONT,
                fontSize: 12,
                fontWeight: 500,
                color: "var(--text-tertiary)",
                border: "1px solid var(--glass-border)",
                borderRadius: "var(--radius-md)",
                padding: "1px 6px",
                fontVariantNumeric: "tabular-nums",
                flexShrink: 0,
              }}
            >
              {`From ₹${Math.round(view.min_entry_inr).toLocaleString("en-IN")}`}
            </span>
          )}
        </span>
        <FollowButton
          viewId={view.id}
          isFollowing={view.is_following}
          followerCount={view.follower_count}
          size="sm"
          onChange={(next) => onFollowChange?.(view.id, next)}
        />
      </div>
    </ViewSurface>
  );
}
