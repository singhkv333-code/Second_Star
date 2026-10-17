/**
 * Gallery — "Everything in one place."
 *
 * Nine of the widgets a trader docks beside the chart, each in a tile — an
 * icon, a name, one line, and the widget itself in Pivot's light theme — on
 * one horizontal belt that drifts by itself, holds still under the pointer,
 * and can be dragged, scrolled or stepped with the arrows. Every
 * NIFTY prices use the saved Yahoo EOD snapshot; the strategy illustration
 * is computed from those bars. The Reliance financials, screener and
 * calendar retain their separately dated saved snapshots.
 */
"use client";

import * as React from "react";
import {
  ArrowLeft, ArrowRight, Bell, CalendarDays, ChartCandlestick, Filter, Landmark, NotebookPen, Sheet, Sigma, Star,
} from "lucide-react";
import { formatINR } from "@/components/landing/film/film-script";
import { Logo } from "./logos";
import {
  CALENDAR, CALENDAR_FROM, SCREEN,
} from "./snapshot";
import { NIFTY_1D, NIFTY_AS_OF, NIFTY_CHANGE_PCT, NIFTY_LAST, NIFTY_LAB, NIFTY_LEVELS, NIFTY_RANGE, NIFTY_RSI14, NIFTY_WATCH } from "./nifty-data";
import { RELIANCE_COMPANY } from "./reliance-financials";

const pct = (n: number, d = 2) => `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(d)}%`;
const chg = (last: number, prev: number) => ((last - prev) / prev) * 100;

type TileProps = { icon: React.ReactNode; title: string; line: string; children: React.ReactNode; fit?: "top" | "center" };

function Tile({ icon, title, line, children, fit = "center" }: TileProps) {
  return (
    <article className="gl-tile">
      <header>
        <h3><span className="gl-ico" aria-hidden="true">{icon}</span>{title}</h3>
        <p>{line}</p>
      </header>
      <div className={`gl-stage ${fit}`}>{children}</div>
    </article>
  );
}

/* The tiles twice over, so the belt can wrap by exactly one set — the
   Motion Primitives InfiniteSlider idea, driven by scrollLeft so the reader
   can take over. */
function Tiles() {
  return (
    <>
      <Tile icon={<ChartCandlestick />} title="Chart" line="Candles, indicators and drawings."><ChartCard /></Tile>
      <Tile icon={<Star />} title="Watchlist" line="Track all your names at one place." fit="top"><WatchCard /></Tile>
      <Tile icon={<Landmark />} title="Financials" line="Valuation, results and filings."><FinCard /></Tile>
      <Tile icon={<Filter />} title="Screener" line="Filter 3500+ stocks by market data." fit="top"><ScreenCard /></Tile>
      <Tile icon={<Sigma />} title="Strategy" line="Inspect a rule against holding."><StrategyCard /></Tile>
      <Tile icon={<CalendarDays />} title="Calendar" line="Earnings and corporate actions." fit="top"><CalendarCard /></Tile>
      <Tile icon={<Bell />} title="Alerts" line="Rules checked on every bar close."><AlertsCard /></Tile>
      <Tile icon={<NotebookPen />} title="Notes" line="Write beside the chart."><NotesCard /></Tile>
      <Tile icon={<Sheet />} title="Sheet" line="A sheet with live market functions."><SheetCard /></Tile>
    </>
  );
}

