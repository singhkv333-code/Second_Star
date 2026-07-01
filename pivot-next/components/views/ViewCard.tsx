"use client";

/**
 * ViewCard — one rounded, border-only gallery card for a ViewSummary, laid out
 * as a calm "belief market" card in the visual grammar of Polymarket / Kalshi:
 * a question up top, the belief's own return path, and TWO side-by-side
 * Yes / No buttons that read the two ways to play it.
 *
 * The ONE difference from a prediction exchange — and it is the whole product:
 * these buttons are NOT binary contracts and carry NO fabricated odds/cents.
 * Pressing Yes opens the view on its bullish, deployable expression (a basket /
 * option structure / pair on REAL securities); pressing No opens it on the
 * honest counter (a hedge, the incumbents, or "no clean trade — sit it out").
 * The number on the Yes button is the strategy's OWN realised return — the
 * honest analog of the cents-price a betting market prints. We never price an
 * outcome; we route a belief to an instrument.
 *
 * DESIGN LAW (see ViewSurface): ROUNDED corners, borders-only (no fills), no
 * jargon on screen (every label routed through a view-format humanizer), >=13px
 * floor, tabular numerals. Fixed height so every column lines up.
 *
 * Colour law: our green/red are RESERVED for real P&L. The Yes/No accents are
 * therefore brand-blue (Yes) and amber (No) — matching the detail-page stance
 * block exactly — and ONLY the realised return renders in profit/loss colour.
 * A "no clean trade" No is dashed + muted, never red (it isn't a loss).
 *
 * Layout (left-aligned vertical stack, padding 20):
 *   (a) meta row      : category eyebrow            | status dot + word
 *   (b) TITLE          (the question)               18/600, 2-line clamp
 *   (c) layman summary  (plain_summary)             15/400, 1-line clamp
 *   (d) return path     full-width sign-tinted sparkline (the belief's curve)
 *   ─── flex spacer, so the buttons bottom-align across the grid ───
 *   (e) YES / NO        two buttons — the two ways to play; each opens the view
 *   ─── hairline ───
 *   (f) footer         : trust word (colour-coded) · positive-rate | Follow
 *
 * When a view has no authored stance (live curated views not yet backfilled, or
 * a still-developing idea) the card degrades gracefully to a single "View
 * details →" affordance — we never fabricate a Yes/No it doesn't have.
 * Whole card (outside the buttons) is a click target → onOpen(view.id).
 */

import * as React from "react";
import { ArrowRight } from "lucide-react";
import type { ViewSummary, StanceIntent } from "@/lib/types";
import { ViewSurface, Hairline } from "./ViewSurface";
import { FollowButton } from "./FollowButton";
import {
  fmtPct,
  signColor,
  categoryLabel,
  statusLabel,
  statusDotColor,
  trustBadge,
  verdictColor,
} from "./view-format";

const CARD_HEIGHT = 376;
const FONT = "var(--font-display)";

// ---------------------------------------------------------------------------
// BestExpression is gaining positive-outcome fields (pct_positive/n_positive)
// on the shared data contract — declared as a local structural extension so
// this file type-checks ahead of that landing in lib/types.ts (harmless once
// the real fields arrive there; TS will simply narrow to the real types).
// ---------------------------------------------------------------------------

type BestExpressionWithPositive = NonNullable<ViewSummary["best_expression"]> & {
  pct_positive?: number | null;
  n_positive?: number | null;
};

// ---------------------------------------------------------------------------
// Sparkline — the belief's own return path, full-width + responsive.
// A plain SVG (viewBox + non-scaling stroke) so it stretches to any column
// width without distorting the line weight. Sign-tinted: green when the path
// ended up, red when it ended down. Never an axis, tooltip, or number — this
// is a texture, not a chart (the real chart lives on the detail page).
// ---------------------------------------------------------------------------

