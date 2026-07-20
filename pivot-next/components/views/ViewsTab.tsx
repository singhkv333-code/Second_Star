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
import type { ViewSummary, ViewDetail, StanceIntent } from "@/lib/types";
import { ShareButton } from "./ShareButton";
import { ViewCard } from "./ViewCard";
import { DEFAULT_FILTERS, type FiltersState } from "./ViewFilters";
import { ViewCategoryBar } from "./ViewCategoryBar";
import { ViewDetailPage } from "./ViewDetailPage";
import { categoryLead } from "./view-format";
import packSummariesRaw from "./pack/viewpack01.summaries.json";
import packDetailsRaw from "./pack/viewpack01.details.json";

// View Pack 01 — the curated opinions shipped as static data (summaries for the
// grid, details for the detail page via detailOverride). This tab renders the
// pack directly, with no /api/views round-trip. Summaries flagged `coming_soon`
// have no details entry: they render as inert teaser cards.
//
// Pack 02 is deliberately NOT merged in here — the tab shows this curated set
// only. Its files stay on disk (and still feed /view-pack) so restoring it is
// a two-line change.
const PACK_SUMMARIES = packSummariesRaw as unknown as ViewSummary[];
const PACK_DETAILS = packDetailsRaw as unknown as Record<string, ViewDetail>;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ViewsTabProps = {
  onOpenWorkflowById: (workflowId: string) => void;
};

// A single shared grid class: 1 / 2 / 3 / 4 columns at <640 / 640–1024 /
// 1024–1536 / >=1536, equal-width cards, 20px gutter, equal heights (cards are
// fixed-height). The 4-up column at 2xl keeps each card narrow on the 1920/2560
// design canvas instead of stretching too wide.
const GRID_CLASS =
  "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 items-stretch";
const GRID_STYLE: React.CSSProperties = { gap: 20 };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
  // Threaded to the detail page so it scrolls to + highlights that stance.
  const [selectedStance, setSelectedStance] =
    React.useState<StanceIntent | null>(null);
  const [filters, setFilters] = React.useState<FiltersState>(DEFAULT_FILTERS);

  // Per-view follow sync: keyed by view id, avoids a full refetch after toggling.
  const [followMap, setFollowMap] = React.useState<
    Record<string, { is_following: boolean; follower_count: number }>
  >({});

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
      // Coming-soon teasers have no detail record; ViewCard already renders
      // them inert, this is the belt-and-braces guard against a blank page.
      if (!PACK_DETAILS[id]) return;
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
        detailOverride={PACK_DETAILS[selectedViewId] ?? null}
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
  // The tab renders View Pack 01 — a curated, static set. (DB-backed views are
  // intentionally not merged in here.)
  const items = PACK_SUMMARIES;
  const categories = deriveCategories(items);
  const visibleItems =
    filters.category === "all"
      ? items
      : items.filter((v) => categoryLead(v.category) === filters.category);

  return (
    <div
      className="views-tab flex flex-col"
      style={{ gap: 24 }}
      data-testid="views-tab"
    >
      {/* Page heading */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <h1
          style={{
            margin: 0,
            fontFamily: "var(--font-serif)",
            fontWeight: "var(--weight-display)" as React.CSSProperties["fontWeight"],
            fontSize: 22,
            letterSpacing: "-0.025em",
            color: "var(--text-primary)",
          }}
        >
          Opinions
        </h1>
        <ShareButton ariaLabel="Share opinions" />
      </div>

      {/* Category ribbon (Polymarket-style) — buckets the views by theme. The
          pack guarantees content, so it shows regardless of backend state. */}
      {categories.length > 1 && (
        <ViewCategoryBar
          categories={categories}
          value={filters.category}
          onChange={(category) => setFilters({ ...filters, category })}
        />
      )}

      {/* Grid — pack views (+ any backend views) always render */}
      {visibleItems.length > 0 && (
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
