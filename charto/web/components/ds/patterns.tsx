"use client";

/**
 * Pivot design system — motion & patterns.
 *
 * Landing-page atmosphere and ornament, all monochrome and CSS-only
 * (keyframes live in globals.css under "DS motion & patterns"):
 *
 *   TickerTape     — seamless marquee of symbol/price/delta cells
 *   ChainFlow      — workflow blocks linked by connectors with a
 *                    traveling pulse (the agent system, animated)
 *   CandlePulse    — breathing candlestick cluster, tape-style
 *   BlueprintTile  — grid + pulsing "+" registration marks
 *   DotDrift       — dot-grid background slowly crawling
 *   ScanTile       — soft band sweeping down a tile
 *
 * Pattern tiles are containers: drop any content on top of them.
 */

import * as React from "react";
import { cn } from "@/lib/utils";
import { Delta, Figure } from "./primitives";

/* ────────────────────────────────────────────────────────────────────
 * Ticker tape
 * ──────────────────────────────────────────────────────────────────── */

export interface TickerItem {
  symbol: string;
  price: string;
  changePct: number;
}

/**
 * Full-width marquee of live-looking quotes. The track renders its
 * items twice and slides exactly half its width, so the loop never
 * jumps. Hover pauses it.
 */
export function TickerTape({
  items,
  durationSec = 36,
  className,
}: {
  items: TickerItem[];
  durationSec?: number;
  className?: string;
}) {
  const cells = (
    <>
      {items.map((it, i) => (
        <span
          key={`${it.symbol}-${i}`}
          className="inline-flex items-center gap-2.5"
          style={{ padding: "0 26px", whiteSpace: "nowrap" }}
        >
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.08em",
              color: "var(--text-primary)",
            }}
          >
            {it.symbol}
          </span>
          <Figure size={12.5} muted>
            {it.price}
          </Figure>
          <Delta value={it.changePct} size={11.5} />
          <span
            aria-hidden
            style={{
              marginLeft: 22,
              width: 3,
              height: 3,
              borderRadius: "50%",
              background: "var(--glass-border-focus)",
            }}
          />
        </span>
      ))}
    </>
  );
  return (
    <div
      className={cn("ds-marquee overflow-hidden", className)}
      style={
        {
          borderTop: "1px solid var(--glass-border)",
          borderBottom: "1px solid var(--glass-border)",
          padding: "11px 0",
          maskImage:
            "linear-gradient(90deg, transparent, black 8%, black 92%, transparent)",
          WebkitMaskImage:
            "linear-gradient(90deg, transparent, black 8%, black 92%, transparent)",
          "--ds-marquee-duration": `${durationSec}s`,
        } as React.CSSProperties
      }
    >
      <div className="ds-marquee-track" aria-hidden="false">
        {cells}
        <span aria-hidden>{cells}</span>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Chain flow — the agent system, animated
 * ──────────────────────────────────────────────────────────────────── */

export interface ChainNode {
  label: string;
  detail: string;
}

/**
 * Workflow blocks joined by connectors that carry a traveling pulse —
 * the register-not-execute pipeline as a living diagram. Pulses are
 * staggered per connector so intent reads left → right.
 */
export function ChainFlow({
  nodes,
  className,
}: {
  nodes: ChainNode[];
  className?: string;
}) {
  return (
    <div className={cn("flex w-full items-stretch", className)}>
      {nodes.map((n, i) => (
        <React.Fragment key={n.label + i}>
          <div
            className="shrink-0"
            style={{
              border: "1px solid var(--glass-border-hover)",
              background: "var(--bg-card)",
              borderRadius: "var(--radius-md)",
              padding: "12px 16px",
              minWidth: 132,
              animation: `ds-node-arm 2.8s var(--ease-tw) ${i * 0.7}s infinite`,
            }}
          >
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 9.5,
                fontWeight: 600,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: "var(--text-tertiary)",
                marginBottom: 5,
              }}
            >
              {n.label}
            </div>
            <div
              style={{
                fontFamily: "var(--font-ui)",
                fontSize: 12.5,
                fontWeight: 500,
                color: "var(--text-primary)",
                whiteSpace: "nowrap",
              }}
            >
              {n.detail}
            </div>
          </div>
          {i < nodes.length - 1 && (
            <div
              className="relative min-w-8 flex-1 self-center"
              style={{ color: "var(--text-primary)" }}
            >
              <div
                aria-hidden
                style={{
                  height: 1,
                  background:
                    "linear-gradient(90deg, var(--glass-border-hover), var(--glass-border-focus), var(--glass-border-hover))",
                }}
              />
              <span
                aria-hidden
                className="ds-flow-dot"
                style={{ animationDelay: `${i * 0.7}s` }}
              />
            </div>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Candle pulse
 * ──────────────────────────────────────────────────────────────────── */

/** Deterministic candle specs — heights in px, independent periods. */
const CANDLES = [
  { h: 26, wick: 38, d: 0.0, s: 2.1 },
  { h: 38, wick: 52, d: 0.35, s: 2.6 },
  { h: 20, wick: 30, d: 0.6, s: 2.2 },
  { h: 44, wick: 58, d: 0.15, s: 2.9 },
  { h: 30, wick: 44, d: 0.8, s: 2.4 },
  { h: 50, wick: 64, d: 0.5, s: 3.1 },
  { h: 34, wick: 46, d: 1.0, s: 2.3 },
];

/**
 * A breathing candlestick cluster — hero ornament / loader. Monochrome
 * ink at graded opacities; set `accent` to tint the final candle with
 * the profit color (a quiet bullish wink).
 */
export function CandlePulse({
  accent = true,
  scale = 1,
  className,
}: {
  accent?: boolean;
  scale?: number;
  className?: string;
}) {
  return (
    <div
      className={cn("flex items-end", className)}
      style={{ gap: 7 * scale, height: 64 * scale }}
      aria-hidden
    >
      {CANDLES.map((c, i) => {
        const last = i === CANDLES.length - 1;
        const color =
          last && accent ? "var(--color-profit)" : "var(--text-primary)";
        return (
          <div
            key={i}
            className="relative flex items-end justify-center"
            style={{ width: 9 * scale, height: c.wick * scale }}
          >
            <span
              style={{
                position: "absolute",
                bottom: 0,
                top: 0,
                width: 1,
                left: "50%",
                background: color,
                opacity: 0.3,
              }}
            />
            <span
              style={{
                position: "relative",
                width: "100%",
                height: c.h * scale,
                background: color,
                opacity: last && accent ? 0.9 : 0.22 + i * 0.09,
                borderRadius: 1.5,
                transformOrigin: "bottom",
                animation: `ds-candle ${c.s}s ease-in-out ${c.d}s infinite`,
              }}
            />
          </div>
        );
      })}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Pattern tiles
 * ──────────────────────────────────────────────────────────────────── */

/** Fixed cross positions (percent coords) — deterministic render. */
const CROSSES: Array<[number, number, number]> = [
  [12, 22, 0],
  [78, 14, 0.9],
  [32, 68, 1.7],
  [88, 62, 0.4],
  [55, 36, 1.2],
];

/**
 * Blueprint tile — hairline grid with pulsing "+" registration marks
 * (the Vercel-style survey marks already hinted at on the landing's
 * dark panels). Works on paper and ink.
 */
export function BlueprintTile({
  className,
  style,
  children,
}: {
  className?: string;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}) {
  return (
    <div
      className={cn("relative overflow-hidden", className)}
      style={{ borderRadius: "var(--radius-lg)", ...style }}
    >
      <div
        aria-hidden
        className="absolute inset-0"
        style={{
          backgroundImage:
            "linear-gradient(var(--glass-border) 1px, transparent 1px), linear-gradient(90deg, var(--glass-border) 1px, transparent 1px)",
          backgroundSize: "44px 44px",
        }}
      />
      {CROSSES.map(([x, y, d], i) => (
        <span
          key={i}
          aria-hidden
          className="absolute"
          style={{
            left: `${x}%`,
            top: `${y}%`,
            width: 11,
            height: 11,
            color: "var(--text-secondary)",
            animation: `ds-cross 3.4s ease-in-out ${d}s infinite`,
          }}
        >
          <svg viewBox="0 0 11 11" width="11" height="11">
            <path
              d="M5.5 0v11M0 5.5h11"
              stroke="currentColor"
              strokeWidth="1"
            />
          </svg>
        </span>
      ))}
      <div className="relative">{children}</div>
    </div>
  );
}

/** Dot-grid background slowly drifting diagonally. */
export function DotDrift({
  className,
  style,
  children,
}: {
  className?: string;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}) {
  return (
    <div
      className={cn("relative overflow-hidden", className)}
      style={{ borderRadius: "var(--radius-lg)", ...style }}
    >
      <div
        aria-hidden
        className="absolute inset-0"
        style={{
          backgroundImage:
            "radial-gradient(var(--glass-border-focus) 1px, transparent 1px)",
          backgroundSize: "28px 28px",
          animation: "ds-drift 14s linear infinite",
        }}
      />
      <div className="relative">{children}</div>
    </div>
  );
}

/** Tile with a soft band sweeping top → bottom (market-scan motif). */
export function ScanTile({
  className,
  style,
  children,
}: {
  className?: string;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}) {
  return (
    <div
      className={cn("relative overflow-hidden", className)}
      style={{ borderRadius: "var(--radius-lg)", ...style }}
    >
      <div
        aria-hidden
        className="absolute inset-x-0"
        style={{
          height: "18%",
          background:
            "linear-gradient(180deg, transparent, var(--surface-active), transparent)",
          animation: "ds-scan 4.6s var(--ease-tw) infinite",
        }}
      />
      <div className="relative">{children}</div>
    </div>
  );
}