function Sparkline({
  values,
  height = 34,
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
// StanceButton — one of the two "ways to play" buttons. `tone` maps to the
// accent (yes → brand-blue, no → amber, muted → the dashed "no clean trade"
// case). The secondary line is the Yes-side return (profit/loss coloured) or
// the No-side plain verdict. An arrow signals it OPENS the view.
// ---------------------------------------------------------------------------

function StanceButton({
  word,
  secondary,
  secondaryColor,
  tone,
  ariaLabel,
  onOpen,
}: {
  word: string;
  secondary: string;
  secondaryColor?: string;
  tone: "yes" | "no" | "muted";
  ariaLabel: string;
  onOpen: (e: React.MouseEvent | React.KeyboardEvent) => void;
}): React.ReactElement {
  const [hover, setHover] = React.useState(false);
  const muted = tone === "muted";
  const accent =
    tone === "yes"
      ? "var(--pivot-blue)"
      : tone === "no"
        ? "var(--color-warn)"
        : "var(--text-tertiary)";
  const wordColor = muted ? "var(--text-tertiary)" : accent;
  const border = muted
    ? "var(--glass-border)"
    : hover
      ? accent
      : "var(--glass-border)";

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
        gap: 3,
        textAlign: "left",
        padding: "9px 13px",
        borderRadius: "var(--radius-md)",
        border: `1px ${muted ? "dashed" : "solid"} ${border}`,
        background: muted
          ? "transparent"
          : hover
            ? `color-mix(in srgb, ${accent} 12%, transparent)`
            : `color-mix(in srgb, ${accent} 6%, transparent)`,
        cursor: "pointer",
        transition:
          "border-color 160ms var(--ease-quartr), background 160ms var(--ease-quartr)",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          fontFamily: FONT,
          fontSize: 13,
          fontWeight: 700,
          letterSpacing: "0.03em",
          color: wordColor,
          lineHeight: 1,
        }}
      >
        {word}
        <ArrowRight size={12} aria-hidden style={{ opacity: 0.75 }} />
      </span>
      <span
        style={{
          fontFamily: FONT,
          fontSize: tone === "yes" ? 17 : 14,
          fontWeight: tone === "yes" ? 700 : 500,
          color: secondaryColor ?? "var(--text-secondary)",
          lineHeight: 1.2,
          letterSpacing: tone === "yes" ? "-0.01em" : "0",
          fontVariantNumeric: "tabular-nums",
          // The Yes return is always short (1 line); a No verdict like "Back
          // the incumbents" wraps to a second line rather than truncating
          // mid-word. Buttons are flex siblings → they stretch to equal height.
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

  const be = view.best_expression as BestExpressionWithPositive | null;
  const hasReturn = be != null && be.total_return_pct != null;
  const curve = be?.equity_curve;
  const curveValues =
    Array.isArray(curve) && curve.length >= 2
      ? curve.map((p) => p.strategy)
      : [];
  const stance = view.stance ?? null;
  const noHasTrade = stance?.no.has_trade === true;
  const title = view.short_title ?? view.plain_one_liner ?? view.title;

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
        height: CARD_HEIGHT,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* ── (a) meta row ─────────────────────────────────────────────── */}
      <div
        className="flex items-center justify-between gap-3"
        style={{ fontSize: 13, lineHeight: 1.3 }}
      >
        <span
          className="truncate"
          style={{
            fontFamily: FONT,
            fontSize: 13,
            fontWeight: 500,
            color: "var(--text-tertiary)",
            letterSpacing: "0.01em",
          }}
        >
          {categoryLabel(view.category)}
        </span>
        <span
          className="inline-flex items-center gap-1.5 shrink-0"
          style={{
            fontFamily: FONT,
            fontSize: 13,
            fontWeight: 500,
            color: "var(--text-secondary)",
            whiteSpace: "nowrap",
          }}
        >
          <span
            aria-hidden
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: statusDotColor(view.status),
              flexShrink: 0,
            }}
          />
          {statusLabel(view.status)}
        </span>
      </div>

      {/* ── (b) TITLE — the belief, phrased as a question ────────────── */}
      <div
        className="line-clamp-2"
        style={{
          marginTop: 14,
          fontFamily: FONT,
          fontSize: 18,
          fontWeight: 600,
          lineHeight: 1.3,
          color: "var(--text-primary)",
          letterSpacing: "-0.01em",
          minHeight: 18 * 1.3 * 2,
        }}
      >
        {title}
      </div>

      {/* ── (c) layman summary — one quiet line under the title ───────── */}
      <p
        className="line-clamp-1"
        style={{
          margin: "8px 0 0 0",
          fontFamily: FONT,
          fontSize: 15,
          fontWeight: 400,
          lineHeight: 1.5,
          color: "var(--text-secondary)",
          minHeight: 15 * 1.5,
        }}
      >
        {view.plain_summary ?? ""}
      </p>

      {/* ── (d) return path — the belief's own curve ─────────────────── */}
      {/* The curve lives in a flex-grow region and is vertically centred, so it
          fills the space between the summary and the buttons instead of leaving
          a dead band — the curve is the hero (Polymarket-style), and every card
          still bottom-aligns its Yes/No buttons regardless of title length. */}
      {curveValues.length >= 2 ? (
        <div
          style={{
            flex: 1,
            minHeight: 60,
            marginTop: 14,
            marginBottom: 14,
            display: "flex",
            alignItems: "center",
          }}
        >
          <div style={{ width: "100%" }}>
            <Sparkline values={curveValues} height={60} />
          </div>
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 12 }} />
      )}

      {/* ── (e) YES / NO — the two ways to play (or a graceful fallback) ─ */}
      {stance ? (
        <div style={{ display: "flex", gap: 10 }}>
          <StanceButton
            tone="yes"
            word="Yes"
            secondary={hasReturn ? fmtPct(be!.total_return_pct) : "See the basket"}
            secondaryColor={hasReturn ? signColor(be!.total_return_pct) : undefined}
            ariaLabel={`Yes — ${stance.yes.verdict}. Open the view.`}
            onOpen={() => onOpen(view.id, "yes")}
          />
          <StanceButton
            tone={noHasTrade ? "no" : "muted"}
            word="No"
            secondary={stance.no.verdict}
            ariaLabel={`No — ${stance.no.verdict}. Open the view.`}
            onOpen={() => onOpen(view.id, "no")}
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
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 8,
            padding: "11px 14px",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--glass-border)",
            background: "var(--bg-base)",
            cursor: "pointer",
            fontFamily: FONT,
          }}
        >
          <span
            style={{
              fontFamily: FONT,
              fontSize: 17,
              fontWeight: 700,
              color: signColor(be!.total_return_pct),
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {fmtPct(be!.total_return_pct)}
          </span>
          <span
            className="inline-flex items-center"
            style={{
              gap: 5,
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-secondary)",
            }}
          >
            View details
            <ArrowRight size={13} aria-hidden />
          </span>
        </button>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span
            style={{
              fontFamily: FONT,
              fontSize: 18,
              fontWeight: 600,
              color: "var(--text-tertiary)",
              lineHeight: 1.3,
              letterSpacing: "-0.01em",
            }}
          >
            Still developing
          </span>
          <span
            style={{
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              lineHeight: 1.4,
            }}
          >
            No finished basket yet — an idea to watch.
          </span>
        </div>
      )}

      <Hairline style={{ marginTop: 16, marginBottom: 12 }} />

      {/* ── (f) footer — trust + track record | Follow ───────────────── */}
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
                  {" · "}
                  {footerTrack(be)}
                </span>
              )}
            </>
          ) : (
            <span style={{ color: "var(--text-tertiary)" }}>Recent idea</span>
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

// ---------------------------------------------------------------------------
// footerTrack — "Positive in 24 of 32" from the own-return distribution.
// Never a benchmark. Empty string when the counts aren't available (the trust
// word then stands alone).
// ---------------------------------------------------------------------------

function footerTrack(be: BestExpressionWithPositive): string {
  if (be.n_positive != null && be.n_episodes != null && be.n_episodes > 0) {
    return `Positive in ${be.n_positive} of ${be.n_episodes}`;
  }
  if (
    be.pct_positive != null &&
    be.n_episodes != null &&
    be.n_episodes > 0
  ) {
    const wins = Math.round((be.pct_positive / 100) * be.n_episodes);
    return `Positive in ${wins} of ${be.n_episodes}`;
  }
  return "";
}
