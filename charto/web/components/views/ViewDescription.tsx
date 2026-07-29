"use client";

/**
 * ViewDescription — the "Essential description" block on a View detail page.
 *
 * A 2-3 line plain-English description, then up to 3 plain bullet points that
 * highlight the important factors (what drives it / how to play it — folded
 * here from the old transmission "why" section).
 *
 * DESIGN LAW (v2): ROUNDED, BORDER-ONLY (no grey fill), plain language (no
 * jargon), >= 13px text, aligned. Renders nothing when there is neither a
 * description, a bullet, nor a caveat.
 */

import * as React from "react";

const FONT = "var(--font-display)";

export function ViewDescription({
  description,
  bullets,
}: {
  description?: string | null;
  bullets?: string[] | null;
  /** Deprecated: the "what if you're wrong" caveat is no longer surfaced here
   * (it lives on the strategy cards). Kept for caller compatibility. */
  caveat?: string | null;
}): React.ReactElement | null {
  const plainBullets = (Array.isArray(bullets) ? bullets : []).filter(
    (b) => typeof b === "string" && b.trim().length > 0,
  );

  const hasDescription =
    typeof description === "string" && description.trim().length > 0;

  if (!hasDescription && plainBullets.length === 0) return null;

  return (
    <section
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-lg)",
        background: "var(--bg-base)",
        padding: 20,
      }}
    >
      <h2
        style={{
          fontFamily: FONT,
          fontSize: 16,
          fontWeight: 600,
          color: "var(--text-primary)",
          lineHeight: 1.3,
          letterSpacing: "-0.01em",
          margin: 0,
        }}
      >
        What this is
      </h2>

      {hasDescription && (
        <p
          style={{
            fontFamily: FONT,
            fontSize: 15,
            fontWeight: 400,
            color: "var(--text-secondary)",
            lineHeight: 1.6,
            margin: 0,
          }}
        >
          {description}
        </p>
      )}

      {plainBullets.length > 0 && (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          {plainBullets.slice(0, 3).map((b, i) => (
            <li
              key={i}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 12,
              }}
            >
              <span
                aria-hidden
                style={{
                  flexShrink: 0,
                  marginTop: 7,
                  width: 6,
                  height: 6,
                  borderRadius: "var(--radius-pill)",
                  background: "var(--pivot-blue)",
                }}
              />
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 14,
                  fontWeight: 400,
                  color: "var(--text-primary)",
                  lineHeight: 1.55,
                }}
              >
                {b}
              </span>
            </li>
          ))}
        </ul>
      )}

    </section>
  );
}

export default ViewDescription;