export function Gallery(): React.ReactElement {
  const loop = React.useRef<HTMLDivElement | null>(null);
  const held = React.useRef(false);

  React.useEffect(() => {
    const el = loop.current;
    if (!el) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0, last = 0, on = false;
    // The belt drifts by itself and wraps by exactly one set, so it never
    // ends; the reader's own scroll, drag or arrow simply moves the same
    // scrollLeft, and the drift picks up from wherever they leave it.
    // `pos` keeps the fraction the browser would round off scrollLeft.
    let pos = 0;
    const wrap = () => {
      const half = el.scrollWidth / 2;
      if (Math.abs(el.scrollLeft - pos) > 2) pos = el.scrollLeft; // the reader moved it
      if (pos >= half) pos -= half;
      else if (pos <= 0) pos += half;
      if (Math.abs(el.scrollLeft - pos) > 0.5) el.scrollLeft = pos;
    };
    const tick = (now: number) => {
      const dt = last ? Math.min(64, now - last) : 16;
      last = now;
      if (!held.current) { pos += dt * 0.045; el.scrollLeft = pos; wrap(); }
      if (on) raf = requestAnimationFrame(tick);
    };
    const io = new IntersectionObserver(([e]) => {
      const vis = !!e?.isIntersecting;
      if (vis && !on && !still) { on = true; last = 0; raf = requestAnimationFrame(tick); }
      if (!vis) { on = false; cancelAnimationFrame(raf); }
    });
    io.observe(el);
    el.scrollLeft = pos = 1;
    el.addEventListener("scroll", wrap, { passive: true });

    // drag with a mouse; touch and trackpads scroll natively
    let x0 = 0, l0 = 0, drag = false;
    const down = (e: PointerEvent) => { if (e.pointerType !== "mouse") return; drag = true; x0 = e.clientX; l0 = el.scrollLeft; el.classList.add("drag"); };
    const move = (e: PointerEvent) => { if (drag) el.scrollLeft = l0 - (e.clientX - x0); };
    const up = () => { drag = false; el.classList.remove("drag"); };
    el.addEventListener("pointerdown", down);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      io.disconnect(); cancelAnimationFrame(raf); el.removeEventListener("scroll", wrap);
      el.removeEventListener("pointerdown", down); window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
    };
  }, []);

  const step = (dir: 1 | -1) => {
    const el = loop.current, tile = el?.querySelector<HTMLElement>(".gl-tile");
    if (el && tile) el.scrollBy({ left: dir * (tile.offsetWidth + 20), behavior: "smooth" });
  };
  const hold = (v: boolean) => () => { held.current = v; };

  return (
    <section id="desk" className="v3 gl">
      <div className="v3-shell gl-top">
        <header className="v3-head" data-reveal>
          <h2>Everything in one place.</h2>
          <p>Explore the widgets beside your chart.</p>
        </header>
        <div className="gl-nav">
          <button type="button" aria-label="Previous widget" onClick={() => step(-1)}><ArrowLeft /></button>
          <button type="button" aria-label="Next widget" onClick={() => step(1)}><ArrowRight /></button>
        </div>
      </div>
      <div
        className="gl-loop" ref={loop}
        onMouseEnter={hold(true)} onMouseLeave={hold(false)} onTouchStart={hold(true)} onTouchEnd={hold(false)}
        onFocus={hold(true)} onBlur={hold(false)}
      >
        <div className="gl-set"><Tiles /></div>
        <div className="gl-set" aria-hidden="true" inert><Tiles /></div>
      </div>
    </section>
  );
}

/* ── the widget card shell ───────────────────────────────────────────────── */

function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={`gl-card ${className ?? ""}`}>{children}</div>;
}

/** The instrument's own mark, as the app shows it; a monogram when it has none. */
function Mark({ s }: { s: string; tone?: string }) {
  return <Logo s={s} size={34} className="gl-logo" />;
}

function Delta({ v, d = 2 }: { v: number; d?: number }) {
  return <span className={`gl-delta ${v >= 0 ? "up" : "dn"}`}>{pct(v, d)}</span>;
}

/* ── Chart ───────────────────────────────────────────────────────────────── */

function ChartCard() {
  const bars = NIFTY_1D.slice(-56);

  const lo = Math.min(...bars.map((b) => b[3])), hi = Math.max(...bars.map((b) => b[2]));
  const W = 300, H = 210, step = W / bars.length;
  const y = (p: number) => 4 + ((hi - p) / (hi - lo)) * (H - 8);
  const last = NIFTY_LAST[4], ch = NIFTY_CHANGE_PCT;
  return (
    <Card>
      <div className="gl-row-head"><Mark s="NIFTY" /><b>NIFTY 50</b><span className="gl-sub">NSE</span></div>
      <div className="gl-well">
        <div className="gl-quote">
          <strong>{formatINR(last)}</strong><Delta v={ch} />
          <span className="gl-tabs"><b>1D</b><i>1W</i><i>1M</i><i>1Y</i></span>
        </div>
        <svg className="gl-candles" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-label="NIFTY daily candles">
          {bars.map(([, o, h, l, c], i) => {
            const x = step * i + step / 2, up = c >= o, top = y(Math.max(o, c));
            return (
              <g key={i} className={up ? "up" : "dn"}>
                <line x1={x} x2={x} y1={y(h)} y2={y(l)} />
                <rect x={x - step * 0.32} width={step * 0.64} y={top} height={Math.max(0.8, y(Math.min(o, c)) - top)} />
              </g>
            );
          })}
        </svg>
        <span className="gl-stamp">{NIFTY_AS_OF}, close · Yahoo fallback</span>
      </div>
    </Card>
  );
}

/* ── Watchlist ───────────────────────────────────────────────────────────── */

