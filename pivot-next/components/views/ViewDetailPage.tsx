"use client";

/**
 * ViewDetailPage — the opened-view detail surface, rebuilt to the canonical
 * hand-drawn reference (top → bottom):
 *
 *   1  header           "← Return to Views" back link · Follow (bare heart)
 *   2  title            view.short_title as a crisp H1 (NOT the long sentence)
 *   2.5 stance           calm YES/NO reading of the view (view.stance, optional)
 *   3  line chart       StrategyLineChart (the strategy's own return path) +
 *                       tier pills "1 · 2 · 3" selector + a "Compare +" overlay
 *   4  description       <ViewDescription/> — 2-3 plain lines + 3 bullets
 *   5  strategies table  <StrategiesTable/> — a real, roomy table w/ expand+deploy
 *   6  benchmark         <BenchmarkComparison/> — heatmap, per-holding returns,
 *                        risk:return ratio, historical alignment, fundamentals,
 *                        other risks (the old confidence folded in here)
 *   7  similar views     <SimilarViews/> — small clickable related-view cards
 *
 * REMOVED: the Timeline / lifecycle section, the standalone confidence section,
 * the standalone transmission section (its "why" now lives in the bullets).
 *
 * DESIGN LAW (v2): ROUNDED corners, BORDER-ONLY (no grey fills), plain language
 * (no jargon), >= 13px text, aligned/symmetrical.
 */

import * as React from "react";
import { ArrowLeft, AlertCircle, Plus, Info } from "lucide-react";
import { getView, deployExpression } from "@/lib/api";
import { isError } from "@/lib/types";
import type { ViewDetail, ExpressionDetail, StanceIntent } from "@/lib/types";
import { FollowButton } from "@/components/views/FollowButton";
import {
  StrategyLineChart,
  type CompareSeries,
} from "@/components/views/charts/LineChart";
import { ViewDescription } from "@/components/views/ViewDescription";
import { StrategiesTable } from "@/components/views/StrategiesTable";
import { BenchmarkComparison } from "@/components/views/BenchmarkComparison";
import { SimilarViews } from "@/components/views/SimilarViews";
import { tierLabel } from "@/components/views/view-format";

const FONT = "var(--font-display)";

// ── small local helpers ─────────────────────────────────────────────────────

function BackLink({ onBack }: { onBack: () => void }): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onBack}
      aria-label="Return to views"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontFamily: FONT,
        fontSize: 13,
        fontWeight: 500,
        color: "var(--text-tertiary)",
        background: "transparent",
        border: "none",
        padding: 0,
        cursor: "pointer",
        borderRadius: "var(--radius-sm)",
      }}
    >
      <ArrowLeft size={14} aria-hidden />
      Return to Views
    </button>
  );
}

function Body({
  children,
  color = "var(--text-secondary)",
  size = 13,
}: {
  children: React.ReactNode;
  color?: string;
  size?: number;
}): React.ReactElement {
  return (
    <p
      style={{
        fontFamily: FONT,
        fontSize: size,
        fontWeight: 400,
        color,
        lineHeight: 1.5,
        margin: 0,
      }}
    >
      {children}
    </p>
  );
}

// Skeleton + error blocks — rounded, border-only.
function SkelBlock({ h }: { h: number }): React.ReactElement {
  return (
    <div
      style={{
        height: h,
        width: "100%",
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-lg)",
      }}
    />
  );
}

function DetailSkeleton(): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <SkelBlock h={20} />
      <SkelBlock h={280} />
      <SkelBlock h={140} />
      <SkelBlock h={220} />
    </div>
  );
}

// The strategy/tier selector pill "1 · 2 · 3".
function TierPill({
  index,
  label,
  selected,
  onClick,
}: {
  index: number;
  label: string;
  selected: boolean;
  onClick: () => void;
}): React.ReactElement {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-pressed={selected}
      title={label}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        fontFamily: FONT,
        fontSize: 13,
        fontWeight: 500,
        color: selected ? "var(--text-primary)" : "var(--text-secondary)",
        background: selected
          ? "color-mix(in srgb, var(--pivot-blue) 8%, transparent)"
          : "var(--bg-base)",
        border: `1px solid ${
          selected
            ? "var(--pivot-blue)"
            : hover
              ? "var(--glass-border-hover)"
              : "var(--glass-border)"
        }`,
        borderRadius: "var(--radius-pill)",
        padding: "7px 14px",
        cursor: "pointer",
        transition: "border-color 180ms var(--ease-quartr)",
      }}
    >
      <span
        aria-hidden
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 18,
          height: 18,
          borderRadius: "var(--radius-pill)",
          fontSize: 13,
          fontWeight: 600,
          color: selected ? "#fff" : "var(--text-tertiary)",
          background: selected ? "var(--pivot-blue)" : "transparent",
          border: selected ? "none" : "1px solid var(--glass-border)",
        }}
      >
        {index}
      </span>
      <span
        style={{
          maxWidth: 180,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
    </button>
  );
}

