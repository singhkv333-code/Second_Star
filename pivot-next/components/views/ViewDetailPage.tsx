"use client";

/**
 * ViewDetailPage — the opened-view detail surface, in the Kalshi-clean
 * /view-detail layout wired to REAL data (top → bottom):
 *
 *   1  header           "← Return to Views" back link · Follow (bare heart)
 *   2  title            short_title H1 + subtitle + hairline market-meta strip
 *                       (Type · Horizon · Resolves · Status)
 *   3  HERO             two-column: <ExpressionReturnsChart/> floating on the
 *                       page (every expression's real curve + real benchmark,
 *                       rescaled to the ticket amount) | <ExpressionTicket/>
 *                       sticky trade ticket (amount → projection per strategy,
 *                       REAL deploy). The ticket is the only card up here.
 *   4  strategies        <StrategiesEditorial/> — table + explanation panel
 *   5  description       <ViewDescription/> — bullets + caveat ("What this is")
 *   6  THE DETAIL BLOCK (very bottom, behind a strong divider):
 *      <BenchmarkComparison/> ("How this strategy behaves") + <SimilarViews/>
 *
 * DESIGN LAW (v2): ROUNDED corners, BORDER-ONLY (no grey fills), hairline
 * section rhythm, color is for data, plain language, >= 13px text.
 */

import * as React from "react";
import { ArrowLeft, AlertCircle, Info } from "lucide-react";
import { categoryLabel } from "@/components/views/view-format";
import { ShareButton } from "./ShareButton";
import { CategoryGlyph } from "@/components/views/ViewCard";
import { getView, type BasketPlaceResponse } from "@/lib/api";
import { isError } from "@/lib/types";
import { toast } from "sonner";
import {
  DeployConfirmModal,
  skipReasonText,
} from "@/components/views/DeployConfirmModal";
import type {
  ViewDetail,
  ExpressionDetail,
  StanceIntent,
  ViewType,
  ViewStatus,
} from "@/lib/types";
import {
  ExpressionReturnsChart,
  ExpressionTicket,
  exprName,
} from "@/components/views/ExpressionHero";
import { StrategiesEditorial } from "@/components/views/StrategiesEditorial";
import {
  isEditableBasket,
  type BasketEdit,
  type PriceMap,
} from "@/components/views/basket";
import { StrategyDeepDive } from "@/components/views/StrategyDeepDive";
import { SimilarViews } from "@/components/views/SimilarViews";

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

// ── header market-meta strip ────────────────────────────────────────────────
// A hairline-separated row of the view's honest facts (Type · Horizon ·
// Resolves · Status), reading like a real market header. Every value comes
// straight from ViewDetail — nothing fabricated; null fields are dropped.

const META_MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** "The three strategies" section heading, honest about the actual count. */
function countWord(n: number, baskets = false): string {
  const words = ["", "one", "two", "three", "four", "five", "six"];
  const w = words[n];
  const many = baskets ? "baskets" : "strategies";
  if (n === 1) return baskets ? "The basket" : "The strategy";
  return w ? `The ${w} ${many}` : `The ${n} ${many}`;
}

/**
 * Views served as *editable baskets*: the holdings ship with pre-decided
 * weights, and the reader sets their own quantities. The section reads
 * "baskets" throughout and non-basket tiers (options structures) are dropped.
 * Rolling out per view — add ids here as each view's constituent data is
 * confirmed.
 */
const BASKET_VIEW_IDS = new Set<string>(["monsoon", "renewable", "gold"]);

