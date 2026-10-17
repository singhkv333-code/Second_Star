/**
 * Patterns — NIFTY's daily chart and its prior 20-session high and low,
 * measured from the candles shown on the canvas.
 */
"use client";

import * as React from "react";
import {
  CandlestickSeries, ColorType, CrosshairMode, LineStyle,
  type IChartApi, type IPrimitivePaneRenderer, type IPrimitivePaneView, type ISeriesApi, type ISeriesPrimitive,
  type Logical, type SeriesAttachedParameter, type Time,
} from "lightweight-charts";
import {
  ArrowUp, Bell, ChartCandlestick, ChevronDown, Crosshair, Expand, LineChart, MessageSquare, Mic, Minus,
  PenLine, Plus, Redo2, Ruler, Settings, Share2, Sparkles, Square, Type, Undo2, User,
} from "lucide-react";
import { LightweightChart } from "@/components/chart/LightweightChart";
import { formatINR } from "@/components/landing/film/film-script";
import { MacWindow, PHONE } from "./frame";
import { Logo } from "./logos";
import { NIFTY_AS_OF, NIFTY_CHANGE, NIFTY_CHANGE_PCT, NIFTY_DAILY, NIFTY_LAST, NIFTY_LEVELS } from "./nifty-data";

const C = { chartBg: "#0d0e12", grid: "rgba(255,255,255,.075)", axis: "#b2b5be", border: "#22252d", up: "#089981", dn: "#f23645", ink: "#f5f5f6" } as const;
const BARS = NIFTY_DAILY;
const ASK = "Show the prior 20-session range on NIFTY.";

/* ── the scene: observed range lines resolved onto the shown bars ─ */

const ROLE: Record<string, string> = { upper: "#ef4444", lower: "#22c55e" };
const roleCol = (r?: string) => ROLE[r ?? ""] ?? "#c084fc";

type Pt = { t: number; v: number };
type Prim =
  | { kind: "poly"; role?: string; pts: readonly Pt[]; closed?: boolean; fill?: boolean; stroke?: boolean; solid?: boolean; label?: string }
  | { kind: "segment"; role?: string; p1: Pt; p2: Pt; dashed?: boolean; label?: string }
  | { kind: "candle"; role?: string; t1: number; t2?: number; hi: number; lo?: number; label?: string };
type SceneKey = "range_high" | "range_low";
const scene = (k: SceneKey): readonly Prim[] => {
  const price = k === "range_high" ? NIFTY_LEVELS[0].price : NIFTY_LEVELS[1].price;
  const role = k === "range_high" ? "upper" : "lower";
  return [{ kind: "segment", role, p1: { t: BARS[BARS.length - 21]![0], v: price }, p2: { t: BARS[BARS.length - 1]![0], v: price }, dashed: true, label: k === "range_high" ? "20D high" : "20D low" }];
};
type Row = { key: SceneKey; name: string; when: string; state: string; dir: "up" | "dn"; record: React.ReactNode };
const ROWS: Row[] = [
  { key: "range_high", name: "20-session high", when: NIFTY_AS_OF, state: "Measured from candles", dir: "dn", record: <span>{formatINR(NIFTY_LEVELS[0].price)}</span> },
  { key: "range_low", name: "20-session low", when: NIFTY_AS_OF, state: "Measured from candles", dir: "up", record: <span>{formatINR(NIFTY_LEVELS[1].price)}</span> },
];

/* ── the section ─────────────────────────────────────────────────────────── */

