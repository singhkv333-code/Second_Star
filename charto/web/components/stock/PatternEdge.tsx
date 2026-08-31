"use client";

/**
 * Pattern edge — a hit rate is meaningless without the rate it beat.
 *
 * Every row is one pattern measured against a CONTROL: the base rate of the
 * same directional move over the same horizon on the same bars. A 58% hit
 * rate against a 57% control is noise wearing a pattern's name, and the only
 * column worth reading is the difference.
 *
 * The rows are THIS COMPANY's. They used to be the pooled market ledger, which
 * is keyed by asset CLASS — so every Indian equity rendered a byte-identical
 * table under a heading that read as the company's own. That view then spent a
 * while behind an "All NSE" toggle, which is the same wrong table one click
 * further away: a control on a company page whose entire effect is to replace
 * the company with the market, under a heading that still says the company's
 * name.
 *
 * The question the toggle was really for — "is this shape unusual HERE, or
 * merely typical" — is a comparison, not a second table, so it is drawn as one:
 * the market's own edge rides on every row as a faint tick on the same bar. A
 * reader sees both readings at once, on one scale, which is the only way that
 * question has ever been answerable.
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

import {
  getPatterns, type PatternStat, type PatternsResponse,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { PatternGlyph } from "./PatternGlyph";
import { Segmented, SECTION_GAP } from "./chrome";

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
  // of the list is a long tail of patterns that do nothing. Rows too thin to
  // judge sink below the rest instead of ranking on an edge their sample
  // cannot support: at nine cases a 30pp "edge" is one trade either way.
  const ranked = [...rows].sort((a, b) => {
    const thin = Number(a.enough === false) - Number(b.enough === false);
    if (thin) return thin;
    return Math.abs(b.edge ?? 0) - Math.abs(a.edge ?? 0);
  });
  const shown = all ? ranked : ranked.slice(0, 8);

  return (
    <div style={{ marginTop: SECTION_GAP }}>
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
            fontSize: 21,
            fontWeight: 600,
            letterSpacing: "-0.022em",
            color: "var(--text-primary)",
          }}
        >
          Pattern Edge
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

      {/* One rule in the whole table, under the header. The frame used to be
          eleven: a box around the block and a line between every pair of rows,
          which is more ink than the eight numbers those lines were separating.
          Rows are held apart by their own height now, and the row under the
          pointer lifts on a wash instead. */}
      <div style={{ margin: "0 -10px" }}>
        <div
          className="pat-head pat-row"
          style={{
            display: "grid",
            gridTemplateColumns: "58px minmax(0,1.5fr) 54px 58px 58px minmax(120px, 1.4fr) 92px",
            gap: 12,
            alignItems: "center",
            padding: "6px 10px 10px",
            fontSize: 10.5,
            fontWeight: 650,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            color: "var(--text-primary)",
            borderBottom: "1px solid var(--glass-border)",
          }}
        >
          <span />
          <span>Pattern</span>
          <span style={{ textAlign: "right" }}>Cases</span>
          <span style={{ textAlign: "right" }}>Hit</span>
          <span style={{ textAlign: "right" }}>Control</span>
          <span style={{ textAlign: "center" }}>Edge · market</span>
          <span style={{ textAlign: "right" }}>vs control</span>
        </div>

        {!data
          ? Array.from({ length: 5 }).map((_, i) => (
              <div key={i} style={{ height: 42 }} />
            ))
          : shown.map((p) => <Row key={`${p.kind}-${p.interval}-${p.horizon}`} p={p} />)}
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

