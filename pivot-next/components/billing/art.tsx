/**
 * Line illustrations for paywall headers, drawn in the theme's ink so they
 * work in light and dark. Each one depicts the FEATURE that triggered the
 * paywall — a grid for chart panes, a bell for alerts — never a generic
 * "premium" ornament.
 */

import * as React from "react";

const INK = "var(--bl-ink)";
const FAINT = "var(--bl-rule-strong)";
const CARD = "var(--bl-card)";

function Candles({ x, y, w, h, n = 9, seed = 1 }: { x: number; y: number; w: number; h: number; n?: number; seed?: number }): React.ReactElement {
  const step = w / n;
  const bars = Array.from({ length: n }, (_, i) => {
    const t = Math.sin((i + seed) * 1.7) * 0.5 + Math.sin((i + seed) * 0.6) * 0.5;
    const mid = y + h / 2 - t * h * 0.32 - (i / n) * h * 0.12;
    const body = h * (0.12 + ((i * 7 + seed) % 5) * 0.03);
    const up = (i + seed) % 3 !== 0;
    return { cx: x + step * (i + 0.5), mid, body, up };
  });
  return (
    <g>
      {bars.map((b, i) => (
        <g key={i}>
          <line x1={b.cx} x2={b.cx} y1={b.mid - b.body} y2={b.mid + b.body} stroke={INK} strokeWidth="1" opacity="0.6" />
          <rect
            x={b.cx - step * 0.26}
            y={b.mid - b.body / 2}
            width={step * 0.52}
            height={b.body}
            rx="1"
            fill={b.up ? CARD : INK}
            stroke={INK}
            strokeWidth="1"
          />
        </g>
      ))}
    </g>
  );
}

function Grid(): React.ReactElement {
  // 4 unlocked panes, 4 more behind the plan
  const cells = Array.from({ length: 8 }, (_, i) => ({ col: i % 4, row: Math.floor(i / 4), locked: i >= 4 }));
  return (
    <g transform="translate(115 14)">
      {cells.map((c) => {
        const x = c.col * 64;
        const y = c.row * 52;
        return (
          <g key={`${c.col}-${c.row}`} opacity={c.locked ? 0.45 : 1}>
            <rect x={x} y={y} width="58" height="46" rx="5" fill={CARD} stroke={c.locked ? FAINT : INK} strokeDasharray={c.locked ? "3 3" : undefined} />
            {c.locked ? (
              <g transform={`translate(${x + 22} ${y + 14})`} stroke={INK} fill="none" strokeWidth="1.4">
                <rect x="1" y="7" width="12" height="9" rx="2" />
                <path d="M4 7V4.5a3 3 0 0 1 6 0V7" />
              </g>
            ) : (
              <Candles x={x + 4} y={y + 6} w={50} h={34} n={6} seed={c.col + c.row * 4} />
            )}
          </g>
        );
      })}
    </g>
  );
}

function Bell(): React.ReactElement {
  return (
    <g transform="translate(120 10)">
      <rect x="0" y="12" width="220" height="92" rx="8" fill={CARD} stroke={FAINT} />
      <Candles x={10} y={20} w={200} h={76} n={14} seed={3} />
      <line x1="0" x2="220" y1="44" y2="44" stroke={INK} strokeDasharray="4 4" strokeWidth="1.2" />
      <g transform="translate(232 26)" stroke={INK} strokeWidth="1.6" fill={CARD}>
        <path d="M12 2a8 8 0 0 0-8 8v6l-3 4h22l-3-4v-6a8 8 0 0 0-8-8z" />
        <path d="M9 23a3 3 0 0 0 6 0" fill="none" />
      </g>
    </g>
  );
}

function Credits({ empty }: { empty: boolean }): React.ReactElement {
  const r = 36;
  const c = 2 * Math.PI * r;
  return (
    <g transform="translate(240 66)">
      <circle r={r} fill="none" stroke={FAINT} strokeWidth="7" />
      <circle r={r} fill="none" stroke={INK} strokeWidth="7" strokeLinecap="round" strokeDasharray={`${empty ? 0.001 : c * 0.7} ${c}`} transform="rotate(-90)" />
      <g transform="translate(-130 -34)" fill={CARD} stroke={INK} strokeWidth="1.2">
        <rect x="0" y="0" width="74" height="22" rx="11" />
        <rect x="10" y="32" width="60" height="22" rx="11" opacity="0.6" />
      </g>
      <g transform="translate(58 -24)" fill={CARD} stroke={INK} strokeWidth="1.2">
        <rect x="0" y="0" width="84" height="40" rx="8" />
        <path d="M10 14h50M10 24h36" stroke={INK} strokeWidth="1.4" />
      </g>
    </g>
  );
}

function Tabs(): React.ReactElement {
  return (
    <g transform="translate(130 20)">
      {[0, 1, 2].map((i) => (
        <g key={i} transform={`translate(${i * 28} ${i * 12})`} opacity={i === 0 ? 0.4 : i === 1 ? 0.7 : 1}>
          <rect x="0" y="0" width="180" height="78" rx="6" fill={CARD} stroke={i === 0 ? FAINT : INK} strokeDasharray={i === 0 ? "3 3" : undefined} />
          <path d="M0 14h180" stroke={FAINT} />
          {i === 2 ? <Candles x={8} y={20} w={164} h={52} n={12} seed={5} /> : null}
        </g>
      ))}
    </g>
  );
}

function Spark(): React.ReactElement {
  return (
    <g transform="translate(140 14)">
      <rect x="0" y="0" width="200" height="100" rx="8" fill={CARD} stroke={FAINT} />
      <Candles x={10} y={10} w={180} h={80} n={13} seed={7} />
      <path d="M10 70 C 60 60, 90 30, 190 22" fill="none" stroke={INK} strokeWidth="1.6" />
    </g>
  );
}

export function PaywallArt({ feature, kind }: { feature?: string; kind?: string }): React.ReactElement {
  let body: React.ReactElement;
  if (kind === "evicted" || feature === "chart.parallel") body = <Tabs />;
  else if (feature === "chart.panes" || feature === "chart.indicators") body = <Grid />;
  else if (feature?.startsWith("alerts.")) body = <Bell />;
  else if (feature === "ai.credits" || feature === "ai.summaries") body = <Credits empty={kind === "quota_exhausted"} />;
  else body = <Spark />;
  return (
    <svg viewBox="0 0 480 132" preserveAspectRatio="xMidYMid meet" role="presentation">
      {body}
    </svg>
  );
}

/** A larger scene for the full-page paywall. */
export function HeroArt({ feature }: { feature?: string }): React.ReactElement {
  return (
    <svg viewBox="0 0 480 360" role="presentation">
      <g transform="translate(0 110) scale(1)">
        <PaywallArtInner feature={feature} />
      </g>
    </svg>
  );
}

function PaywallArtInner({ feature }: { feature?: string }): React.ReactElement {
  if (feature === "chart.panes" || feature === "chart.indicators") return <Grid />;
  if (feature?.startsWith("alerts.")) return <Bell />;
  if (feature === "chart.parallel") return <Tabs />;
  if (feature === "ai.credits") return <Credits empty={false} />;
  return <Spark />;
}
