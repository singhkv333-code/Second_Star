"use client";

/**
 * ViewDescription — the "Essential description" block on a View detail page.
 *
 * A 2-3 line plain-English description, then up to 3 plain bullet points that
 * highlight the important factors (what drives it / how to play it — folded
 * here from the old transmission "why" section). The "what if you're wrong"
 * caveat is promoted OUT of that flat list into its own emphasized, warn-
 * tinted line (GOAL D — information hierarchy), sourced from an explicit
 * `caveat` prop or detected from a "Main caveat: ..." bullet.
 *
 * DESIGN LAW (v2): ROUNDED, BORDER-ONLY (no grey fill), plain language (no
 * jargon), >= 13px text, aligned. Renders nothing when there is neither a
 * description, a bullet, nor a caveat.
 */

import * as React from "react";
import { AlertCircle } from "lucide-react";

const FONT = "var(--font-display)";

// Detects a bullet that IS the "main caveat" line so it can be promoted out
// of the plain list, e.g. "Main caveat: if the whole market falls, the
// bundle usually falls too." -> "if the whole market falls, the bundle
// usually falls too."
const CAVEAT_PREFIX_RE = /^main caveat\s*:?\s*/i;

export function ViewDescription({
  description,
  bullets,
  caveat = null,
}: {
  description?: string | null;
  bullets?: string[] | null;
  /** Explicit "what if you're wrong" line. Falls back to a detected
   * "Main caveat: ..." bullet when absent. */
  caveat?: string | null;
}): React.ReactElement | null {
  const safeBullets = (Array.isArray(bullets) ? bullets : []).filter(
    (b) => typeof b === "string" && b.trim().length > 0,
  );

  // Prefer an explicit `caveat` prop; otherwise pull the first "Main
  // caveat: ..." bullet out of the plain list and use it instead.
  const explicitCaveat =
    typeof caveat === "string" && caveat.trim().length > 0
      ? caveat.trim()
      : null;
  let detectedCaveat: string | null = null;
  const plainBullets: string[] = [];
  for (const b of safeBullets) {
    if (
      !explicitCaveat &&
      !detectedCaveat &&
      CAVEAT_PREFIX_RE.test(b.trim())
    ) {
      detectedCaveat = b.trim().replace(CAVEAT_PREFIX_RE, "");
      continue;
    }
    plainBullets.push(b);
  }
  const shownCaveat = explicitCaveat ?? detectedCaveat;

  const hasDescription =
    typeof description === "string" && description.trim().length > 0;

  if (!hasDescription && plainBullets.length === 0 && !shownCaveat)
    return null;

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

      {shownCaveat && (
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
            border:
              "1px solid color-mix(in srgb, var(--color-warn) 45%, var(--glass-border))",
            borderRadius: "var(--radius-md)",
            background: "color-mix(in srgb, var(--color-warn) 7%, transparent)",
            padding: "12px 14px",
          }}
        >
          <AlertCircle
            size={15}
            aria-hidden
            style={{ color: "var(--color-warn)", flexShrink: 0, marginTop: 2 }}
          />
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <span
              style={{
                fontFamily: FONT,
                fontSize: 13,
                fontWeight: 600,
                color: "var(--color-warn)",
                lineHeight: 1.3,
              }}
            >
              If you&apos;re wrong
            </span>
            <span
              style={{
                fontFamily: FONT,
                fontSize: 14,
                fontWeight: 400,
                color: "var(--text-primary)",
                lineHeight: 1.55,
              }}
            >
              {shownCaveat}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

export default ViewDescription;
