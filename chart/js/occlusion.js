/* Charto — "is anything DRAWN under this box?"
 *
 * The legends float over the plot. TradingView's answer to that is a plate:
 * a wash of the page's paper behind the text, present WHENEVER the series
 * runs behind it and absent when the corner is empty sky. Charto had the
 * plate but hung it on :hover, which is the wrong trigger twice over — it
 * appears when nothing needs it (the pointer over a legend on blank chart)
 * and, far worse, it is missing exactly when it is needed (candles behind
 * "Supertrend 10 3 · 64,272" and no pointer anywhere near it).
 *
 * So the trigger has to be the fact itself: does the drawn series intersect
 * this rectangle. This module answers that, for any box, in any pane, and is
 * shared by both legends — the indicator rows top-left (js/indlegend.js) and
 * the drawings chips top-right (js/scene.js).
 *
 * WHY GEOMETRY AND NOT PIXELS. Reading the canvas back would be the literal
 * answer, but getImageData over a legend-sized region on every pan, zoom and
 * crosshair move is a GPU→CPU stall per frame, and it cannot run at all until
 * after the library has painted. The series data plus the chart's own two
 * projections (timeToCoordinate, priceToCoordinate) give the same answer from
 * numbers we already have, before anything is drawn.
 *
 * WHAT COUNTS AS DRAWN. Every visible series in the pane: the candles' full
 * high-low span, a histogram's bar from its value down to its base, a line's
 * actual segment between two points (interpolated, so a line that crosses the
 * box without either endpoint inside it still counts). Hidden series — the
 * legend's own eye — do not count, which is the point: hiding a study should
 * take its plate with it.
 *
 * WHAT DOES NOT. Drawings and scene annotations are canvas primitives rather
 * than series, so a chip sitting over nothing but a trendline gets no plate.
 * That is the honest reading of "the chart is behind this": a hairline does
 * not make text unreadable the way a wall of candles does.
 */
"use strict";

