/**
 * Tour — the workspace, exactly as it opens, with a cursor using it.
 *
 * A replica of Pivot's desk (`chart`): the 46px top bar (Share,
 * the layout menu, + Widgets, full screen, Chat), the drawing rail, the chart
 * pane with its own bar and readout, and the chat column on its empty state —
 * "Start asking with" and the twelve drawn tiles, their glyphs copied from
 * `preview/js/icons.js`. The cursor then does what a first session does:
 *
 *   1. opens + Widgets — the hub and its five shelves — and docks a
 *      Watchlist beside the chart, then searches the hub for the Calendar
 *      and docks it under the watchlist;
 *   2. clicks through the watchlist, and the chart follows the symbol;
 *   3. opens Indicators and adds an EMA calculated from NIFTY daily closes;
 *   4. asks for the prior 20-session range and draws its measured bounds.
 *
 * NIFTY bars are frozen from Yahoo Finance; secondary stock bars use the
 * older local dataserver snapshot. The marks are the app's own.
 * It plays only while on screen and rests on the finished desk under reduced
 * motion.
 */
"use client";

import * as React from "react";
import {
  CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle,
  type IChartApi, type IPriceLine, type ISeriesApi, type Time,
} from "lightweight-charts";
import {
  ArrowUp, Camera, ChartCandlestick, ChevronDown, ChevronUp, Clock, Eraser, Expand, GripVertical, Download,
  LineChart, Magnet, Maximize2, MessageSquare, Mic, MousePointer2, PenLine, Plus, Redo2, Ruler, Search, Settings,
  Share2, Sparkles, Square, Star, Trash2, CalendarDays, Filter, Layers, Briefcase, Landmark, Newspaper, FileText,
  Files, BookOpen, NotebookPen, Sheet, Globe, Code2, Tv, Type, Undo2, User, X, Crosshair, AlignLeft, Spline, Sigma, Bell, Copy,
} from "lucide-react";
import { LightweightChart } from "@/components/chart/LightweightChart";
import { formatINR } from "@/components/landing/film/film-script";
import { MacWindow, PHONE, fitBars } from "./frame";
import { Logo } from "./logos";
import { CALENDAR, WATCH } from "./snapshot";
import { DAILY } from "./snapshot-bars";
import { NIFTY_DAILY, NIFTY_EMA20, NIFTY_LEVELS, NIFTY_WATCH } from "./nifty-data";