export function Patterns(): React.ReactElement {
  const ref = React.useRef<HTMLElement | null>(null);
  const [k, setK] = React.useState(0);
  const [run, setRun] = React.useState(false);
  const [held, setHeld] = React.useState(false);

  React.useEffect(() => {
    const el = ref.current;
    if (!el || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const io = new IntersectionObserver(([e]) => setRun(!!e?.isIntersecting), { threshold: 0.3 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  React.useEffect(() => {
    if (!run || held) return;
    const id = window.setTimeout(() => setK((v) => (v + 1) % ROWS.length), 3200);
    return () => clearTimeout(id);
  }, [run, held, k]);

  // On a phone the detector's rows are cards on a sideways belt, and the card
  // that settles in view is the shape drawn on the chart. The auto-advance
  // slides the belt to its card; a swipe the reader makes picks the pattern.
  // `steering` marks the belt's own slides so they are not read back as one.
  const listRef = React.useRef<HTMLUListElement | null>(null);
  const steering = React.useRef(0);
  const sideways = (ul: HTMLUListElement) => ul.scrollWidth > ul.clientWidth + 1;
  const stop = (ul: HTMLUListElement, li: HTMLElement) =>
    Math.min(li.offsetLeft - parseFloat(getComputedStyle(ul).scrollPaddingLeft || "0"), ul.scrollWidth - ul.clientWidth);
  React.useEffect(() => {
    const ul = listRef.current, li = ul?.children[k] as HTMLElement | undefined;
    if (!ul || !li || !sideways(ul)) return;
    const left = stop(ul, li);
    if (Math.abs(ul.scrollLeft - left) < 2) return;
    steering.current = performance.now() + 800;
    ul.scrollTo({ left, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [k]);
  React.useEffect(() => {
    const ul = listRef.current;
    if (!ul) return;
    let t = 0;
    const settled = () => {
      if (performance.now() < steering.current || !sideways(ul)) return;
      let best = 0, gap = Infinity;
      Array.from(ul.children).forEach((li, i) => {
        const d = Math.abs(stop(ul, li as HTMLElement) - ul.scrollLeft);
        if (d < gap) { gap = d; best = i; }
      });
      setK(best);
    };
    const onScroll = () => { clearTimeout(t); t = window.setTimeout(settled, 120); };
    ul.addEventListener("scroll", onScroll, { passive: true });
    return () => { ul.removeEventListener("scroll", onScroll); clearTimeout(t); };
  }, []);

  const row = ROWS[k]!;
  return (
    <section id="patterns" className="v3 pt" ref={ref}>
      <div className="v3-shell">
        <header className="v3-head" data-reveal>
          <h2>Auto-detect levels and patterns.</h2>
          <p>Here, NIFTY's 20-session high and low are measured from the candles that set them.</p>
        </header>
        <MacWindow title="NIFTY — Pivot" className="pt-win">
          <div className="px-app">
            <AppBar />
            <div className="px-main">
              <Rail />
              <div className="px-chartpane">
                <PaneBar />
                <div className="px-chartwrap">
                  <Readout />
                  <PatternChart row={row} />
                  <Chips row={row} />
                </div>
              </div>
              <div className="px-chat pt-col" onMouseEnter={() => setHeld(true)} onMouseLeave={() => setHeld(false)}>
                <div className="px-seg"><b>Research</b><i>Execution</i></div>
                <div className="pt-thread">
                  <div className="px-user" data-show="">{ASK}</div>
                  <div className="pt-card">
                    <header><b>Chart range</b><span>NIFTY · 1D · prior 20 sessions</span></header>
                    <ul>

                      {ROWS.map((r, i) => (
                        <li key={r.key}>
                          <button type="button" className={i === k ? "on" : undefined} onClick={() => setK(i)}>
                            <span className={`pt-dot ${r.dir}`} />
                            <span className="pt-name"><b>{r.name}</b><small>{r.when} · {r.state}</small></span>
                            <span className="pt-rec">{r.record}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                    <footer>Historical range from observed daily bars. Not a forecast.</footer>
                  </div>
                </div>
                <div className="px-composer">
                  <span className="px-ph">Ask about this chart…</span>
                  <div className="px-comp-row">
                    <span className="px-chip"><Logo s="NIFTY" size={16} />NIFTY</span>
                    <span className="px-model"><Sparkles />Pivot<ChevronDown /></span>
                    <span className="px-grow" />
                    <Mic />
                    <span className="px-send"><ArrowUp /></span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </MacWindow>
      </div>
    </section>
  );
}

/* ── chrome ──────────────────────────────────────────────────────────────── */

function AppBar() {
  return (
    <div className="px-bar">
      <span className="px-word">Pivot.</span>
      <span className="px-grow" />
      <span className="px-btn ghost"><Share2 />Share</span>
      <span className="px-btn plain">Unnamed<ChevronDown /></span>
      <span className="px-btn plain"><Plus />Widgets</span>
      <span className="px-icon"><Expand /></span>
      <span className="px-btn on"><MessageSquare />Chat</span>
      <span className="px-icon"><User /></span>
    </div>
  );
}

function Rail() {
  const tools = [Crosshair, PenLine, Minus, LineChart, Square, Ruler, Type, Bell];
  return (
    <div className="px-rail" aria-hidden="true">
      {tools.map((T, i) => <span key={i} className={i === 0 ? "on" : undefined}><T /></span>)}
    </div>
  );
}

function PaneBar() {
  return (
    <div className="px-panebar">
      <span className="px-tab on">Chart</span>
      <span className="px-tab">1D</span>
      <span className="px-icon sm"><ChartCandlestick /></span>
      <span className="px-tab"><LineChart />Indicators<ChevronDown /></span>
      <span className="px-tab"><Plus />Compare</span>
      <span className="px-icon sm dim"><Undo2 /></span>
      <span className="px-icon sm dim"><Redo2 /></span>
      <span className="px-grow" />
      <span className="px-icon sm"><Settings /></span>
    </div>
  );
}

function Readout() {
  const p = NIFTY_LAST;
  const dn = NIFTY_CHANGE < 0;
  return (
    <div className="px-readout">
      <div className="px-ro-title"><Logo s="NIFTY" size={18} />NIFTY <i>· 1D ·</i> <em>NSE · Yahoo EOD</em></div>
      <div className={`px-ro-row ${dn ? "dn" : "up"}`}>
        <span><i>O</i>{formatINR(p[1])}</span><span><i>H</i>{formatINR(p[2])}</span>
        <span><i>L</i>{formatINR(p[3])}</span><span><i>C</i>{formatINR(p[4])}</span>
        <span>{NIFTY_CHANGE.toFixed(1)} ({NIFTY_CHANGE_PCT.toFixed(2)}%)</span>

      </div>
    </div>
  );
}

/* ── the chart, with the selected shape drawn ────────────────────────────── */

/* The drawn shapes' names, stacked top-right in their role colour: the scene
   layer's legend (scene.js paintChips), not labels pinned to the candles. */
function Chips({ row }: { row: Row }) {
  const named = scene(row.key).filter((p) => p.label);
  return (
    <div className="pt-chips" key={row.key}>
      {named.map((p) => <span key={p.label} style={{ color: roleCol(p.role) }}>{p.label}</span>)}
    </div>
  );
}

/** A bar time → its logical index, interpolated between bars, so a vertex
 *  lands where the chart put that day whatever the visible range. */
function logicalOf(t: number): number {
  let lo = 0, hi = BARS.length - 1;
  if (t <= BARS[0]![0]) return 0;
  if (t >= BARS[hi]![0]) return hi;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (BARS[m]![0] <= t) lo = m; else hi = m; }
  const a = BARS[lo]![0], b = BARS[hi]![0];
  return lo + (t - a) / (b - a);
}

type DrawTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/** The pattern scene as a series primitive, painted the way the workspace
 *  paints it (scene.js + geometry.js): a fill-only polygon tints the body at
 *  0.12 of the role colour, the outline through the swings is solid at 1.5px,
 *  the neckline dashes 7/4, and a candlestick pattern is one small dot six
 *  pixels over the bar's high. Redrawn by the chart on every frame, so it
 *  never lags a resize. */
class ScenePrimitive implements ISeriesPrimitive<Time> {
  private prims: readonly Prim[] = [];
  private p: SeriesAttachedParameter<Time> | null = null;
  private view: IPrimitivePaneView;
  constructor() {
    const renderer: IPrimitivePaneRenderer = { draw: (t) => this.draw(t) };
    this.view = { zOrder: () => "normal", renderer: () => renderer };
  }
  attached(p: SeriesAttachedParameter<Time>) { this.p = p; }
  detached() { this.p = null; }
  paneViews() { return [this.view]; }
  set(prims: readonly Prim[]) { this.prims = prims; this.p?.requestUpdate(); }
  private draw(target: DrawTarget) {
    const p = this.p;
    if (!p) return;
    const ts = p.chart.timeScale(), se = p.series;
    const X = (t: number) => ts.logicalToCoordinate(logicalOf(t) as Logical);
    const Y = (v: number) => se.priceToCoordinate(v);
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      ctx.lineJoin = "round"; ctx.lineCap = "round";
      for (const a of this.prims) {
        const col = roleCol(a.role);
        ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1.5;
        if (a.kind === "poly") {
          const pts = a.pts.map((q) => [X(q.t), Y(q.v)] as const);
          if (pts.some(([x, y]) => x == null || y == null)) continue;
          ctx.setLineDash(a.solid || a.fill ? [] : [7, 4]);
          ctx.beginPath();
          pts.forEach(([x, y], i) => (i ? ctx.lineTo(x!, y!) : ctx.moveTo(x!, y!)));
          if (a.closed) ctx.closePath();
          if (a.fill) { ctx.globalAlpha = 0.12; ctx.fill(); ctx.globalAlpha = 1; }
          if (a.stroke !== false) ctx.stroke();
        } else if (a.kind === "segment") {
          const x1 = X(a.p1.t), y1 = Y(a.p1.v), x2 = X(a.p2.t), y2 = Y(a.p2.v);
          if (x1 == null || y1 == null || x2 == null || y2 == null) continue;
          ctx.setLineDash(a.dashed ? [7, 4] : []);
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        } else if (a.kind === "candle") {
          const x1 = X(a.t1), x2 = X(a.t2 ?? a.t1), y = Y(a.hi);
          if (x1 == null || x2 == null || y == null) continue;
          ctx.setLineDash([]);
          ctx.beginPath(); ctx.arc((x1 + x2) / 2, y - 6 - 2.3, 2.3, 0, Math.PI * 2); ctx.fill();
        }
      }
      ctx.setLineDash([]);
    });
  }
}

/** On a phone the chart shows the selected shape, not the whole 120 bars: the
 *  bars it spans with a margin either side, and never fewer than `min`, so
 *  the candles are bodies you can read and the drawing is large enough to
 *  follow. A wide window keeps every bar. */
function frameShape(chart: IChartApi, key: SceneKey, min = 34) {
  if (!window.matchMedia(PHONE).matches) { chart.timeScale().fitContent(); return; }
  const ts: number[] = [];
  for (const a of scene(key)) {
    if (a.kind === "poly") ts.push(...a.pts.map((q) => q.t));
    else if (a.kind === "segment") ts.push(a.p1.t, a.p2.t);
    else ts.push(a.t1, a.t2 ?? a.t1);
  }
  const n = BARS.length;
  let from = ts.length ? logicalOf(Math.min(...ts)) - 5 : n - min;
  let to = ts.length ? logicalOf(Math.max(...ts)) + 5 : n;
  if (to - from < min) { const mid = (from + to) / 2; from = mid - min / 2; to = mid + min / 2; }
  if (to > n + 1) { from -= to - (n + 1); to = n + 1; }
  if (from < -0.5) { to += -0.5 - from; from = -0.5; }
  chart.timeScale().setVisibleLogicalRange({ from, to });
}

function PatternChart({ row }: { row: Row }) {
  const api = React.useRef<{ chart: IChartApi; k: ISeriesApi<"Candlestick">; prim: ScenePrimitive } | null>(null);
  const rowRef = React.useRef(row);
  rowRef.current = row;
  React.useEffect(() => {
    const a = api.current;
    if (!a) return;
    a.prim.set(scene(row.key));
    frameShape(a.chart, row.key);
  }, [row]);
  React.useEffect(() => {
    const mq = window.matchMedia(PHONE);
    const on = () => { const a = api.current; if (a) frameShape(a.chart, rowRef.current.key); };
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return (
    <LightweightChart
      height="100%"
      options={{
        layout: { background: { type: ColorType.Solid, color: C.chartBg }, textColor: C.axis, fontFamily: "Inter, sans-serif", fontSize: 11, attributionLogo: false },
        grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
        rightPriceScale: { borderColor: C.border, scaleMargins: { top: 0.2, bottom: 0.08 } },
        timeScale: { borderColor: C.border, rightOffset: 3 },
        crosshair: { mode: CrosshairMode.Hidden },
        handleScroll: false, handleScale: false,
      }}
      onReady={(chart: IChartApi) => {
        const k = chart.addSeries(CandlestickSeries, {
          upColor: C.up, downColor: C.dn, wickUpColor: C.up, wickDownColor: C.dn, borderVisible: false,
          priceLineColor: C.dn, priceLineStyle: LineStyle.Dotted,
        });
        k.setData(BARS.map(([t, o, h, l, c]) => ({ time: t as unknown as Time, open: o, high: h, low: l, close: c })));
        const prim = new ScenePrimitive();
        k.attachPrimitive(prim);
        prim.set(scene(rowRef.current.key));
        api.current = { chart, k, prim };
        frameShape(chart, rowRef.current.key);
        return () => { api.current = null; };
      }}
    />
  );
}
