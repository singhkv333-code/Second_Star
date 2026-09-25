"use client";

import React, { useState } from "react";

/** True for a logo that is already a finished square tile (SharePerks' icons,
 *  which carry their own background). Those draw edge to edge; padding and a
 *  white plate would put a tile inside a tile. Wordmarks (logo.dev) still get
 *  the padded plate so they don't touch the edge. */
export function isTileLogo(url?: string | null): boolean {
  return !!url && url.includes("company-logo.shareperks.in/");
}

/**
 * CompanyLogo — renders a company's logo (img.logo.dev, served by the
 * backend StockQuote.logo_url) inside a rounded square, falling back to a
 * sector-hued first-letter monogram when there is no logo URL or the image
 * fails to load. Mirrors the SourceLogo pattern used for news badges.
 *
 * Plain <img> (not next/image) on purpose: next.config has no remotePatterns
 * for img.logo.dev, and a hot-linked CDN logo with an onError fallback is the
 * proven pattern here. Attribution for logo.dev's free tier is rendered once,
 * globally, in the app footer — see AppFooter.
 */
export function CompanyLogo({
  logoUrl,
  name,
  symbol,
  hue,
  size = 56,
}: {
  logoUrl?: string | null;
  name: string;
  symbol: string;
  /** Sector-derived colour for the monogram fallback (matches the old glyph).
   *  Optional — defaults to a neutral tint for list rows that don't compute one. */
  hue?: string;
  size?: number;
}): React.ReactElement {
  const [errored, setErrored] = useState(false);
  const tint = hue ?? "var(--text-secondary)";

  const initial =
    name.trim()[0]?.toUpperCase() ?? symbol[0]?.toUpperCase() ?? "•";

  const box: React.CSSProperties = {
    width: size,
    height: size,
    borderRadius: "var(--radius-md)",
  };

  // Monogram fallback — identical look to the legacy brand glyph.
  if (!logoUrl || errored) {
    return (
      <div
        aria-hidden="true"
        className="flex shrink-0 items-center justify-center"
        style={{
          ...box,
          background: hue ? `${hue}22` : "var(--surface-2, rgba(0,0,0,0.05))",
          // No rim at all. The tinted fill already reads as a tile, so the
          // border only drew a box around a placeholder and made it louder
          // than the real logos beside it. This matches ScreenerPage's own
          // BrandGlyph, which arrived at `border: none` for the same reason.
          border: "none",
          color: tint,
          fontFamily: "var(--font-ui)",
          fontSize: Math.round(size * 0.43),
          fontWeight: 600,
          letterSpacing: "-0.02em",
        }}
      >
        {initial}
      </div>
    );
  }

  return (
    <img
      src={logoUrl}
      alt={`${name} logo`}
      width={size}
      height={size}
      className="shrink-0 object-contain"
      style={isTileLogo(logoUrl) ? {
        ...box,
        // A hairline so a tile with a white background still has an edge.
        boxShadow: "0 0 0 1px var(--border, rgba(0,0,0,0.06))",
      } : {
        ...box,
        background: "var(--surface-1, #fff)",
        border: "1px solid var(--border, rgba(0,0,0,0.04))",
        padding: Math.round(size * 0.12),
      }}
      onError={() => setErrored(true)}
      loading="lazy"
    />
  );
}
