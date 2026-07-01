"use client";

/**
 * Shared auth brand panel — the dark surface on the left of the login /
 * signup split layout. Theme-independent by design (always dark). Carries
 * the Pivot wordmark over a static, abstract black-&-white "wave field":
 * a topographic ridge of flowing lines, with a soft glow, grain and
 * vignette. No animation.
 */

import type { ReactElement } from "react";

// ---------------------------------------------------------------------------
// Deterministic wave-field geometry (computed once at module load)
// ---------------------------------------------------------------------------

const VB_W = 600;
const VB_H = 800;
const LINE_COUNT = 30;

function buildWave(yBase: number, amp: number, phase: number): string {
  const pts: string[] = [];
  for (let x = -40; x <= VB_W + 40; x += 8) {
    const t = x / VB_W;
    const y =
      yBase +
      Math.sin(t * Math.PI * 2 + phase) * amp +
      Math.sin(t * Math.PI * 5 + phase * 1.7) * amp * 0.22;
    pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  }
  return "M" + pts.join(" L");
}

const WAVES: string[] = Array.from({ length: LINE_COUNT }, (_, i) => {
  // Amplitude swells toward the middle lines for a 3D-ridge feel.
  const g = Math.exp(-((i - LINE_COUNT / 2) ** 2) / (2 * 8 * 8));
  const amp = 6 + 26 * g;
  return buildWave(60 + i * 23, amp, i * 0.42);
});

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function BrandPanel(): ReactElement {
  return (
    <div
      className="relative hidden flex-col justify-between overflow-hidden lg:flex lg:w-1/2"
      style={{
        background: "radial-gradient(130% 110% at 30% 18%, #161618 0%, #0a0a0b 52%, #060607 100%)",
        padding: "56px 56px",
        color: "#fbfcfc",
      }}
    >
      {/* Wave field */}
      <svg
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 h-full w-full"
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="xMidYMid slice"
      >
        <defs>
          <linearGradient id="bp-line" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0" />
            <stop offset="30%" stopColor="#ffffff" stopOpacity="0.55" />
            <stop offset="70%" stopColor="#ffffff" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
          </linearGradient>
          <radialGradient id="bp-fade" cx="40%" cy="46%" r="70%">
            <stop offset="0%" stopColor="white" stopOpacity="1" />
            <stop offset="100%" stopColor="white" stopOpacity="0" />
          </radialGradient>
          <mask id="bp-mask">
            <rect width={VB_W} height={VB_H} fill="url(#bp-fade)" />
          </mask>
        </defs>

        <g mask="url(#bp-mask)" fill="none" stroke="url(#bp-line)" strokeWidth="1" strokeLinecap="round">
          {WAVES.map((d, i) => (
            <path key={i} d={d} opacity={0.12 + 0.5 * Math.exp(-((i - LINE_COUNT / 2) ** 2) / (2 * 9 * 9))} />
          ))}
        </g>
      </svg>

      {/* Film grain */}
      <svg aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full" style={{ opacity: 0.05 }}>
        <filter id="bp-grain">
          <feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="2" stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="100%" height="100%" filter="url(#bp-grain)" />
      </svg>

      {/* Vignette */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{ boxShadow: "inset 0 0 220px 60px rgba(0,0,0,0.65)" }}
      />

      {/* Wordmark */}
      <div className="relative z-10">
        <span
          style={{
            fontFamily: "var(--font-experiment)",
            fontWeight: 600,
            fontSize: 30,
            letterSpacing: "-0.03em",
          }}
        >
          pivot
        </span>
      </div>

      {/* Footer */}
      <p className="relative z-10 text-xs" style={{ color: "rgba(255,255,255,0.4)" }}>
        Data &amp; analysis only. Not financial advice.
      </p>
    </div>
  );
}
