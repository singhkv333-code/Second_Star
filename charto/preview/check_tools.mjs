/* Charto — the drawing catalogue's smoke test.  `node check_tools.mjs`
 *
 * There is no test runner in front of this app's JavaScript, and the ratio
 * family is the one part of it that genuinely needs one: a Gann fan whose 1×1
 * misses the corner, an arc that crosses the move at the wrong fraction or a
 * spiral that flies off the top of the chart all LOOK like drawings. They
 * render, they hit-test, they drag, and every number beside them is wrong.
 *
 * So this loads geometry.js and tools.js into a sandbox, builds every tool in
 * the catalogue against a synthetic price series, and checks two classes of
 * thing:
 *
 *   1. NOTHING IS BROKEN — every tool builds, produces primitives, has no
 *      non-finite coordinate, and every primitive projects.
 *   2. THE CONSTRUCTIONS ARE WHAT THEY CLAIM — the assertions a reader of the
 *      comments in tools.js would expect to hold, stated as arithmetic.
 *
 * The ratio ARRAYS are guarded on the other side, by data/test_drawtools.py,
 * which reads this same tools.js and compares it against the backend's copy.
 * Between them, a ratio cannot change in one place only.
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), "js");
const ctx = vm.createContext({
  // the two globals the catalogue reaches for, stubbed to the shape it uses
  Theme: { c: () => "#000000" },
  Sym: { num: (n) => String(Math.round(n * 100) / 100), price: String },
  Math, console, JSON,
});
for (const f of ["geometry.js", "tools.js"]) {
  vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), ctx, { filename: f });
}
const Geo = vm.runInContext("Geo", ctx);
const Tools = vm.runInContext("Tools", ctx);

let fails = 0;
const ok = (name, cond, extra = "") => {
  console.log((cond ? "  ok  " : "FAIL  ") + name + (extra ? "  — " + extra : ""));
  if (!cond) fails++;
};
const near = (a, b, tol = 1e-9) => Math.abs(a - b) <= tol * Math.max(1, Math.abs(b));
const on = (pts, q) => pts.some((p) => near(p.t, q.t) && near(p.v, q.v));

/* ── 1. every tool builds and projects ─────────────────────────────────── */
const T0 = 1750000000, IV = 86400;
const bars = [];
let p = 1200;
for (let i = 0; i < 400; i++) {
  p += Math.sin(i / 9) * 12 + Math.cos(i / 31) * 5;
  bars.push({ time: T0 + i * IV, open: p - 2, high: p + 8, low: p - 8, close: p, volume: 1e6 });
}
const buildCtx = Tools.makeCtx({
  getBars: () => bars, getIntervalSec: () => IV,
  tToX: (t) => (t - T0) / IV, vToY: (v) => 1000 - v,
});
const env = { tToX: (t) => (t - T0) / IV, vToY: (v) => 1000 - v, w: 900, h: 500 };
const ANCHORS = [
  { t: bars[100].time, v: bars[100].low },
  { t: bars[200].time, v: bars[200].high },
  { t: bars[260].time, v: bars[260].low },
  { t: bars[320].time, v: bars[320].high },
];

console.log("── every tool builds ──");
for (const [id, spec] of Object.entries(Tools.SPECS)) {
  const n = spec.anchors === "free" ? 4 : spec.anchors;
  const pts = ANCHORS.slice(0, n).map((q) => ({ ...q }));
  let prims;
  try {
    prims = spec.build(pts, buildCtx, { pts, text: "x", pane: "price" });
  } catch (e) { ok(id, false, `threw: ${e.message}`); continue; }
  if (!Array.isArray(prims) || !prims.length) { ok(id, false, "built nothing"); continue; }
  let nan = 0;
  const chk = (o) => { for (const k of ["t", "v"]) if (o && typeof o[k] === "number" && !Number.isFinite(o[k])) nan++; };
  for (const pr of prims) {
    chk(pr.a); chk(pr.b); chk(pr.entry);
    for (const k of ["v", "v1", "v2", "t", "t1", "t2"]) {
      if (typeof pr[k] === "number" && !Number.isFinite(pr[k])) nan++;
    }
    (pr.pts || []).forEach(chk);
  }
  const unprojected = prims.filter((pr) => Geo.project(pr, env) === null).length;
  ok(id, !nan && !unprojected,
     nan ? `${nan} non-finite coordinate(s)` : unprojected ? `${unprojected} would not project` : "");
}

