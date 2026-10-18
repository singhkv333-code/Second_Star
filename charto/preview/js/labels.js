/* Charto preview — captions written ON the drawings, where there is room.
 *
 * The chat's annotations used to name themselves in a list stacked in the
 * top-right corner. That kept the candles clear, but on a marked-up chart the
 * list became a wall of text over the OHLC readout, and a reader had to match
 * each line to its shape by colour. A caption belongs to its shape.
 *
 * What made captions-on-shapes bad before was collision: eight labels laid
 * blindly beside eight shapes land on each other and on the bars. So this
 * module PLACES them. Each annotation offers a few candidate spots on or
 * beside its own geometry (along a level, inside a zone, along a trendline,
 * up a vertical line), and a greedy pass takes the annotations in priority
 * order and gives each the cheapest spot that:
 *   - stays inside the plot,
 *   - touches no caption already placed and none of the chart's own chrome
 *     (the OHLC readout, the indicator legend, the layers control),
 *   - and covers as few candles as it can (a skyline of the visible bars).
 * A caption with nowhere clean to go is left off; its shape still answers
 * the pointer, and the hover card carries the full text.
 *
 * Pure geometry: no canvas, no chart. scene.js measures and paints.
 */
"use strict";

const Labels = (() => {
  const LINE_H = 12;      // text box height at the 10px caption size
  const PAD = 2;          // air either side of the words
  const GAP = 2;          // the closest two captions may sit
  const EDGE = 2;         // the closest a caption may sit to the plot's edge
  const MAX_CHARS = 28;

  /** The caption the chart can afford. Detector labels are written for a
   *  card ("Support zone 1,293 · 3 touches, 0 of 1 re-tests held"); on the
   *  chart the first clause says what the shape IS, and the rest belongs in
   *  the hover card. */
  function shorten(text, max = MAX_CHARS) {
    let s = String(text == null ? "" : text).split(" · ")[0].replace(/\s+/g, " ").trim();
    if (s.length <= max) return s;
    // "POC / near-term balance 1,304" → "POC 1,304": the name before the
    // slash and the number the line is AT, never a number cut in half
    const num = (s.match(/[-+]?₹?\d[\d,]*(?:\.\d+)?%?$/) || [""])[0];
    if (s.includes(" / ")) {
      const head = s.split(" / ")[0].trim();
      s = num && !head.endsWith(num) ? `${head} ${num}` : head;
    }
    if (s.length > max) s = s.replace(/\s*\([^)]*\)/g, "").trim();
    if (s.length > max) {
      const cut = s.slice(0, max - 1);
      const sp = cut.lastIndexOf(" ");
      s = (sp > max * 0.5 ? cut.slice(0, sp) : cut).replace(/[\s,/·-]+$/, "") + "…";
    }
    return s;
  }

  /** Where the bars are, by column. Each STEP-px column keeps the union of
   *  every candle's high-low that falls in it, so asking "how much of this
   *  box is candles" is a walk over a handful of columns, not over the bars. */
  function sky(width, step = 3) {
    const n = Math.max(1, Math.ceil(width / step));
    const top = new Float32Array(n).fill(Infinity);
    const bot = new Float32Array(n).fill(-Infinity);
    return {
      add(x0, x1, y0, y1) {
        if (!Number.isFinite(x0 + x1 + y0 + y1)) return;
        if (y0 > y1) { const t = y0; y0 = y1; y1 = t; }
        const a = Math.max(0, Math.floor(x0 / step));
        const b = Math.min(n - 1, Math.floor(x1 / step));
        for (let i = a; i <= b; i++) {
          if (y0 < top[i]) top[i] = y0;
          if (y1 > bot[i]) bot[i] = y1;
        }
      },
      /** Share of the box's columns whose candles reach into it, 0..1. */
      cover(r) {
        const a = Math.max(0, Math.floor(r.x0 / step));
        const b = Math.min(n - 1, Math.floor(r.x1 / step));
        if (b < a) return 0;
        let hit = 0;
        for (let i = a; i <= b; i++) if (top[i] <= r.y1 && bot[i] >= r.y0) hit++;
        return hit / (b - a + 1);
      },
    };
  }

  /** The box a candidate puts the text in. (x, y) is the anchor; ax/ay say
   *  which point of the box sits on it (0 left/top, .5 centre, 1 right/bottom).
   *  A vertical caption (rot) swaps the box's sides. */
  function boxOf(c, tw) {
    const w = c.rot ? LINE_H : tw + PAD * 2;
    const h = c.rot ? tw + PAD * 2 : LINE_H;
    const x0 = c.x - (c.ax ?? 0) * w, y0 = c.y - (c.ay ?? 0) * h;
    return { x0, y0, x1: x0 + w, y1: y0 + h };
  }

  const meets = (a, b, g) => a.x0 < b.x1 + g && b.x0 < a.x1 + g && a.y0 < b.y1 + g && b.y0 < a.y1 + g;

  /**
   * @param {Array} reqs  [{id, text, col, prio, hot, cands: [{x, y, ax, ay, rot}]}]
   * @param {Object} o    {w, h, measure(text) -> px, blocked: [rect], sky, max, prev: Map id -> cand index}
   * @returns {Array}     [{id, text, col, rot, busy, x0, y0, x1, y1, cx, cy, k}] — what to paint;
 *                      `busy` means lines or bars run behind it, so it wants a plate
   */
  function place(reqs, o) {
    const out = [];
    const taken = (o.blocked || []).slice();
    const order = reqs.filter((r) => r.text && r.cands && r.cands.length)
      .sort((a, b) => (b.hot - a.hot) || (b.prio - a.prio));
    let quiet = 0;
    for (const r of order) {
      // a caption running up a vertical line sits in its own thin column, so
      // it is not what crowds a chart; only the horizontal ones spend the budget
      const upright = r.cands.every((c) => c.rot);
      if (!r.hot && !upright && o.max != null && quiet >= o.max) continue;
      const tw = o.measure(r.text);
      let best = null, bestCost = Infinity;
      r.cands.forEach((c, k) => {
        const b = boxOf(c, tw);
        if (b.x0 < EDGE || b.y0 < EDGE || b.x1 > o.w - EDGE || b.y1 > o.h - EDGE) return;
        for (const t of taken) if (meets(b, t, GAP)) return;
        // earlier candidates are the shape's preferred spots; candles under
        // the words cost most; last frame's spot is kept unless it went bad,
        // so captions do not hop about while the chart pans
        const under = o.sky ? o.sky.cover(b) : 0;
        let crossed = 0;
        // another shape's line through the words reads as a strike-through
        for (const y of o.hlines || []) if (y > b.y0 + 1 && y < b.y1 - 1) crossed++;
        for (const x of o.vlines || []) if (x > b.x0 + 1 && x < b.x1 - 1) crossed++;
        let cost = k * 0.4 + under * 8 + crossed * 1.5;
        if (o.prev && o.prev.get(r.id) === k) cost -= 3;
        if (cost < bestCost) { bestCost = cost; best = { b, c, k, busy: crossed > 0 || under > 0.15 }; }
      });
      if (!best) continue;
      taken.push(best.b);
      if (!r.hot && !upright) quiet++;
      out.push({ id: r.id, text: r.text, col: r.col, hot: !!r.hot, rot: !!best.c.rot, k: best.k,
                 busy: best.busy,
                 ...best.b, cx: (best.b.x0 + best.b.x1) / 2, cy: (best.b.y0 + best.b.y1) / 2 });
    }
    return out;
  }

  /* ── candidate spots, per shape ─────────────────────────────────────── */
  const G = 3;  // gap between a line and the caption that names it

  /** A horizontal line across the plot: right end first (where a trader
   *  reads levels, next to the price axis), then the left end, then middle;
   *  above the line before below it. `x1` stops short of a volume profile. */
  function alongLevel(y, w, x1 = w - 6) {
    const xs = [[x1, 1], [6, 0], [w * 0.5, 0.5], [w * 0.72, 0.5], [w * 0.28, 0.5]];
    const out = [];
    for (const [x, ax] of xs) {
      out.push({ x, y: y - G, ax, ay: 1 });
      out.push({ x, y: y + G, ax, ay: 0 });
    }
    return out;
  }

  /** A band: inside it when it is tall enough to hold a line of text, at
   *  either edge; otherwise just outside its top or bottom edge. */
  function alongZone(yTop, yBot, w, x1 = w - 6) {
    const out = [];
    const xs = [[x1, 1], [6, 0], [w * 0.5, 0.5]];
    if (yBot - yTop >= LINE_H + 4) {
      for (const [x, ax] of xs) {
        out.push({ x, y: yTop + 2, ax, ay: 0 });
        out.push({ x, y: yBot - 2, ax, ay: 1 });
      }
    }
    for (const [x, ax] of xs) {
      out.push({ x, y: yTop - G, ax, ay: 1 });
      out.push({ x, y: yBot + G, ax, ay: 0 });
    }
    return out;
  }

  /** A line between two points: spots along it, above and below, and to
   *  either side for a steep one. An arrow is named near its head, where
   *  the claim it makes ends up. */
  function alongSegment(p, q, arrow = false) {
    const ts = arrow ? [0.82, 0.65, 0.92, 0.5] : [0.5, 0.33, 0.67, 0.18, 0.82];
    const out = [];
    for (const t of ts) {
      const x = p[0] + (q[0] - p[0]) * t, y = p[1] + (q[1] - p[1]) * t;
      out.push({ x, y: y - G - 1, ax: 0.5, ay: 1 });
      out.push({ x, y: y + G + 1, ax: 0.5, ay: 0 });
      out.push({ x: x + 6, y, ax: 0, ay: 0.5 });
      out.push({ x: x - 6, y, ax: 1, ay: 0.5 });
    }
    return out;
  }

  /** A vertical line: the caption runs up the line, at its top or foot,
   *  on whichever side is free. Six results lines a few bars apart can all
   *  be named this way; side by side as horizontal text they cannot. */
  function alongVline(x, h) {
    return [
      { x: x + 2, y: 6, ax: 0, ay: 0, rot: true },
      { x: x - 2, y: 6, ax: 1, ay: 0, rot: true },
      { x: x + 2, y: h - 6, ax: 0, ay: 1, rot: true },
      { x: x - 2, y: h - 6, ax: 1, ay: 1, rot: true },
      { x: x + 2, y: h * 0.5, ax: 0, ay: 0.5, rot: true },
    ];
  }

  /** A point-like anchor (a pattern's first swing, a box corner, a dot):
   *  the four corners around it, above before below. */
  function aroundPoint(x, y) {
    return [
      { x, y: y - 5, ax: 0.5, ay: 1 },
      { x: x + 5, y: y - 3, ax: 0, ay: 1 },
      { x: x - 5, y: y - 3, ax: 1, ay: 1 },
      { x, y: y + 5, ax: 0.5, ay: 0 },
      { x: x + 5, y: y + 3, ax: 0, ay: 0 },
      { x: x - 5, y: y + 3, ax: 1, ay: 0 },
    ];
  }

  return { shorten, sky, place, boxOf, alongLevel, alongZone, alongSegment, alongVline,
           aroundPoint, LINE_H, PAD };
})();

if (typeof module !== "undefined" && module.exports) module.exports = Labels;