/** Title-case a lowercase token as an honest fallback for unmapped values. */
function titleCase(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

// NOTE: the wire values are lowercase ("event"/"theme"/"relative", "open"…)
// and richer than the stale TS unions (ViewType claims EVENT|THEME only, and
// ViewStatus has no "open"). Both formatters normalise casing and fall back to
// a title-cased raw value so a new/unmapped value never gets mislabelled.
function fmtViewType(t: ViewType): string {
  const k = String(t).toLowerCase();
  if (k === "event") return "Event view";
  if (k === "relative") return "Relative view";
  if (k === "theme") return "Theme view";
  return `${titleCase(k)} view`;
}

/** ISO date → "31 Dec 2026" (UTC so the day never shifts across timezones). */
function fmtResolutionDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getUTCDate()} ${META_MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** Lifecycle status → plain label; `accent` lights the live states (Open). */
function fmtStatus(s: ViewStatus): { label: string; accent: boolean } {
  switch (String(s).toLowerCase()) {
    case "open":
    case "published":
      return { label: "Open", accent: false };
    case "developing":
      return { label: "Developing", accent: false };
    case "consensus":
      return { label: "Consensus", accent: false };
    case "resolved":
      return { label: "Resolved", accent: false };
    case "archived":
      return { label: "Archived", accent: false };
    case "draft":
      return { label: "Draft", accent: false };
    default:
      return { label: titleCase(String(s)), accent: false };
  }
}

interface MetaItem {
  label: string;
  value: string;
  accent?: boolean;
}

/** Build the strip's items from a view, dropping any null/empty facts. */
function buildMetaItems(view: ViewDetail): MetaItem[] {
  const items: MetaItem[] = [{ label: "Type", value: fmtViewType(view.view_type) }];
  if (view.time_horizon) items.push({ label: "Horizon", value: view.time_horizon });
  const resolves = fmtResolutionDate(view.resolution_date);
  if (resolves) items.push({ label: "Resolves", value: resolves });
  const st = fmtStatus(view.status);
  items.push({ label: "Status", value: st.label, accent: st.accent });
  return items;
}

/** One fact in the meta strip, split from the previous by a vertical hairline. */
function MetaStat({
  label,
  value,
  first,
  accent,
}: {
  label: string;
  value: string;
  first: boolean;
  accent?: boolean;
}): React.ReactElement {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 3,
        padding: first ? "0 18px 0 0" : "0 18px",
        borderLeft: first ? "none" : "1px solid var(--glass-border)",
      }}
    >
      <span
        style={{
          fontFamily: FONT,
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          color: "var(--text-tertiary)",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: FONT,
          fontVariantNumeric: "tabular-nums",
          fontSize: 14,
          fontWeight: 600,
          color: accent ? "var(--pivot-blue)" : "var(--text-primary)",
          whiteSpace: "nowrap",
        }}
      >
        {value}
      </span>
    </div>
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
  // onOpenWorkflowById is no longer used here — Deploy places immediately via
  // the confirm modal instead of arming a workflow — but the prop stays on the
  // interface so the parent contract (ViewsTab) is unchanged.
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
  const [, setFollowState] = React.useState<{
    is_following: boolean;
    follower_count: number;
  } | null>(null);

  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  // ₹ amount typed into the trade ticket — shared with the chart so both
  // rescale together (the /view-detail single-source-of-truth pattern).
  const [amount, setAmount] = React.useState<number>(100_000);
  const [deployingId, setDeployingId] = React.useState<string | null>(null);
  const [deployError, setDeployError] = React.useState<string | null>(null);
  const [deepDiveOpen, setDeepDiveOpen] = React.useState(false);
  // The expression whose deploy confirmation modal is open (null = closed).
  const [placeExpr, setPlaceExpr] = React.useState<ExpressionDetail | null>(
    null,
  );

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
    setDeployError(null);
    setDeployingId(null);
    setDeepDiveOpen(false);
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

  // Baskets mode + the reader's per-basket edits. Held here (not in the card)
  // because the calculator in the hero prices whatever basket they land on.
  const basketMode = view != null && BASKET_VIEW_IDS.has(view.id);

  // In baskets mode the section is baskets and nothing else: tiers with no
  // weighted constituents (an options structure has none) are dropped, so the
  // chart, the calculator, the cards and the heading count all agree.
  const allExprs = view?.expressions ?? [];
  const exprs = basketMode ? allExprs.filter(isEditableBasket) : allExprs;
  const selectedExpr =
    exprs.find((e) => e.id === selectedId) ?? exprs[0] ?? null;

  const [basketEdits, setBasketEdits] = React.useState<Record<string, BasketEdit>>({});
  // Live prices, reported up by the holding rows. Held here so the cards and
  // the calculator price the same basket the same way.
  const [basketPrices, setBasketPrices] = React.useState<Record<string, PriceMap>>({});
  // Edits belong to the view being read — drop them when the reader moves on.
  React.useEffect(() => {
    setBasketEdits({});
    setBasketPrices({});
  }, [view?.id]);

  // Bails out when the price hasn't moved: rows report on every quote tick, and
  // returning the same state object stops React re-rendering over a no-op.
  const handlePrice = React.useCallback(
    (exprId: string, key: string, price: number) => {
      setBasketPrices((prev) => {
        if (prev[exprId]?.[key] === price) return prev;
        return { ...prev, [exprId]: { ...(prev[exprId] ?? {}), [key]: price } };
      });
    },
    [],
  );

  // Deploy = execute now (paper). Opens the confirm modal, which previews the
  // exact whole-share/unit basket and — on confirm — places it into the paper
  // book. No workflow/agent is created; a strategy that can't be placed shows
  // the exact reason in the modal (handled by DeployConfirmModal).
  function handleDeploy(expr: ExpressionDetail) {
    setDeployError(null);
    setPlaceExpr(expr);
  }

  function handlePlaced(res: BasketPlaceResponse): void {
    const n = res.count;
    const where = res.routed_to === "broker" ? "your broker" : "your paper book";
    const skipNote =
      res.skipped.length > 0
        ? "Skipped " +
          res.skipped
            .map((s) => `${s.symbol} (${skipReasonText(s.status)})`)
            .join(", ") +
          ". "
        : "";
    toast.success(`Placed ${n} ${n === 1 ? "leg" : "legs"} into ${where}.`, {
      description: skipNote + "Track it in Portfolio → My Views.",
    });
  }

  // The full statistical deep-dive (StrategyDeepDive) is rendered INLINE as an
  // accordion that expands directly below the strategies block — the table,
  // explanation panel, belief header and chart all stay on screen; nothing is
  // swapped out and the page does not jump. It's bound to selectedExpr, so the
  // open analysis always tracks whichever strategy is selected, and it stays
  // mounted (hidden by the collapsed accordion) so open AND close both animate.

  return (
    <div
      style={{
        // Capped + centered so wide screens get real side gutters before the
        // content begins (on top of the pane's own padding), rather than the
        // chart + ticket stretching nearly edge-to-edge.
        width: "100%",
        maxWidth: 1360,
        marginInline: "auto",
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
            <ShareButton ariaLabel="Share this view" />
          </div>

          {/* ── 2+3 · GRID: (title + chart) | sticky trade ticket ──
             The title lives in the LEFT column so the ticket (right column)
             top-aligns with the category thumbnail at the top of the title. */}
          <style>{`
            .vwd-hero { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 44px; align-items: start; }
            .vwd-left { display: flex; flex-direction: column; gap: 28px; min-width: 0; }
            .vwd-sticky { position: sticky; top: 20px; }
            .vwd-accordion { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 340ms var(--ease-quartr); }
            .vwd-accordion.open { grid-template-rows: 1fr; }
            .vwd-accordion > .vwd-accordion-inner { overflow: hidden; min-height: 0; opacity: 0; transition: opacity 260ms var(--ease-quartr); }
            .vwd-accordion.open > .vwd-accordion-inner { opacity: 1; }
            @media (prefers-reduced-motion: reduce) {
              .vwd-accordion { transition: none; }
              .vwd-accordion > .vwd-accordion-inner { transition: none; }
            }
            @media (max-width: 940px) {
              .vwd-hero { grid-template-columns: 1fr; gap: 24px; }
              .vwd-sticky { position: static; }
            }
          `}</style>
          <div className="vwd-hero">
            <div className="vwd-left">
              {/* title + eyebrow + meta strip */}
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <div
                  style={{ display: "flex", alignItems: "flex-start", gap: 16 }}
                >
              <CategoryGlyph category={view.category} seed={view.id} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <span
                  style={{
                    display: "block",
                    fontFamily: FONT,
                    fontSize: 12,
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                    color: "var(--text-tertiary)",
                  }}
                >
                  {categoryLabel(view.category)}
                </span>
                <h1
                  style={{
                    fontFamily: FONT,
                    fontSize: 30,
                    fontWeight: 600,
                    lineHeight: 1.2,
                    letterSpacing: "-0.02em",
                    color: "var(--text-primary)",
                    margin: "6px 0 0",
                  }}
                >
                  {view.short_title ?? view.plain_one_liner ?? "—"}
                </h1>
              </div>
            </div>
            {(view.description ?? view.plain_summary) && (
              <p
                style={{
                  margin: 0,
                  maxWidth: 760,
                  fontFamily: FONT,
                  fontSize: 15,
                  lineHeight: 1.55,
                  color: "var(--text-secondary)",
                }}
              >
                {view.description ?? view.plain_summary}
              </p>
            )}
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "8px 0",
                paddingTop: 14,
                borderTop: "1px solid var(--glass-border)",
              }}
            >
              {buildMetaItems(view).map((m, i) => (
                <MetaStat
                  key={m.label}
                  label={m.label}
                  value={m.value}
                  first={i === 0}
                  accent={m.accent}
                />
              ))}
            </div>
              </div>

              <ExpressionReturnsChart
              expressions={exprs}
              selectedId={selectedExpr?.id ?? null}
              amount={amount}
              benchmarkLabel={view.benchmark_label}
              caption={
                (selectedExpr?.equity_curve?.length ?? 0) >= 2 ? (
                  <>
                    The average single occurrence, across{" "}
                    {selectedExpr?.curve_n_episodes ??
                      selectedExpr?.n_episodes ??
                      0}{" "}
                    past occurrences — the typical return while deployed, not
                    added up across occurrences.{" "}
                    {selectedExpr ? exprName(selectedExpr) : "Strategy"},{" "}
                    ₹{amount.toLocaleString("en-IN")} invested per occurrence ·{" "}
                    {selectedExpr?.trust_badge ?? "Unproven"} — this is
                    analysis, not financial advice.
                  </>
                ) : (
                  <>
                    This view is still developing — no deployable basket yet,
                    so there is no return path to show. This is analysis, not
                    financial advice.
                  </>
                )
              }
            />
            </div>
            {exprs.length > 0 && (
              <div className="vwd-sticky">
                <ExpressionTicket
                  expressions={exprs}
                  selectedId={selectedExpr?.id ?? null}
                  onSelect={(id) => setSelectedId(id)}
                  amount={amount}
                  onAmount={setAmount}
                  onDeploy={handleDeploy}
                  deployingId={deployingId}
                  deployError={deployError}
                  basketMode={basketMode}
                  edits={basketEdits}
                  prices={basketPrices}
                />
              </div>
            )}
          </div>

          {/* ── 4 · THE STRATEGIES (editorial table + explanation panel) ── */}
          {exprs.length > 0 && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 18,
                borderTop: "1px solid var(--glass-border)",
                paddingTop: 28,
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <h2
                  style={{
                    fontFamily: FONT,
                    fontSize: 17,
                    fontWeight: 650,
                    color: "var(--text-primary)",
                    lineHeight: 1.3,
                    letterSpacing: "-0.01em",
                    margin: 0,
                  }}
                >
                  {countWord(exprs.length, basketMode)}
                </h2>
              </div>
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
              <StrategiesEditorial
                expressions={exprs}
                amount={amount}
                openAnalysisId={deepDiveOpen ? (selectedExpr?.id ?? null) : null}
                onToggleAnalysis={(id) => {
                  if (id === selectedExpr?.id) {
                    setDeepDiveOpen((v) => !v);
                  } else {
                    setSelectedId(id);
                    setDeepDiveOpen(true);
                  }
                }}
                basketMode={basketMode}
                edits={basketEdits}
                onEdit={(id, next) =>
                  setBasketEdits((prev) => ({ ...prev, [id]: next }))
                }
                prices={basketPrices}
                onPrice={handlePrice}
              />

              {/* Inline accordion: the full analysis expands directly below the
                  strategies, tracking the selected strategy. Nothing is swapped
                  out and the page never jumps. */}
              <div
                className={`vwd-accordion${deepDiveOpen ? " open" : ""}`}
                aria-hidden={!deepDiveOpen}
              >
                <div className="vwd-accordion-inner">
                  {selectedExpr && (
                    <div
                      style={{
                        borderTop: "1px solid var(--glass-border)",
                        paddingTop: 24,
                        marginTop: 4,
                      }}
                    >
                      <StrategyDeepDive
                        expression={selectedExpr}
                        viewTitle={view?.short_title ?? view?.title ?? null}
                        onBack={() => setDeepDiveOpen(false)}
                        showBackLink={false}
                      />
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* ── 6 · THE DETAIL BLOCK (kept at the very bottom) ──
              "How this strategy behaves" + everything beneath it lives here,
              set apart by a strong divider so the top of the page stays the
              clean, approachable surface and the dense analytics are a
              deliberate scroll away. */}
          <section
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 28,
              borderTop: "1px solid var(--glass-border)",
              paddingTop: 28,
              marginTop: 8,
            }}
          >
            <SimilarViews items={view.similar_views} onOpen={openSibling} />
          </section>
        </>
      )}

      {/* Deploy = execute now: confirm-then-place into the paper book. */}
      <DeployConfirmModal
        expr={placeExpr}
        amount={amount}
        onClose={() => setPlaceExpr(null)}
        onPlaced={handlePlaced}
      />
    </div>
  );
}