const C = { chartBg: "#0d0e12", grid: "rgba(255,255,255,.075)", axis: "#b2b5be", border: "#22252d", up: "#089981", dn: "#f23645" } as const;
const WATCHLIST = ["RELIANCE", "NIFTY", "HDFCBANK", "TCS", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC"] as const;
const SWITCH = ["HDFCBANK", "TCS", "NIFTY"] as const;
/* The marked range is computed from NIFTY's prior 20 daily candles. */
const ASK = "Mark the 20-session high and low on the daily chart";
const LEVELS = NIFTY_LEVELS;

/* Phones see the return between two RELIANCE results. Dates are the sessions
   the market first reacted to each filing, from the dataserver's `results`
   table (both announced after market); closes and the move are read off the
   same daily bars the chart draws. */
const EARN = (() => {
  const sym = "RELIANCE" as const;
  const bars = DAILY[sym]!;
  const at = (t: number) => bars.findIndex((b) => b[0] === t);
  const a = { t: 1777228200, q: "Q4 FY26", d: "27 Apr" }, b = { t: 1784485800, q: "Q1 FY27", d: "20 Jul" };
  const ia = at(a.t), ib = at(b.t);
  const ca = bars[ia]![4], cb = bars[ib]![4];
  return { sym, a: { ...a, i: ia, c: ca }, b: { ...b, i: ib, c: cb }, pct: (cb / ca - 1) * 100, sessions: ib - ia };
})();
/* A few of the indicator engine's fifty (`/indicators`), as the menu lists them. */
const IND_MENU: [string, string][] = [["EMA", "Exponential moving average"], ["SMA", "Simple moving average"], ["RSI", "Relative strength index"], ["MACD", "Moving average convergence divergence"], ["Bollinger Bands", "Bands two deviations around an average"], ["Supertrend", "ATR-based trend direction"], ["VWAP", "Volume-weighted average price"]];

/* The empty chat's twelve tiles (`chat.js` TEMPLATES) and their drawings
   (`icons.js` T). `a`/`af` carry the one accent; `up`/`down` the plan's two. */
const TILES: [string, string][] = [
  ["Levels", '<path d="M7 32h26"/><path d="M11 15v14M18 12v17M25 17v12M31 14v15"/><rect x="9" y="19" width="4" height="6" rx="1"/><rect x="16" y="16" width="4" height="9" rx="1"/><rect x="23" y="20" width="4" height="5" rx="1"/><rect x="29" y="18" width="4" height="7" rx="1"/><path class="a" d="M6 14.5h28M6 28h28" stroke-dasharray="3 2.5"/>'],
  ["Patterns", '<path d="M7 32h26"/><path d="M10 27.5 14 17.6l4 7.9 4-6 4 3.5 3.5-1.2"/><path class="a" d="M9 15 31 22M9 29l22-7"/>'],
  ["Trends", '<path d="M7 32h26"/><path d="M11 20v9M18 16v13M25 19v10M31 12v17"/><path class="a" d="M10 28.5 32 13"/>'],
  ["Indicators", '<path d="M7 17l5-5 5 4 5-7 5 6 4-3"/><path d="M6 22h28" opacity=".55"/><path class="a" d="M7 30c3 0 3-5 6-5s3 6 6 6 3-7 6-7 3 4 6 4"/>'],
  ["Screenshot", '<path class="a" d="M7 14.5V9.5A2 2 0 0 1 9 7.5h5"/><path class="a" d="M33 14.5V9.5a2 2 0 0 0-2-2h-5"/><path class="a" d="M7 25.5v5a2 2 0 0 0 2 2h5"/><path class="a" d="M33 25.5v5a2 2 0 0 1-2 2h-5"/><path d="M11 26h18"/><path d="M12 23l4-6 4 4 4-7 4 5"/>'],
  ["Why it moved", '<path d="M7 32h26"/><path d="M9 28l4-1.5 4 1"/><path class="a" d="M17 27.5 23 15"/><path d="M23 15l4 3 5-2.5"/><path class="af" d="M28.6 8l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" stroke="none"/>'],
  ["Plan", '<path d="M7 32h26"/><path d="M13 15v14M20 12v17M27 18v11"/><path class="up" d="M8 12h26"/><path d="M8 21h26" stroke-dasharray="3 2.5"/><path class="down" d="M8 28h26"/>'],
  ["Evidence", '<path d="M7 32h26"/><path d="M8 22l5-6 5 4 5-7"/><path class="a" d="M23 29v-6M28 29v-11M33 29v-8"/><path d="M21 32h14" opacity=".55"/>'],
  ["Screen", '<rect x="6" y="8" width="28" height="24" rx="3"/><path d="M6 15h28M15 15v17M25 15v17" opacity=".55"/><path d="M8 27l3-3 2 2M17 26l3-4 2 3M27 28l2-3 2 2"/><rect class="af" x="15" y="15" width="10" height="8.5" rx="0" stroke="none" opacity=".16"/><path class="a" d="M17 21l3-4 2 3"/>'],
  ["Compare", '<path d="M7 32h26"/><path d="M8 26l6-3 6-6 6-4 6-2"/><path class="a" d="M8 26l6 1 6 3 6-1 6 2"/>'],
  ["Alert", '<path d="M7 32h26"/><path d="M7 27l6-2 5-5 5-3"/><path d="M6 20h28" stroke-dasharray="3 2.5"/><path class="a" d="M27 19a4.5 4.5 0 0 1 9 0c0 4 1.2 5 1.2 5H25.8s1.2-1 1.2-5z"/><path class="a" d="M30 27.5a1.8 1.8 0 0 0 3 0"/>'],
  ["Earnings", '<path d="M7 32h26"/><path d="M11 20v9M18 22v7M31 14v15"/><rect x="9" y="22" width="4" height="5" rx="1"/><rect x="16" y="24" width="4" height="4" rx="1"/><rect x="29" y="17" width="4" height="9" rx="1"/><path class="a" d="M25 11v21" stroke-dasharray="3 2.5"/><rect class="af" x="21.5" y="6.5" width="7" height="5" rx="1.4" stroke="none"/>'],
];

/* Two of the chat's own openers (`chat.js` TEMPLATES), prompts verbatim. */
const NEXT: [string, string][] = [
  ["Patterns", "What chart patterns are forming on NIFTY right now? Mark them."],
  ["Why it moved", "Explain NIFTY's last big move — what happened, and how far did it travel?"],
];

/* The hub's shelves and the cards on the first of them, in the app's order. */
const SHELVES = ["All", "Market", "Research", "Tools", "Media"];
const HUB_CARDS: [string, React.ComponentType<{ className?: string }>][] = [
  ["Strategy", Sigma], ["Chart", ChartCandlestick], ["Watchlist", Star], ["Alerts", Bell], ["Screener", Filter],
  ["Market depth", Layers], ["Portfolio", Briefcase], ["Financials", Landmark], ["News", Newspaper], ["PDF", FileText],
  ["Documents", Files], ["Calendar", CalendarDays], ["Journal", BookOpen], ["Notes", NotebookPen], ["Sheet", Sheet],
  ["Browser", Globe], ["Code", Code2], ["Live TV", Tv],
];

type Sym = (typeof WATCHLIST)[number];
type Cursor = { x: number; y: number; down: boolean; on: boolean };

const sleep = (ms: number, alive: () => boolean) => new Promise<void>((res, rej) => setTimeout(() => (alive() ? res() : rej(new Error("stop"))), ms));

export function Tour(): React.ReactElement {
  const appRef = React.useRef<HTMLDivElement | null>(null);
  const [sym, setSym] = React.useState<Sym>("NIFTY");
  const [hub, setHub] = React.useState(false);
  const [menu, setMenu] = React.useState(false);
  const [docked, setDocked] = React.useState(false);
  const [cal, setCal] = React.useState(false);
  const [find, setFind] = React.useState("");
  const [ema, setEma] = React.useState(false);
  const [phase, setPhase] = React.useState(0); // 0 empty chat · 1 asked · 2 thinking · 3 answered
  const [draft, setDraft] = React.useState("");
  const [cur, setCur] = React.useState<Cursor>({ x: 0.55, y: 0.55, down: false, on: false });
  const [earn, setEarn] = React.useState(0); // phones: 0 off · 1 first results · 2 second · 3 the return between

  /** Move the cursor onto an element by its data-t, in px within the app. */
  const aim = React.useCallback((t: string) => {
    const app = appRef.current, el = app?.querySelector<HTMLElement>(`[data-t="${t}"]`);
    if (!app || !el) return;
    const a = app.getBoundingClientRect(), r = el.getBoundingClientRect();
    setCur((c) => ({ ...c, on: true, x: (r.left + r.width * 0.5 - a.left) / a.width, y: (r.top + r.height * 0.55 - a.top) / a.height }));
  }, []);

  React.useEffect(() => {
    const app = appRef.current;
    if (!app) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setDocked(true); setCal(true); setEma(true); setPhase(3); return; }
    let live = false, run = 0;
    const play = async (id: number) => {
      const alive = () => live && run === id;
      const wait = (ms: number) => sleep(ms, alive);
      const click = async () => { setCur((c) => ({ ...c, down: true })); await wait(160); setCur((c) => ({ ...c, down: false })); };
      const go = async (t: string, ms = 950) => { aim(t); await wait(ms); await click(); };
      try {
        for (;;) {
          // A phone hides the chat, rail, hub and cursor, so the desk tour would
          // play to nothing. It gets the chart's own story instead: the move
          // between two results, then the levels.
          if (window.matchMedia(PHONE).matches) {
            setHub(false); setMenu(false); setDocked(false); setCal(false); setEma(false); setDraft(""); setPhase(0); setEarn(0);
            setSym(EARN.sym); await wait(1100);
            setEarn(1); await wait(1300);
            setEarn(2); await wait(1300);
            setEarn(3); await wait(4600);
            setEarn(0); await wait(500);
            setSym("NIFTY"); await wait(900);
            setPhase(3); await wait(4200);
            setPhase(0); await wait(400);
            continue;
          }
          setEarn(0);
          setSym("NIFTY"); setHub(false); setMenu(false); setDocked(false); setCal(false); setFind(""); setEma(false); setPhase(0); setDraft("");
          await wait(1100);
          await go("widgets"); setHub(true); await wait(800);
          await go("hub-Watchlist"); setHub(false); setDocked(true); await wait(1200);
          await go("widgets"); setHub(true); await wait(600);
          for (const q of ["c", "ca", "cal"]) { setFind(q); await wait(220); }
          await wait(400);
          await go("hub-Calendar", 800); setHub(false); setFind(""); setCal(true); await wait(1200);
          for (const s of SWITCH) { await go(`row-${s}`, 850); setSym(s); await wait(1300); }
          await go("ind"); setMenu(true); await wait(700);
          await go("ind-EMA", 800); setMenu(false); setEma(true); await wait(1300);
          await go("composer", 900);
          const words = ASK.split(" ");
          for (let i = 1; i <= words.length; i++) { setDraft(words.slice(0, i).join(" ")); await wait(110); }
          setDraft(ASK); await wait(300);
          await go("send", 700); setDraft(""); setPhase(1); await wait(700);
          setPhase(2); await wait(1900);
          setPhase(3); await wait(5200);
          setCur((c) => ({ ...c, on: false })); await wait(700);
        }
      } catch { /* left the screen: the next entry starts over */ }
    };
    const io = new IntersectionObserver(([e]) => {
      const on = !!e?.isIntersecting;
      if (on && !live) { live = true; run += 1; void play(run); }
      if (!on) live = false;
    }, { threshold: 0.3 });
    io.observe(app);
    return () => { live = false; io.disconnect(); };
  }, [aim]);

  return (
    <section id="demo" className="v3 tr" data-nav-theme="dark">
      <div className="v3-shell">
        <header className="v3-head" data-reveal>
          <h2>Built around your trading.</h2>
          <p>Customize your charts, tools and workspace.</p>
        </header>
        <MacWindow title={`${sym} — Pivot`} className="tr-win">
          <div className="tw" ref={appRef}>
            <TopBar hub={hub} />
            <div className="tw-main">
              <Rail />
              <ChartPane sym={sym} ema={ema} levels={phase >= 3 && sym === "NIFTY"} menu={menu} dock={`${docked}${cal}`} earn={sym === EARN.sym ? earn : 0} />
              <div className={`tw-dock${docked ? " on" : ""}${cal ? " cal" : ""}`}><div className="tw-dock-w"><WatchPane sym={sym} /></div><div className="tw-dock-c"><CalPane /></div></div>
              <ChatCol sym={sym} draft={draft} phase={phase} />
            </div>
            <Hub open={hub} find={find} />
            <span
              className={`tw-cursor${cur.on ? " on" : ""}${cur.down ? " down" : ""}`} aria-hidden="true"
              style={{ left: `${cur.x * 100}%`, top: `${cur.y * 100}%` }}
            >
              <MousePointer2 />
            </span>
          </div>
        </MacWindow>
      </div>
    </section>
  );
}

