/* Charto preview — the tool catalogue.
 *
 * A tool is DATA: how many anchors it takes, and how those anchors compose
 * into primitives. Rendering, hit-testing, dragging, persistence and
 * pane-routing all live in the runtime, so a new tool cannot introduce a
 * geometry bug — it has no geometry of its own.
 *
 * build(a, ctx) -> [primitive]
 *   a   : anchors, [{t, v}], already in the units of the pane it was drawn in
 *   ctx : { fmt, fmtPct, bars, barsBetween, valuesBetween, isPrice }
 */
"use strict";

const Tools = (() => {
  const G = Geo;
  const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
  // never the candle red/green — those mean "closed down / up"
  const FIB_COLORS = ["#787b86", "#f5a524", "#ff9800", "#c084fc", "#22d3ee", "#4ea8f2", "#787b86"];

  const mid = (a, b) => ({ t: (a.t + b.t) / 2, v: (a.v + b.v) / 2 });

  /* ── the pitchfork family ───────────────────────────────────────────────
   * Four tools, ONE construction. Given a handle origin and the two pivots
   * that form the base, a pitchfork is:
   *
   *   base    the segment through the two outer pivots (a handle you grab,
   *           drawn thin and dashed so it never competes with the tines)
   *   median  origin → the base's midpoint, extended right for ever
   *   tines   the same heading, translated onto each outer pivot
   *
   * The heading is a DATA-space vector (Δtime, Δvalue), so the three tines
   * stay parallel at every zoom level — the rule the whole geometry module
   * is built on. The variants differ in two arguments and nothing else:
   * where the handle starts, and how far the outer tines are pulled in
   * toward the median. Writing four near-identical builders would have been
   * four chances for them to stop agreeing about the other five things a
   * pitchfork is.
   *
   * `inset` is a fraction of the distance from each outer pivot to the
   * median's foot: 0 leaves the tines ON the pivots (every variant but one),
   * 0.5 draws the fork at half width.
   */
  function pitchfork(origin, p1, p2, inset = 0) {
    const m = mid(p1, p2);
    const dt = m.t - origin.t, dv = m.v - origin.v;
    const along = (p) => ({ t: p.t + dt, v: p.v + dv });
    const pull = (p) => (inset
      ? { t: p.t + (m.t - p.t) * inset, v: p.v + (m.v - p.v) * inset }
      : p);
    const r1 = pull(p1), r2 = pull(p2);
    return [
      G.segment(p1, p2, { dash: [4, 4], width: 1 }),
      G.segment(origin, m, { extend: "right" }),
      G.segment(r1, along(r1), { extend: "right" }),
      G.segment(r2, along(r2), { extend: "right" }),
    ];
  }

  const SPECS = {
    // ── lines ────────────────────────────────────────────
    trend: { label: "Trend line", anchors: 2, group: "lines", section: "lines",
      key: "T",
      build: (a) => [G.segment(a[0], a[1])] },

    ray: { label: "Ray", anchors: 2, group: "lines", section: "lines",
      build: (a) => [G.segment(a[0], a[1], { extend: "right", arrow: true })] },

    /* The trend line that STATES what it spans. Same two anchors, same
     * stroke — the difference is that you do not have to read the axes to
     * know what the move was worth. */
    infoLine: { label: "Info line", anchors: 2, group: "lines", section: "lines",
      build: (a, c) => {
        const d = a[1].v - a[0].v;
        const pct = a[0].v ? c.fmtPct((d / a[0].v) * 100) : "—";
        return [G.segment(a[0], a[1]),
                G.label(a[1], `${d >= 0 ? "+" : ""}${c.fmt(d)}  (${pct})`
                              + `  ·  ${c.barsBetween(a[0].t, a[1].t)} bars`)];
      } },

    extended: { label: "Extended line", anchors: 2, group: "lines", section: "lines",
      build: (a) => [G.segment(a[0], a[1], { extend: "both" })] },

    /* The slope, as an angle, against a horizontal drawn from the first
     * anchor — the angle needs something to be an angle FROM, and a dashed
     * reference leg is cheaper to read than a protractor.
     *
     * The degrees are measured on SCREEN (see buildCtx.degrees), so the
     * figure moves as you zoom. That is the honest behaviour and the one
     * TradingView has: an angle between a price axis and a time axis is a
     * fact about the two scales, not about the two anchors. The percent and
     * the bar count beside it are the parts that do not move, so the label
     * says all three and the reader can see which is which. */
    trendAngle: { label: "Trend angle", anchors: 2, group: "lines", section: "lines",
      build: (a, c, d) => {
        const deg = c.degrees(a[0], a[1], d && d.pane);
        const pct = a[0].v ? c.fmtPct(((a[1].v - a[0].v) / a[0].v) * 100) : "—";
        const ref = { t: a[1].t, v: a[0].v };
        return [
          G.segment(a[0], a[1]),
          G.segment(a[0], ref, { dash: [3, 3], width: 1 }),
          G.label(ref, `${deg === null ? "—" : deg.toFixed(1) + "°"}`
                       + `  ·  ${pct} over ${c.barsBetween(a[0].t, a[1].t)} bars`),
        ];
      } },

    hline: { label: "Horizontal line", anchors: 1, group: "lines", section: "lines",
      key: "H",
      build: (a, c) => [G.hline(a[0].v), G.label(a[0], c.fmt(a[0].v))] },

    /* A level that only claims the future. One anchor, and the stroke runs
     * right from it — the shape of "this level holds from here on", which a
     * full-width horizontal cannot say. The price chip goes on the LEFT,
     * because the right is where the line is. */
    hray: { label: "Horizontal ray", anchors: 1, group: "lines", section: "lines",
      key: "J",
      build: (a, c) => [
        // 500 bars is past the right edge at every zoom this chart allows;
        // the point of it is only to fix the heading as exactly horizontal
        // before the projector clips the ray to the pane.
        G.segment(a[0], { t: a[0].t + 500 * c.iv, v: a[0].v }, { extend: "right" }),
        G.label(a[0], c.fmt(a[0].v), { align: "left" }),
      ] },

    vline: { label: "Vertical line", anchors: 1, group: "lines", section: "lines",
      key: "V",
      build: (a) => [G.vline(a[0].t)] },

    crossline: { label: "Crossline", anchors: 1, group: "lines", section: "lines",
      key: "C",
      build: (a, c) => [G.hline(a[0].v), G.vline(a[0].t), G.label(a[0], c.fmt(a[0].v))] },

    // ── channels ─────────────────────────────────────────
    channel: { label: "Parallel channel", anchors: 3, group: "lines", section: "channels",
      key: "P",
      // third anchor sets the width; the copy keeps the SAME data-space
      // slope, so the two edges stay parallel at every zoom level
      build: (a) => {
        const off = a[2].v - G.valueAt(a[0], a[1], a[2].t);
        const c0 = { t: a[0].t, v: a[0].v + off }, c1 = { t: a[1].t, v: a[1].v + off };
        return [G.segment(a[0], a[1]), G.segment(c0, c1),
                G.poly([a[0], a[1], c1, c0], { closed: true, fill: true })];
      } },

    regression: { label: "Regression trend", anchors: 2, group: "lines", section: "channels",
      // least squares over the bars between the anchors, ±2σ bands
      build: (a, c) => {
        const vals = c.valuesBetween(a[0].t, a[1].t);
        const fit = G.linearFit(vals);
        if (!fit || vals.length < 3) return [G.segment(a[0], a[1], { dash: [4, 4] })];
        const n = vals.length - 1;
        const y0 = fit.intercept, y1 = fit.intercept + fit.slope * n;
        const lo = { t: a[0].t, v: y0 }, hi = { t: a[1].t, v: y1 };
        const k = 2 * fit.sigma;
        return [
          G.segment(lo, hi),
          G.segment({ t: lo.t, v: y0 + k }, { t: hi.t, v: y1 + k }, { dash: [4, 4] }),
          G.segment({ t: lo.t, v: y0 - k }, { t: hi.t, v: y1 - k }, { dash: [4, 4] }),
          G.label(hi, `σ ${c.fmt(fit.sigma)}`),
        ];
      } },

    /* The wedge, drawn honestly. Two anchors give the sloped edge; the third
     * gives a FLAT edge at its own price across the same span — which is the
     * shape an ascending triangle or a descending one actually makes, and
     * the one a parallel channel cannot draw because its second edge is a
     * copy of the first. The fill does not stroke: the two edges are drawn
     * by their own primitives, and a closed polygon over them would cap the
     * ends with two lines the tool never claimed. */
    flatChannel: { label: "Flat top/bottom", anchors: 3, group: "lines", section: "channels",
      build: (a) => {
        const f0 = { t: a[0].t, v: a[2].v }, f1 = { t: a[1].t, v: a[2].v };
        return [G.segment(a[0], a[1]), G.segment(f0, f1),
                G.poly([a[0], a[1], f1, f0],
                       { closed: true, fill: true, stroke: false })];
      } },

    /* Two edges with no parallel constraint between them — four anchors,
     * two of them yours to place freely. Broadening and contracting
     * formations live here: their edges converge, which a parallel channel
     * is by construction unable to draw. */
    disjointChannel: { label: "Disjoint channel", anchors: 4, group: "lines",
      section: "channels",
      build: (a) => [G.segment(a[0], a[1]), G.segment(a[2], a[3]),
                     G.poly([a[0], a[1], a[3], a[2]],
                            { closed: true, fill: true, stroke: false })] },

    // ── pitchforks ───────────────────────────────────────
    // Three pivots each. What differs between the four is one thing: where
    // the handle starts. See pitchfork() above for the shared construction.
    pitchfork: { label: "Pitchfork", anchors: 3, group: "lines", section: "pitchforks",
      // Andrews' original: the handle starts at the first pivot itself.
      build: (a) => pitchfork(a[0], a[1], a[2]) },

    schiff: { label: "Schiff pitchfork", anchors: 3, group: "lines", section: "pitchforks",
      // Schiff's correction: the handle is lifted to the midpoint of the
      // PRICE distance between pivots 1 and 2, still at pivot 1's time. It
      // flattens the median when the first leg was unusually steep.
      build: (a) => pitchfork({ t: a[0].t, v: (a[0].v + a[1].v) / 2 }, a[1], a[2]) },

    schiffModified: { label: "Modified Schiff pitchfork", anchors: 3, group: "lines",
      section: "pitchforks",
      // The same correction applied on BOTH axes: the origin is the full
      // midpoint of pivots 1 and 2, so the handle shortens in time as well.
      build: (a) => pitchfork(mid(a[0], a[1]), a[1], a[2]) },

    insidePitchfork: { label: "Inside pitchfork", anchors: 3, group: "lines",
      section: "pitchforks",
      // Andrews' handle, drawn at HALF WIDTH: the outer tines are pulled
      // halfway in to the median, so the channel sits inside the swing that
      // defined it rather than on its extremes. It is the variant for a
      // market that respected the middle of the last fork and never the
      // edges — the standard one would keep offering rails price has not
      // touched in months.
      //
      // (The other reading of "inside" — start the handle at the middle
      // pivot — is not this tool, and should not be. It puts the median's
      // foot at mid(P1,P3), which is where the middle pivot usually already
      // IS, and a handle of zero length draws four vertical lines.)
      build: (a) => pitchfork(a[0], a[1], a[2], 0.5) },

    // ── fib ──────────────────────────────────────────────
    fib: { label: "Fib retracement", anchors: 2, group: "fib",
      key: "F",
      build: (a, c) => {
        const out = [G.segment(a[0], a[1], { dash: [3, 3], width: 1 })];
        for (const [i, lv] of G.ladder(a[0].v, a[1].v, FIB).entries()) {
          const p = { t: a[0].t, v: lv.v }, q = { t: a[1].t, v: lv.v };
          out.push(G.segment(p, q, { color: FIB_COLORS[i] }),
                   G.label(q, `${(lv.ratio * 100).toFixed(1)}%  ${c.fmt(lv.v)}`,
                           { color: FIB_COLORS[i] }));
        }
        return out;
      } },

    // ── shapes ───────────────────────────────────────────
    rect: { label: "Rectangle", anchors: 2, group: "shapes",
      build: (a) => [G.box(a[0], a[1], { fill: true })] },

    triangle: { label: "Triangle", anchors: 3, group: "shapes",
      build: (a) => [G.poly([a[0], a[1], a[2]], { closed: true, fill: true })] },

    brush: { label: "Brush", anchors: "free", group: "shapes",
      build: (a) => [G.poly(a)] },

    // ── measure ──────────────────────────────────────────
    // One family, three readouts. Each states exactly what it measured, so
    // "how far" is never ambiguous between price, time, or both.
    priceRange: { label: "Price range", anchors: 2, group: "measure",
      build: (a, c) => {
        const d = a[1].v - a[0].v;
        return [G.band(a[0].v, a[1].v, { fillAlpha: 0.1 }),
                G.label(mid(a[0], a[1]),
                        `${d >= 0 ? "+" : ""}${c.fmt(d)}  (${c.fmtPct(d / a[0].v * 100)})`)];
      } },

    dateRange: { label: "Date range", anchors: 2, group: "measure",
      // a span of time, so it shades the whole strip — a zero-height box
      // between two vlines is invisible and impossible to grab
      build: (a, c) => [
        G.vband(a[0].t, a[1].t, { fillAlpha: 0.1 }),
        G.label(mid(a[0], a[1]), `${c.barsBetween(a[0].t, a[1].t)} bars`),
      ] },

    measure: { label: "Measure", anchors: 2, group: "measure",
      build: (a, c) => {
        const d = a[1].v - a[0].v;
        return [G.box(a[0], a[1], { fill: true }),
                G.label(mid(a[0], a[1]),
                        `${d >= 0 ? "+" : ""}${c.fmt(d)} (${c.fmtPct(d / a[0].v * 100)})`
                        + `  ·  ${c.barsBetween(a[0].t, a[1].t)} bars`)];
      } },

    // ── position ─────────────────────────────────────────
    // entry → target → stop. The box you actually care about is the ratio,
    // so it is stated rather than left to be eyeballed.
    long: { label: "Long position", anchors: 3, group: "position",
      build: (a, c) => positionTool(a, c, "long") },
    short: { label: "Short position", anchors: 3, group: "position",
      build: (a, c) => positionTool(a, c, "short") },

    // ── annotate ─────────────────────────────────────────
    text: { label: "Text", anchors: 1, group: "annotate", text: true,
      build: (a, c, d) => [G.label(a[0], d.text || "…", { align: "right" })] },
  };

  function positionTool(a, c, side) {
    const [entry, target, stop] = a;
    const rr = G.riskReward(entry.v, target.v, stop.v);
    const pct = (v) => Math.abs((v - entry.v) / entry.v * 100).toFixed(2);
    const dist = (v) => c.fmt(Math.abs(v - entry.v));
    /* Two lines, TradingView's two: the side, and the ratio. The first line
     * used to carry the breakeven hit rate as well — 1/(1+RR), the win rate
     * the shape silently demands — which is a true and useful number and the
     * wrong place for it. It doubled the chip's width, so the chip covered
     * twice the candles, and the reader had to parse a sentence to find the
     * one figure they opened the tool for. */
    const center = [
      side === "long" ? "Long" : "Short",
      `Risk/reward ratio: ${rr === null ? "—" : rr.toFixed(2)}`,
    ];
    return [G.position(
      { t: Math.min(entry.t, target.t, stop.t), v: entry.v },
      { v: stop.v, text: `Stop: ${c.fmt(stop.v)} (${pct(stop.v)}%) ${dist(stop.v)}` },
      [{ v: target.v, text: `Target: ${c.fmt(target.v)} (${pct(target.v)}%) ${dist(target.v)}` }],
      { t1: Math.max(entry.t, target.t, stop.t), center,
        tone: G.positionTone(entry.v, c.last, side) })];
  }

  /* A GROUP is one rail button and one flyout. A SECTION is a labelled band
   * inside that flyout.
   *
   * Lines carries three of them, the way TradingView's does — and for the
   * same reason: channels and pitchforks ARE lines (every one of them is
   * built from G.segment), and a reader looking for a parallel channel
   * looks under the line tool, not beside it. A rail button per section
   * would put three near-identical glyphs in a vertical strip 34px wide and
   * ask the reader to tell a channel's icon from a pitchfork's at that size.
   *
   * A group with no `sections` renders as it always did: one heading, then
   * its tools in catalogue order. */
  const GROUPS = [
    { id: "lines", label: "Lines", icon: "trend",
      sections: [["lines", "Lines"], ["channels", "Channels"],
                 ["pitchforks", "Pitchforks"]] },
    { id: "fib", label: "Fibonacci", icon: "fib" },
    { id: "shapes", label: "Shapes", icon: "rect" },
    { id: "measure", label: "Measure", icon: "measure" },
    { id: "position", label: "Position", icon: "position" },
    { id: "annotate", label: "Annotations", icon: "text" },
  ];

  return { SPECS, GROUPS, FIB, FIB_COLORS };
})();
