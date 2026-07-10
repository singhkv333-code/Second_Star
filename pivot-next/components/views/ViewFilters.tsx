"use client";

/**
 * ViewFilters — filter row for the Views gallery.
 *
 * Controls: status (All / Developing / Open / Resolved)
 *           view_type (All types / Event / Theme)
 *
 * DESIGN LAW: ROUNDED (pill) border-only toggle tags. Active = border
 * var(--glass-border-focus) + text var(--text-primary). NO ink-fill, NO
 * background. 13px. Labels routed through statusLabel / viewTypeLabel — a
 * raw enum token never reaches the screen.
 */

import * as React from "react";
import type { ViewStatus, ViewType } from "@/lib/types";
import { statusLabel, viewTypeLabel } from "./view-format";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type FiltersState = {
  status: ViewStatus | "all";
  view_type: ViewType | "all";
  category: string | "all";
};

export const DEFAULT_FILTERS: FiltersState = {
  status: "all",
  view_type: "all",
  category: "all",
};

type ViewFiltersProps = {
  value: FiltersState;
  onChange: (v: FiltersState) => void;
  counts?: Partial<Record<ViewStatus, number>>;
};

// ---------------------------------------------------------------------------
// Filter option lists — labels via humanizers (never a raw enum token).
// ---------------------------------------------------------------------------

const STATUS_OPTIONS: { value: FiltersState["status"]; label: string }[] = [
  { value: "all", label: "All" },
  { value: "developing", label: statusLabel("developing") },
  { value: "published", label: statusLabel("published") },
  { value: "resolved", label: statusLabel("resolved") },
];

const TYPE_OPTIONS: { value: FiltersState["view_type"]; label: string }[] = [
  { value: "all", label: "All types" },
  { value: "EVENT", label: viewTypeLabel("EVENT") },
  { value: "THEME", label: viewTypeLabel("THEME") },
];

// ---------------------------------------------------------------------------
// FilterTag — rounded pill, border-only toggle (no fill)
// ---------------------------------------------------------------------------

function FilterTag({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}): React.ReactElement {
  const [hover, setHover] = React.useState(false);
  const borderColor = active
    ? "var(--glass-border-focus)"
    : hover
      ? "var(--glass-border-hover)"
      : "var(--glass-border)";
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-pressed={active}
      style={{
        padding: "6px 14px",
        background: "var(--bg-base)",
        border: `1px solid ${borderColor}`,
        borderRadius: "var(--radius-sm)",
        color: active ? "var(--text-primary)" : "var(--text-secondary)",
        fontFamily: "var(--font-display)",
        fontSize: 13,
        fontWeight: 500,
        lineHeight: 1.2,
        cursor: "pointer",
        transition: "border-color 180ms var(--ease-quartr), color 180ms var(--ease-quartr)",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// ViewFilters
// ---------------------------------------------------------------------------

export function ViewFilters({
  value,
  onChange,
}: ViewFiltersProps): React.ReactElement {
  return (
    <div
      className="flex flex-wrap items-center"
      style={{ gap: 12 }}
      role="group"
      aria-label="Filter views"
      data-testid="view-filters"
    >
      {/* Status filters */}
      <div className="flex flex-wrap items-center" style={{ gap: 8 }}>
        {STATUS_OPTIONS.map((opt) => (
          <FilterTag
            key={opt.value}
            active={value.status === opt.value}
            onClick={() => onChange({ ...value, status: opt.value })}
          >
            {opt.label}
          </FilterTag>
        ))}
      </div>

      {/* Vertical hairline separator */}
      <div
        aria-hidden
        style={{
          width: 1,
          height: 20,
          background: "var(--glass-border)",
          flexShrink: 0,
        }}
      />

      {/* View type filters */}
      <div className="flex flex-wrap items-center" style={{ gap: 8 }}>
        {TYPE_OPTIONS.map((opt) => (
          <FilterTag
            key={opt.value}
            active={value.view_type === opt.value}
            onClick={() => onChange({ ...value, view_type: opt.value })}
          >
            {opt.label}
          </FilterTag>
        ))}
      </div>
    </div>
  );
}
