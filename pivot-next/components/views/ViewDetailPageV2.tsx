"use client";

/**
 * ViewDetailPageV2 — the redesigned opened-view detail surface (Kalshi-style),
 * wired to REAL backend data. Same props/contract as the original
 * ViewDetailPage, so ViewsTab can swap it in without any other change.
 *
 *   header    "← Return to Opinion Markets" · Follow (heart)
 *   title     the belief as a crisp question (short_title) + plain subtitle
 *   meta      Resolves · Horizon · Type · Status
 *   chart     ReturnsChart — "what ₹X could become", modelled per strategy
 *   ticket    StrategyCalculator — amount + per-strategy projection + deploy
 *   table     StrategyTable + StrategyExplanation (side-by-side, editorial)
 *
 * Data flow: getView(id) → ViewDetail; viewToStrategies() adapts expressions to
 * the StrategyConfig[] the new components read. Deploy is register-not-execute:
 * arming the calculator calls deployExpression() and, on success, hands the new
 * workflow to the parent so the user can open + confirm it.
 */

import * as React from "react";
import { ArrowLeft } from "lucide-react";

import { getView, deployExpression } from "@/lib/api";
import { isError } from "@/lib/types";
import type { ViewDetail, StanceIntent } from "@/lib/types";
import { FollowButton } from "@/components/views/FollowButton";
import { viewToStrategies } from "@/components/views/view-strategy-adapter";
import { ReturnsChart } from "@/components/view-detail/ReturnsChart";
import { StrategyCalculator } from "@/components/view-detail/StrategyCalculator";
import { StrategyTable } from "@/components/view-detail/StrategyTable";
import { StrategyExplanation } from "@/components/view-detail/StrategyExplanation";

const FONT = "var(--font-display)";
const DEFAULT_AMOUNT = 100_000;

interface Props {
  viewId: string;
  onBack: () => void;
  onOpenWorkflowById: (workflowId: string) => void;
  initialStance?: StanceIntent | null;
}

// ── small local bits ─────────────────────────────────────────────────────────

function BackLink({ onBack }: { onBack: () => void }): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onBack}
      aria-label="Return to Opinion Markets"
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
      }}
    >
      <ArrowLeft size={14} aria-hidden />
      Return to Opinion Markets
    </button>
  );
}