/* ── chrome ──────────────────────────────────────────────────────────────── */

function TopBar({ hub }: { hub: boolean }) {
  return (
    <div className="tw-top">
      <span className="tw-word">Pivot.</span>
      <span className="tw-grow" />
      <span className="tw-btn ol"><Share2 />Share</span>
      <span className="tw-btn plain">Unnamed<ChevronUp /></span>
      <span className={`tw-btn${hub ? " solid" : " plain"}`} data-t="widgets"><Plus />Widgets</span>
      <span className="tw-ico"><Expand /></span>
      <span className="tw-btn ol on"><MessageSquare />Chat</span>
      <span className="tw-ico"><User /></span>
    </div>
  );
}

function Rail() {
  const top = [Crosshair, Spline, AlignLeft, PenLine, Square, Ruler, LineChart, Type, Magnet];
  const bottom = [Camera, Eraser, Download, Trash2];
  return (
    <div className="tw-rail" aria-hidden="true">
      {top.map((T, i) => <span key={i} className={i === 0 ? "on" : undefined}><T /></span>)}
      <span className="tw-grow" />
      {bottom.map((T, i) => <span key={`b${i}`}><T /></span>)}
    </div>
  );
}

function PaneBar({ children, tools }: { children: React.ReactNode; tools?: React.ReactNode }) {
  return (
    <div className="tw-pbar">
      <GripVertical className="tw-grip" />
      {children}
      <span className="tw-grow" />
      {tools}
      <span className="tw-ico sm"><Maximize2 /></span>
      <span className="tw-ico sm"><X /></span>
    </div>
  );
}

