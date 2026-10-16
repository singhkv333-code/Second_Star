/**
 * Workspace — "Set up your view of the market."
 *
 * Four cards fitted together, in the manner of a product bento: a small
 * kicker, one claim, one line, and a drawn object that moves.
 *
 *   Layouts         the desk in outline, its panes re-flowing between three
 *                   saved arrangements.
 *   Research        one question fanning out to the kinds of reads the chat
 *                   makes (the names on its "start asking with" grid) and
 *                   landing back on the chart; a pulse runs down the live
 *                   wire, after Magic UI's AnimatedBeam.
 *   Coverage        narrow coloured bars, one per market Pivot covers; the
 *                   active one opens to say what is in it.
 *   Shared setups   a read-only desk, "Make it mine", and the copy that keeps
 *                   its credit.
 *
 * The motion runs only while a card is on screen and holds still under
 * reduced motion.
 */
"use client";

import * as React from "react";
import {
  ArrowUp, ArrowUpRight, ChartCandlestick, Check, ChevronDown, Filter, GripVertical, Landmark, MessageSquare,
  MousePointer2, NotebookPen, Plus, Share2, Star, X,
} from "lucide-react";
import { Logo } from "./logos";
import { formatINR } from "@/components/landing/film/film-script";
import { SCREEN } from "./snapshot";
import { NIFTY_1D, NIFTY_AS_OF, NIFTY_CHANGE_PCT, NIFTY_LAST, NIFTY_LEVELS, NIFTY_RANGE, NIFTY_RSI14, NIFTY_WATCH } from "./nifty-data";
import { RELIANCE_COMPANY } from "./reliance-financials";