// ── stance block (GOAL B) ───────────────────────────────────────────────────
// A calm, presentation-only YES/NO reading of the view's title question. Never
// a bet, never a contract — just a readable framing above the strategies.

function StancePill({
  label,
  accent,
}: {
  label: string;
  accent: string | null;
}): React.ReactElement {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontFamily: FONT,
        fontSize: 13,
        fontWeight: 600,
        letterSpacing: "0.02em",
        color: accent ?? "var(--text-tertiary)",
        background: accent
          ? `color-mix(in srgb, ${accent} 10%, transparent)`
          : "transparent",
        border: `1px solid ${accent ?? "var(--glass-border)"}`,
        borderRadius: "var(--radius-pill)",
        padding: "2px 10px",
        width: "fit-content",
      }}
    >
      {label}
    </span>
  );
}

function StanceCard({
  pillLabel,
  accent,
  verdict,
  summary,
  footnote,
  muted = false,
  highlighted = false,
}: {
  pillLabel: string;
  accent: string | null;
  verdict: string;
  summary: string;
  footnote?: string;
  muted?: boolean;
  /** The side a Yes/No card press picked — drawn with an accent border + a
   *  soft outline ring (no box-shadow, no fill; design-law clean). */
  highlighted?: boolean;
}): React.ReactElement {
  // A "no clean trade" (muted) side rings in muted grey, not the amber accent —
  // matching the gallery card's muted treatment so the two surfaces agree.
  const ring = muted ? "var(--text-tertiary)" : (accent ?? "var(--text-tertiary)");
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        // A muted (no-clean-trade) side gets a DASHED border so it reads as
        // categorically "not a live position" at a glance — not just a paler pill.
        border: `1px ${muted ? "dashed" : "solid"} ${
          highlighted ? ring : "var(--glass-border)"
        }`,
        borderRadius: "var(--radius-lg)",
        background: "var(--bg-base)",
        outline: highlighted
          ? `2px solid color-mix(in srgb, ${ring} 32%, transparent)`
          : undefined,
        outlineOffset: highlighted ? 2 : undefined,
        transition: "border-color 200ms var(--ease-quartr)",
        padding: 18,
        minWidth: 0,
      }}
    >
      <StancePill label={pillLabel} accent={muted ? null : accent} />
      <span
        style={{
          fontFamily: FONT,
          fontSize: 15,
          fontWeight: 600,
          color: muted ? "var(--text-secondary)" : "var(--text-primary)",
          lineHeight: 1.3,
        }}
      >
        {verdict}
      </span>
      <Body color={muted ? "var(--text-tertiary)" : "var(--text-secondary)"}>
        {summary}
      </Body>
      {footnote && (
        <Body color="var(--text-tertiary)" size={13}>
          {footnote}
        </Body>
      )}
    </div>
  );
}

function StanceBlock({
  stance,
  highlight = null,
}: {
  stance: NonNullable<ViewDetail["stance"]>;
  highlight?: StanceIntent | null;
}): React.ReactElement {
  const noHasTrade = stance.no.has_trade === true;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 13,
          fontWeight: 500,
          color: "var(--text-tertiary)",
        }}
      >
        Your call
      </span>
      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            "repeat(auto-fit, minmax(min(100%, 300px), 1fr))",
          gap: 12,
        }}
      >
        <StanceCard
          pillLabel="YES"
          accent="var(--pivot-blue)"
          verdict={stance.yes.verdict}
          summary={stance.yes.summary}
          // When the reader arrived on No, don't point them "down to the
          // strategies" from the YES card — the No note by the table carries
          // the honest framing instead.
          footnote={
            highlight === "no"
              ? undefined
              : "Expressed by the strategies below ↓"
          }
          highlighted={highlight === "yes"}
        />
        <StanceCard
          pillLabel="NO"
          accent="var(--color-warn)"
          verdict={stance.no.verdict}
          summary={stance.no.summary}
          muted={!noHasTrade}
          highlighted={highlight === "no"}
          footnote={
            noHasTrade
              ? undefined
              : "Nothing to arm — sitting this one out is the call."
          }
        />
      </div>
    </div>
  );
}