function MetaCell({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 10.5,
          fontWeight: 600,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--text-tertiary)",
        }}
      >
        {label}
      </span>
      <span
        className="truncate"
        style={{
          fontFamily: FONT,
          fontSize: 14,
          fontWeight: 600,
          color: accent ? "var(--pivot-blue)" : "var(--text-primary)",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function fmtDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function statusLabel(status: string): string {
  const s = status.toLowerCase();
  if (s.includes("open")) return "Open";
  if (s.includes("develop")) return "Developing";
  if (s.includes("consensus")) return "Consensus";
  if (s.includes("resolv")) return "Resolved";
  if (s.includes("archiv")) return "Archived";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

// ── page ─────────────────────────────────────────────────────────────────────

export function ViewDetailPageV2({
  viewId,
  onBack,
  onOpenWorkflowById,
  initialStance = null,
}: Props): React.ReactElement {
  const [currentId, setCurrentId] = React.useState(viewId);
  React.useEffect(() => setCurrentId(viewId), [viewId]);

  const [view, setView] = React.useState<ViewDetail | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const [amount, setAmount] = React.useState<number>(DEFAULT_AMOUNT);
  const [selectedId, setSelectedId] = React.useState<string>("");
  const [deployNote, setDeployNote] = React.useState<string | null>(null);

  // NOTE: unlike the standalone /view-detail route, this renders INSIDE
  // AppShell, whose tab container is already `min-h-0 overflow-y-auto` and owns
  // the scroll. Releasing the root html/body lock here would break that flex
  // scroll chain and clip the page — so we deliberately do NOT touch it.

  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    getView(currentId).then((res) => {
      if (!alive) return;
      if (isError(res)) {
        setError(res.error.message || "Could not load this opinion.");
        setView(null);
      } else {
        setView(res.data);
        // Preselect the highlighted/best expression, else the first.
        const best =
          res.data.best_expression?.id ?? res.data.expressions[0]?.id ?? "";
        setSelectedId(best);
      }
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [currentId]);

  // Esc returns to the gallery.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onBack();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

  const strategies = React.useMemo(
    () => (view ? viewToStrategies(view) : []),
    [view],
  );

  const handleDeploy = React.useCallback(
    (strategyId: string, capital: number) => {
      setDeployNote(null);
      deployExpression(strategyId, { capital_inr: capital }).then((res) => {
        if (isError(res)) {
          setDeployNote(res.error.message || "Could not register this draft.");
          return;
        }
        setDeployNote("Draft automation registered — opening it to review.");
        if (res.data.workflow_id) onOpenWorkflowById(res.data.workflow_id);
      });
    },
    [onOpenWorkflowById],
  );

  const wrap: React.CSSProperties = {
    maxWidth: 1120,
    margin: "0 auto",
    padding: "8px 4px 80px",
  };

  if (loading) {
    return (
      <div style={wrap}>
        <BackLink onBack={onBack} />
        <p style={{ fontFamily: FONT, color: "var(--text-tertiary)", marginTop: 24 }}>
          Loading…
        </p>
      </div>
    );
  }

  if (error || !view) {
    return (
      <div style={wrap}>
        <BackLink onBack={onBack} />
        <p style={{ fontFamily: FONT, color: "var(--color-loss)", marginTop: 24 }}>
          {error ?? "This opinion is unavailable."}
        </p>
      </div>
    );
  }

  const title = view.short_title || view.title;
  const subtitle = view.plain_thesis || view.description || view.thesis;
  const resolves = fmtDate(view.resolution_date);
  const horizon = view.time_horizon;
  const typeLabel = view.category?.split("·").pop()?.trim() || view.view_type;
  const void_initialStance = initialStance; // reserved: stance deep-link

  return (
    <div style={wrap}>
      {/* header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <BackLink onBack={onBack} />
        <FollowButton
          viewId={view.id}
          isFollowing={view.is_following}
          followerCount={view.follower_count}
        />
      </div>

      {/* title + subtitle */}
      <h1
        style={{
          fontFamily: FONT,
          fontSize: "clamp(24px, 3.4vw, 34px)",
          fontWeight: 700,
          lineHeight: 1.15,
          letterSpacing: "-0.01em",
          color: "var(--text-primary)",
          margin: "18px 0 10px",
          textWrap: "balance",
        }}
      >
        {title}
      </h1>
      {subtitle && (
        <p
          style={{
            fontFamily: FONT,
            fontSize: 15.5,
            lineHeight: 1.55,
            color: "var(--text-secondary)",
            maxWidth: "72ch",
            margin: 0,
          }}
        >
          {subtitle}
        </p>
      )}

      {/* meta row */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "18px 40px",
          padding: "18px 0",
          margin: "18px 0 8px",
          borderTop: "1px solid var(--glass-border)",
          borderBottom: "1px solid var(--glass-border)",
        }}
      >
        {resolves && <MetaCell label="Resolves" value={resolves} />}
        {horizon && <MetaCell label="Horizon" value={horizon} />}
        {typeLabel && <MetaCell label="Type" value={typeLabel} />}
        <MetaCell label="Status" value={statusLabel(view.status)} accent />
      </div>

      {strategies.length === 0 ? (
        <p
          style={{
            fontFamily: FONT,
            fontSize: 14,
            color: "var(--text-tertiary)",
            marginTop: 24,
          }}
        >
          No deployable strategy has been built for this opinion yet.
        </p>
      ) : (
        (() => {
          const first = strategies[0]!;
          const activeId = selectedId || first.id;
          const activeStrategy =
            strategies.find((s) => s.id === activeId) ?? first;
          return (
        <>
          {/* returns chart */}
          <div style={{ marginTop: 20 }}>
            <ReturnsChart
              amount={amount}
              strategies={strategies}
              highlightId={activeId}
            />
          </div>

          {/* calculator */}
          <div style={{ marginTop: 28 }}>
            <StrategyCalculator
              strategies={strategies}
              selectedId={activeId}
              onSelect={setSelectedId}
              amount={amount}
              onAmount={setAmount}
              onDeploy={handleDeploy}
            />
            {deployNote && (
              <p
                style={{
                  fontFamily: FONT,
                  fontSize: 12.5,
                  color: "var(--text-secondary)",
                  marginTop: 10,
                }}
              >
                {deployNote}
              </p>
            )}
          </div>

          {/* strategies table + explanation */}
          <div
            style={{
              marginTop: 36,
              display: "grid",
              gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)",
              gap: 28,
              alignItems: "start",
            }}
            className="vd-table-grid"
          >
            <StrategyTable
              strategies={strategies}
              selectedId={activeId}
              onSelect={setSelectedId}
            />
            <StrategyExplanation strategy={activeStrategy} />
          </div>
        </>
          );
        })()
      )}

      <style>{`
        @media (max-width: 860px) {
          .vd-table-grid { grid-template-columns: 1fr !important; }
        }
      `}</style>
      {/* reserved for a future stance deep-link */}
      {void_initialStance ? null : null}
    </div>
  );
}

export default ViewDetailPageV2;
