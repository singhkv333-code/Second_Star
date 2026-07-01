"use client";

/**
 * ViewCard — one rounded, border-only gallery card for a ViewSummary.
 *
 * DESIGN LAW (see ViewSurface): ROUNDED corners (radii, never square),
 * borders-only (no fills), no jargon on screen (every label routed through a
 * view-format humanizer), one hero number per card, >=13px floor. Fixed height
 * so every column lines up.
 *
 * Layout (left-aligned vertical stack, padding 20):
 *   (a) meta row      : category eyebrow            | status dot + word
 *   (b) TITLE          (short_title)                18/600, 2-line clamp
 *   (c) layman summary  (plain_summary)             15/400, 1-line clamp
 *   ─── hairline ───
 *   (e) HERO block    : avg return / occurrence (30/600)      | mini line
 *   (f) quiet line    : positive-outcome hit rate · worst drop · trust word
 *   ─── hairline ───
 *   (h) footer        : "View →"                    | FollowButton (bare heart)
 *
 * No benchmark comparison anywhere on this card (design law — the strategy's
 * own return + risk context is the only performance number a user sees). The
 * mini line chart traces the best/highest-returning strategy's own equity
 * curve. Whole card is the click target → onOpen(view.id).
 */

import * as React from "react";
import type { ViewSummary } from "@/lib/types";
import { ViewSurface, Hairline } from "./ViewSurface";
import { Num } from "./Stat";
import { MiniLine } from "./charts/MiniLine";
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

const CARD_HEIGHT = 360;

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
// ViewCard
// ---------------------------------------------------------------------------

type ViewCardProps = {
  view: ViewSummary;
  onOpen: (id: string) => void;
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
  const [viewHover, setViewHover] = React.useState(false);

  const handleKey = (e: React.KeyboardEvent<HTMLElement>): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen(view.id);
    }
  };

  const be = view.best_expression;
  const hasReturn = be != null && be.total_return_pct != null;
  const curve = be?.equity_curve;
  const hasCurve = Array.isArray(curve) && curve.length >= 2;
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
            fontFamily: "var(--font-display)",
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
            fontFamily: "var(--font-display)",
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

      {/* ── (b) TITLE — the crisp 7-8 word liner ─────────────────────── */}
      <div
        className="line-clamp-2"
        style={{
          marginTop: 14,
          fontFamily: "var(--font-display)",
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
          fontFamily: "var(--font-display)",
          fontSize: 15,
          fontWeight: 400,
          lineHeight: 1.5,
          color: "var(--text-secondary)",
          minHeight: 15 * 1.5,
        }}
      >
        {view.plain_summary ?? ""}
      </p>

      <Hairline style={{ marginTop: 16, marginBottom: 16 }} />

      {hasReturn ? (
        <>
          {/* ── (e) HERO block — the strategy's own avg return | mini line ── */}
          <div className="flex items-end justify-between gap-3">
            <div className="flex flex-col" style={{ gap: 4, minWidth: 0 }}>
              <Num size="hero" weight={600} color={signColor(be!.total_return_pct)}>
                {fmtPct(be!.total_return_pct)}
              </Num>
              <span
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "var(--text-tertiary)",
                  lineHeight: 1.3,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {be!.n_episodes != null
                  ? `Avg over ${be!.n_episodes} occurrences`
                  : "Avg per occurrence"}
              </span>
            </div>
            {hasCurve && (
              <div className="shrink-0" style={{ alignSelf: "center" }}>
                <MiniLine series={curve} width={108} height={40} />
              </div>
            )}
          </div>

          {/* ── (f) quiet line — stats, then trust on its own line ─────── */}
          <div
            style={{
              marginTop: 12,
              display: "flex",
              flexDirection: "column",
              gap: 5,
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fontWeight: 500,
                color: "var(--text-secondary)",
                lineHeight: 1.3,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {statLine(be!)}
            </span>
            <span
              className="inline-flex items-center gap-1.5"
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fontWeight: 500,
                color: verdictColor(be!.trust_verdict),
                lineHeight: 1.3,
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: verdictColor(be!.trust_verdict),
                  flexShrink: 0,
                }}
              />
              {trustBadge(be!.trust_verdict)}
            </span>
          </div>
        </>
      ) : (
        /* ── (e′) developing state — no stray dash, no fabricated number ── */
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span
            style={{
              fontFamily: "var(--font-display)",
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
              fontFamily: "var(--font-display)",
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

      {/* spacer pushes footer to the bottom for column alignment */}
      <div style={{ flex: 1 }} />

      <Hairline style={{ marginBottom: 12 }} />

      {/* ── (h) footer ───────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-3">
        <span
          onMouseEnter={() => setViewHover(true)}
          onMouseLeave={() => setViewHover(false)}
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            fontWeight: 600,
            color: viewHover ? "var(--pivot-blue)" : "var(--text-secondary)",
            transition: "color 180ms var(--ease-quartr)",
            whiteSpace: "nowrap",
          }}
        >
          View →
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
// positiveHitLabel — "Positive in 3 of 4 occurrences"
// Local copy of the contract helper being added to view-format.ts (same
// name/signature: pctPositive, nEpisodes → sentence). Kept local so this file
// type-checks and behaves correctly regardless of when that lands; safe to
// swap for the shared import once it exists. NEVER references a benchmark.
// ---------------------------------------------------------------------------

function positiveHitLabel(
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
  const positive = Math.round((pctPositive / 100) * nEpisodes);
  return `Positive in ${positive} of ${nEpisodes} occurrences`;
}

// ---------------------------------------------------------------------------
// statLine — "Positive in 3 of 4 occurrences · Worst drop −12%"
// Positive-outcome rate (own returns, never a benchmark) + worst-drop only;
// the trust verdict renders on its own line so the row never truncates
// mid-word. Drops any part that has no data; falls back to "Recent idea" when
// nothing at all is available.
// ---------------------------------------------------------------------------

function statLine(be: NonNullable<ViewSummary["best_expression"]>): string {
  const bx = be as BestExpressionWithPositive;
  const parts: string[] = [];

  if (bx.n_positive != null && be.n_episodes != null && be.n_episodes > 0) {
    parts.push(`Positive in ${bx.n_positive} of ${be.n_episodes} occurrences`);
  } else if (bx.pct_positive != null && be.n_episodes != null && be.n_episodes > 0) {
    parts.push(positiveHitLabel(bx.pct_positive, be.n_episodes));
  }

  if (be.worst_drop_pct != null) {
    parts.push(`Worst drop ${fmtPct(be.worst_drop_pct, 0)}`);
  }

  return parts.length > 0 ? parts.join(" · ") : "Recent idea";
}
