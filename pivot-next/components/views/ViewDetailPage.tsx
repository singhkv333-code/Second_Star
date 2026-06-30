"use client";

/**
 * ViewDetailPage — the opened-view detail surface, rebuilt to the canonical
 * hand-drawn reference (top → bottom):
 *
 *   1  header           "← Return to Views" back link · Follow (bare heart)
 *   2  title            view.short_title as a crisp H1 (NOT the long sentence)
 *   3  line chart       StrategyLineChart (strategy vs Nifty) + tier pills
 *                       "1 · 2 · 3" selector + a "Compare +" overlay toggle
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
import { ArrowLeft, AlertCircle, Plus } from "lucide-react";
import { getView, deployExpression } from "@/lib/api";
import { isError } from "@/lib/types";
import type { ViewDetail, ExpressionDetail } from "@/lib/types";
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
}

export function ViewDetailPage({
  viewId,
  onBack,
  onOpenWorkflowById,
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
  }, [currentId]);

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

          {/* ── 3 · LINE CHART + tier selector + Compare ── */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <StrategyLineChart
              series={selectedExpr?.equity_curve ?? []}
              compareSeries={compareSeries}
              benchmarkLabel={view.benchmark_label ?? "Nifty 50"}
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
                  Return path while deployed ·{" "}
                  {selectedExpr?.curve_n_episodes ??
                    selectedExpr?.n_episodes ??
                    0}{" "}
                  episodes (only the days the strategy is in the market).{" "}
                  {selectedExpr?.strategy_name ?? "Strategy"} vs{" "}
                  {view.benchmark_label ?? "Nifty 50"}, ₹1,00,000 invested ·{" "}
                  {selectedExpr?.trust_badge ?? "Unproven"} — this is analysis,
                  not financial advice.
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
