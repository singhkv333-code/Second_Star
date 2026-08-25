/**
 * film-primitives — inert DOM for the product film. No GSAP lives here.
 *
 * Every element the choreography touches carries a `data-film-*` attribute;
 * that attribute set is the animation's API to this markup. The timeline in
 * `film-demo.tsx` reaches everything through `gsap.utils.selector`, so nothing
 * here needs a ref threaded through it, and this file can be edited on its own
 * as long as the data attributes survive.
 *
 * The chrome is a transcription of the shipped app, not an impression of it:
 * the header follows `charto/preview/index.html`'s `<header>` control for
 * control (wordmark · symbol pill · interval pill · indicators · undo/redo ·
 * spacer · eraser · settings · layout name · pane grid · screenshot · Chat ·
 * account), the chat panel follows its `<aside class="chatpanel">` (actions row
 * on top, thread, composer), and the icon paths below are copied from
 * `preview/js/icons.js` so a glyph here is the same glyph there.
 */
"use client";

import * as React from "react";
import {
  CHANGE_PCT,
  LAST,
  SIDEBAR_W,
  TOPBAR_H,
  formatINR,
  type Annotation,
} from "./film-script";
import type { ChartMap } from "./film-chart";

/**
 * Icon paths lifted verbatim from `preview/js/icons.js`. Copied rather than
 * imported because that module is a plain-script IIFE in the prototype, not an
 * ES module this bundle can reach.
 */
const ICONS = {
  indicators: '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="m19 9-5 5-4-4-3 3"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronUp: '<path d="M18 15l-6-6-6 6"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5 5.5 5.5 0 0 1-5.5 5.5H11"/>',
  redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5 5.5 5.5 0 0 0 9.5 20H13"/>',
  eraser:
    '<path d="M21 21H8a2 2 0 0 1-1.42-.587l-3.994-3.999a2 2 0 0 1 0-2.828l10-10a2 2 0 0 1 2.829 0l5.999 6a2 2 0 0 1 0 2.828L12.834 21"/><path d="m5.082 11.09 8.828 8.828"/>',
  settings:
    '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  camera:
    '<path d="M14.5 4h-5L7.2 6.8H4a2 2 0 0 0-2 2V18a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8.8a2 2 0 0 0-2-2h-3.2L14.5 4z"/><circle cx="12" cy="13" r="3.2"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
  arrowUp: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  /** The single-pane grid, as `Icons.layoutSvg` draws it for a 1×1 layout. */
  layout: '<rect x="3" y="3" width="18" height="18" rx="2"/>',
} as const;

/** `Icons.svg(name, size)` — same 24-box, same stroke geometry. */
function Icon({ name, size = "sm" }: { name: keyof typeof ICONS; size?: "sm" | "xs" | "" }) {
  return (
    <svg
      className={`film-icon${size ? ` ${size}` : ""}`}
      viewBox="0 0 24 24"
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: ICONS[name] }}
    />
  );
}

/** The presentation cursor. Outside the zoom wrapper, so it never scales. */
export function FilmCursor() {
  return (
    <>
      <span data-film-ripple className="film-ripple" aria-hidden="true" />
      <svg
        data-film-cursor
        className="film-cursor"
        viewBox="0 0 24 24"
        width="26"
        height="26"
        aria-hidden="true"
      >
        {/* Dark outline under a white fill so the pointer stays legible over
            both the near-black chart and the lighter panel surfaces. */}
        <path
          d="M5 2.5 19.2 12.1 12.4 12.9 9.1 19.6Z"
          fill="#fff"
          stroke="rgba(0,0,0,.55)"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
      </svg>
    </>
  );
}

/**
 * The app header. Same order and same controls as the product's `<header>`;
 * on the narrow composition the middle group stands down exactly where the
 * product's own laptop breakpoint drops it, rather than shrinking everything.
 */
export function FilmTopbar({ narrow }: { narrow: boolean }) {
  return (
    <header className="film-header" style={{ height: TOPBAR_H }}>
      <span className="film-brand">
        Pivot<span className="dot">.</span>
      </span>

      <span className="film-symbol">
        <span className="film-co-logo" aria-hidden="true">
          R
        </span>
        RELIANCE <span className="ex">NSE</span>
      </span>

      <span className="film-vsep" />
      <span className="film-btn film-iv-pill">1D</span>

      {!narrow && (
        <>
          <span className="film-vsep" />
          <span className="film-btn">
            <Icon name="indicators" />
            Indicators
            <Icon name="chevronDown" size="xs" />
          </span>

          <span className="film-vsep" />
          <span className="film-btn icon is-off">
            <Icon name="undo" />
          </span>
          <span className="film-btn icon is-off">
            <Icon name="redo" />
          </span>
        </>
      )}

      <span className="film-spacer" />

      {!narrow && (
        <>
          {/* The eraser only exists while the chat has drawn something, which
              is exactly the window this film spends most of its loop in. */}
          <span className="film-btn icon" data-film-eraser>
            <Icon name="eraser" />
          </span>
          <span className="film-btn icon">
            <Icon name="settings" />
          </span>
          <span className="film-ly-btn">
            <span className="ly-name">Unnamed</span>
            <Icon name="chevronUp" size="xs" />
          </span>
          <span className="film-btn icon">
            <Icon name="layout" />
          </span>
          <span className="film-btn icon">
            <Icon name="camera" />
          </span>
        </>
      )}

      <span className="film-btn film-chat-toggle">
        <Icon name="chat" />
        Chat
      </span>
      <span className="film-acct">A</span>
    </header>
  );
}

