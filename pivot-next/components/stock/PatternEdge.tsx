"use client";

/**
 * Pattern edge — a hit rate is meaningless without the rate it beat.
 *
 * Every row is one pattern measured across the Indian equity universe, against
 * a CONTROL: the base rate of the same directional move on bars where the
 * pattern did not fire. A 58% hit rate against a 57% control is noise wearing
 * a pattern's name, and the only column worth reading is the difference.
 *
 * Negative edges are shown, not filtered. A pattern that reliably fails is as
 * useful as one that works, and hiding those rows would turn a measurement
 * into an advertisement.
 *
 * The bar diverges from a fixed centre line rather than filling from the left,
 * because the sign is the finding — a reader should be able to see which side
 * of zero the pattern sits on without reading a number.
 */

import * as React from "react";

import { getPatterns, type PatternStat, type PatternsResponse } from "@/lib/api";
import { isError } from "@/lib/types";
import { PatternGlyph } from "./PatternGlyph";
import { Segmented } from "./chrome";

const HORIZONS = [5, 10, 20];
const INTERVALS = [
  { value: "15m", label: "15m" },
  { value: "1h", label: "1h" },
  { value: "1d", label: "Daily" },
];

/** Widest edge the bar scale runs to. Fixed rather than data-derived so the
 *  bars mean the same thing when the interval changes under the reader. */
const SCALE_PP = 12;

function pretty(kind: string): string {
  return kind.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

function compactN(n: number): string {
  if (n >= 1e5) return `${(n / 1e5).toFixed(1)}L`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

export function PatternEdge({ symbol }: { symbol: string }): React.ReactElement | null {
  const [interval, setInterval] = React.useState("1d");
  const [horizon, setHorizon] = React.useState(20);
  const [data, setData] = React.useState<PatternsResponse | null>(null);
  const [dead, setDead] = React.useState(false);
  const [all, setAll] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    setData(null);
    getPatterns(symbol, interval, horizon)
      .then((r) => {
        if (cancelled) return;
        if (isError(r)) { setDead(true); return; }
        setData(r.data);
      })
      .catch(() => { if (!cancelled) setDead(true); });
    return () => { cancelled = true; };
  }, [symbol, interval, horizon]);

  if (dead) return null;
  const rows = data?.patterns ?? [];
  if (data && !rows.length) return null;

  // Strongest edges either way, which is where the information is — the middle
  // of the list is a long tail of patterns that do nothing.
  const ranked = [...rows].sort((a, b) => Math.abs(b.edge ?? 0) - Math.abs(a.edge ?? 0));
  const shown = all ? ranked : ranked.slice(0, 8);

  return (
    <div style={{ marginTop: 26 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          marginBottom: 10,
        }}
      >
        <h3
          style={{
            margin: 0,
            fontFamily: "var(--font-ui)",
            fontSize: 17,
            fontWeight: 600,
            letterSpacing: "-0.015em",
            color: "var(--text-primary)",
          }}
        >
          Pattern edge
        </h3>
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <Segmented value={interval} options={INTERVALS} onChange={setInterval} />
          <Segmented
            value={String(horizon)}
            options={HORIZONS.map((h) => ({ value: String(h), label: `${h} bars` }))}
            onChange={(v) => setHorizon(Number(v))}
          />
        </div>
      </div>

      <div style={{ borderTop: "1px solid var(--glass-border)", borderBottom: "1px solid var(--glass-border)" }}>
        <div
          className="pat-head pat-row"
          style={{
            display: "grid",
            gridTemplateColumns: "58px minmax(0,1.5fr) 54px 58px 58px minmax(120px, 1.4fr) 92px",
            gap: 12,
            alignItems: "center",
            padding: "8px 0",
            fontSize: 10.5,
            fontWeight: 650,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            color: "var(--text-tertiary)",
            borderBottom: "1px solid var(--glass-border)",
          }}
        >
          <span />
          <span>Pattern</span>
          <span style={{ textAlign: "right" }}>Cases</span>
          <span style={{ textAlign: "right" }}>Hit</span>
          <span style={{ textAlign: "right" }}>Control</span>
          <span style={{ textAlign: "center" }}>Edge</span>
          <span style={{ textAlign: "right" }}>vs control</span>
        </div>

        {!data
          ? Array.from({ length: 5 }).map((_, i) => (
              <div key={i} style={{ height: 38, borderTop: i ? "1px solid var(--glass-border)" : undefined }} />
            ))
          : shown.map((p, i) => <Row key={`${p.kind}-${p.interval}-${p.horizon}`} p={p} first={i === 0} />)}
      </div>

      {ranked.length > 8 ? (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          style={{
            marginTop: 10,
            border: "none",
            background: "transparent",
            padding: 0,
            cursor: "pointer",
            fontFamily: "var(--font-ui)",
            fontSize: 11.5,
            color: "var(--text-secondary)",
          }}
        >
          {all ? "Show strongest" : `Show all ${ranked.length}`}
        </button>
      ) : null}

      <style>{`
        @media (max-width: 720px) {
          .pat-row { grid-template-columns: 50px minmax(0,1.4fr) 48px 52px minmax(90px,1fr) 78px !important; }
          .pat-row > .pat-control { display: none !important; }
        }
      `}</style>
    </div>
  );
}

