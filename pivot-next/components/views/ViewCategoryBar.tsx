"use client";

/**
 * ViewCategoryBar — the Polymarket-style category selector: a single horizontal,
 * swipeable row of chips ("All" + each theme bucket) that filters the gallery.
 *
 * The active chip is a SOLID inverted pill (ink background, page-colour text) —
 * the one on-screen place we invert, exactly like Polymarket's dark "All" pill —
 * so the current category is unmistakable at a glance. Inactive chips are quiet
 * text that lift to a subtle wash on hover. The row scrolls horizontally with no
 * visible scrollbar (quartr-no-scrollbar) and never wraps, so it reads as one
 * calm ribbon under the heading regardless of how many categories exist.
 *
 * Categories are the leading THEME words derived from the loaded views
 * (view-format.categoryLead), so the bar always mirrors exactly what's on the
 * grid — no hardcoded taxonomy.
 */

import * as React from "react";

const FONT = "var(--font-display)";

// ---------------------------------------------------------------------------
// CategoryChip — one pill. Active = solid ink fill; inactive = quiet text.
// ---------------------------------------------------------------------------

function CategoryChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}): React.ReactElement {
  const [hover, setHover] = React.useState(false);
  const background = active
    ? "var(--text-primary)"
    : hover
      ? "color-mix(in srgb, var(--text-tertiary) 14%, var(--bg-base))"
      : "transparent";
  const color = active
    ? "var(--bg-base)"
    : hover
      ? "var(--text-primary)"
      : "var(--text-secondary)";
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-pressed={active}
      style={{
        flexShrink: 0,
        padding: "7px 15px",
        background,
        color,
        border: "none",
        borderRadius: "var(--radius-pill)",
        fontFamily: FONT,
        fontSize: 13.5,
        fontWeight: active ? 600 : 500,
        lineHeight: 1.2,
        whiteSpace: "nowrap",
        cursor: "pointer",
        transition:
          "background 160ms var(--ease-quartr), color 160ms var(--ease-quartr)",
      }}
    >
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// ViewCategoryBar
// ---------------------------------------------------------------------------

export type ViewCategoryBarProps = {
  /** Distinct theme buckets, in display order (e.g. ["AI","Energy","Autos"]). */
  categories: string[];
  /** The active bucket, or "all". */
  value: string;
  onChange: (next: string) => void;
};

export function ViewCategoryBar({
  categories,
  value,
  onChange,
}: ViewCategoryBarProps): React.ReactElement {
  return (
    <div
      className="quartr-no-scrollbar flex items-center"
      style={{ gap: 4, overflowX: "auto", overflowY: "hidden" }}
      role="tablist"
      aria-label="Filter views by category"
      data-testid="view-category-bar"
    >
      <CategoryChip
        label="All"
        active={value === "all"}
        onClick={() => onChange("all")}
      />
      {categories.map((cat) => (
        <CategoryChip
          key={cat}
          label={cat}
          active={value === cat}
          onClick={() => onChange(cat)}
        />
      ))}
    </div>
  );
}