/**
 * The chart's in-pane legend — the product's `.readout`: the instrument on one
 * line, then the OHLCV row with the candle's own change beside it. Values are
 * the last bar's, which is what the product shows when the pointer is off the
 * chart.
 */
export function FilmReadout({ narrow }: { narrow: boolean }) {
  const up = LAST.close >= LAST.open;
  const cls = up ? "up" : "down";
  const chg = LAST.close - LAST.open;
  const f = (n: number) => formatINR(n).replace(/\.00$/, "");
  // The product's phone rule drops the figures a narrow pane cannot hold and
  // keeps the close. Wrapping five of them instead pushes the legend down over
  // the candles, which is worse than showing fewer.
  const figures: [string, string][] = narrow
    ? [["C", f(LAST.close)]]
    : [
        ["O", f(LAST.open)],
        ["H", f(LAST.high)],
        ["L", f(LAST.low)],
        ["C", f(LAST.close)],
      ];
  return (
    <div className="film-readout" aria-hidden="true">
      <div className="title">
        <span className="film-co-logo lg">R</span>
        RELIANCE
        <span className="sep">·</span>1D
        <span className="sep">·</span>
        <span className="ex">NSE</span>
      </div>
      <div className="row">
        {figures.map(([k, v]) => (
          <span key={k}>
            <i>{k}</i> <b className={cls}>{v}</b>
          </span>
        ))}
        {!narrow && (
          <span>
            <i>V</i> <b className={cls}>1,81,49,155</b>
          </span>
        )}
        <span className="film-chg">
          <b className={cls}>
            {chg >= 0 ? "+" : "−"}
            {Math.abs(chg).toFixed(1)} ({CHANGE_PCT >= 0 ? "+" : ""}
            {CHANGE_PCT.toFixed(2)}%)
          </b>
        </span>
      </div>
    </div>
  );
}

/**
 * The annotation layer. Renders in pixel space computed from the chart's own
 * coordinate API, so it can only be drawn once `map` exists. Everything starts
 * hidden — the timeline is what reveals it.
 */
export function FilmAnnotations({
  scenes,
  map,
  narrow,
}: {
  scenes: { id: string; annotations: Annotation[] }[];
  map: ChartMap | null;
  narrow: boolean;
}) {
  if (!map) return null;
  const clampX = (x: number) => Math.min(x, map.plotRight);
  // Labels are placed relative to the geometry they annotate, then pinned
  // inside the plot area — otherwise the right-hand ones slide under the price
  // scale on the narrow composition.
  const pinX = (x: number, w: number) => Math.max(2, Math.min(x, map.plotRight - w));
  const LW = narrow ? 122 : 168;
  // EVERY scene's annotations render at once, all hidden. GSAP resolves tween
  // targets when the tween is BUILT, so anything mounted later by React would
  // simply have no target and never animate.
  const annotations = scenes.flatMap((s) =>
    s.annotations.map((a) => ({ ...a, id: `${s.id}-${a.id}`, scene: s.id })),
  );

  return (
    <svg className="film-anno" data-film-anno-layer aria-hidden="true">
      {annotations.map((a) => {
        if (a.kind === "zone") {
          const yTop = map.y(a.to);
          const yBot = map.y(a.from);
          const x0 = map.x(a.i0);
          const x1 = clampX(map.x(a.i1));
          const h = Math.max(6, yBot - yTop);
          return (
            <g key={a.id} data-film-anno={a.id} className={`film-zone tone-${a.tone}`}>
              {/* scaleY grows the band out of its own centre line, which reads
                  as a zone being measured rather than a box being dropped in. */}
              <rect
                data-film-anno-body
                x={x0}
                y={yTop}
                width={Math.max(1, x1 - x0)}
                height={h}
                rx="2"
              />
              <line data-film-anno-edge x1={x0} y1={yTop} x2={x1} y2={yTop} />
              <line data-film-anno-edge x1={x0} y1={yBot} x2={x1} y2={yBot} />
              <g
                data-film-anno-label
                transform={`translate(${pinX(x1 - LW - 4, LW)}, ${yTop + h / 2 - 9})`}
              >
                <rect width={LW} height="18" rx="3" />
                <text x="8" y="13">
                  {narrow ? a.label.split(" · ")[0] : a.label}
                </text>
              </g>
            </g>
          );
        }

        if (a.kind === "line") {
          const p0 = a.points[0]!;
          const p1 = a.points[a.points.length - 1]!;
          const x0 = map.x(p0.i);
          const x1 = clampX(map.x(p1.i));
          const y0 = map.y(p0.price);
          const y1 = map.y(p1.price);
          const len = Math.hypot(x1 - x0, y1 - y0);
          return (
            <g key={a.id} data-film-anno={a.id} className={`film-line tone-${a.tone}`}>
              {/* Stroke-dash draw-on: the dash equals the line length, so
                  animating the offset to 0 walks the stroke into existence. */}
              <line
                data-film-anno-draw
                x1={x0}
                y1={y0}
                x2={x1}
                y2={y1}
                strokeDasharray={a.dashed ? "6 5" : len}
                strokeDashoffset={a.dashed ? 0 : len}
                data-film-len={len}
                data-film-dashed={a.dashed ? "1" : "0"}
              />
              {a.label && (
                <g
                  data-film-anno-label
                  transform={`translate(${pinX(x0 + 8, a.label.length * 6.2 + 16)}, ${
                    Math.min(y0, y1) - 22
                  })`}
                >
                  <rect width={a.label.length * 6.2 + 16} height="17" rx="3" />
                  <text x="8" y="12">
                    {a.label}
                  </text>
                </g>
              )}
            </g>
          );
        }

        const x = map.x(a.i);
        // The event line starts BELOW the readout rather than at the pane's
        // top edge: the legend owns that strip in the product, and a Q3 pill
        // laid over "RELIANCE · 1D · NSE" reads as a rendering fault.
        const MARK_TOP = 52;
        return (
          <g key={a.id} data-film-anno={a.id} className="film-marker">
            <line data-film-anno-draw x1={x} y1={MARK_TOP} x2={x} y2={map.height - 30} />
            <g data-film-anno-label transform={`translate(${pinX(x - 30, 60)}, ${MARK_TOP})`}>
              <rect width="60" height="17" rx="8" />
              <text x="30" y="12" textAnchor="middle">
                {a.label}
              </text>
            </g>
            <g
              data-film-anno-detail
              transform={`translate(${pinX(x - 74, 148)}, ${map.height - 58})`}
            >
              <rect width="148" height="18" rx="3" />
              <text x="74" y="13" textAnchor="middle">
                {a.detail}
              </text>
            </g>
          </g>
        );
      })}
    </svg>
  );
}

