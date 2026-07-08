"use client";

/**
 * ViewsTab — master/detail root for the "Opinion Markets" tab.
 *
 * Grid mode: fetches listViews on mount + filter change; renders the ViewCard
 * gallery with border-only loading skeletons, error + empty states.
 *
 * Detail mode: selectedViewId !== null → renders ViewDetailPage (sibling).
 *
 * Follow changes bubble up from ViewCard so the grid stays in sync without
 * a re-fetch.
 *
 * ViewCategoryBar (adopted from the collaborator's 943e782 redesign) is wired
 * below the heading row as a horizontal category ribbon. Category filtering is
 * done CLIENT-SIDE (not sent to the API) so the bar always shows all theme
 * buckets regardless of the active selection. Status + type filters continue to
 * go to the API via ViewFilters.
 *
 * DESIGN LAW: borders-only, ROUNDED corners, >=13px floor (see ViewSurface).
 */

import * as React from "react";
import { AlertCircle, ArrowLeft, Briefcase, RefreshCw } from "lucide-react";
import { listViews } from "@/lib/api";
import { isError } from "@/lib/types";
import type { ViewSummary, StanceIntent } from "@/lib/types";
import { ViewCard } from "./ViewCard";
import { ViewFilters, DEFAULT_FILTERS, type FiltersState } from "./ViewFilters";
import { ViewCategoryBar } from "./ViewCategoryBar";
import { ViewDetailPageV2 as ViewDetailPage } from "./ViewDetailPageV2";
import { MyViews } from "./MyViews";
import { categoryLead } from "./view-format";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: ViewSummary[] };

export type ViewsTabProps = {
  onOpenWorkflowById: (workflowId: string) => void;
};

// A single shared grid class: 1 / 2 / 3 / 4 columns at <640 / 640–1024 /
// 1024–1536 / >=1536. Equal-width cards, 20px gutter, equal heights via
// items-stretch (adopted from the collaborator — cards are h-full, not fixed
// height, so rows stretch to the tallest card in each row).
const GRID_CLASS =
  "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 items-stretch";
const GRID_STYLE: React.CSSProperties = { gap: 20 };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * filtersToQuery — converts UI filter state to API query params.
 * NOTE: `category` is intentionally NOT sent here — it is filtered
 * client-side so ViewCategoryBar can always show all available buckets.
 */
function filtersToQuery(f: FiltersState): {
  status?: string;
  view_type?: string;
} {
  const q: { status?: string; view_type?: string } = {};
  if (f.status !== "all") q.status = f.status;
  if (f.view_type !== "all") q.view_type = f.view_type;
  return q;
}