function useCycle(n: number, ms: number) {
  const ref = React.useRef<HTMLElement | null>(null);
  const [k, setK] = React.useState(0);
  const [live, setLive] = React.useState(false);
  React.useEffect(() => {
    const el = ref.current;
    if (!el || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const io = new IntersectionObserver(([e]) => setLive(!!e?.isIntersecting), { threshold: 0.3 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  React.useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => setK((v) => (v + 1) % n), ms);
    return () => clearInterval(id);
  }, [live, n, ms]);
  return { ref, k, setK, live };
}

export function Workspace(): React.ReactElement {
  return (
    <section id="workspace" className="v3 ws">
      <div className="v3-shell">
        <header className="v3-head" data-reveal>
          <h2>Set up your view of the market.</h2>
          <p>Arrange it, ask it, share it.</p>
        </header>
        <div className="ws-grid">
          <LayoutsCard />
          <ResearchCard />
          <CoverageCard />
          <SharedCard />
        </div>
      </div>
    </section>
  );
}

function Copy({ kick, title, line }: { kick: string; title: React.ReactNode; line: string }) {
  return (
    <div className="ws-copy">
      <p className="kick">{kick}</p>
      <h3>{title}</h3>
      <p>{line}</p>
    </div>
  );
}

/* ── Layouts ─────────────────────────────────────────────────────────────── */

type Rect = [number, number, number, number]; // left, top, width, height in %
const PANES = ["Chart", "Watchlist", "Financials", "Screener", "Notes", "Chat"] as const;
type Pane = (typeof PANES)[number];
const LAYOUTS: { name: string; at: Partial<Record<Pane, Rect>> }[] = [
  { name: "Trading", at: { Chart: [0, 0, 47, 100], Watchlist: [47.6, 0, 25.6, 61], Notes: [47.6, 61.8, 25.6, 38.2], Chat: [73.8, 0, 26.2, 100] } },
  { name: "Research", at: { Chart: [0, 0, 49.6, 100], Financials: [50.2, 0, 25, 61], Notes: [50.2, 61.8, 25, 38.2], Chat: [75.8, 0, 24.2, 100] } },
  { name: "Review", at: { Chart: [0, 0, 58.8, 57], Watchlist: [0, 57.8, 58.8, 42.2], Screener: [59.4, 0, 40.6, 55], Financials: [59.4, 55.8, 40.6, 44.2] } },
];
const PANE_ICON: Record<Pane, React.ComponentType> = {
  Chart: ChartCandlestick, Watchlist: Star, Financials: Landmark, Screener: Filter, Notes: NotebookPen, Chat: MessageSquare,
};

/** The last bars of NIFTY's daily series, drawn small. */
function MiniCandles({ n = 40, levels = false }: { n?: number; levels?: boolean }) {
  const bars = NIFTY_1D.slice(-n);
  const lv = levels ? NIFTY_LEVELS.map((x) => x.price) : [];

  const lo = Math.min(...bars.map((b) => b[3])), hi = Math.max(...bars.map((b) => b[2]), ...lv);
  const y = (p: number) => 4 + ((hi - p) / (hi - lo)) * 52;
  const w = 156 / n;
  return (
    <svg className="ws-mc" viewBox="0 0 160 60" preserveAspectRatio="none" aria-hidden="true">
      {lv.map((p) => <line key={p} className="lv" x1={0} x2={160} y1={y(p)} y2={y(p)} />)}
      {bars.map(([, o, h, l, c], i) => {
        const x = 2 + i * w, top = y(Math.max(o, c));
        return (
          <g key={i} className={c >= o ? "up" : "dn"}>
            <line x1={x + w * 0.3} x2={x + w * 0.3} y1={y(h)} y2={y(l)} />
            <rect x={x} width={w * 0.6} y={top} height={Math.max(0.6, y(Math.min(o, c)) - top)} />
          </g>
        );
      })}
    </svg>
  );
}

/** The chart pane, small but whole: readout, candles, a price axis. */
function MiniChart() {
  const bars = NIFTY_1D.slice(-60);

  const lo = Math.min(...bars.map((b) => b[3])), hi = Math.max(...bars.map((b) => b[2]));
  const ticks = [0, 1, 2, 3].map((i) => hi - ((hi - lo) * (i + 0.5)) / 4);
  return (
    <div className="ws-chartbody">
      <div className="ws-ro"><Logo s="NIFTY" size={16} /><b>NIFTY</b><span>· 1D · NSE · Yahoo EOD</span><em className={NIFTY_CHANGE_PCT >= 0 ? "up" : "dn"}>{formatINR(NIFTY_LAST[4])} {NIFTY_CHANGE_PCT.toFixed(2)}%</em></div>
      <div className="ws-plot"><MiniCandles n={60} /></div>

      <div className="ws-axis">{ticks.map((t) => <span key={t}>{Math.round(t).toLocaleString("en-IN")}</span>)}</div>
    </div>
  );
}

const ROWS = ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC"] as const;

function PaneBody({ p }: { p: Pane }) {
  if (p === "Chart") return <MiniChart />;
  if (p === "Watchlist") {
    return (
      <ul className="ws-rows">
        <li className="h"><span>Yahoo EOD · {NIFTY_AS_OF}</span></li>
        {ROWS.map((s) => {
          const w = NIFTY_WATCH[s], c = ((w.last - w.prev) / w.prev) * 100;
          return <li key={s}><Logo s={s} size={16} /><b>{s}</b><span>{formatINR(w.last)}</span><em className={c >= 0 ? "up" : "dn"}>{c >= 0 ? "+" : ""}{c.toFixed(2)}%</em></li>;
        })}
      </ul>
    );
  }
  if (p === "Screener") {
    return (
      <ul className="ws-rows">
        <li className="h"><span>Most traded · 20-day turnover</span></li>
        {SCREEN.rows.slice(0, 5).map((r) => <li key={r.symbol}><Logo s={r.symbol} size={16} /><b>{r.symbol}</b><span>{formatINR(r.close)}</span><em>₹{Math.round(r.turnover ?? 0).toLocaleString("en-IN")} Cr</em></li>)}
      </ul>
    );
  }
  if (p === "Financials") {
    const c = RELIANCE_COMPANY, r = c.range_52w, at = ((c.price.last - r.low) / (r.high - r.low)) * 100;
    const rows: [string, string][] = [
      ["Market cap", `₹${(c.market_cap / 1e12).toFixed(2)} L Cr`], ["P/E", c.pe.toFixed(1)], ["P/B", c.pb.toFixed(2)],
      ["EV / EBITDA", c.ev_ebitda.toFixed(1)], ["EPS", c.eps.toFixed(2)], ["RSI 14", String(c.metrics.rsi14)],
    ];
    return (
      <div className="ws-fin">
        <div className="ws-fin-n"><Logo s="RELIANCE" size={16} /><b>{c.long_name} · {c.price.as_of}</b></div>
        <div className="ws-fin-r"><span><i style={{ left: `${at}%` }} /></span><small><b>{formatINR(r.low)}</b>52-week<b>{formatINR(r.high)}</b></small></div>
        <dl>{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      </div>
    );
  }
  if (p === "Notes") {
    return (
      <div className="ws-note">
        <span>{formatINR(NIFTY_LAST[4])} · {NIFTY_AS_OF}</span>
        <p>Prior 20-session high {formatINR(NIFTY_LEVELS[0].price)}; low {formatINR(NIFTY_LEVELS[1].price)}.</p>
        <span>RSI 14 · {NIFTY_RSI14}</span>
        <p>{Math.abs((NIFTY_LAST[4] / NIFTY_RANGE.high - 1) * 100).toFixed(1)}% below the available 2026 high.</p>
      </div>
    );
  }
  return (
    <div className="ws-chat">
      <div className="ws-seg"><b>Research</b><i>Execution</i></div>
      <p className="u">Mark the 20-session high and low on the daily chart</p>
      <p className="t">Measured from daily candles</p>
      <p>On the <b>NIFTY daily chart</b>, the prior 20-session high is <b>{formatINR(NIFTY_LEVELS[0].price)}</b> and the low is <b>{formatINR(NIFTY_LEVELS[1].price)}</b>.</p>
      <p className="t">2 on chart</p>
      <p className="f">How often has the low been revisited?</p>
      <p className="f">Does the weekly chart show the same range?</p>
      <span className="ws-compose">Ask about this chart…<i><ArrowUp /></i></span>
    </div>
  );
}

function LayoutsCard() {
  const { ref, k, setK } = useCycle(LAYOUTS.length, 3200);
  const lay = LAYOUTS[k]!;
  return (
    <article className="ws-card ws-ink ws-full ws-lay" ref={ref as React.Ref<HTMLElement>}>
      <div className="ws-copy">
        <p className="kick">Layouts</p>
        <h3>Dock it. Save it.</h3>
        <p>Arrange and save eighteen widgets.</p>
        <ul className="ws-laylist" role="tablist" aria-label="Saved layouts">
          {LAYOUTS.map((l, i) => (
            <li key={l.name}>
              <button type="button" role="tab" aria-selected={i === k} className={i === k ? "on" : undefined} onClick={() => setK(i)}>
                <b>{l.name}</b><span>{Object.keys(l.at).join(" · ")}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="ws-app" aria-hidden="true">
        <div className="ws-app-bar">
          <span className="ws-word">Pivot.</span><span className="ws-grow" />
          <span className="ws-b out"><Share2 />Share</span>
          <span className="ws-b" key={lay.name}><span className="ws-lname">{lay.name}</span><ChevronDown /></span>
          <span className="ws-b"><Plus />Widgets</span>
          <span className="ws-b out on"><MessageSquare />Chat</span>
        </div>
        <div className="ws-panes">
          {PANES.map((p) => {
            const r = lay.at[p];
            const I = PANE_ICON[p];
            const style = (r
              ? { left: `${r[0]}%`, top: `${r[1]}%`, width: `${r[2]}%`, height: `${r[3]}%` }
              : { left: "50%", top: "50%", width: "0%", height: "0%", opacity: 0 }) as React.CSSProperties;
            return (
              <div key={p} className={`ws-pane ${p.toLowerCase()}`} style={style}>
                <header><GripVertical className="g" /><I />{p}<span className="ws-grow" /><X className="x" /></header>
                <div className="ws-pbody"><PaneBody p={p} /></div>
              </div>
            );
          })}
        </div>
      </div>
    </article>
  );
}

/* ── Research ────────────────────────────────────────────────────────────── */

const READS = ["Levels", "Patterns", "Why it moved", "Earnings"];

function ResearchCard() {
  const { ref, k } = useCycle(READS.length, 1500);
  return (
    <article className="ws-card ws-salmon" ref={ref as React.Ref<HTMLElement>}>
      <Copy kick="Research mode" title="Ask all your questions." line="Answers drawn on the chart." />
      <div className="ws-flow" aria-hidden="true">
        <svg className="ws-wires" viewBox="0 0 400 220" preserveAspectRatio="none">
          {READS.map((_, i) => {
            const x = 50 + i * 100;
            const d = [`M200 24 C200 70 ${x} 64 ${x} 110`, `M${x} 110 C${x} 156 200 150 200 196`];
            return (
              <g key={i} className={i === k ? "on" : undefined}>
                {d.map((p, j) => <path key={`b${j}`} className="base" d={p} />)}
                {d.map((p, j) => <path key={`m${j}`} className={`beam b${j}`} d={p} pathLength={100} />)}
              </g>
            );
          })}
        </svg>
        <span className="ws-node q">Ask about this chart</span>
        <div className="ws-reads">
          {READS.map((r, i) => <span key={r} className={`ws-node${i === k ? " on" : ""}`}>{r}</span>)}
        </div>
        <span className="ws-node out">On the chart</span>
      </div>
    </article>
  );
}

/* ── Coverage ────────────────────────────────────────────────────────────── */

const MARKETS = [
  { name: "Equities", where: "NSE · BSE", eg: "TCS, HDFCBANK, ICICIBANK", marks: ["TCS", "HDFCBANK", "ICICIBANK"], tone: "g" },
  { name: "Indices", where: "NSE · BSE", eg: "NIFTY, BANKNIFTY, SENSEX", marks: ["NIFTY", "BANKNIFTY", "SENSEX"], tone: "b" },
  { name: "Options", where: "NSE F&O", eg: "Index and stock options", marks: ["NIFTY", "BANKNIFTY"], tone: "v" },
  { name: "Commodities", where: "MCX", eg: "Gold, silver, crude oil", marks: ["GOLD", "SILVER", "CRUDEOIL"], tone: "a" },
  { name: "Crypto", where: "On the chart", eg: "BTC, ETH", marks: ["BTC", "ETH"], tone: "r" },
];

function CoverageCard() {
  const { ref, k, setK } = useCycle(MARKETS.length, 2400);
  return (
    <article className="ws-card ws-fog" ref={ref as React.Ref<HTMLElement>}>
      <Copy kick="Coverage" title="Markets in one view." line="One chart and chat for each market." />
      <div className="ws-bars">
        {MARKETS.map((m, i) => (
          <button
            key={m.name} type="button" className={`ws-bar ${m.tone}${i === k ? " on" : ""}`}
            onMouseEnter={() => setK(i)} onFocus={() => setK(i)} onClick={() => setK(i)} aria-pressed={i === k}
          >
            <span className="ws-bar-in">
              <span className="ws-marks">{m.marks.map((x) => <Logo key={x} s={x} size={32} />)}</span>
              <b>{m.name}</b>
              <small>{m.where}</small>
              <em>{m.eg}</em>
            </span>
          </button>
        ))}
      </div>
    </article>
  );
}

/* ── Shared setups ───────────────────────────────────────────────────────── */

function SharedCard() {
  return (
    <article className="ws-card ws-bone">
      <Copy kick="Shared setups" title="Share your setup." line="A link to copy, with your credit." />
      <div className="ws-share" aria-hidden="true">
        <div className="ws-win src">
          <div className="ws-win-bar"><span className="ws-lights"><i /><i /><i /></span><span className="ws-wt"><Logo s="NIFTY" size={18} />NIFTY · 1D</span><span className="ws-make">Make it mine</span></div>
          <div className="ws-win-body"><MiniCandles n={60} levels /></div>

          <MousePointer2 className="ws-cursor" />
        </div>
        <div className="ws-win dst">
          <div className="ws-win-bar"><span className="ws-lights"><i /><i /><i /></span><span className="ws-wt">My layouts</span><span className="ws-saved"><Check />Saved</span></div>
          <div className="ws-win-body"><MiniCandles n={36} levels /></div>
          <div className="ws-win-foot">From a shared setup<ArrowUpRight /></div>
        </div>
      </div>
    </article>
  );
}