// ── default expression pick ─────────────────────────────────────────────────
// Lead with the SAME headline strategy the gallery card features (best_expression),
// so the card number and the detail chart agree. Fall back to the conservative
// (primary) tier, then to the highest-returning one with a real number.
function pickDefault(
  exprs: ExpressionDetail[],
  headlineId?: string | null,
): ExpressionDetail | null {
  if (exprs.length === 0) return null;
  if (headlineId) {
    const m = exprs.find((e) => e.id === headlineId);
    if (m) return m;
  }
  const cons = exprs.find(
    (e) => e.tier === "conservative" && e.strategy_total_pct != null,
  );
  if (cons) return cons;
  return [...exprs].sort(
    (a, b) => (b.strategy_total_pct ?? -Infinity) - (a.strategy_total_pct ?? -Infinity),
  )[0]!;
}

interface ViewDetailPageProps {
  viewId: string;
  onBack: () => void;
  onOpenWorkflowById: (workflowId: string) => void;
  /**
   * Static detail to render instead of fetching from /api/views/{id}. Used by
   * the standalone /view-pack showcase to render curated views through this
   * exact component. When set, the fetch is skipped entirely.
   */
  detailOverride?: ViewDetail | null;
  /**
   * Which Yes/No side the gallery card press intended. When set (and the view
   * carries a stance), the page scrolls to + highlights that side of the "Your
   * call" block on open — the deployment/strategy link the Yes/No buttons
   * promise. Null when opened via the card body (plain overview).
   */
  initialStance?: StanceIntent | null;
}

