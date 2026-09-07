"use client";

/**
 * BrokerLogo — renders a broker's brand mark from /public/brokers/{id}.svg.
 *
 * The SVGs are hand-authored, square (0 0 48 48 viewBox), and already carry
 * their own rounded tile + brand color, so we just size the <img> and let the
 * viewBox scale. If the asset 404s (a broker the backend added before its logo
 * shipped) we fall back to a monogram tile tinted with the broker accent so
 * the row never renders a broken-image glyph.
 */

import { useState } from "react";

export function BrokerLogo({
  brokerId,
  logo,
  name,
  accent,
  size = 40,
  className,
}: {
  brokerId: string;
  /** Server-provided path; defaults to /brokers/{id}.svg. */
  logo?: string;
  name: string;
  accent: string;
  size?: number;
  className?: string;
}): React.ReactElement {
  const [errored, setErrored] = useState(false);
  const src = logo || `/brokers/${brokerId}.svg`;

  if (errored) {
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
    // Static, tiny (~0.5 KB) hand-authored SVG with its own viewBox — next/image
    // adds no value here and the rest of the app (AppShell logo, news badges)
    // uses a plain <img> for the same reason.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={name}
      width={size}
      height={size}
      className={className}
      onError={() => setErrored(true)}
      style={{ display: "block", borderRadius: Math.round(size * 0.23), flexShrink: 0 }}
    />
  );
}