/* ── chart ───────────────────────────────────────────────────────────────── */

const fmtV = new Intl.NumberFormat("en-IN");

function ChartPane({ sym, ema, levels, menu, dock, earn }: { sym: Sym; ema: boolean; levels: boolean; menu: boolean; dock: string; earn: number }) {
  const bars = sym === "NIFTY" ? NIFTY_DAILY : DAILY[sym]!;
  const [, o, h, l, c, v] = bars[bars.length - 1]!;
  const prev = bars[bars.length - 2]![4];
  const ch = c - prev, pct = (ch / prev) * 100, dn = ch < 0;
  const showEma = ema && sym === "NIFTY";
  const e20 = NIFTY_EMA20;
  return (
    <div className="tw-pane tw-chart">
      <PaneBar tools={<><span className="tw-ico sm tw-wide"><Square /></span><span className="tw-ico sm"><Settings /></span></>}>
        <span className="tw-tab on">Chart</span>
        <span className="tw-tab">1D</span>
        <span className="tw-ico sm"><ChartCandlestick /></span>
        <span className={`tw-tab${menu ? " on" : ""}`} data-t="ind"><LineChart />Indicators<ChevronDown /></span>
        <span className="tw-tab tw-wide"><Plus />Compare</span>
        <span className="tw-ico sm dim tw-wide"><Undo2 /></span>
        <span className="tw-ico sm dim tw-wide"><Redo2 /></span>
      </PaneBar>
      <div className="tw-cwrap">
        <div className="tw-ro">
          <div className="tw-ro-t"><Logo s={sym} size={18} />{sym}<i>· 1D ·</i><em>{sym === "NIFTY" ? "NSE · Yahoo EOD" : "NSE"}</em></div>
          <div className={`tw-ro-r ${dn ? "dn" : "up"}`}>
            <span className="ro-x"><i>O</i>{formatINR(o)}</span><span className="ro-x"><i>H</i>{formatINR(h)}</span><span className="ro-x"><i>L</i>{formatINR(l)}</span>
            <span><i>C</i>{formatINR(c)}</span><span className="ro-x"><i>V</i>{fmtV.format(v)}</span>
            <span>{ch >= 0 ? "+" : ""}{ch.toFixed(2)} ({pct >= 0 ? "+" : ""}{pct.toFixed(2)}%)</span>
          </div>
          {showEma && <div className="tw-ro-ind"><i />EMA 20 <b>{formatINR(e20[e20.length - 1]![1])}</b></div>}
          {levels && (
            <ul className="tw-levels">
              {LEVELS.map((x) => <li key={x.price} className={x.role === "H" ? "dn" : "up"}>20D {x.role} {formatINR(x.price)}</li>)}
            </ul>
          )}
        </div>
        <Candles sym={sym} ema={showEma} levels={levels} dock={dock} earn={earn} />
        <div className={`tw-menu${menu ? " open" : ""}`} aria-hidden="true">
          <div className="tw-hub-find"><Search />Search 50 indicators</div>
          <ul>{IND_MENU.map(([n, d]) => <li key={n} data-t={`ind-${n}`}><b>{n}</b><span>{d}</span></li>)}</ul>
        </div>
      </div>
    </div>
  );
}