/* ── 2. the constructions are what they claim ──────────────────────────── */
console.log("\n── the constructions hold ──");
const C = { t: 1000, v: 100 }, P = { t: 1400, v: 180 };
const dt = P.t - C.t, dv = P.v - C.v;

for (const r of [0.382, 0.618, 1]) {
  ok(`a fib ring at ${r} crosses the move at ${r}`,
     on(Geo.crossArcPts(C, dt, dv, r, Math.PI, 64), { t: C.t + dt * r, v: C.v + dv * r }));
}
{
  const fwd = Geo.crossArcPts(C, dt, dv, 0.618, Math.PI / 2, 40);
  ok("a fib arc crosses the trend line at its own ratio",
     on(fwd, { t: C.t + dt * 0.618, v: C.v + dv * 0.618 }));
  ok("a fib arc opens the way the move went", fwd.every((q) => q.t >= C.t - 1e-6));
  const back = Geo.crossArcPts(C, -dt, dv, 0.618, Math.PI / 2, 40);
  ok("a fib arc drawn right-to-left opens the other way",
     back.every((q) => q.t <= C.t + 1e-6)
     && on(back, { t: C.t - dt * 0.618, v: C.v + dv * 0.618 }));
}
{
  const s = Geo.spiralPts(C, dt, dv, 3, 288);
  ok("a fib spiral starts exactly on its outer anchor",
     near(s[0].t, P.t) && near(s[0].v, P.v));
  const rad = (q) => Math.hypot((q.t - C.t) / dt, (q.v - C.v) / dv);
  ok("a fib spiral winds inward", rad(s[s.length - 1]) < rad(s[0]) * 0.01);
  // it bulges past the corner it starts on — that IS a golden spiral, and the
  // only thing worth asserting is that it stays within reach of the drag
  const worst = Math.max(...s.map((q) =>
    Math.max(Math.abs(q.t - C.t) / Math.abs(dt), Math.abs(q.v - C.v) / Math.abs(dv))));
  ok("a fib spiral stays within reach of the drag", worst < 1.5, `${worst.toFixed(3)}×`);
}
{
  const O = { t: 1000, v: 100 }, R1 = { t: 1600, v: 100 }, R2 = { t: 1400, v: 260 };
  for (const r of [0.382, 0.786]) {
    const c = Geo.blendArcPts(O, R1, R2, r, 40);
    ok(`a wedge rung at ${r} meets both rays at ${r}`,
       near(c[0].t, O.t + (R1.t - O.t) * r) && near(c[0].v, O.v + (R1.v - O.v) * r)
       && near(c[40].t, O.t + (R2.t - O.t) * r) && near(c[40].v, O.v + (R2.v - O.v) * r));
    const mid = c[20];
    const chord = { t: (c[0].t + c[40].t) / 2, v: (c[0].v + c[40].v) / 2 };
    ok(`a wedge rung at ${r} bows rather than cutting straight`,
       Math.hypot(mid.t - chord.t, (mid.v - chord.v) * 4) > 1);
  }
}
{
  const prims = Tools.SPECS.gannFan.build([C, P], buildCtx);
  const one = prims.find((q) => q.kind === "segment" && q.width === 1.6);
  ok("the Gann 1×1 runs corner to corner", !!one && near(one.b.t, P.t) && near(one.b.v, P.v));
  const labels = prims.filter((q) => q.kind === "label").map((q) => q.text).join(",");
  ok("the Gann fan is labelled price×time",
     labels === "8×1,4×1,3×1,2×1,1×1,1×2,1×3,1×4,1×8", labels);
}
{
  const lad = Geo.ladder(1200, 1000, Tools.FIB);
  ok("0% sits at the leg's END and 100% at its START",
     near(lad[0].v, 1000) && near(lad[lad.length - 1].v, 1200));
  ok("61.8% of a 1200→1000 leg is 1123.60", near(lad[4].v, 1123.6, 1e-6));
}
ok("61.8% is the same colour on every tool", Tools.colorOf(0.618) === "#22d3ee");
ok("the retracement palette is unchanged",
   JSON.stringify(Tools.FIB_COLORS)
   === JSON.stringify(["#787b86", "#f5a524", "#ff9800", "#c084fc", "#22d3ee", "#4ea8f2", "#787b86"]));
{
  // a one-anchor square has to square itself against the real bars, and to
  // open AWAY from the half of the range its anchor sits in
  const square = (anchor) => {
    const box = Tools.SPECS.gannSquareFixed.build([anchor], buildCtx)
      .find((q) => q.kind === "box");
    const rng = buildCtx.rangeBetween(anchor.t, anchor.t + Tools.GANN_SQUARE_BARS * IV);
    return { box, rng };
  };
  const wide = { t: bars[100].time, v: bars[100].low };
  const { box, rng } = square(wide);
  ok("a fixed Gann square runs 52 bars forward",
     !!box && box.b.t === wide.t + Tools.GANN_SQUARE_BARS * IV);
  ok("a fixed Gann square is as tall as those bars ranged",
     near(Math.abs(box.b.v - box.a.v), rng.hi - rng.lo));
  ok("a fixed Gann square opens away from its anchor's half of the range",
     (wide.v <= (rng.hi + rng.lo) / 2) === (box.b.v > box.a.v));
}

