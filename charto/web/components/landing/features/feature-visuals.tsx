/**
 * feature-visuals — the product fragments the features section quotes.
 *
 * These are transcriptions, not impressions. Every panel below follows the
 * markup and the vocabulary of the thing it stands for in the shipped
 * prototype:
 *
 *   ProductFrame  → the app's pane chrome: a titled head over a body, and
 *                   nothing under it — the app has no footer on a pane either
 *   AskedLine     → the thread's user turn (`.turn.user .bubble`)
 *   LevelsPanel   → the level list a `get_levels` reply prints
 *   MovePanel     → the `move` card (`cards.js` → `move`)
 *   RungsPanel    → the `timeframes` card's `.scan-read` rows
 *   ScreenPanel   → a `screen_universe` result, rows + as-of footer
 *   AlertPanel    → the alerts widget row (`.al-row`)
 *   PlanPanel / JournalPanel / RecallLine → plan_position and list_trades
 *   DetectionChips → get_levels + get_patterns + volume_profile, one chip each
 *   WorkspacePanel → the pane grid (`panes.js` SPECS), a saved layout's
 *                    per-pane symbol/interval, and `indicators.js` legends
 *   FundamentalsPanel → the company page's KeyMetricsStrip + FinancialsPanel
 *   ChartOpsPanel  → the view operations chat can perform on the workspace
 *
 * Nothing here computes. Every number arrives from `feature-data` already
 * decided, exactly as the real panels take theirs from the tool payload — a
 * card that derived its own figure could disagree with the prose beside it,
 * which is the one thing these panels exist to prevent.
 *
 * They are also inert by construction: no inputs, no handlers, no fetches, no
 * storage. A landing-page demo cannot place, persist, or change anything,
 * because there is nothing in it that could.
 *
 * UNPLACED, deliberately kept: `MovePanel`, and now `WorkspacePanel`,
 * `PlanPanel`, `JournalPanel` and `RecallPanel`. The workspace and journal
 * slots are figures rather than panes — see `feature-figures` for why — but
 * these are faithful transcriptions of shipped surfaces, and the transcription
 * is the expensive half. They cost nothing but the lines; delete them when the
 * surfaces they quote change, not because the page stopped pointing at them.
 */
"use client";

import * as React from "react";
import {
  DEMO_ALERT,
  DEMO_CHART_OPS,
  DEMO_DETECTIONS,
  DEMO_EXCHANGE,
  DEMO_FUNDAMENTALS,
  DEMO_INDICATORS,
  DEMO_JOURNAL,
  DEMO_LAYOUTS,
  DEMO_LEVELS,
  DEMO_MOVE,
  DEMO_PANES,
  DEMO_PLAN,
  DEMO_RECALL,
  DEMO_RUNGS,
  DEMO_SCREEN,
  DEMO_SYMBOL,
} from "./feature-data";

/* ── shared chrome ───────────────────────────────────────────────────────── */

/**
 * The app surface a fragment sits on. `head` is the chart pane's readout line
 * when there is a chart, and the panel's own title otherwise.
 */
export function ProductFrame({
  head,
  flush = false,
  children,
}: {
  head?: React.ReactNode;
  /** Chart panes bleed to the frame edge; text panels get the inset. */
  flush?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="cf-frame" aria-hidden="true">
      {head && <div className="cf-frame-head">{head}</div>}
      <div className={`cf-frame-body${flush ? " flush" : ""}`}>{children}</div>
    </div>
  );
}

/** The instrument line the chart pane carries — the product's `.readout`. */
export function Readout({ interval = "1D" }: { interval?: string }) {
  return (
    <span className="cf-readout">
      <span className="cf-logo">R</span>
      {DEMO_SYMBOL}
      <i>·</i>
      {interval}
      <i>·</i>
      <em>{DEMO_EXCHANGE}</em>
    </span>
  );
}

/** A user turn as the thread draws one: quiet, right-weighted, on `--muted`. */
export function AskedLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="cf-asked">
      <span>{children}</span>
    </p>
  );
}

/** The stat strip every card opens with — `.scan-stats`. */
function Stats({
  items,
}: {
  items: readonly { k: string; v: string; q?: string; tone?: "up" | "down" }[];
}) {
  return (
    <div className="cf-stats">
      {items.map((s) => (
        <div key={s.k} className="cf-stat">
          <span className="k">{s.k}</span>
          <b className={`v${s.tone ? ` ${s.tone}` : ""}`}>{s.v}</b>
          {s.q && <span className="q">{s.q}</span>}
        </div>
      ))}
    </div>
  );
}