const WATCH_ROWS = ["RELIANCE", "HDFCBANK", "TCS", "ICICIBANK", "BHARTIARTL", "INFY", "SBIN"] as const;
const TONES = ["amber", "blue", "violet", "rose", "red", "teal", "indigo"];

function WatchCard() {
  return (
    <Card>
      <div className="gl-row-head"><b>Watchlist</b></div>
      <div className="gl-well">
        <ul className="gl-list watch">
          {WATCH_ROWS.map((s, k) => {
            const w = NIFTY_WATCH[s];
            const c = chg(w.last, w.prev);
            return (
              <li key={s}>
                <Mark s={s} tone={TONES[k]} />
                <span className="gl-sym">{s}</span>
                <span className="gl-num">{formatINR(w.last)}</span>
                <Delta v={c} />
              </li>
            );
          })}
        </ul>
      </div>
      <p className="gl-foot">Yahoo EOD · {NIFTY_AS_OF}</p>
    </Card>
  );
}

/* ── Financials ──────────────────────────────────────────────────────────── */

function FinCard() {
  const c = RELIANCE_COMPANY;
  const r = c.range_52w;
  const at = ((c.price.last - r.low) / (r.high - r.low)) * 100;
  const cap = `₹${(c.market_cap / 1e12).toFixed(2)} L Cr`;
  const cells: [string, string, string?][] = [
    ["Market cap", cap], ["P/E", c.pe.toFixed(1), `consolidated, ${c.eps_period}`],
    ["P/B", c.pb.toFixed(2)], ["EV / EBITDA", c.ev_ebitda.toFixed(1)],
    ["EPS", c.eps.toFixed(2), c.eps_period], ["Book value / share", c.book_value_ps.toFixed(2)],
  ];
  return (
    <Card>
      <div className="gl-fin-name"><b>{c.long_name}</b><span>{c.sector} · {c.industry}</span></div>
      <div className="gl-well">
        <div className="gl-quote flat"><strong>{formatINR(c.price.last)}</strong><Delta v={c.price.change_pct} /></div>
        <div className="gl-range">
          <span className="gl-range-bar"><i style={{ left: `${at}%` }} /></span>
          <span className="gl-range-ends"><b>{formatINR(r.low)}</b><em>52-week range</em><b>{formatINR(r.high)}</b></span>
        </div>
        <dl className="gl-metrics">
          {cells.map(([k, v, n]) => <div key={k}><dt>{k}</dt><dd>{v}</dd>{n && <small>{n}</small>}</div>)}
        </dl>
        <span className="gl-stamp">Reliance snapshot · {c.price.as_of}</span>
      </div>
    </Card>
  );
}

/* ── Screener ────────────────────────────────────────────────────────────── */

