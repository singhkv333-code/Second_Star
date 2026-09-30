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

  /* ── the ratio catalogue ──────────────────────────────────────────────────
   * Every tool in the Fibonacci and Gann families is the same idea twice:
   * measure a span, then divide it. What separates them is only WHICH span
   * (price, time, or both), which divisions, and what shape the divisions are
   * drawn as. Declaring the divisions once, here, is what stops one tool's
   * 61.8% being 0.618 and another's being 0.62.
   *
   * What it does NOT do — and an earlier version of this comment wrongly
   * claimed it did — is make a ratio mean the same PRICE everywhere. A
   * RETRACEMENT is measured back from the leg's end (0% at the end, 100% at
   * the start, because a full retracement returns to where the move began); a
   * RADIAL tool — circles, arcs, wedges — and a Gann grid measure out from
   * the first anchor. So on the same two anchors the ring labelled 61.8%
   * crosses the move where the retracement's ladder says 38.2%. Both are the
   * standard reading of their own tool; they are simply not the same ladder,
   * and this file must not pretend otherwise.
   */
  const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
  // beyond 1 the ladder stops retracing and starts projecting: 127.2% is
  // √1.272 territory, 161.8% and 261.8% are the ratios harmonic traders
  // measure a completed leg against
  const FIB_EXT = [0, 0.382, 0.5, 0.618, 1, 1.272, 1.618, 2.618, 4.236];
  // fans and arcs drop the endpoints — a ray at 0% is the time axis and a
  // ray at 100% is the trend line, and both are already drawn
  const FIB_FAN = [0.236, 0.382, 0.5, 0.618, 0.786];
  const FIB_ARC = [0.236, 0.382, 0.5, 0.618, 0.786, 1];
  // time zones count in fibonacci NUMBERS, not ratios: the nth vertical is
  // the nth term of the sequence, in units of the span you dragged
  const FIB_TIME = [0, 1, 2, 3, 5, 8, 13, 21, 34, 55];
  const FIB_TIME_R = [0.618, 1, 1.618, 2.618, 4.236];
  // Gann divides by quarters and eighths, not by the golden ratio — the two
  // that overlap (0.382/0.618) are there because he used them too
  const GANN = [0, 0.25, 0.382, 0.5, 0.618, 0.75, 1];
  const GANN_EIGHTHS = [0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1];
  // [time, price] multiples. [1,1] is the 1×1 — one unit of price per unit
  // of time, the line Gann's whole method is anchored on.
  const GANN_FAN = [[1, 8], [1, 4], [1, 3], [1, 2], [1, 1],
                    [2, 1], [3, 1], [4, 1], [8, 1]];
  // how wide a one-anchor Gann square is, in bars. 52 because a Gann square
  // is a CYCLE and the year is the cycle he squared most often — on a daily
  // chart that is the quarter-year, on 5m it is the session's afternoon.
  const GANN_SQUARE_BARS = 52;

  /* Colour is keyed by the RATIO VALUE, not by position in a list — so the
   * cyan means 0.618 wherever you meet it, which an index-keyed palette
   * cannot promise, because the same index is 0.5 on one tool and 1.272 on
   * another.
   *
   * It keys the ratio; it does not claim the ratio marks the same price on
   * two different tools. See the note above the catalogue: a retracement
   * counts back from the leg's end and a ring counts out from its centre, so
   * a cyan chip on a fib and a cyan chip on a circle are the same FRACTION
   * measured from opposite ends. Never the candle red/green: those mean
   * "closed down / up" and nothing else on this chart. */
  const RATIO_COLOR = {
    0: "#787b86", 1: "#787b86", 0.125: "#787b86",
    0.236: "#f5a524", 0.25: "#f5a524", 4.236: "#f5a524",
    0.375: "#ff9800", 0.382: "#ff9800",
    0.5: "#c084fc", 0.625: "#c084fc", 2.618: "#c084fc",
    0.618: "#22d3ee", 1.618: "#22d3ee",
    0.75: "#4ea8f2", 0.786: "#4ea8f2", 0.875: "#4ea8f2", 1.272: "#4ea8f2",
  };
  const colorOf = (r) => RATIO_COLOR[r] || "#787b86";
  const pct = (r) => `${(r * 100).toFixed(1)}%`;
  // kept as an export because the scene layer indexes it alongside FIB;
  // derived, so the two can never be edited apart
  const FIB_COLORS = FIB.map(colorOf);

  const mid = (a, b) => ({ t: (a.t + b.t) / 2, v: (a.v + b.v) / 2 });
  /** A point `r` of the way from p to q, in both axes. The one operation
   *  every tool below is made of.
   *
   *  It takes the context because the TIME half is measured in bars, not in
   *  seconds — the point has to land on the line as DRAWN, and the line as
   *  drawn is straight across the bars. Interpolating the timestamp instead
   *  puts the point off its own line wherever a weekend or an overnight gap
   *  falls between p and q. See ctx.tLerp. */
  const along = (c, p, q, r) => ({ t: c.tLerp(p.t, q.t, r),
                                   v: p.v + (q.v - p.v) * r });

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

  /* ── the Gann family ────────────────────────────────────────────────────
   * Three tools, ONE construction, exactly as the pitchforks are four tools
   * and one construction. A Gann figure is a rectangle spanning a swing, cut
   * into a grid, with the corner-to-corner 1×1 as its spine — and what the
   * three disagree about is only which divisions to cut it into and whether
   * the fan and the arcs come with it.
   *
   * The 1×1 is the load-bearing line: everything Gann claimed rests on price
   * and time moving at one unit each, and every other angle in the fan is
   * that line at a rational multiple. Drawing it thicker than the grid is
   * not decoration — it is which line the tool is about.
   */
  function gannGrid(p0, p1, ratios, c) {
    const out = [G.box(p0, p1, { width: 1, dash: [3, 3] })];
    for (const r of ratios) {
      const v = p0.v + (p1.v - p0.v) * r, t = c.tLerp(p0.t, p1.t, r);
      out.push(
        G.segment({ t: p0.t, v }, { t: p1.t, v }, { color: colorOf(r), width: 1 }),
        G.segment({ t, v: p0.v }, { t, v: p1.v }, { color: colorOf(r), width: 1 }),
        G.label({ t: p1.t, v }, `${pct(r)}  ${c.fmt(v)}`, { color: colorOf(r) }));
    }
    return out;
  }

  /** The fan and the arcs, clipped to the box — the parts that make a Gann
   *  square more than a grid. `ratios` drives the arcs so a square cut into
   *  eighths gets eight arcs and one cut by the golden ratios gets those. */
  function gannSquareParts(p0, p1, ratios, c) {
    const dt = p1.t - p0.t, dv = p1.v - p0.v;
    const out = [];
    for (const [x, y] of GANN_FAN) {
      const m = Math.max(x, y);
      // the a×b line exits whichever edge it reaches first, so it is drawn
      // to that exit and no further — a fan that overshot its own square
      // would be claiming angles outside the cycle it is measuring
      out.push(G.segment(p0, { t: c.tLerp(p0.t, p1.t, x / m),
                                v: p0.v + dv * (y / m) },
                         { width: x === y ? 1.6 : 1,
                           color: x === y ? undefined : "#787b86" }));
    }
    for (const r of ratios) {
      if (!r || r > 1) continue;
      // in index space, like every other sampled curve — see ctx.curve
      out.push(G.poly(c.curve([p0, p1], (i) =>
                 G.arcPts(i[0], (i[1].t - i[0].t) * r, (i[1].v - i[0].v) * r,
                          0, Math.PI / 2, 32)),
                      { color: colorOf(r), width: 1 }));
    }
    return out;
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
    // The leg's START is anchor 1 (100%) and its END is anchor 2 (0%). That
    // orientation is the convention the whole app runs on — the chat's fib,
    // the evaluator's ratios and this tool all read it the same way — so
    // reversing it here would silently rename every level on the chart.
    fib: { label: "Fib retracement", anchors: 2, group: "fib", section: "fib",
      key: "F",
      build: (a, c) => {
        const out = [G.segment(a[0], a[1], { dash: [3, 3], width: 1 })];
        for (const lv of G.ladder(a[0].v, a[1].v, FIB)) {
          const p = { t: a[0].t, v: lv.v }, q = { t: a[1].t, v: lv.v };
          out.push(G.segment(p, q, { color: colorOf(lv.ratio) }),
                   G.label(q, `${pct(lv.ratio)}  ${c.fmt(lv.v)}`,
                           { color: colorOf(lv.ratio) }));
        }
        return out;
      } },

    /* Retracement measures a move against ITSELF; extension measures the NEXT
     * move against the last one. Three anchors: the leg (1→2), then where the
     * pullback ended (3). Every level is anchor 3 plus a multiple of the leg,
     * which is why the ladder runs past 100% — the tool exists to say where a
     * move might END, not where it might pause. */
    fibExtension: { label: "Trend-based fib extension", anchors: 3,
      group: "fib", section: "fib",
      build: (a, c) => {
        const leg = a[1].v - a[0].v;
        const t0 = Math.min(a[0].t, a[1].t, a[2].t);
        const out = [G.segment(a[0], a[1], { dash: [3, 3], width: 1 }),
                     G.segment(a[1], a[2], { dash: [3, 3], width: 1 })];
        for (const r of FIB_EXT) {
          const v = a[2].v + leg * r;
          // 500 BARS, not 500 × interval seconds — the second point only
          // fixes the heading as exactly horizontal before the projector
          // clips it, and a fraction of the wall clock is not a heading.
          // Extended both ways: a projection you cannot see until it is
          // already behind price is a projection that arrived too late.
          out.push(G.segment({ t: t0, v }, { t: c.tShift(t0, 500), v },
                             { extend: "both", color: colorOf(r) }),
                   G.label({ t: a[2].t, v }, `${pct(r)}  ${c.fmt(v)}`,
                           { color: colorOf(r) }));
        }
        return out;
      } },

    /* A retracement laid along a TREND instead of along the price axis. The
     * first two anchors are the baseline, the third sets the 100% rail, and
     * the ladder is drawn parallel between them — so the levels slope with
     * the move rather than sitting flat under it. Same construction as the
     * parallel channel, same reason its edges stay parallel: the offset is a
     * data-space value, not a pixel gap. */
    fibChannel: { label: "Fib channel", anchors: 3, group: "fib", section: "fib",
      build: (a, c) => {
        // where the baseline sits UNDER anchor 3 — measured in bars, because
        // the baseline as drawn is straight across the bars and reading it in
        // seconds puts the 100% rail off by however much gap lies between
        const base = a[0].v + (a[1].v - a[0].v)
          * (c.barsFrom(a[0].t, a[1].t)
             ? c.barsFrom(a[0].t, a[2].t) / c.barsFrom(a[0].t, a[1].t) : 1);
        const off = a[2].v - base;
        const out = [];
        for (const r of FIB) {
          const p = { t: a[0].t, v: a[0].v + off * r };
          const q = { t: a[1].t, v: a[1].v + off * r };
          // BOTH ways. A channel is a claim about a trend's slope, and a
          // trend does not begin at the bar you happened to click first —
          // extending only right left the rails starting mid-chart with
          // nothing behind them, so half the structure they were fitted to
          // had no line over it.
          out.push(G.segment(p, q, { extend: "both", color: colorOf(r) }),
                   G.label(q, pct(r), { color: colorOf(r) }));
        }
        return out;
      } },

    /* The only fib tool that says nothing about price. Two anchors set ONE
     * unit of time, and the verticals land on the fibonacci numbers of it —
     * 1, 2, 3, 5, 8, 13 units out — so the claim is about WHEN a turn is due,
     * not where. The labels are the numbers themselves, because "the 13th"
     * is what the tool is for and a percentage would hide it. */
    fibTimeZone: { label: "Fib time zone", anchors: 2, group: "fib", section: "fib",
      build: (a, c) => {
        // in BARS. Counted in seconds, the 8th, 13th, 21st, 34th and 55th
        // verticals all walked into the same overnight gap and stacked on one
        // column — six of ten marks on one pixel, and the tool's whole range
        // empty to the right of it.
        const u = c.barsFrom(a[0].t, a[1].t);
        if (!u) return [G.vline(a[0].t)];
        const out = [];
        for (const n of FIB_TIME) {
          const t = c.tShift(a[0].t, u * n);
          out.push(G.vline(t, { width: n ? 1 : 1.6 }),
                   G.label({ t, v: a[0].v }, String(n)));
        }
        return out;
      } },

    /* Two fans in one tool, and they are not the same claim. The PRICE fan
     * rays cut the far edge at fib fractions of the move's height; the TIME
     * fan rays cut the bottom edge at fib fractions of its width. A move that
     * respects the 61.8% price ray is holding its slope; one that respects
     * the 61.8% time ray is holding its pace. */
    fibSpeedFan: { label: "Fib speed resistance fan", anchors: 2,
      group: "fib", section: "fib",
      build: (a, c) => {
        const out = [G.box(a[0], a[1], { width: 1, dash: [3, 3] }),
                     G.segment(a[0], a[1], { width: 1.6 })];
        for (const r of FIB_FAN) {
          const pv = { t: a[1].t, v: a[0].v + (a[1].v - a[0].v) * r };
          const pt = { t: c.tLerp(a[0].t, a[1].t, r), v: a[1].v };
          out.push(G.segment(a[0], pv, { extend: "right", color: colorOf(r) }),
                   G.segment(a[0], pt, { extend: "right", color: colorOf(r) }),
                   G.label(pv, pct(r), { color: colorOf(r) }));
        }
        return out;
      } },

    /* Fib time zone's three-anchor cousin: the first two measure a move that
     * already happened, the third says where to start counting, and the
     * verticals fall at ratios of that duration. Where the time zone asks
     * "how long is one unit?", this asks "how long did the last leg take?" —
     * which is the more defensible question, because the unit came off the
     * market rather than off a drag. */
    fibTimeExtension: { label: "Trend-based fib time", anchors: 3,
      group: "fib", section: "fib",
      build: (a, c) => {
        // the measured duration, in BARS — see fibTimeZone
        const u = c.barsFrom(a[0].t, a[1].t);
        const out = [G.segment(a[0], a[1], { dash: [3, 3], width: 1 }),
                     G.segment(a[1], a[2], { dash: [3, 3], width: 1 })];
        if (!u) return out;
        for (const r of FIB_TIME_R) {
          const t = c.tShift(a[2].t, u * r);
          out.push(G.vline(t, { color: colorOf(r) }),
                   G.label({ t, v: a[2].v }, pct(r), { color: colorOf(r) }));
        }
        return out;
      } },

    /* Rings about a pivot, crossing the move at each ratio. The centre is
     * anchor 1 and anchor 2 is the 100% ring, so each ring answers "how far
     * from the turn, in the units of that first swing" — in price AND time at
     * once, which is the one thing a flat ladder cannot say. */
    fibCircles: { label: "Fib circles", anchors: 2, group: "fib", section: "fib",
      build: (a, c) => {
        const out = [G.segment(a[0], a[1], { dash: [3, 3], width: 1 })];
        for (const r of FIB_ARC) {
          out.push(G.poly(c.curve(a, (i) =>
                     G.crossArcPts(i[0], i[1].t - i[0].t, i[1].v - i[0].v,
                                   r, Math.PI, 64)),
                          { color: colorOf(r) }),
                   G.label(along(c, a[0], a[1], r), pct(r), { color: colorOf(r) }));
        }
        return out;
      } },

    /* The same rings, opened out into one continuous curve that shrinks by φ
     * every quarter turn. Anchor 2 is the spiral's OUTER end and it winds
     * inward to anchor 1 — see the note in js/geometry.js for why the
     * textbook's outward direction is unusable on a chart. */
    fibSpiral: { label: "Fib spiral", anchors: 2, group: "fib", section: "fib",
      build: (a, c) => [
        G.segment(a[0], a[1], { dash: [3, 3], width: 1 }),
        G.poly(c.curve(a, (i) =>
          G.spiralPts(i[0], i[1].t - i[0].t, i[1].v - i[0].v, 3, 288))),
      ] },

    /* Half-rings off the start of a move, crossing the trend line at each
     * ratio. The circles' claim is about distance from a pivot in every
     * direction; the arcs' is about distance ALONG a move — so they open
     * toward where the move went and say nothing about behind it. */
    fibArcs: { label: "Fib speed resistance arcs", anchors: 2,
      group: "fib", section: "fib",
      build: (a, c) => {
        const out = [G.segment(a[0], a[1], { width: 1.6 })];
        for (const r of FIB_ARC) {
          out.push(G.poly(c.curve(a, (i) =>
                     G.crossArcPts(i[0], i[1].t - i[0].t, i[1].v - i[0].v,
                                   r, Math.PI / 2, 40)),
                          { color: colorOf(r) }),
                   G.label(along(c, a[0], a[1], r), pct(r), { color: colorOf(r) }));
        }
        return out;
      } },

    /* An apex and two rays, with the fib fractions drawn as the rungs
     * between them. It is the tool for a formation that OPENS — a broadening
     * top, an expanding triangle — where the levels that matter are not flat
     * and not parallel but fan out with the structure. */
    fibWedge: { label: "Fib wedge", anchors: 3, group: "fib", section: "fib",
      build: (a, c) => {
        const out = [G.segment(a[0], a[1]), G.segment(a[0], a[2])];
        for (const r of FIB_ARC) {
          out.push(G.poly(c.curve(a, (i) => G.blendArcPts(i[0], i[1], i[2], r)),
                          { color: colorOf(r) }),
                   G.label(along(c, a[0], a[2], r), pct(r), { color: colorOf(r) }));
        }
        return out;
      } },

    /* A pitchfork whose tines are fib fractions of the base instead of its
     * two ends. Same three pivots, same handle; the difference is that a
     * fork offers you the median and the edges, and a fan offers the whole
     * ladder in between — which is what you want when price has been
     * respecting the inside of the channel rather than its rails. */
    pitchfan: { label: "Pitchfan", anchors: 3, group: "fib", section: "fib",
      build: (a, c) => {
        const out = [G.segment(a[1], a[2], { dash: [4, 4], width: 1 })];
        for (const r of FIB) {
          out.push(G.segment(a[0], along(c, a[1], a[2], r),
                             { extend: "right", color: colorOf(r) }));
        }
        return out;
      } },

    // ── gann ─────────────────────────────────────────────
    /* Price and time, cut by the same fractions. The box is the swing you
     * dragged; every horizontal is a fraction of its height and every
     * vertical the same fraction of its width, so a level and a date carry
     * the identical claim. It is the Gann family's grid with none of its
     * angles — the tool for reading a range, not a slope. */
    gannBox: { label: "Gann box", anchors: 2, group: "fib", section: "gann",
      build: (a, c) => gannGrid(a[0], a[1], GANN, c) },

    /* The grid, plus the two things that make it Gann's: the fan of rational
     * angles about the origin corner, and the arcs that carry each fraction
     * around from the time axis to the price one. Two anchors, so the square
     * covers exactly the swing you gave it. */
    gannSquare: { label: "Gann square", anchors: 2, group: "fib", section: "gann",
      build: (a, c) => [...gannGrid(a[0], a[1], GANN, c),
                        ...gannSquareParts(a[0], a[1], GANN, c)] },

    /* One anchor, and the square sizes itself: 52 bars wide, and as tall as
     * those 52 bars actually ranged.
     *
     * TradingView's fixed square is square in PIXELS — it re-derives its
     * second anchor from the chart's scale ratio, so the figure changes shape
     * the moment you rescale the price axis. That is a statement about the
     * window, not about the market, and this chart does not let a drawing's
     * geometry live in pixels (see js/geometry.js). Squared against the real
     * range instead, "one unit of price per unit of time" means something a
     * reader can check: the cell is one bar wide and one fifty-second of the
     * cycle's own range tall. Cut into eighths, which is Gann's own division
     * of a range and the reason the tool is called fixed at all. */
    gannSquareFixed: { label: "Gann square fixed", anchors: 1, group: "fib",
      section: "gann",
      build: (a, c) => {
        const t1 = c.tShift(a[0].t, GANN_SQUARE_BARS);
        const rng = c.rangeBetween(a[0].t, t1);
        // no bars to square against — fall back to a tenth of the anchor's
        // own price, which keeps the figure on screen and visibly generic
        const h = rng ? (rng.hi - rng.lo) : Math.abs(a[0].v) * 0.1;
        // up from a pivot in the lower half of the range, down from one in
        // the upper half: a square drawn off a high belongs under it
        const up = !rng || a[0].v <= (rng.hi + rng.lo) / 2;
        const p1 = { t: t1, v: a[0].v + (up ? h : -h) };
        return [...gannGrid(a[0], p1, GANN_EIGHTHS, c),
                ...gannSquareParts(a[0], p1, GANN_EIGHTHS, c)];
      } },

    /* The angles on their own, running past the swing that set them. Anchor
     * 2 defines the 1×1 — one unit of price per unit of time — and every
     * other ray is that rate at a whole-number multiple. Which ray price is
     * riding is the whole reading: above the 1×1 is strength, below it is
     * the trend giving up time it cannot get back. */
    gannFan: { label: "Gann fan", anchors: 2, group: "fib", section: "gann",
      build: (a, c) => {
        const dv = a[1].v - a[0].v;
        const out = [];
        for (const [x, y] of GANN_FAN) {
          const m = Math.max(x, y);
          // a Gann angle is price per BAR — he counted trading days, and a
          // ray aimed a fraction of the way along the wall clock is a
          // different angle from the one the notation names
          const p = { t: c.tLerp(a[0].t, a[1].t, x / m), v: a[0].v + dv * (y / m) };
          out.push(G.segment(a[0], p, { extend: "right",
                                        width: x === y ? 1.6 : 1 }),
                   G.label(p, `${y}×${x}`));
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
    /* Fibonacci and Gann under one rail button, and one flyout with two
     * bands. They are the same argument — a span, divided by ratios — made
     * with different ratios, and a reader hunting for a Gann fan looks where
     * the fib fan was. A second rail button would put two near-identical
     * ladder glyphs in a 34px strip. */
    { id: "fib", label: "Fibonacci", icon: "fib",
      sections: [["fib", "Fibonacci"], ["gann", "Gann"]] },
    { id: "shapes", label: "Shapes", icon: "rect" },
    { id: "measure", label: "Measure", icon: "measure" },
    { id: "position", label: "Position", icon: "position" },
    { id: "annotate", label: "Annotations", icon: "text" },
  ];

  /* ── the build context ──────────────────────────────────────────────────
   * What a builder is allowed to know beyond its own anchors: how to format a
   * number, how big a bar is, and a few readings off the loaded bars.
   *
   * It lives HERE, not in the drawing runtime, because two layers run these
   * builders — the user's rail (js/drawings.js) and the chat's scene
   * (js/scene.js). A fib the chat drew and a fib the user dragged must be the
   * same shape, and the surest way to guarantee that is for both to call the
   * same build() with the same context rather than for one of them to keep a
   * second copy that drifts.
   *
   * env: { getBars, getIntervalSec, tToX, vToY(v, paneKey), toBarTime? }
   *
   * `toBarTime` exists because the two layers hold time differently: a shape
   * the user dragged is already stamped in the chart's own clock, and one the
   * chat sent carries raw epoch seconds that the scene shifts on the way in.
   * Anything that COMPARES an anchor against a loaded bar has to go through
   * it, or a reading lands half a day off on one layer and not the other.
   */
  function makeCtx(env) {
    const bt = env.toBarTime || ((t) => t);
    const fbt = env.fromBarTime || ((t) => t);
    return {
      fmt: (n) => Sym.num(n),
      fmtPct: (p) => `${p >= 0 ? "+" : ""}${p.toFixed(2)}%`,
      // seconds per bar. A tool that has to point somewhere OFF the loaded
      // range — a ray's far end — needs the axis's own step to do it; a
      // literal number of seconds would mean something different on a 1m
      // chart and on a daily one.
      get iv() { return env.getIntervalSec(); },
      /* The ONE screen-space reading a tool may take, and the only place in
       * the catalogue that is allowed to see pixels.
       *
       * An angle on a price chart has no data-space meaning: price over time
       * is not a ratio of like quantities, so "38°" is a statement about the
       * two axes' current scales and nothing else. Expressing it as
       * percent-per-bar instead — which this tool tried first — is arithmetic
       * that is correct and useless: a line a reader would call 45° came back
       * as 1.4°, because a percent and a bar are not the same size.
       *
       * So the tool reads the projection, exactly as TradingView's does, and
       * the number moves when you zoom. That is not a bug in the reading; it
       * is what the reading IS. Anchors stay in data space — this reads the
       * pane, it does not store anything from it. */
      degrees(p, q, pane) {
        const x0 = env.tToX(p.t), x1 = env.tToX(q.t);
        const y0 = env.vToY(p.v, pane || "price"), y1 = env.vToY(q.v, pane || "price");
        if ([x0, x1, y0, y1].some((n) => n === null || n === undefined)) return null;
        if (x0 === x1 && y0 === y1) return null;
        // screen y grows downward; a rising line has to read as a positive angle
        return (Math.atan2(y0 - y1, x1 - x0) * 180) / Math.PI;
      },
      barsBetween(t0, t1) {
        return Math.max(1, Math.round(Math.abs(t1 - t0) / env.getIntervalSec()));
      },
      /** The last loaded close — where the market actually IS. Only the
       *  position tool asks: it is what decides whether a plan is currently
       *  in its reward half or its risk half, which is the one thing about a
       *  plan that changes without anybody dragging it. Null on an empty
       *  chart, and the tool paints its neutral state rather than guessing. */
      get last() {
        const bars = env.getBars();
        return bars.length ? bars[bars.length - 1].close : null;
      },
      valuesBetween(t0, t1) {
        const bars = env.getBars();
        const lo = Math.min(bt(t0), bt(t1)), hi = Math.max(bt(t0), bt(t1));
        return bars.filter((b) => b.time >= lo && b.time <= hi).map((b) => b.close);
      },
      /* ── sampled curves live in BAR-INDEX space ───────────────────────────
       * The time axis on this chart is not a clock, it is a queue of bars.
       * A weekend takes no width; neither does the sixteen hours between one
       * session's close and the next one's open. So a curve sampled in
       * wall-clock seconds has every arc that crosses a gap squashed onto a
       * single pixel column — the first fib circles drew as a rectangle with
       * one rounded edge, because three quarters of each ring landed inside
       * an overnight gap.
       *
       * Straight-line tools never noticed: a segment is projected at its two
       * ENDS and drawn straight between them, so nothing in the middle is
       * ever asked where it goes. Only a shape made of sampled points can be
       * wrong here, and every one of them is new.
       *
       * `curve` is the fix and the whole of it: anchors in, index space for
       * the maths, times back out. Fractional indices are what the projector
       * wants anyway (see logicalToX), and past either end of the loaded
       * bars it steps by the axis's own spacing — which is all a projection
       * into blank chart can ever be.
       */
      indexAt(t) {
        const bars = env.getBars();
        const T = bt(t);
        const iv = env.getIntervalSec() || 60;
        if (!bars.length) return T / iv;
        const n = bars.length - 1;
        if (T <= bars[0].time) {
          const step = n ? (bars[1].time - bars[0].time) || iv : iv;
          return (T - bars[0].time) / step;
        }
        if (T >= bars[n].time) {
          const step = n ? (bars[n].time - bars[n - 1].time) || iv : iv;
          return n + (T - bars[n].time) / step;
        }
        let lo = 0, hi = n;
        while (hi - lo > 1) {
          const m = (lo + hi) >> 1;
          if (bars[m].time <= T) lo = m; else hi = m;
        }
        const span = bars[hi].time - bars[lo].time || 1;
        return lo + (T - bars[lo].time) / span;
      },
      timeAt(i) {
        const bars = env.getBars();
        const iv = env.getIntervalSec() || 60;
        if (!bars.length) return fbt(i * iv);
        const n = bars.length - 1;
        if (i <= 0) {
          const step = n ? (bars[1].time - bars[0].time) || iv : iv;
          return fbt(Math.round(bars[0].time + i * step));
        }
        if (i >= n) {
          const step = n ? (bars[n].time - bars[n - 1].time) || iv : iv;
          return fbt(Math.round(bars[n].time + (i - n) * step));
        }
        const lo = Math.floor(i);
        const span = bars[lo + 1].time - bars[lo].time;
        return fbt(Math.round(bars[lo].time + (i - lo) * span));
      },
      /** Anchors → index space → `gen` → back to times. Every sampled curve
       *  in the catalogue goes through here; none of them does its own
       *  conversion, so none of them can forget to. */
      curve(anchors, gen) {
        const ix = anchors.map((a) => ({ t: this.indexAt(a.t), v: a.v }));
        return gen(ix).map((p) => ({ t: this.timeAt(p.t), v: p.v }));
      },
      /* …and the same correction for a SINGLE time, which is the half the
       * first version of this missed.
       *
       * `curve` covered the shapes made of sampled points and stopped there,
       * on the reasoning that a straight line is projected only at its ends.
       * True — but a tool does not only draw lines, it also DERIVES times: a
       * time zone's nth vertical, a Gann grid's 61.8% column, the far corner
       * of a fixed square, the point a fan ray is aimed through. Every one of
       * those is a fraction of a span, and computing it in seconds means the
       * fraction is of WALL CLOCK — so on an intraday chart a "half way"
       * column lands wherever the overnight gap happens to put it, and four
       * of a Gann box's seven divisions stacked on one pixel column.
       *
       * A span is a number of BARS. These two are the only honest way to say
       * that, and every fraction-of-time in the catalogue goes through them.
       */
      /** The time `r` of the way from t0 to t1, measured in bars. */
      tLerp(t0, t1, r) {
        const i0 = this.indexAt(t0), i1 = this.indexAt(t1);
        return this.timeAt(i0 + (i1 - i0) * r);
      },
      /** `n` bars from `t` — forward or back, past the loaded range if need be. */
      tShift(t, n) { return this.timeAt(this.indexAt(t) + n); },
      /** How many BARS from t0 to t1. Signed, and fractional: a span is not
       *  obliged to start and end on a bar. */
      barsFrom(t0, t1) { return this.indexAt(t1) - this.indexAt(t0); },
      /** The true high and low over a span — what a one-anchor tool squares
       *  itself against. Closes are not enough here: a square built off the
       *  closing range would be smaller than the swing it claims to cover.
       *  Null when the span holds no bars, and the caller must handle that
       *  rather than draw a zero-height figure. */
      rangeBetween(t0, t1) {
        const bars = env.getBars();
        const lo = Math.min(bt(t0), bt(t1)), hi = Math.max(bt(t0), bt(t1));
        let h = -Infinity, l = Infinity;
        for (const b of bars) {
          if (b.time < lo || b.time > hi) continue;
          if (b.high > h) h = b.high;
          if (b.low < l) l = b.low;
        }
        return h > -Infinity && h > l ? { hi: h, lo: l } : null;
      },
    };
  }

  return { SPECS, GROUPS, makeCtx, colorOf,
           FIB, FIB_COLORS, FIB_EXT, FIB_FAN, FIB_ARC, FIB_TIME, FIB_TIME_R,
           GANN, GANN_EIGHTHS, GANN_FAN, GANN_SQUARE_BARS };
})();