/* ── 3. sampled curves survive session gaps ────────────────────────────────
 * The regression this section exists for: an intraday chart's time axis is
 * one slot per BAR, so the sixteen hours between one close and the next open
 * take no width at all. Sampled in wall-clock seconds, three quarters of
 * every fib ring landed inside a gap and drew as a single vertical column —
 * the shape rendered, hit-tested and dragged, and was a rectangle.
 */
console.log("\n── curves survive session gaps ──");
{
  // 5-minute bars, 75 per session, then a 16-hour hole — a real NSE day
  const gapped = [];
  let t = 1750000000, q = 1000;
  for (let d = 0; d < 12; d++) {
    for (let i = 0; i < 75; i++) { gapped.push({ time: t, open: q, high: q + 3, low: q - 3, close: q, volume: 1 }); t += 300; q += 0.4; }
    t += 16 * 3600;
  }
  const gapCtx = Tools.makeCtx({
    getBars: () => gapped, getIntervalSec: () => 300,
    tToX: (x) => x, vToY: (y) => y,
  });
  const A = [{ t: gapped[100].time, v: gapped[100].close },
             { t: gapped[400].time, v: gapped[400].close }];
  for (const tool of ["fibCircles", "fibArcs", "fibSpiral", "fibWedge", "gannSquare"]) {
    const n = Tools.SPECS[tool].anchors;
    const pts = n === 3 ? [...A, { t: gapped[700].time, v: gapped[700].close }] : A;
    const polys = Tools.SPECS[tool].build(pts, gapCtx).filter((p) => p.kind === "poly");
    // measure the curve where the axis is real: bar index, not seconds
    const idx = polys.flatMap((p) => p.pts.map((z) => gapCtx.indexAt(z.t)));
    const spread = Math.max(...idx) - Math.min(...idx);
    // …and count how many DISTINCT columns it occupies. A curve squashed into
    // a gap piles onto a handful; a real one spreads over hundreds.
    const columns = new Set(idx.map((i) => Math.round(i))).size;
    ok(`${tool} spreads across the bars, not into the gaps`,
       spread > 100 && columns > 40, `${Math.round(spread)} bars, ${columns} columns`);
  }
}

console.log(fails ? `\n${fails} FAILURE(S)` : `\nall clear — ${Object.keys(Tools.SPECS).length} tools`);
process.exit(fails ? 1 : 0);