function Row({ p, first }: { p: PatternStat; first: boolean }): React.ReactElement {
  const edge = p.edge ?? 0;
  const se = p.se ?? 0;
  // Two standard errors is the conventional bar for "this is not the sample
  // talking". Below it the row is drawn muted rather than dropped: the reader
  // should see that the pattern was measured and came back inconclusive.
  const solid = Math.abs(edge) >= 2 * se;
  const tone = edge > 0 ? "var(--color-profit)" : edge < 0 ? "var(--color-loss)" : "var(--text-tertiary)";
  const half = Math.min(50, (Math.abs(edge) / SCALE_PP) * 50);

  return (
    <div
      className="pat-row"
      style={{
        display: "grid",
        gridTemplateColumns: "58px minmax(0,1.5fr) 54px 58px 58px minmax(120px, 1.4fr) 92px",
        gap: 12,
        alignItems: "center",
        minHeight: 38,
        borderTop: first ? undefined : "1px solid var(--glass-border)",
      }}
    >
      <PatternGlyph kind={p.kind} />
      <span style={{ fontSize: 12.5, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {pretty(p.kind)}
      </span>
      <span style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 11.5, fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)" }}>
        {compactN(p.n)}
      </span>
      <span style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 11.5, fontVariantNumeric: "tabular-nums", color: "var(--text-primary)" }}>
        {p.rate !== null ? `${p.rate.toFixed(1)}%` : "—"}
      </span>
      <span className="pat-control" style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 11.5, fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)" }}>
        {p.control !== null ? `${p.control.toFixed(1)}%` : "—"}
      </span>

      {/* The diverging bar. The centre rule is the control, so the bar is
          literally the distance from it. */}
      <span style={{ position: "relative", height: 16, display: "block" }}>
        <span style={{ position: "absolute", inset: 0, top: 6, height: 4, borderRadius: 2, background: "var(--bg-elevated)" }} />
        <span style={{ position: "absolute", left: "50%", top: 1, width: 1, height: 14, background: "var(--glass-border-hover)" }} />
        <span
          style={{
            position: "absolute",
            top: 6,
            height: 4,
            borderRadius: 2,
            background: tone,
            opacity: solid ? 0.95 : 0.4,
            left: edge >= 0 ? "50%" : `${50 - half}%`,
            width: `${half}%`,
          }}
        />
      </span>

      <span style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 11.5, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: tone, opacity: solid ? 1 : 0.6 }}>
        {edge >= 0 ? "+" : "−"}{Math.abs(edge).toFixed(1)}
        <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}> ±{se.toFixed(1)}</span>
      </span>
    </div>
  );
}