const Occlusion = (() => {
  /* `series.data()` is not a getter — it maps the internal rows into fresh
   * objects on every call, so calling it per frame per series is a per-frame
   * allocation of the whole history. Cache it, and let the series' OWN
   * dataChanged signal be what invalidates: no TTL to tune, no staleness. */
  const cache = new WeakMap();
  const sinks = new Set();          // "some series' data moved" → re-probe

  function rowsOf(s) {
    let e = cache.get(s);
    if (!e) {
      e = { rows: null };
      try {
        s.subscribeDataChanged(() => {
          e.rows = null;
          for (const f of sinks) f();
        });
      } catch { /* a series type without the subscription: cache once */ }
      cache.set(s, e);
    }
    if (!e.rows) {
      try { e.rows = s.data() || []; } catch { e.rows = []; }
    }
    return e.rows;
  }

  const OHLC = { Candlestick: true, Bar: true };
  const LINE = { Line: true, Area: true, Baseline: true };

  function create(chart) {
    /** First index whose x is at or past `want`. Binary search on the
     *  PROJECTION rather than on time, because the projection is what the box
     *  is measured in and it is monotonic in time either way. Two of these
     *  per series bound the window to the handful of bars actually under the
     *  legend, so nothing scales with the length of the history. */
    function lowerBound(rows, want) {
      const ts = chart.timeScale();
      let lo = 0, hi = rows.length;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        const c = ts.timeToCoordinate(rows[mid].time);
        if (c === null || c < want) lo = mid + 1; else hi = mid;
      }
      return lo;
    }

    /**
     * @param {number} paneIndex  which pane the boxes float over
     * @param {DOMRect[]} rects   boxes in CLIENT space (getBoundingClientRect)
     * @returns {boolean[]} one verdict per rect, in order
     */
    function probe(paneIndex, rects) {
      const out = rects.map(() => false);
      if (!rects.length) return out;

      /* A frame can be queued before a chart is removed and run after — a
       * closed split pane, a torn-down legend — and every call below would
       * throw on the disposed instance. Nothing is drawn under a chart that
       * no longer exists, so that is simply the answer. */
      let panes = null;
      try { panes = chart.panes ? chart.panes() : null; } catch { return out; }
      const pane = panes && panes[paneIndex];
      const el = pane && pane.getHTMLElement && pane.getHTMLElement();
      if (!el) return out;
      const pr = el.getBoundingClientRect();
      if (!pr.width || !pr.height) return out;

      /* The pane element spans the price axes as well as the plot, but
       * timeToCoordinate measures from the PLOT's left edge — so the axis
       * widths are what reconcile the two frames. (Charto only ever mounts a
       * right scale; the left is asked for anyway rather than assumed zero.) */
      let axisR = 0, axisL = 0;
      try { axisR = chart.priceScale("right").width() || 0; } catch { /* pre-layout */ }
      try { axisL = chart.priceScale("left").width() || 0; } catch { /* none */ }
      const plotL = pr.left + axisL, plotR = pr.right - axisR;

      // client space → (x from the plot's left, y from the pane's top), which
      // is exactly the frame the two projections below speak.
      const boxes = [];
      rects.forEach((r, i) => {
        const x0 = Math.max(r.left, plotL) - plotL;
        const x1 = Math.min(r.right, plotR) - plotL;
        const y0 = Math.max(r.top, pr.top) - pr.top;
        const y1 = Math.min(r.bottom, pr.bottom) - pr.top;
        // A box clipped away entirely — scrolled off, or sitting on the price
        // axis — has nothing behind it by definition.
        if (x1 > x0 && y1 > y0) boxes.push({ i, x0, x1, y0, y1 });
      });
      if (!boxes.length) return out;

      let live = boxes.length;
      let X0 = Infinity, X1 = -Infinity;
      for (const b of boxes) { X0 = Math.min(X0, b.x0); X1 = Math.max(X1, b.x1); }

      /** A box is answered ONCE. Settled boxes drop out of every later test,
       *  so a legend over dense candles costs the first bar it meets. */
      function settle(b) { b.done = true; out[b.i] = true; live--; }

      /** An axis-aligned mark: a candle's high-low, a histogram bar. */
      function mark(xa, xb, ya, yb) {
        if (ya > yb) { const t = ya; ya = yb; yb = t; }
        for (const b of boxes) {
          if (b.done) continue;
          if (xb < b.x0 || xa > b.x1 || yb < b.y0 || ya > b.y1) continue;
          settle(b);
        }
      }

      /** A line segment, clipped to each box's own x-span and interpolated —
       *  the union of the endpoints would call a steep segment a hit across
       *  its whole height, which over a 19px legend row is most of them. */
      function seg(xa, ya, xb, yb) {
        const dx = xb - xa;
        if (!(dx > 0)) { mark(xa, xb, ya, yb); return; }
        for (const b of boxes) {
          if (b.done) continue;
          const cx0 = Math.max(xa, b.x0), cx1 = Math.min(xb, b.x1);
          if (cx1 < cx0) continue;
          const q0 = ya + (yb - ya) * ((cx0 - xa) / dx);
          const q1 = ya + (yb - ya) * ((cx1 - xa) / dx);
          if (Math.max(q0, q1) < b.y0 || Math.min(q0, q1) > b.y1) continue;
          settle(b);
        }
      }

      const ts = chart.timeScale();
      // A bar is a body plus wicks, not a hairline at its centre. .35 of the
      // spacing is LWC's own body half-width at ordinary zooms; the floor
      // keeps a fully zoomed-out chart from measuring a sub-pixel column.
      let spacing = 6;
      try { spacing = ts.options().barSpacing || 6; } catch { /* defaults */ }
      const half = Math.max(0.5, spacing * 0.35);

      for (const s of (pane.getSeries ? pane.getSeries() : [])) {
        if (!live) break;
        let opt = null;
        try { opt = s.options(); } catch { /* torn down mid-frame */ continue; }
        if (opt && opt.visible === false) continue;      // the eye, honoured
        let type = "";
        try { type = s.seriesType(); } catch { /* unknown → skip below */ }

        const rows = rowsOf(s);
        if (!rows.length) continue;
        let i0 = lowerBound(rows, X0 - half) - 1;        // the bar just left…
        let i1 = lowerBound(rows, X1 + half);            // …and the one past
        i0 = Math.max(0, i0);
        i1 = Math.min(rows.length - 1, i1);
        if (i1 < i0) continue;

        if (OHLC[type] || type === "Histogram") {
          const hist = type === "Histogram";
          // A histogram bar runs from its value to its base; the base is a
          // price like any other, so the same projection places it. Volume
          // sits on its own scale and lands near the pane floor, which is
          // where it is drawn.
          let base = null;
          if (hist) {
            const bv = opt && opt.base != null ? opt.base : 0;
            base = s.priceToCoordinate(bv);
            if (base === null) base = pr.height;
          }
          for (let k = i0; k <= i1 && live; k++) {
            const d = rows[k];
            const x = ts.timeToCoordinate(d.time);
            if (x === null) continue;
            let ya, yb;
            if (hist) {
              if (d.value == null) continue;             // whitespace
              ya = s.priceToCoordinate(d.value); yb = base;
            } else {
              if (d.high == null || d.low == null) continue;
              ya = s.priceToCoordinate(d.high); yb = s.priceToCoordinate(d.low);
            }
            if (ya === null || yb === null) continue;
            mark(x - half, x + half, ya, yb);
          }
        } else if (LINE[type]) {
          // Area and Baseline are measured by their STROKE, not their fill:
          // the fill fades to near-nothing away from the line, and treating
          // it as opaque would put a plate under every legend on the chart.
          let px = null, py = null;
          for (let k = i0; k <= i1 && live; k++) {
            const d = rows[k];
            const x = ts.timeToCoordinate(d.time);
            const y = d.value == null ? null : s.priceToCoordinate(d.value);
            // A gap in the data breaks the line — the next point starts a new
            // one rather than joining across the hole the library leaves.
            if (x === null || y === null) { px = null; continue; }
            if (px !== null) seg(px, py, x, y);
            else mark(x - 1, x + 1, y - 1, y + 1);
            px = x; py = y;
          }
        }
      }
      return out;
    }

    return {
      probe,
      /** Fires when any series' data changed — the one repaint trigger the
       *  chart does not announce through the crosshair or the time scale. */
      onData(cb) { sinks.add(cb); return () => sinks.delete(cb); },
    };
  }

  return { create };
})();