/**
 * The chat panel — the product's `.chatpanel`. Actions row on top (new
 * conversation, history), thread, composer pinned to the bottom.
 *
 * The turns follow the product's two shapes exactly: the user's is a quiet
 * right-weighted `.bubble` on `--muted`, and the assistant's is BARE PROSE at
 * full measure with no container, no avatar and no status chip. That second
 * one is the part a mock usually gets wrong — a bordered card reads as a
 * widget, and the product deliberately does not have one.
 */
export function FilmSidebar({ narrow }: { narrow: boolean }) {
  return (
    <aside className="film-chatpanel" style={{ width: narrow ? "100%" : SIDEBAR_W }}>
      {/* The + lives here, at the top of the panel, exactly as the product has
          it: it starts a NEW CONVERSATION. It is not a composer attach button,
          which is why it does not belong beside the send arrow. */}
      <div className="film-chat-actions">
        <span className="film-chat-action" data-film-new>
          <Icon name="plus" />
        </span>
        <span className="film-chat-action">
          <Icon name="clock" />
        </span>
      </div>

      <div className="film-thread" data-film-thread>
        <div className="film-thread-inner">
          <div className="film-turn film-turn-greet" data-film-greeting>
            <div className="film-prose">
              <p>Ask about this chart — structure, events, or what just moved.</p>
            </div>
          </div>

          {/* One user turn and one assistant turn, rewritten per scene rather
              than appended, so the thread never grows past the panel. */}
          <div className="film-turn film-turn-user" data-film-user-msg>
            <div className="film-bubble" data-film-user-text />
          </div>

          <div className="film-turn" data-film-thinking>
            <span className="film-think">
              <i />
              <i />
              <i />
            </span>
          </div>

          <div className="film-turn film-turn-assistant" data-film-answer>
            <div className="film-prose">
              <p data-film-answer-text />
            </div>
            <div className="film-refs" data-film-tags />
          </div>
        </div>
      </div>

      <div className="film-composer-wrap">
        <div className="film-composer" data-film-composer>
          <p className="film-composer-line">
            <span data-film-typed />
            <span className="film-caret" data-film-caret />
            <span className="film-placeholder" data-film-placeholder>
              Ask about this chart…
            </span>
          </p>
          <div className="film-composer-row">
            <span className="film-ctx-flag">
              <span className="film-co-logo" aria-hidden="true">
                R
              </span>
              <span className="sym">RELIANCE</span>
            </span>
            <span className="film-spacer" />
            <span className="film-mic-btn">
              <Icon name="mic" size="xs" />
            </span>
            <span className="film-send" data-film-send aria-hidden="true">
              <Icon name="arrowUp" size="xs" />
            </span>
          </div>
        </div>
      </div>
    </aside>
  );
}

/** Scene counter under the frame — tells the viewer the loop has four beats. */
export function FilmProgress({ count }: { count: number }) {
  return (
    <div className="film-progress" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <span key={i} data-film-dot={i} />
      ))}
    </div>
  );
}