/** Distinct theme buckets from the loaded views, in first-seen order. */
function deriveCategories(items: ViewSummary[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of items) {
    const lead = categoryLead(v.category);
    if (lead && !seen.has(lead)) {
      seen.add(lead);
      out.push(lead);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// ViewsTab
// ---------------------------------------------------------------------------

export function ViewsTab({
  onOpenWorkflowById,
}: ViewsTabProps): React.ReactElement {
  const [selectedViewId, setSelectedViewId] = React.useState<string | null>(
    null,
  );
  // Which Yes/No side a card press intended (null = opened via the card body).
  const [selectedStance, setSelectedStance] =
    React.useState<StanceIntent | null>(null);
  // "gallery" = the curated grid; "mine" = the user's My Views ledger.
  const [mode, setMode] = React.useState<"gallery" | "mine">("gallery");
  const [filters, setFilters] = React.useState<FiltersState>(DEFAULT_FILTERS);
  const [state, setState] = React.useState<FetchState>({ kind: "loading" });

  // Per-view follow sync: keyed by view id, avoids a full refetch on toggle.
  const [followMap, setFollowMap] = React.useState<
    Record<string, { is_following: boolean; follower_count: number }>
  >({});

  const load = React.useCallback((f: FiltersState): void => {
    setState({ kind: "loading" });
    listViews(filtersToQuery(f))
      .then((result) => {
        if (isError(result)) {
          setState({ kind: "error", message: result.error.message });
          return;
        }
        setState({ kind: "ok", items: result.data.items });
        setFollowMap({});
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  }, []);

  // Re-fetch when status/view_type filters change (not category — that's client-side).
  React.useEffect(() => {
    load(filters);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.status, filters.view_type, load]);

  const handleFollowChange = React.useCallback(
    (
      id: string,
      next: { is_following: boolean; follower_count: number },
    ): void => {
      setFollowMap((m) => ({ ...m, [id]: next }));
    },
    [],
  );

  const openView = React.useCallback(
    (id: string, intent?: StanceIntent): void => {
      setSelectedStance(intent ?? null);
      setSelectedViewId(id);
    },
    [],
  );

  // ── Detail mode ───────────────────────────────────────────────────────────
  if (selectedViewId !== null) {
    return (
      <ViewDetailPage
        viewId={selectedViewId}
        initialStance={selectedStance}
        onBack={() => {
          setSelectedViewId(null);
          setSelectedStance(null);
        }}
        onOpenWorkflowById={onOpenWorkflowById}
      />
    );
  }

  // ── My Views mode — the user's deployment ledger ──────────────────────────
  if (mode === "mine") {
    return (
      <div
        className="views-tab flex flex-col"
        style={{ gap: 20 }}
        data-testid="views-tab"
      >
        <button
          type="button"
          onClick={() => setMode("gallery")}
          data-testid="my-views-back"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            alignSelf: "flex-start",
            background: "none",
            border: "none",
            padding: 0,
            fontFamily: "var(--font-display)",
            fontSize: 13.5,
            fontWeight: 600,
            color: "var(--text-secondary)",
            cursor: "pointer",
          }}
        >
          <ArrowLeft size={14} aria-hidden />
          All views
        </button>
        <MyViews
          onOpenView={(id) => {
            setMode("gallery");
            openView(id);
          }}
          onBrowse={() => setMode("gallery")}
        />
      </div>
    );
  }

  // ── Grid mode ─────────────────────────────────────────────────────────────
  const allItems = state.kind === "ok" ? state.items : [];
  const categories = deriveCategories(allItems);
  const visibleItems =
    filters.category === "all"
      ? allItems
      : allItems.filter((v) => categoryLead(v.category) === filters.category);

  return (
    <div
      className="views-tab flex flex-col"
      style={{ gap: 24 }}
      data-testid="views-tab"
    >
      {/* Page heading + My Opinions button */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 16,
        }}
      >
        <div>
          <h1
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 28,
              fontWeight: 600,
              letterSpacing: "-0.02em",
              color: "var(--text-primary)",
              margin: "0 0 6px 0",
              lineHeight: 1.2,
            }}
          >
            Opinion Markets
          </h1>
          <p
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 15,
              fontWeight: 400,
              color: "var(--text-secondary)",
              margin: 0,
              lineHeight: 1.5,
            }}
          >
            Beliefs, expressed as deployable strategies — with the return each one has paid.
          </p>
        </div>
        <MyViewsButton onClick={() => setMode("mine")} />
      </div>

      {/* ViewCategoryBar — category ribbon (from collaborator's redesign).
          Only rendered when we have multiple buckets (items loaded). */}
      {categories.length > 1 && (
        <ViewCategoryBar
          categories={categories}
          value={filters.category}
          onChange={(category) => setFilters((f) => ({ ...f, category }))}
        />
      )}

      {/* Status + type filters */}
      <ViewFilters value={filters} onChange={setFilters} />

      {/* Loading skeletons */}
      {state.kind === "loading" && <ViewsGridSkeleton />}

      {/* Error */}
      {state.kind === "error" && (
        <ViewsErrorState message={state.message} onRetry={() => load(filters)} />
      )}

      {/* Empty state */}
      {state.kind === "ok" && visibleItems.length === 0 && <ViewsEmptyState />}

      {/* Grid */}
      {state.kind === "ok" && visibleItems.length > 0 && (
        <div
          className={GRID_CLASS}
          style={GRID_STYLE}
          data-testid="views-grid"
          role="list"
        >
          {visibleItems.map((view) => {
            const follow = followMap[view.id];
            const merged: ViewSummary = follow
              ? { ...view, ...follow }
              : view;
            return (
              <div key={view.id} role="listitem" className="h-full">
                <ViewCard
                  view={merged}
                  onOpen={openView}
                  onFollowChange={handleFollowChange}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MyViewsButton — entry to the user's deployment ledger.
// Exported so the /view-pack showcase renders the identical affordance.
// ---------------------------------------------------------------------------

export function MyViewsButton({
  onClick,
}: {
  onClick: () => void;
}): React.ReactElement {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      data-testid="my-views-button"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        flexShrink: 0,
        padding: "10px 18px",
        borderRadius: "var(--radius-md)",
        border: `1px solid ${hover ? "var(--glass-border-hover)" : "var(--glass-border)"}`,
        background: "var(--bg-base)",
        fontFamily: "var(--font-display)",
        fontSize: 13.5,
        fontWeight: 600,
        letterSpacing: "-0.01em",
        color: "var(--text-primary)",
        cursor: "pointer",
        transition: "border-color 160ms var(--ease-quartr)",
        whiteSpace: "nowrap",
      }}
    >
      <Briefcase size={14} aria-hidden />
      My Opinions
    </button>
  );
}

// ---------------------------------------------------------------------------
// ViewsGridSkeleton — border-only placeholders while the list loads
// ---------------------------------------------------------------------------

function ViewsGridSkeleton(): React.ReactElement {
  return (
    <div className={GRID_CLASS} style={GRID_STYLE} data-testid="views-loading">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="animate-pulse"
          style={{
            height: 340,
            width: "100%",
            background: "var(--bg-base)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-lg)",
          }}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ViewsErrorState
// ---------------------------------------------------------------------------

function ViewsErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}): React.ReactElement {
  return (
    <div
      role="alert"
      data-testid="views-error"
      className="flex flex-col items-center justify-center text-center"
      style={{
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-lg)",
        padding: "48px 24px",
        gap: 12,
      }}
    >
      <AlertCircle
        size={24}
        aria-hidden
        style={{ color: "var(--color-loss)" }}
      />
      <p
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 18,
          fontWeight: 600,
          color: "var(--text-primary)",
          margin: 0,
        }}
      >
        Couldn&apos;t load views
      </p>
      <p
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 15,
          color: "var(--text-secondary)",
          margin: 0,
          maxWidth: 360,
        }}
      >
        {message}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center"
        style={{
          marginTop: 4,
          gap: 8,
          padding: "8px 16px",
          background: "var(--bg-base)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
          color: "var(--text-primary)",
          fontFamily: "var(--font-display)",
          fontSize: 15,
          fontWeight: 500,
          cursor: "pointer",
          transition: "border-color 180ms var(--ease-quartr)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = "var(--glass-border-hover)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = "var(--glass-border)";
        }}
      >
        <RefreshCw size={15} aria-hidden />
        Retry
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ViewsEmptyState
// ---------------------------------------------------------------------------

function ViewsEmptyState(): React.ReactElement {
  return (
    <div
      data-testid="views-empty"
      className="flex flex-col items-center justify-center text-center"
      style={{
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-lg)",
        padding: "56px 24px",
        gap: 10,
      }}
    >
      <p
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 18,
          fontWeight: 600,
          color: "var(--text-primary)",
          margin: 0,
        }}
      >
        No opinion markets yet
      </p>
      <p
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 15,
          color: "var(--text-secondary)",
          margin: 0,
          maxWidth: 380,
          lineHeight: 1.5,
        }}
      >
        Curated market beliefs will appear here — each one explained, with the
        return it has paid, and ready to deploy.
      </p>
    </div>
  );
}