type EarnGeo = { xa: number; xb: number; ya: number; yb: number; bottom: number; right: number };

/** Frame both results with room either side; otherwise the screen's usual fit. */
function frame(chart: IChartApi, sym: string, earn: number): void {
  if (earn > 0 && sym === EARN.sym) chart.timeScale().setVisibleLogicalRange({ from: EARN.a.i - 6, to: EARN.b.i + 8 });
  else fitBars(chart, (sym === "NIFTY" ? NIFTY_DAILY : DAILY[sym]!).length);
}

function Candles({ sym, ema, levels, dock, earn }: { sym: Sym; ema: boolean; levels: boolean; dock: string; earn: number }) {
  const ref = React.useRef<{ chart: IChartApi; k: ISeriesApi<"Candlestick">; v: ISeriesApi<"Histogram">; e: ISeriesApi<"Line">; lines: IPriceLine[] } | null>(null);
  const state = React.useRef({ sym, ema, levels, earn });
  state.current = { sym, ema, levels, earn };
  const [geo, setGeo] = React.useState<EarnGeo | null>(null);
  // where the two results sit on screen, re-read whenever the view moves
  const measure = React.useCallback(() => {
    const api = ref.current, { sym: s, earn: n } = state.current;
    if (!api || !n || s !== EARN.sym) { setGeo(null); return; }
    const ts = api.chart.timeScale();
    const xa = ts.timeToCoordinate(EARN.a.t as unknown as Time), xb = ts.timeToCoordinate(EARN.b.t as unknown as Time);
    const ya = api.k.priceToCoordinate(EARN.a.c), yb = api.k.priceToCoordinate(EARN.b.c);
    if (xa == null || xb == null || ya == null || yb == null) { setGeo(null); return; }
    const el = api.chart.chartElement();
    setGeo({ xa, xb, ya, yb, bottom: ts.height(), right: el.clientWidth - api.k.priceScale().width() });
  }, []);
  const apply = React.useCallback(() => {
    const api = ref.current;
    if (!api) return;
    const { sym: s, ema: e, levels: lv } = state.current;
    const b = s === "NIFTY" ? NIFTY_DAILY : DAILY[s]!;
    api.k.setData(b.map(([t, o, h, l, c]) => ({ time: t as unknown as Time, open: o, high: h, low: l, close: c })));
    api.v.setData(b.map(([t, o, , , c, v]) => ({ time: t as unknown as Time, value: v, color: c >= o ? "rgba(8,153,129,.45)" : "rgba(242,54,69,.45)" })));
    api.e.setData(e ? NIFTY_EMA20.map(([t, v]) => ({ time: t as unknown as Time, value: v })) : []);
    api.lines.forEach((l) => api.k.removePriceLine(l));
    api.lines = lv ? LEVELS.map((x) => api.k.createPriceLine({ price: x.price, color: x.role === "H" ? C.dn : C.up, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" })) : [];
    frame(api.chart, s, state.current.earn);
    requestAnimationFrame(() => requestAnimationFrame(measure));
  }, [measure]);
  React.useEffect(() => { apply(); }, [sym, ema, levels, apply]);
  // entering or leaving the results view reframes; the steps inside it only redraw
  const framed = earn > 0;
  React.useEffect(() => {
    const api = ref.current;
    if (!api) return;
    frame(api.chart, state.current.sym, state.current.earn);
    requestAnimationFrame(() => requestAnimationFrame(measure));
  }, [framed, measure]);
  // turning a phone or a narrowing window changes how many bars fit
  React.useEffect(() => {
    const mq = window.matchMedia(PHONE);
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [apply]);
  // The pane changes width when a widget docks or leaves; re-fit once it settles.
  React.useEffect(() => {
    const id = window.setTimeout(() => { const api = ref.current; if (api) frame(api.chart, state.current.sym, state.current.earn); }, 760);
    return () => clearTimeout(id);
  }, [dock]);
  return (
    <>
    <LightweightChart
      height="100%"
      options={{
        layout: { background: { type: ColorType.Solid, color: C.chartBg }, textColor: C.axis, fontFamily: "Inter, sans-serif", fontSize: 11, attributionLogo: false },
        grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
        rightPriceScale: { borderColor: C.border, scaleMargins: { top: 0.18, bottom: 0.22 } },
        timeScale: { borderColor: C.border, rightOffset: 3 },
        crosshair: { mode: CrosshairMode.Hidden },
        handleScroll: false, handleScale: false,
      }}
      onReady={(chart) => {
        const k = chart.addSeries(CandlestickSeries, {
          upColor: C.up, downColor: C.dn, wickUpColor: C.up, wickDownColor: C.dn, borderVisible: false,
          priceLineColor: C.dn, priceLineStyle: LineStyle.Dotted,
        });
        const v = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
        chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
        const e = chart.addSeries(LineSeries, { color: "#2962ff", lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
        ref.current = { chart, k, v, e, lines: [] };
        apply();
        chart.timeScale().subscribeVisibleLogicalRangeChange(measure);
        chart.timeScale().subscribeSizeChange(measure);
        return () => {
          chart.timeScale().unsubscribeVisibleLogicalRangeChange(measure);
          chart.timeScale().unsubscribeSizeChange(measure);
          ref.current = null;
        };
      }}
    />
    {geo ? <EarnOverlay geo={geo} step={earn} /> : null}
    </>
  );
}

/** Two results on the chart and the move between them, drawn step by step. */
function EarnOverlay({ geo, step }: { geo: EarnGeo; step: number }) {
  const { xa, xb, ya, yb, bottom, right } = geo;
  const top = 58, up = EARN.pct >= 0;
  const mid = (xa + xb) / 2, chipY = Math.max(top + 46, Math.min(ya, yb) - 46);
  return (
    <div className={`te te-s${step} ${up ? "up" : "dn"}`} aria-hidden="true" style={{ ["--b" as string]: `${bottom}px` }}>
      <div className="te-band" style={{ left: xa, width: xb - xa, top }} />
      <i className="te-rule a" style={{ left: xa, top }} />
      <i className="te-rule b" style={{ left: xb, top }} />
      <span className="te-tag a" style={{ left: xa, top: top - 2 }}><b>{EARN.a.q} results</b>{EARN.a.d}</span>
      <span className="te-tag b" style={{ left: Math.min(xb, right - 4), top: top - 2 }}><b>{EARN.b.q} results</b>{EARN.b.d}</span>
      <svg className="te-link" width="100%" height="100%">
        <line x1={xa} y1={ya} x2={xb} y2={yb} pathLength={1} />
      </svg>
      <i className="te-dot a" style={{ left: xa, top: ya }} />
      <i className="te-dot b" style={{ left: xb, top: yb }} />
      <span className="te-chip" style={{ left: mid, top: chipY }}>
        {up ? "+" : "−"}{Math.abs(EARN.pct).toFixed(2)}%
      </span>
    </div>
  );
}

/* ── watchlist ───────────────────────────────────────────────────────────── */

function WatchPane({ sym }: { sym: Sym }) {
  return (
    <div className="tw-pane tw-watch">
      <PaneBar><span className="tw-tab on"><Star />Watchlist</span></PaneBar>
      <div className="tw-wl-head"><span>Symbol</span><span>Last</span><span>Chg%</span></div>
      <ul className="tw-wl">
        {WATCHLIST.map((s) => {
          const w = s === "NIFTY" ? NIFTY_WATCH.NIFTY : WATCH[s]!, p = ((w.last - w.prev) / w.prev) * 100;
          return (
            <li key={s} data-t={`row-${s}`} className={s === sym ? "on" : undefined}>
              <span className="tw-wl-s"><Logo s={s} size={24} />{s}</span>
              <span>{formatINR(w.last)}</span>
              <span className={p >= 0 ? "up" : "dn"}>{p >= 0 ? "+" : ""}{p.toFixed(2)}%</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ── calendar: the desk's other docked widget ────────────────────────────── */

const MONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const KIND: Record<string, string> = { macro: "Economy", results: "Results", action: "Corporate action", board: "Board meeting" };

function CalPane() {
  return (
    <div className="tw-pane tw-cal">
      <PaneBar><span className="tw-tab on"><CalendarDays />Calendar</span></PaneBar>
      <ul className="tw-cl">
        {CALENDAR.map((e, i) => {
          const [, m, d] = e.date.split("-");
          return (
            <li key={i}>
              <span className="tw-cl-d"><b>{Number(d)}</b>{MONS[Number(m) - 1]}</span>
              {e.symbol ? <Logo s={e.symbol} size={24} /> : null}
              <span className="tw-cl-t"><b>{e.symbol ?? e.title}</b><small>{KIND[e.kind] ?? e.kind}{e.time ? ` · ${e.time} IST` : ""}</small></span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ── chat ────────────────────────────────────────────────────────────────── */

function ChatCol({ sym, draft, phase }: { sym: Sym; draft: string; phase: number }) {
  return (
    <div className="tw-chat">
      <div className="tw-seg"><b>Research</b><i>Execution</i><span className="tw-grow" /><span className="tw-ico sm"><Plus /></span><span className="tw-ico sm"><Clock /></span></div>
      {phase === 0 ? (
        <div className="tw-empty">
          <p>Start asking with</p>
          <ul>
            {TILES.map(([name, body]) => (
              <li key={name}>
                <svg viewBox="0 0 40 40" aria-hidden="true" dangerouslySetInnerHTML={{ __html: body }} />
                <span>{name}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="tw-thread">
          <div className="tw-user">{ASK}</div>
          {phase === 2 && <div className="tw-wait"><span className="px-bars" aria-hidden="true"><i /><i /><i /></span>Measuring levels</div>}
          {phase >= 3 && (
            <div className="tw-reply">
              <span className="tw-thought">Measured from daily candles</span>
              <p>
                NIFTY's prior 20 sessions reached a high of <b>{formatINR(LEVELS[0].price)}</b> and a low of <b>{formatINR(LEVELS[1].price)}</b>.
                Both lines are drawn from the recorded daily candles.
              </p>
              <div className="tw-meta"><Copy /><span>Daily bars</span><span>·</span><span>2 on chart</span><ChevronDown /></div>
              <ul className="tw-follow">
                <li>How often has this low been revisited?</li>
                <li>Does the weekly chart show the same range?</li>
              </ul>
              <ul className="tw-next">
                {NEXT.map(([name, q]) => {
                  const body = TILES.find(([n]) => n === name)![1];
                  return (
                    <li key={name}>
                      <svg viewBox="0 0 40 40" aria-hidden="true" dangerouslySetInnerHTML={{ __html: body }} />
                      <span><b>{name}</b>{q}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}
      <div className="tw-comp" data-t="composer">
        <span className={`tw-ph${draft ? " typed" : ""}`}>{draft || "Ask about this chart…"}</span>
        <div className="tw-comp-r">
          <span className="tw-chip"><Logo s={sym} size={16} />{sym}</span>
          <span className="tw-model"><Sparkles />Pivot<ChevronDown /></span>
          <span className="tw-grow" />
          <Mic />
          <span className="tw-send" data-t="send"><ArrowUp /></span>
        </div>
      </div>
    </div>
  );
}

/* ── the widget hub ──────────────────────────────────────────────────────── */

function Hub({ open, find }: { open: boolean; find: string }) {
  const cards = HUB_CARDS.filter(([n]) => n.toLowerCase().includes(find.toLowerCase())).slice(0, 4);
  return (
    <div className={`tw-hub${open ? " open" : ""}`} aria-hidden="true">
      <div className={`tw-hub-find${find ? " typed" : ""}`}><Search />{find || "Search widgets"}</div>
      <div className="tw-hub-tabs">{SHELVES.map((s, i) => <span key={s} className={i === 0 ? "on" : undefined}>{s}</span>)}</div>
      <div className="tw-hub-cards">
        {cards.map(([name, I]) => (
          <div key={name} className="tw-hub-card" data-t={`hub-${name}`}>
            <div className="tw-hub-pic"><i /><i /><i /></div>
            <span><I />{name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