function ScreenCard() {
  return (
    <Card>
      <div className="gl-row-head"><b>Most traded</b><span className="gl-sub">20-day turnover</span></div>
      <div className="gl-well">
        <ul className="gl-list">
          {SCREEN.rows.slice(0, 5).map((r, k) => (
            <li key={r.symbol}>
              <Mark s={r.symbol} tone={TONES[(k + 2) % TONES.length]} />
              <span className="gl-sym">{r.symbol}<small>{r.name}</small></span>
              <span className="gl-num">{formatINR(r.close)}</span>
              <span className="gl-turn">₹{Math.round(r.turnover ?? 0).toLocaleString("en-US")} Cr</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="gl-foot">5 of {SCREEN.universe} · EOD {SCREEN.as_of}</p>
    </Card>
  );
}

/* ── Strategy ────────────────────────────────────────────────────────────── */

function StrategyCard() {
  const eq = NIFTY_LAB.equity;
  const lo = Math.min(...eq), hi = Math.max(...eq), W = 300, H = 120;
  const y = (v: number) => 4 + ((hi - v) / (hi - lo)) * (H - 8);
  const d = eq.map((v, i) => `${i ? "L" : "M"}${((i / (eq.length - 1)) * W).toFixed(1)},${y(v).toFixed(1)}`).join("");
  return (
    <Card>
      <div className="gl-row-head"><b>Moving-average cross</b></div>
      <p className="gl-rule">EMA(20) crosses above EMA(50) · NIFTY · 1D · 2026</p>
      <div className="gl-well tight">
        <svg className="gl-eq" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-label="Equity curve">
          <line x1={0} x2={W} y1={y(100)} y2={y(100)} className="base" />
          <path d={d} />
        </svg>
      </div>
      <dl className="gl-stats">
        <div><dt>Strategy</dt><dd>{pct(NIFTY_LAB.ret, 1)}</dd></div>
        <div><dt>Buy & hold</dt><dd>{pct(NIFTY_LAB.buy_hold, 1)}</dd></div>
        <div><dt>Entries</dt><dd>{NIFTY_LAB.trades}</dd></div>
        <div><dt>Max DD</dt><dd>{NIFTY_LAB.max_dd.toFixed(1)}%</dd></div>
      </dl>
      <div className="gl-verdict"><span>Status</span><b>{NIFTY_LAB.verdict}</b></div>
    </Card>
  );
}

/* ── Calendar ────────────────────────────────────────────────────────────── */

const KIND: Record<string, [string, string]> = {
  macro: ["Economy", "amber"], results: ["Results", "up"], action: ["Corporate action", "blue"], board: ["Board meeting", "blue"],
};
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function CalendarCard() {
  return (
    <Card>
      <div className="gl-row-head"><b>This week</b><span className="gl-sub">From {CALENDAR_FROM}</span></div>
      <div className="gl-well">
        <ul className="gl-cal">
          {CALENDAR.map((e, k) => {
            const [, m, dd] = e.date.split("-");
            const [label, tone] = KIND[e.kind] ?? ["Event", "blue"];
            return (
              <li key={k}>
                <span className="gl-day"><b>{Number(dd)}</b><small>{MON[Number(m) - 1]}</small></span>
                <span className="gl-ev">{e.symbol ?? e.title}<small>{e.symbol ? (e.period ?? e.title) : e.time ? `${e.time} IST` : ""}</small></span>
                <span className={`gl-kind ${tone}`}><i />{label}</span>
              </li>
            );
          })}
        </ul>
      </div>
    </Card>
  );
}

/* ── Alerts ──────────────────────────────────────────────────────────────── */

function AlertsCard() {
  const rows = [
    { sym: "NIFTY", rule: "Close above prior 20D high", lvl: formatINR(NIFTY_LEVELS[0].price), state: "Example" },
    { sym: "NIFTY", rule: "Close below prior 20D low", lvl: formatINR(NIFTY_LEVELS[1].price), state: "Example" },
  ];
  return (
    <Card>
      <div className="gl-row-head"><b>Alerts</b><span className="gl-sub">On bar close</span></div>
      <div className="gl-well">
        <ul className="gl-alerts">
          {rows.map((r) => (
            <li key={r.lvl}>
              <span className="gl-bell" aria-hidden="true"><Bell /></span>
              <span className="gl-ev">{r.sym}<small>{r.rule} <b>{r.lvl}</b></small></span>
              <span className="gl-armed"><i />{r.state}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="gl-foot">Notifies you. Never places an order.</p>
    </Card>
  );
}

/* ── Notes ───────────────────────────────────────────────────────────────── */

function NotesCard() {
  return (
    <Card>
      <div className="gl-row-head"><Mark s="NIFTY" /><b>NIFTY</b><span className="gl-sub">Notes</span></div>
      <div className="gl-note">
        <span className="gl-note-stamp">{formatINR(NIFTY_LAST[4])} · {NIFTY_AS_OF}</span>
        <p>The prior 20-session range runs from {formatINR(NIFTY_LEVELS[1].price)} to {formatINR(NIFTY_LEVELS[0].price)}.</p>
        <span className="gl-note-stamp soft">RSI 14 · {NIFTY_RSI14}</span>
        <p>{Math.abs((NIFTY_LAST[4] / NIFTY_RANGE.high - 1) * 100).toFixed(1)}% below the available 2026 high.</p>
      </div>
    </Card>
  );
}

/* ── Sheet ───────────────────────────────────────────────────────────────── */

function SheetCard() {
  const rows = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC"] as const;
  return (
    <Card className="flush">
      <div className="gl-fx"><b>fx</b><code>=PRICE(A2)</code><small className="gl-sub">Yahoo EOD · {NIFTY_AS_OF}</small></div>
      <table className="gl-sheet">
        <thead><tr><th /><th>A</th><th>B</th><th>C</th></tr></thead>
        <tbody>
          <tr><th>1</th><td className="h">Symbol</td><td className="h">Last</td><td className="h">Chg %</td></tr>
          {rows.map((s, i) => {
            const w = NIFTY_WATCH[s], c = chg(w.last, w.prev);
            return (
              <tr key={s}>
                <th>{i + 2}</th><td>{s}</td>
                <td className={`n${i === 0 ? " sel" : ""}`}>{formatINR(w.last)}</td>
                <td className={`n ${c >= 0 ? "up" : "dn"}`}>{pct(c)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