/** A titled band inside a card — `.scan-sec`. */
function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="cf-sec">
      <h4>
        {title}
        {note && <span className="n">{note}</span>}
      </h4>
      {children}
    </div>
  );
}

/* ── 01 · levels that carry their own record ─────────────────────────────── */

export function LevelsPanel() {
  return (
    <div className="cf-levels">
      {DEMO_LEVELS.map((l) => (
        <div key={l.id} className={`cf-level ${l.role}`}>
          <b className="px">{l.price}</b>
          <span className={`badge ${l.tone}`}>{l.record}</span>
          <span className="src">{l.from}</span>
          <span className="rec">
            {l.touches}
            <i>·</i>
            {l.reaction}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── 02 · why it moved, or that nobody knows ─────────────────────────────── */

export function MovePanel() {
  return (
    <div className="cf-move">
      <Stats items={DEMO_MOVE.stats} />

      <Section title="How much of it the index already explains">
        <div className="cf-bars">
          {DEMO_MOVE.attribution.map((b) => (
            <div key={b.label} className="cf-bar">
              <span className="lb">{b.label}</span>
              <b className="num">{b.value}</b>
              <span className="track">
                <i className={`fill ${b.fill}`} style={{ width: `${b.width}%` }} />
              </span>
            </div>
          ))}
        </div>
      </Section>

      <Section title="The answer">
        <p className="cf-verdict">{DEMO_MOVE.verdict}</p>
        <p className="cf-verdict-note">{DEMO_MOVE.verdictNote}</p>
        <ul className="cf-context">
          {DEMO_MOVE.context.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

/* ── 03 · every timeframe, measured the same way ─────────────────────────── */

export function RungsPanel() {
  return (
    <div className="cf-rungs">
      {DEMO_RUNGS.map((r) => (
        <div
          key={r.label}
          className={`cf-read ${r.stance === "Bullish" ? "up" : "down"}`}
        >
          <b className="nm">{r.label}</b>
          <span className="nt">{r.reads}</span>
          <b className="num">{r.stance}</b>
        </div>
      ))}
    </div>
  );
}

/* ── 04 · one question, the whole universe ───────────────────────────────── */

export function ScreenPanel() {
  return (
    <div className="cf-screen">
      <div className="cf-rows">
        {DEMO_SCREEN.rows.map((r) => (
          <div key={r.sym} className="cf-row">
            <b className="what">{r.sym}</b>
            <span className="when">{r.note}</span>
            <span className="num">{r.num}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── 05 · say it once, and it watches ────────────────────────────────────── */

export function AlertPanel() {
  return (
    <div className="cf-alert">
      <div className="cf-al-row">
        <i className="dot" />
        <div className="main">
          <div className="sym">
            {DEMO_SYMBOL}
            <em>{DEMO_EXCHANGE}</em>
          </div>
          {DEMO_ALERT.conditions.map((c, i) => (
            <div key={c.left} className="cond">
              {i > 0 && <span className="and">and</span>}
              {c.left} {c.op} <b>{c.right}</b>
            </div>
          ))}
          <div className="meta">{DEMO_ALERT.meta}</div>
        </div>
        <span className="state">{DEMO_ALERT.state}</span>
      </div>
      <p className="cf-al-note">{DEMO_ALERT.note}</p>
    </div>
  );
}

/* ── 06 · it remembers what you did ──────────────────────────────────────── */

export function PlanPanel() {
  return (
    <div className="cf-plan">
      <div className="cf-plan-rows">
        {DEMO_PLAN.rows.map((r) => (
          <div key={r.k} className="cf-plan-row">
            <span className="k">{r.k}</span>
            <b className="v">{r.v}</b>
            <span className="q">{r.q}</span>
          </div>
        ))}
      </div>
      <p className="cf-plan-foot">
        <b>{DEMO_PLAN.rr}</b>
        <span>{DEMO_PLAN.breakeven}</span>
      </p>
      <p className="cf-plan-basis">Built on {DEMO_PLAN.basis}</p>
    </div>
  );
}

export function JournalPanel() {
  return (
    <div className="cf-journal">
      <Stats items={DEMO_JOURNAL.stats} />
      <p className="cf-journal-foot">{DEMO_JOURNAL.adherence}</p>
      <p className="cf-journal-last">
        <span>{DEMO_JOURNAL.last.what}</span>
        <i>{DEMO_JOURNAL.last.when}</i>
        <b>{DEMO_JOURNAL.last.r}</b>
      </p>
    </div>
  );
}

export function RecallPanel() {
  return (
    <div className="cf-recalls">
      {DEMO_RECALL.map((r) => (
        <div key={r.when} className="cf-recall">
          <span className="when">{r.when}</span>
          <p>{r.line}</p>
        </div>
      ))}
    </div>
  );
}

/* ── 01 · what the three structure passes found ──────────────────────────── */

/** One chip per detector, above the level list: patterns, levels and zones are
 *  three separate passes and the strip is what stops them reading as one. */
export function DetectionChips() {
  return (
    <div className="cf-dets">
      {DEMO_DETECTIONS.map((d) => (
        <span key={d.k} className={`cf-det ${d.tone}`}>
          <b>{d.k}</b>
          {d.v}
        </span>
      ))}
    </div>
  );
}

/* ── 02 · the workspace ──────────────────────────────────────────────────── */

/**
 * A layout as the menu draws it: the SPEC's rows become `grid-template-areas`
 * and each distinct letter becomes one cell. Same source of truth as the app —
 * a glyph cannot drift from a layout it is generated from.
 */
function LayoutGlyph({ spec }: { spec: readonly string[] }) {
  const areas = Array.from(new Set(spec.join("")));
  return (
    <i
      className="cf-ws-glyph"
      style={{
        gridTemplateAreas: spec.map((r) => `"${[...r].join(" ")}"`).join(" "),
      }}
    >
      {areas.map((a) => (
        <span key={a} style={{ gridArea: a }} />
      ))}
    </i>
  );
}

/** A pane's price path — a polyline over a fixed 100 × 26 box, so every pane
 *  is drawn on the same scale and the shapes stay comparable. */
function Spark({ points, tone }: { points: readonly number[]; tone: "up" | "down" }) {
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const d = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * 100;
      const y = 24 - ((p - lo) / span) * 22;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className={`cf-ws-spark ${tone}`} viewBox="0 0 100 26" preserveAspectRatio="none">
      <polyline points={d} fill="none" strokeWidth="1.4" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function WorkspacePanel() {
  return (
    <div className="cf-ws">
      <div className="cf-ws-lays">
        {DEMO_LAYOUTS.map((l) => (
          <span key={l.id} className={`cf-ws-lay${"on" in l && l.on ? " on" : ""}`}>
            <LayoutGlyph spec={l.spec} />
            {l.label}
          </span>
        ))}
      </div>

      <div className="cf-ws-grid">
        {DEMO_PANES.map((p) => (
          <div key={p.sym} className={`cf-ws-pane${"main" in p && p.main ? " main" : ""}`}>
            <span className="hd">
              <b>{p.sym}</b>
              <i>{p.interval}</i>
            </span>
            <Spark points={p.spark} tone={p.tone} />
            <span className="ft">{p.note}</span>
          </div>
        ))}
      </div>

      <div className="cf-ws-inds">
        {DEMO_INDICATORS.map((i) => (
          <code key={i}>{i}</code>
        ))}
      </div>
    </div>
  );
}

/* ── 06 · the company page, quoted ───────────────────────────────────────── */

export function FundamentalsPanel() {
  return (
    <div className="cf-fun">
      <div className="cf-fun-tiles">
        {DEMO_FUNDAMENTALS.tiles.map((t) => (
          <div key={t.k} className="cf-fun-tile">
            <span className="k">{t.k}</span>
            <b className="v">{t.v}</b>
          </div>
        ))}
      </div>

      <Section title="Financial performance" note={DEMO_FUNDAMENTALS.unit}>
        <div className="cf-fun-rows">
          <div className="cf-fun-row head">
            <span className="k" />
            {DEMO_FUNDAMENTALS.years.map((y) => (
              <b key={y}>{y}</b>
            ))}
          </div>
          {DEMO_FUNDAMENTALS.rows.map((r) => (
            <div key={r.k} className="cf-fun-row">
              <span className="k">{r.k}</span>
              {r.vals.map((v, i) => (
                <b key={DEMO_FUNDAMENTALS.years[i]}>{v}</b>
              ))}
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}

/* ── 08 · the chart doing what the sentence said ─────────────────────────── */

export function ChartOpsPanel() {
  return (
    <div className="cf-ops">
      {DEMO_CHART_OPS.map((o) => (
        <div key={o.said} className="cf-op">
          <span className="said">{o.said}</span>
          <span className="did">
            <code>{o.tool}</code>
            {o.did}
          </span>
        </div>
      ))}
    </div>
  );
}
