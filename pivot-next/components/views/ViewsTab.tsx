"use client";

/**
 * ViewsTab — master/detail root for the Views tab.
 *
 * Grid mode: fetches listViews on mount + filter change; renders the ViewCard
 * gallery with square border-only loading skeletons, error + empty states.
 *
 * Detail mode: selectedViewId !== null → renders ViewDetailPage (sibling).
 *
 * Follow changes bubble up from ViewCard so the grid stays in sync without
 * a re-fetch.
 *
 * DESIGN LAW: borders-only, ROUNDED corners (radii), >=13px floor (see
 * ViewSurface).
 */

import * as React from "react";
import { AlertCircle, RefreshCw } from "lucide-react";
import { listViews } from "@/lib/api";
import { isError } from "@/lib/types";
import type { ViewSummary, StanceIntent } from "@/lib/types";
import { ViewCard } from "./ViewCard";
import { ViewFilters, DEFAULT_FILTERS, type FiltersState } from "./ViewFilters";
import { ViewDetailPage } from "./ViewDetailPage";

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

// A single shared grid class: 1 / 2 / 3 columns at <640 / 640–1024 / >=1024,
// equal-width cards, 20px gutter, equal heights (cards are fixed-height).
const GRID_CLASS =
  "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 items-stretch";
const GRID_STYLE: React.CSSProperties = { gap: 20 };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function filtersToQuery(f: FiltersState): {
  status?: string;
  view_type?: string;
  category?: string;
} {
  const q: { status?: string; view_type?: string; category?: string } = {};
  if (f.status !== "all") q.status = f.status;
  if (f.view_type !== "all") q.view_type = f.view_type;
  if (f.category !== "all") q.category = f.category;
  return q;
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
  // Threaded to the detail page so it scrolls to + highlights that stance.
  const [selectedStance, setSelectedStance] =
    React.useState<StanceIntent | null>(null);
  const [filters, setFilters] = React.useState<FiltersState>(DEFAULT_FILTERS);
  const [state, setState] = React.useState<FetchState>({ kind: "loading" });

  // Per-view follow sync: keyed by view id, avoids a full refetch after toggling.
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

  React.useEffect(() => {
    load(filters);
  }, [filters, load]);

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

  // ── Grid mode ─────────────────────────────────────────────────────────────
  return (
    <div
      className="views-tab flex flex-col"
      style={{ gap: 24 }}
      data-testid="views-tab"
    >
      {/* Page heading */}
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
          Views
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

      {/* Filters */}
      <ViewFilters value={filters} onChange={setFilters} />

      {/* Loading skeletons */}
      {state.kind === "loading" && <ViewsGridSkeleton />}

      {/* Error */}
      {state.kind === "error" && (
        <ViewsErrorState message={state.message} onRetry={() => load(filters)} />
      )}

      {/* Empty state */}
      {state.kind === "ok" && state.items.length === 0 && <ViewsEmptyState />}

      {/* Grid */}
      {state.kind === "ok" && state.items.length > 0 && (
        <div
          className={GRID_CLASS}
          style={GRID_STYLE}
          data-testid="views-grid"
          role="list"
        >
          {state.items.map((view) => {
            const follow = followMap[view.id];
            const merged: ViewSummary = follow
              ? { ...view, ...follow }
              : view;
            return (
              <div key={view.id} role="listitem">
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
// ViewsGridSkeleton — rounded, border-only placeholders while the list loads
// ---------------------------------------------------------------------------

function ViewsGridSkeleton(): React.ReactElement {
  return (
    <div className={GRID_CLASS} style={GRID_STYLE} data-testid="views-loading">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="animate-pulse"
          style={{
            height: 376,
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
// ViewsErrorState — rounded border-only, copy >=15px, no fill
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
// ViewsEmptyState — rounded border-only, copy >=15px, no fill
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
        No views yet
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