export function ViewDetailPage({
  viewId,
  onBack,
  onOpenWorkflowById,
  detailOverride = null,
  initialStance = null,
}: ViewDetailPageProps): React.ReactElement {
  // Internal navigation id — lets "Similar views" open a sibling without the
  // parent re-keying us. Resets whenever the parent hands a new viewId.
  const [currentId, setCurrentId] = React.useState(viewId);
  React.useEffect(() => setCurrentId(viewId), [viewId]);

  const [view, setView] = React.useState<ViewDetail | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [followState, setFollowState] = React.useState<{
    is_following: boolean;
    follower_count: number;
  } | null>(null);

  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [compareOn, setCompareOn] = React.useState(false);
  const [deployingId, setDeployingId] = React.useState<string | null>(null);
  const [deployError, setDeployError] = React.useState<string | null>(null);

  const load = React.useCallback(() => {
    setLoading(true);
    setError(null);
    getView(currentId).then((res) => {
      if (isError(res)) {
        setError(res.error.message);
        setLoading(false);
        return;
      }
      setView(res.data);
      setFollowState({
        is_following: res.data.is_following,
        follower_count: res.data.follower_count,
      });
      setSelectedId(
        pickDefault(
          res.data.expressions ?? [],
          res.data.best_expression?.id ?? null,
        )?.id ?? null,
      );
      setLoading(false);
    });
  }, [currentId]);

  React.useEffect(() => {
    let cancelled = false;
    setView(null);
    setFollowState(null);
    setSelectedId(null);
    setCompareOn(false);
    setDeployError(null);
    setDeployingId(null);
    if (detailOverride) {
      setView(detailOverride);
      setFollowState({
        is_following: detailOverride.is_following,
        follower_count: detailOverride.follower_count,
      });
      setSelectedId(
        pickDefault(
          detailOverride.expressions ?? [],
          detailOverride.best_expression?.id ?? null,
        )?.id ?? null,
      );
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }
    setLoading(true);
    setError(null);
    getView(currentId).then((res) => {
      if (cancelled) return;
      if (isError(res)) {
        setError(res.error.message);
        setLoading(false);
        return;
      }
      setView(res.data);
      setFollowState({
        is_following: res.data.is_following,
        follower_count: res.data.follower_count,
      });
      setSelectedId(
        pickDefault(
          res.data.expressions ?? [],
          res.data.best_expression?.id ?? null,
        )?.id ?? null,
      );
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [currentId, detailOverride]);

  // Esc → back to the gallery.
  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onBack();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

  function openSibling(id: string) {
    setCurrentId(id);
    if (typeof window !== "undefined")
      window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // When the view was opened by a Yes/No card press, bring the "Your call"
  // block into view once it has painted — the promise the buttons make ("open
  // the view on this side"). The highlight (passed to StanceBlock) draws the
  // eye to the chosen side; from there the strategies/deploy table is one
  // glance down. No-op when opened via the card body (initialStance null).
  const stanceRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    if (!initialStance || loading || !view?.stance) return;
    const el = stanceRef.current;
    if (!el || typeof window === "undefined") return;
    const t = window.setTimeout(() => {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 140);
    return () => window.clearTimeout(t);
  }, [initialStance, loading, view?.stance, currentId]);

  const exprs = view?.expressions ?? [];
  const selectedExpr =
    exprs.find((e) => e.id === selectedId) ?? exprs[0] ?? null;

  async function handleDeploy(expr: ExpressionDetail) {
    if (deployingId) return;
    setDeployError(null);
    if (expr.workflow_id) {
      onOpenWorkflowById(expr.workflow_id);
      return;
    }
    setDeployingId(expr.id);
    const res = await deployExpression(expr.id);
    setDeployingId(null);
    if (isError(res)) {
      setDeployError(res.error.message);
      return;
    }
    onOpenWorkflowById(res.data.workflow_id);
  }

  // Overlay the OTHER tiers' curves when "Compare +" is on.
  const compareSeries: CompareSeries[] =
    compareOn && selectedExpr
      ? exprs
          .filter((e) => e.id !== selectedExpr.id)
          .map((e) => ({
            label: e.strategy_name ?? tierLabel(e.tier),
            series: e.equity_curve ?? [],
          }))
          .filter((cs) => cs.series.length >= 2)
      : [];

  return (
    <div
      style={{
        // Full-width content — fills the available area like a chat response
        // (the page padding around this surface supplies the side breathing room).
        width: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 28,
      }}
    >
      {loading && (
        <>
          <BackLink onBack={onBack} />
          <DetailSkeleton />
        </>
      )}

      {!loading && error && (
        <>
          <BackLink onBack={onBack} />
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "16px 20px",
              border: "1px solid var(--glass-border)",
              borderRadius: "var(--radius-lg)",
              background: "var(--bg-base)",
            }}
          >
            <AlertCircle
              size={16}
              aria-hidden
              style={{ color: "var(--color-loss)", flexShrink: 0 }}
            />
            <Body color="var(--color-loss)" size={14}>
              {error}
            </Body>
            <button
              type="button"
              onClick={load}
              style={{
                marginLeft: "auto",
                fontFamily: FONT,
                fontSize: 13,
                fontWeight: 500,
                color: "var(--text-secondary)",
                background: "var(--bg-base)",
                border: "1px solid var(--glass-border)",
                borderRadius: "var(--radius-md)",
                padding: "8px 14px",
                cursor: "pointer",
              }}
            >
              Retry
            </button>
          </div>
        </>
      )}

      {!loading && !error && view && (
        <>
          {/* ── 1 · HEADER ── */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 12,
            }}
          >
            <BackLink onBack={onBack} />
            {followState && (
              <FollowButton
                viewId={view.id}
                isFollowing={followState.is_following}
                followerCount={followState.follower_count}
                size="md"
                onChange={(next) => setFollowState(next)}
              />
            )}
          </div>

          {/* ── 2 · TITLE (crisp short_title) ── */}
          <h1
            style={{
              fontFamily: FONT,
              fontSize: 30,
              fontWeight: 600,
              lineHeight: 1.2,
              letterSpacing: "-0.02em",
              color: "var(--text-primary)",
              margin: 0,
            }}
          >
            {view.short_title ?? view.plain_one_liner ?? "—"}
          </h1>

          {/* ── 2.5 · STANCE (YES / NO reading) ── */}
          {view.stance && (
            <div ref={stanceRef}>
              <StanceBlock stance={view.stance} highlight={initialStance} />
            </div>
          )}

          {/* ── 3 · LINE CHART + tier selector + Compare ── */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <StrategyLineChart
              series={selectedExpr?.equity_curve ?? []}
              compareSeries={compareSeries}
              strategyLabel={
                selectedExpr?.strategy_name ??
                (selectedExpr ? tierLabel(selectedExpr.tier) : "Strategy")
              }
              episodeBoundaries={selectedExpr?.episode_boundaries ?? []}
              height={260}
            />

            {exprs.length > 0 && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: 8,
                }}
              >
                {exprs.map((e, i) => (
                  <TierPill
                    key={e.id}
                    index={i + 1}
                    label={e.strategy_name ?? tierLabel(e.tier)}
                    selected={selectedExpr?.id === e.id}
                    onClick={() => {
                      setSelectedId(e.id);
                    }}
                  />
                ))}

                {exprs.length > 1 && (
                  <button
                    type="button"
                    onClick={() => setCompareOn((v) => !v)}
                    aria-pressed={compareOn}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 5,
                      fontFamily: FONT,
                      fontSize: 13,
                      fontWeight: 500,
                      color: compareOn
                        ? "var(--text-primary)"
                        : "var(--text-secondary)",
                      background: "var(--bg-base)",
                      border: `1px solid ${
                        compareOn
                          ? "var(--glass-border-focus)"
                          : "var(--glass-border)"
                      }`,
                      borderRadius: "var(--radius-pill)",
                      padding: "7px 14px",
                      cursor: "pointer",
                      transition: "border-color 180ms var(--ease-quartr)",
                    }}
                  >
                    <Plus
                      size={13}
                      aria-hidden
                      style={{
                        transform: compareOn ? "rotate(45deg)" : "none",
                        transition: "transform 180ms var(--ease-quartr)",
                      }}
                    />
                    {compareOn ? "Comparing" : "Compare"}
                  </button>
                )}
              </div>
            )}

            <Body color="var(--text-tertiary)" size={13}>
              {(selectedExpr?.equity_curve?.length ?? 0) >= 2 ? (
                <>
                  The average single occurrence, across{" "}
                  {selectedExpr?.curve_n_episodes ??
                    selectedExpr?.n_episodes ??
                    0}{" "}
                  past occurrences — the typical return while deployed, not added
                  up across occurrences.{" "}
                  {selectedExpr?.strategy_name ?? "Strategy"}, ₹1,00,000
                  invested per occurrence ·{" "}
                  {selectedExpr?.trust_badge ?? "Unproven"} — this is
                  analysis, not financial advice.
                </>
              ) : (
                <>
                  This view is still developing — no deployable basket yet, so
                  there is no return path to show. This is analysis, not
                  financial advice.
                </>
              )}
            </Body>
          </div>

          {/* ── 4 · DESCRIPTION ── */}
          <ViewDescription
            description={view.description}
            bullets={view.bullets}
            caveat={view.caveat}
          />

          {/* ── 5 · STRATEGIES TABLE ── */}
          {exprs.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <h2
                style={{
                  fontFamily: FONT,
                  fontSize: 18,
                  fontWeight: 600,
                  color: "var(--text-primary)",
                  lineHeight: 1.3,
                  letterSpacing: "-0.01em",
                  margin: 0,
                }}
              >
                Strategies
              </h2>
              {/* No follow-through: our curated views author only the YES-side
                  expressions (the basket/option tiers). When the reader came in
                  on No, say so plainly — the strategies express Yes; No means
                  sit in the index (or, for an asymmetric event, no clean trade).
                  This closes the gap where a No-picker could deploy the Yes
                  bundle thinking it was "their" pick. */}
              {initialStance === "no" && view.stance && (
                <div
                  style={{
                    display: "flex",
                    gap: 10,
                    padding: "12px 14px",
                    border: "1px dashed var(--glass-border)",
                    borderRadius: "var(--radius-lg)",
                    background: "var(--bg-base)",
                  }}
                >
                  <Info
                    size={15}
                    aria-hidden
                    style={{
                      color: "var(--text-tertiary)",
                      flexShrink: 0,
                      marginTop: 2,
                    }}
                  />
                  <Body color="var(--text-secondary)" size={13}>
                    You leaned{" "}
                    <strong style={{ color: "var(--text-primary)", fontWeight: 600 }}>
                      No
                    </strong>{" "}
                    — {view.stance.no.verdict}.{" "}
                    {view.stance.no.has_trade
                      ? "That's the sit-in-the-index default — there's nothing here to arm. The strategies below express the Yes case."
                      : "There's no clean position to arm here — sitting it out is the call. The strategies below express the Yes case."}
                  </Body>
                </div>
              )}
              <StrategiesTable
                expressions={exprs}
                selectedId={selectedExpr?.id ?? null}
                onSelect={(id) => setSelectedId(id)}
                onDeploy={handleDeploy}
                deployingId={deployingId}
                deployError={deployError}
              />
            </div>
          )}

          {/* ── 6 · BENCHMARK COMPARISON ── */}
          <BenchmarkComparison view={view} expr={selectedExpr} />

          {/* ── 7 · SIMILAR VIEWS ── */}
          <SimilarViews items={view.similar_views} onOpen={openSibling} />
        </>
      )}
    </div>
  );
}