function Row({ p }: { p: PatternStat }): React.ReactElement {
  const edge = p.edge ?? 0;
  const se = p.se ?? 0;
  // Two standard errors is the conventional bar for "this is not the sample
  // talking". Below it the row is drawn muted rather than dropped: the reader
  // should see that the pattern was measured and came back inconclusive.
  //
  // Too few cases fails the same way and is drawn the same way, deliberately.
  // Both say "this number is not load-bearing", and giving them two visual
  // languages would suggest they are two different findings.
  const thin = p.enough === false;
  const solid = !thin && Math.abs(edge) >= 2 * se;
  const tone = edge > 0 ? "var(--color-profit)" : edge < 0 ? "var(--color-loss)" : "var(--text-tertiary)";
  const half = Math.min(50, (Math.abs(edge) / SCALE_PP) * 50);

  // The same pattern's edge across the whole market, on the same scale. This
  // is what the "All NSE" scope was for, and it belongs here rather than in a
  // table of its own: the finding is the DISTANCE between the two, and two
  // tables cannot show a distance.
  const mkt = p.market_edge ?? null;
  const mktHalf = mkt === null ? null
    : Math.min(50, (Math.abs(mkt) / SCALE_PP) * 50);
  const mktLeft = mkt === null || mktHalf === null ? null
    : mkt >= 0 ? 50 + mktHalf : 50 - mktHalf;

  return (
    <div
      className="pat-row"
      style={{
        display: "grid",
        gridTemplateColumns: "58px minmax(0,1.5fr) 54px 58px 58px minmax(120px, 1.4fr) 92px",
        gap: 12,
        alignItems: "center",
        minHeight: 42,
        padding: "0 10px",
        borderRadius: 10,
        transition: "background 120ms ease",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-hover)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
    >
      <PatternGlyph kind={p.kind} />
      <span style={{ fontSize: 13.5, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {pretty(p.kind)}
      </span>
      <span
        title={thin ? `${p.n} cases — too few to read an edge from` : undefined}
        style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 11.5, fontVariantNumeric: "tabular-nums", color: thin ? "var(--text-tertiary)" : "var(--text-secondary)" }}
      >
        {compactN(p.n)}
      </span>
      <span style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12.5, fontVariantNumeric: "tabular-nums", color: "var(--text-primary)" }}>
        {p.rate !== null ? `${p.rate.toFixed(1)}%` : "—"}
      </span>
      <span className="pat-control" style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 11.5, fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)" }}>
        {p.control !== null ? `${p.control.toFixed(1)}%` : "—"}
      </span>

      {/* The diverging bar. The centre rule is the control, so the bar is
          literally the distance from it. */}
      <span style={{ position: "relative", height: 18, display: "block" }}>
        <span style={{ position: "absolute", inset: 0, top: 6, height: 6, borderRadius: 3, background: "var(--surface-track)" }} />
        {/* The control is a tick the height of the bar, not a rule through the
            row: it marks where zero is, and it only has to reach as far as the
            thing it is marking. */}
        <span style={{ position: "absolute", left: "50%", top: 4, width: 1, height: 10, background: "var(--glass-border-hover)" }} />
        <span
          style={{
            position: "absolute",
            top: 6,
            height: 6,
            borderRadius: 3,
            background: tone,
            opacity: solid ? 1 : 0.38,
            left: edge >= 0 ? "50%" : `${50 - half}%`,
            width: `${half}%`,
          }}
        />
        {/* Where the market sits on the same scale. A hairline, not a second
            bar: it is the reference the company's bar is read against, and
            drawing it with equal weight would make the row an argument
            between two measurements rather than one measurement in context. */}
        {mktLeft !== null ? (
          <span
            aria-hidden
            style={{
              position: "absolute",
              left: `${mktLeft}%`,
              top: 2,
              width: 1,
              height: 14,
              background: "var(--text-tertiary)",
              opacity: 0.75,
            }}
          />
        ) : null}
      </span>

      <span
        title={mkt !== null
          ? `${pretty(p.kind)}: ${edge >= 0 ? "+" : "−"}${Math.abs(edge).toFixed(1)}pp here `
            + `vs ${mkt >= 0 ? "+" : "−"}${Math.abs(mkt).toFixed(1)}pp across the market`
            + (p.market_n ? ` (${compactN(p.market_n)} instances)` : "")
          : undefined}
        style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: tone, opacity: solid ? 1 : 0.6 }}
      >
        {edge >= 0 ? "+" : "−"}{Math.abs(edge).toFixed(1)}
        <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}> ±{se.toFixed(1)}</span>
      </span>
    </div>
  );
}
