"use client";

/**
 * SimilarViews — small "related views" cards at the foot of a View detail page.
 * Each is a crisp short title (clickable) that opens that view.
 *
 * DESIGN LAW (v2): ROUNDED (var(--radius-lg)), BORDER-ONLY (hover lifts the
 * border color, never a fill), plain language, >= 13px. Renders nothing when
 * there are no siblings.
 */

import * as React from "react";
import { ArrowUpRight } from "lucide-react";
import type { SimilarView } from "@/lib/types";

const FONT = "var(--font-display)";

function SimilarCard({
  item,
  onOpen,
}: {
  item: SimilarView;
  onOpen: (id: string) => void;
}): React.ReactElement {
  const [hover, setHover] = React.useState(false);
  const title = item.short_title ?? "Related view";
  return (
    <button
      type="button"
      onClick={() => onOpen(item.id)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        gap: 12,
        textAlign: "left",
        width: "100%",
        border: `1px solid ${
          hover ? "var(--glass-border-hover)" : "var(--glass-border)"
        }`,
        borderRadius: "var(--radius-lg)",
        background: "var(--bg-base)",
        padding: "16px 18px",
        cursor: "pointer",
        transition: "border-color 180ms var(--ease-quartr)",
      }}
    >
      <span
        style={{
          fontFamily: FONT,
          fontSize: 15,
          fontWeight: 600,
          color: "var(--text-primary)",
          lineHeight: 1.35,
        }}
      >
        {title}
      </span>
      <ArrowUpRight
        size={16}
        aria-hidden
        style={{
          flexShrink: 0,
          marginTop: 2,
          color: hover ? "var(--text-secondary)" : "var(--text-tertiary)",
          transition: "color 180ms var(--ease-quartr)",
        }}
      />
    </button>
  );
}

export function SimilarViews({
  items,
  onOpen,
}: {
  items?: SimilarView[] | null;
  onOpen: (id: string) => void;
}): React.ReactElement | null {
  const safe = (Array.isArray(items) ? items : []).filter((v) => v && v.id);
  if (safe.length === 0) return null;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 16 }}>
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
        Similar views
      </h2>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
          gap: 12,
        }}
      >
        {safe.map((v) => (
          <SimilarCard key={v.id} item={v} onOpen={onOpen} />
        ))}
      </div>
    </section>
  );
}

export default SimilarViews;
