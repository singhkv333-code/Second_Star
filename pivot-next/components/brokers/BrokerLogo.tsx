"use client";

/**
 * BrokerLogo — renders a broker's brand mark.
 *
 * Source order (same CDN the home page's company logos use, so brokers and
 * stocks look like one system):
 *   1. logo.dev, by the broker's real domain — the actual current brand mark.
 *   2. the hand-authored /public/brokers/{id}.svg, if logo.dev 404s / errors.
 *   3. an accent-tinted monogram tile, so a row never shows a broken glyph.
 */

import { useState } from "react";
import { brokerLogo } from "@/lib/publicAssets";

// Publishable logo.dev token (pk_…) — the SAME one HomeTab uses; safe in the
// client, it is what the backend embeds in company logo_url values. logo.dev
// serves by domain, so each broker's real mark comes from its own site.
const LOGODEV_TOKEN = "pk_X3WtLGU0RTuTq-o9GTLEsg";

// Broker id → the domain logo.dev should resolve. `kite` is Zerodha's product
// name; both map to zerodha.com. Unknown ids fall through to the local SVG.
const BROKER_DOMAINS: Record<string, string> = {
  kite: "zerodha.com",
  zerodha: "zerodha.com",
  upstox: "upstox.com",
  fyers: "fyers.in",
  groww: "groww.in",
  angelone: "angelone.in",
  angel: "angelone.in",
  dhan: "dhan.co",
  "5paisa": "5paisa.com",
  aliceblue: "aliceblueonline.com",
  icici: "icicidirect.com",
  kotak: "kotaksecurities.com",
};

function logoDevUrl(domain: string, size: number): string {
  // Retina-ish: request 2× the CSS size, capped, so small marks stay crisp.
  const px = Math.min(256, Math.max(64, size * 2));
  return `https://img.logo.dev/${domain}?token=${LOGODEV_TOKEN}&size=${px}&format=png`;
}

export function BrokerLogo({
  brokerId,
  logo,
  name,
  accent,
  size = 40,
  className,
}: {
  brokerId: string;
  /** Server-provided path; used as the 2nd fallback when logo.dev has no mark. */
  logo?: string;
  name: string;
  accent: string;
  size?: number;
  className?: string;
}): React.ReactElement {
  const domain = BROKER_DOMAINS[brokerId.toLowerCase()];
  // Stage: 0 = logo.dev (if we know a domain), 1 = local SVG, 2 = monogram.
  const [stage, setStage] = useState<0 | 1 | 2>(domain ? 0 : 1);

  const src =
    stage === 0 && domain
      ? logoDevUrl(domain, size)
      : stage === 1
        ? brokerLogo(brokerId, logo) ?? null
        : null;

  if (src === null) {
    // Accent-tinted monogram fallback — same rounded-tile silhouette as the
    // real marks so the grid stays visually even.
    const radius = Math.round(size * 0.23);
    return (
      <span
        aria-label={name}
        role="img"
        className={className}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: size,
          height: size,
          borderRadius: radius,
          background: `color-mix(in srgb, ${accent} 16%, transparent)`,
          color: accent,
          fontFamily: "var(--font-display)",
          fontWeight: 700,
          fontSize: Math.round(size * 0.46),
          letterSpacing: "-0.03em",
          flexShrink: 0,
        }}
      >
        {name.trim().charAt(0).toUpperCase() || "?"}
      </span>
    );
  }

  return (
    // Plain <img> (not next/image): a hot-linked CDN mark with an onError chain
    // is the proven pattern here (same as CompanyLogo). object-contain +
    // white-ish plate keeps a wordmark from touching the rounded edge.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={name}
      width={size}
      height={size}
      className={className}
      onError={() => setStage((s) => (s === 0 ? 1 : 2))}
      loading="lazy"
      style={{
        display: "block",
        width: size,
        height: size,
        borderRadius: Math.round(size * 0.23),
        objectFit: "contain",
        flexShrink: 0,
        // logo.dev marks arrive on a transparent/white ground; a faint plate
        // gives wordmarks an edge without boxing the square SVGs too hard.
        background: stage === 0 ? "#fff" : "transparent",
      }}
    />
  );
}
