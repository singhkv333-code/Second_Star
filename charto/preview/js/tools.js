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

  const SPECS = {
    // ── lines ────────────────────────────────────────────
    trend: { label: "Trend line", anchors: 2, group: "lines",
      build: (a) => [G.segment(a[0], a[1])] },

    ray: { label: "Ray", anchors: 2, group: "lines",
      build: (a) => [G.segment(a[0], a[1], { extend: "right", arrow: true })] },

    extended: { label: "Extended line", anchors: 2, group: "lines",
      build: (a) => [G.segment(a[0], a[1], { extend: "both" })] },

    hline: { label: "Horizontal line", anchors: 1, group: "lines",
      build: (a, c) => [G.hline(a[0].v), G.label(a[0], c.fmt(a[0].v))] },

    vline: { label: "Vertical line", anchors: 1, group: "lines",
      build: (a) => [G.vline(a[0].t)] },

    // ── channels ─────────────────────────────────────────
    channel: { label: "Parallel channel", anchors: 3, group: "channels",
      // third anchor sets the width; the copy keeps the SAME data-space
      // slope, so the two edges stay parallel at every zoom level
      build: (a) => {
        const off = a[2].v - G.valueAt(a[0], a[1], a[2].t);
        const c0 = { t: a[0].t, v: a[0].v + off }, c1 = { t: a[1].t, v: a[1].v + off };
        return [G.segment(a[0], a[1]), G.segment(c0, c1),
                G.poly([a[0], a[1], c1, c0], { closed: true, fill: true })];
      } },

    regression: { label: "Regression trend", anchors: 2, group: "channels",
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

    // ── fib ──────────────────────────────────────────────
    fib: { label: "Fib retracement", anchors: 2, group: "fib",
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
    // breakeven hit rate 1/(1+RR): the win rate this shape silently demands
    const center = rr === null
      ? [`${side === "long" ? "Long" : "Short"}`, "Risk/reward ratio: —"]
      : [`${side === "long" ? "Long" : "Short"} · needs ${Math.round(100 / (1 + rr))}% to break even`,
         `Risk/reward ratio: ${rr.toFixed(2)}`];
    return [G.position(
      { t: Math.min(entry.t, target.t, stop.t), v: entry.v },
      { v: stop.v, text: `Stop: ${c.fmt(stop.v)} (${pct(stop.v)}%) ${dist(stop.v)}` },
      [{ v: target.v, text: `Target: ${c.fmt(target.v)} (${pct(target.v)}%) ${dist(target.v)}` }],
      { t1: Math.max(entry.t, target.t, stop.t), center })];
  }

  const GROUPS = [
    { id: "lines", label: "Lines", icon: "trend" },
    { id: "channels", label: "Channels", icon: "ray" },
    { id: "fib", label: "Fibonacci", icon: "fib" },
    { id: "shapes", label: "Shapes", icon: "rect" },
    { id: "measure", label: "Measure", icon: "measure" },
    { id: "position", label: "Position", icon: "position" },
    { id: "annotate", label: "Annotations", icon: "text" },
  ];

  return { SPECS, GROUPS, FIB, FIB_COLORS };
})();
