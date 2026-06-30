"use client";

import React, { useState } from "react";

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
          border: hue ? `1px solid ${hue}55` : "1px solid var(--border, rgba(0,0,0,0.08))",
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
      style={{
        ...box,
        background: "var(--surface-1, #fff)",
        border: "1px solid var(--border, rgba(0,0,0,0.08))",
        padding: Math.round(size * 0.12),
      }}
      onError={() => setErrored(true)}
      loading="lazy"
    />
  );
}
