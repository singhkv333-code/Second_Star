/**
 * Plays — "Turn ideas into plays that watch the market."
 *
 * The Plays tab, transcribed: the same markup and classes as
 * chart/js/playkit.js and playchat.js, in the app's light palette
 * (plays.css, scoped under `.ply`).
 *
 *   The window    plays itself: the app's prompt bar, the ask typed into
 *                 it, then the conversation that
 *                 builds the play: the steps, the reply, and the strategy
 *                 card (Signal → Trigger → Action on the ruled canvas, wired).
 *                 Three prompts, one full flow each, then the next. It runs
 *                 only while on screen and holds a built play under reduced
 *                 motion.
 *   The showcase  independent landing examples with explicit rule summaries.
 *
 * The animated window computes its market values from captured daily bars
 * (`snapshot-bars`). The showcase below it illustrates user-defined rules.
 */
"use client";

import * as React from "react";
import { MacWindow } from "./frame";
import { logoSrc } from "./logos";
import { Ic } from "./plays-icons";
import { DAILY } from "./snapshot-bars";
import "./plays.css";

/* ── the plays, computed ────────────────────────────────────────────────── */

type Bar = [number, number, number, number, number, number];
type Kind = "alert" | "buy" | "exit";

interface Spec {
  tab: string; ask: string; sym: string; title: string; lede: string;
  kind: Kind; op: "<" | ">"; levelName: string;
}
interface Play extends Spec { bars: Bar[]; close: number[]; level: number[]; fires: number[]; from: number }

const sma = (c: number[], n: number) => c.map((_, i) => i < n - 1 ? NaN : c.slice(i - n + 1, i + 1).reduce((a, v) => a + v, 0) / n);
/** The highest high (or lowest low) of the `n` sessions before each one. */
const prior = (b: Bar[], n: number, f: number, pick: (...v: number[]) => number) =>
  b.map((_, i) => i < n ? NaN : pick(...b.slice(i - n, i).map((x) => x[f]!)));

/** A rule on daily closes against a level: it fires on each session the
 *  close crosses to the rule's side of the level. */
function play(spec: Spec, levelOf: (b: Bar[], c: number[]) => number[]): Play {
  const bars = (DAILY[spec.sym] ?? []) as Bar[];
  const close = bars.map((b) => b[4]);
  const level = levelOf(bars, close);
  const from = level.findIndex((v) => !Number.isNaN(v)) + 1;
  const hit = (i: number) => spec.op === "<" ? close[i]! < level[i]! : close[i]! > level[i]!;
  const fires = close.flatMap((_, i) => i >= from && hit(i) && !hit(i - 1) ? [i] : []);
  return { ...spec, bars, close, level, fires, from };
}

const PLAYS: Play[] = [
  play({
    tab: "Alert", sym: "HDFCBANK", kind: "alert", op: "<", levelName: "50-day average",
    ask: "Tell me when HDFC Bank closes below its 50-day average",
    title: "HDFC Bank below its 50-day average",
    lede: "Tells you in the Inbox when HDFCBANK closes below its 50-day average on the daily chart.",
  }, (_, c) => sma(c, 50)),
  play({
    tab: "Swing buy", sym: "TCS", kind: "buy", op: ">", levelName: "20-day high",
    ask: "Buy ₹25,000 of TCS on a close above its 20-day high, stop 5%, target 12%",
    title: "TCS above its 20-day high",
    lede: "Buys ₹25,000 of TCS on paper when it closes above its 20-day high, with a 5% stop and a 12% target.",
  }, (b) => prior(b, 20, 2, Math.max)),
  play({
    tab: "Exit", sym: "RELIANCE", kind: "exit", op: "<", levelName: "20-day low",
    ask: "Exit my RELIANCE position if it closes below its 20-day low",
    title: "Exit RELIANCE on a 20-day low",
    lede: "Sells your RELIANCE position on paper when it closes below its 20-day low on the daily chart.",
  }, (b) => prior(b, 20, 3, Math.min)),
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** A bar's session in IST, written without Intl so server and client agree. */
const ist = (t: number) => new Date((t + 19800) * 1000);
const day = (t: number) => { const d = ist(t); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };
const dayY = (t: number) => `${day(t)} ${ist(t).getUTCFullYear()}`;
const num = (v: number) => v.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/** What every view of a play reads: the last close against its level. */
function facts(p: Play) {
  const L = p.close.length - 1, now = p.close[L] ?? 0, lvl = p.level[L] ?? 0;
  const met = p.op === "<" ? now < lvl : now > lvl;
  return { now, lvl, met, away: Math.abs(now - lvl) / now * 100, asof: p.bars[L]?.[0] ?? 0, span: L - p.from + 1 };
}

/* ── the app's helpers, as components ───────────────────────────────────── */

const HUES = ["#f0570f", "#2563eb", "#16a34a", "#7c4dff", "#d63d8f", "#0a86c4", "#b26b00", "#475569"];
const hue = (s: string) => HUES[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % HUES.length];

/** Kit.logo: the letter on the symbol's hue, the mark over it. */
function Logo({ s, cls = "" }: { s: string; cls?: string }) {
  const src = logoSrc(s);
  const letter = s.startsWith("NIFTY") ? "N" : s.slice(0, 1);
  return (
    <span className={`logo mono-mark${src ? " has-img" : ""} ${cls}`} style={{ background: hue(s) }}>
      {letter}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {src && <img className="in" src={src} alt="" />}
    </span>
  );
}

/** Kit.bigNum: the decimals a step quieter. */
function Big({ v }: { v: number }) {
  const [a, b] = num(v).split(".");
  return <div className="big">{a}<span className="dec">.{b}</span></div>;
}

/** The signal's sparkline: three months of closes, a dot on each fire. */
function Spark({ p }: { p: Play }) {
  const W = 260, H = 46, from = Math.max(0, p.close.length - 64);
  const pts = p.close.slice(from);
  const lo = Math.min(...pts), hi = Math.max(...pts), pad = (hi - lo) * 0.08;
  const x = (i: number) => (i / (pts.length - 1)) * (W - 4);
  const y = (v: number) => 6 + (1 - (v - lo + pad) / (hi - lo + 2 * pad)) * (H - 10);
  const d = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("");
  return (
    <div className="sig-chart chart">
      <svg viewBox={`0 0 ${W} ${H}`} aria-label={`${p.sym} closes, three months`} role="img">
        <path className="ln" d={d} />
        {p.fires.filter((i) => i >= from).map((i) => <circle key={i} className="mark" cx={x(i - from)} cy={y(p.close[i]!)} r="4" />)}
      </svg>
    </div>
  );
}

/** Kit.levelStrip, on daily closes: the level dashed, the gap shaded. */
function LevelStrip({ p }: { p: Play }) {
  const { lvl, now, met, away } = facts(p);
  const W = 300, H = 58, padR = 80, padT = 8, padB = 8;
  const vs = p.close.slice(-20), ls = p.level.slice(-20);
  let lo = Math.min(...vs, ...ls), hi = Math.max(...vs, ...ls);
  const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;
  const x = (i: number) => (i / (vs.length - 1)) * (W - padR);
  const y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
  const path = (a: number[]) => a.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("");
  const line = path(vs);
  const back = ls.map((v, i) => `L${x(i).toFixed(1)} ${y(v).toFixed(1)}`).reverse().join("");
  const ly = y(lvl), lastX = x(vs.length - 1), nowY = y(now);
  const tagY = Math.max(padT + 6, Math.min(H - padB - 2, (nowY + ly) / 2 + 3.5));
  return (
    <div className={`lvl-strip${met ? " met" : ""}`}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${p.sym} closes against its ${p.levelName}`}>
        <path className="lv-fill" d={`${line}${back}Z`} />
        <path className="lv-level" d={path(ls)} />
        <path className="lv-line" d={line} />
        <circle className="lv-dot" cx={lastX} cy={nowY} r="2.6" />
        {!met && <>
          <line className="lv-gap" x1={lastX + 10} x2={lastX + 10} y1={Math.min(nowY, ly)} y2={Math.max(nowY, ly)} />
          <line className="lv-gap" x1={lastX + 7} x2={lastX + 13} y1={nowY} y2={nowY} />
        </>}
        <text className="lv-tag" x={lastX + 18} y={tagY}>{met ? "Met" : `${away.toFixed(2)}% away`}</text>
      </svg>
      <div className="lvl-cap"><span>Last 20 daily closes</span><span>level {num(lvl)}</span></div>
    </div>
  );
}

/** Kit.wires: the curved links between the nodes, measured once laid out. */
function useWires(ref: React.RefObject<HTMLDivElement | null>, ready: boolean, hot: boolean) {
  const [d, setD] = React.useState("");
  React.useEffect(() => {
    const el = ref.current;
    if (!el || !ready) return setD("");
    const draw = () => {
      const svg = el.querySelector<SVGElement>("svg.wires");
      if (!svg || getComputedStyle(svg).display === "none") return setD("");
      const box = el.getBoundingClientRect();
      const at = (n: Element) => { const r = n.getBoundingClientRect(); return { l: r.left - box.left, r: r.right - box.left, m: (r.top + r.bottom) / 2 - box.top }; };
      const s = el.querySelector('[data-node="s0"]'), t = el.querySelector('[data-node="t"]'), a = el.querySelector('[data-node="a"]');
      if (!s || !t || !a) return;
      const path = (x1: number, y1: number, x2: number, y2: number) => {
        const mx = (x1 + x2) / 2, r = Math.min(10, Math.abs(y2 - y1) / 2), dir = y2 > y1 ? 1 : -1;
        const p = Math.abs(y2 - y1) < 2 ? `M${x1} ${y1} H${x2}` :
          `M${x1} ${y1} H${mx - r} Q${mx} ${y1} ${mx} ${y1 + dir * r} V${y2 - dir * r} Q${mx} ${y2} ${mx + r} ${y2} H${x2 - 1}`;
        return `<path pathLength="1" class="${hot ? "hot" : ""}" d="${p}"/><circle cx="${x1}" cy="${y1}" r="3"/><circle cx="${x2}" cy="${y2}" r="3"/>`;
      };
      const S = at(s), T = at(t), A = at(a);
      setD(path(S.r, S.m, T.l, T.m) + path(T.r, T.m, A.l, A.m));
    };
    const ro = new ResizeObserver(draw);
    ro.observe(el);
    draw();
    return () => ro.disconnect();
  }, [ref, ready, hot]);
  return d;
}

/* ── the window ─────────────────────────────────────────────────────────── */

/** The home: the app's composer alone, the ask typed into it. */
function Home({ ask, typed, sending }: { ask: string; typed: number; sending: boolean }) {
  return (
    <div className="py-home">
      <div className="composer">
        <div className="ta">{ask.slice(0, typed)}<i className="caret" /></div>
        <div className="bar"><span className="grow" /><span className="mic"><Ic n="mic" /></span>
          <span className={`send${typed > 0 ? " on" : ""}${sending ? " press" : ""}`}><Ic n="up" /></span></div>
      </div>
    </div>
  );
}

/** The action node, by what the play does when it fires. */
function Action({ p }: { p: Play }) {
  const head = <div className="nh"><Ic n="action" /><span>Action</span><span className="grow" /><span className="pill armed">Active</span></div>;
  if (p.kind === "alert") return (
    <>{head}
      <div className="act-title"><Ic n="bell" className="mark-ic" /><span>Alert you in the Inbox</span></div>
      <div className="inner">
        <div className="src"><Ic n="bell" /><span>Every daily close</span></div>
        <div className="note">Each alert says what it saw, with the value that decided it.</div>
      </div>
    </>
  );
  const buy = p.kind === "buy";
  return (
    <>{head}
      <div className="act-title"><span className={`chip ${buy ? "buy" : "sell"}`}>{buy ? "Buy" : "Sell"}</span>
        <Logo s={p.sym} cls="sm" /><span>{buy ? `₹25,000 of ${p.sym}` : `Your ${p.sym} position`}</span></div>
      <div className="inner">
        <div className="src"><Ic n="clock" /><span>Fills at the next open, on paper</span></div>
        <div className="note">{buy ? "Stop 5% below the fill, target 12% above it." : "Closes the whole position. Nothing else is touched."}</div>
      </div>
    </>
  );
}

/** The conversation that builds the play; `stage` reveals it a step at a time. */
function Thread({ p, stage, leaving }: { p: Play; stage: number; leaving: boolean }) {
  const cv = React.useRef<HTMLDivElement | null>(null);
  const { now, lvl, met, away, asof, span } = facts(p);
  const wires = useWires(cv, stage >= 8, met);
  const last = p.fires[p.fires.length - 1];
  const n = p.fires.length;
  const on = (s: number) => stage >= s ? " on" : "";
  const step = (i: number, icon: string, text: React.ReactNode) => (
    <li className={`rv${on(i)}`}>{stage === i ? <i className="spin" /> : <Ic n={icon} />}<span>{text}</span></li>
  );
  return (
    <div className={`py-main${leaving ? " out" : ""}`}>
      <div className="msg-u"><div>{p.ask}</div></div>
      <ul className="steps">
        {step(1, "chart", <>Reading {p.sym}&apos;s chart <Logo s={p.sym} cls="xs" /></>)}
        {step(2, "flow", "Building the triggers and trade rules")}
      </ul>
      <div className={`prose rv${on(3)}`}><p>Here is the play. {p.sym} closed at <strong>{num(now)}</strong> on {dayY(asof)}, {away.toFixed(2)}% {now < lvl ? "below" : "above"} its {p.levelName} of <strong>{num(lvl)}</strong>. Over the last {span} sessions this rule would have fired <strong>{n === 1 ? "once" : `${n} times`}</strong>.</p></div>
      <div className={`strat rv${on(4)}`}>
        <div className="strat-head">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="row" style={{ gap: 8, marginBottom: 3 }}>
              <h3>{p.title}</h3>
              <span className="pill armed"><i className="dot" />Active</span>
            </div>
          </div>
          <div className="logos"><Logo s={p.sym} cls="lg" /></div>
          <div className="lede-wrap"><div className="strat-lede"><p>{p.lede}</p></div></div>
        </div>
        <div className="canvas ruled" ref={cv}>
          <svg className="wires" dangerouslySetInnerHTML={{ __html: wires }} />
          <div className="col">
            <div className={`node signal rv${on(5)}`} data-node="s0">
              <div className="tt">{p.sym} price</div>
              <div className="inner">
                <div className="src"><Logo s={p.sym} cls="sm" /><span className="grow" />
                  <span className="pill wait"><Ic n="clock" />As of {day(asof)}</span></div>
                <Big v={now} />
                <Spark p={p} />
              </div>
            </div>
          </div>
          <div className="col">
            <div className={`node rv${on(6)}`} data-node="t">
              <div className="nh"><Ic n="trigger" /><span>Trigger</span><span className="grow" /><span className={`pill${met ? " met" : ""}`}>{met ? "Met" : "Not met"}</span></div>
              <div className="cond">
                <span>If <Logo s={p.sym} cls="xs" />{p.sym} close</span>
                <span className="chip sig"><Ic n="signal" />{num(now)}</span>
                <span className="chip op">{p.op}</span>
                <span>{p.levelName}</span>
                <span className="chip val"><Ic n="signal" />{num(lvl)}</span>
              </div>
              <LevelStrip p={p} />
            </div>
          </div>
          <div className="col">
            <div className={`node rv${on(7)}`} data-node="a"><Action p={p} /></div>
          </div>
        </div>
        <div className={`strat-foot rv${on(8)}`}>
          <div className="facts">
            <span>Every daily close</span><span>{p.kind === "alert" ? "Tells you" : p.kind === "buy" ? "Buys on paper" : "Sells on paper"}</span>
            <span>Would have fired <b>{n}×</b> in {span} days{last != null && <>, last on <b>{dayY(p.bars[last]![0])}</b></>}</span>
          </div>
          <span className="btn sm" aria-hidden="true"><Ic n="pause" />Pause</span>
          <span className="btn sm" aria-hidden="true">Open</span>
        </div>
      </div>
    </div>
  );
}

/* The flow's clock, in ms. Typing starts at TYPE_AT, a character every
   TYPE_MS; after the send the thread reveals stage s at REVEAL[s]; the
   built play holds for HOLD. */
const TYPE_AT = 900, TYPE_MS = 38, SEND_PAUSE = 650, PRESS_MS = 260;
const REVEAL = [0, 350, 1050, 1750, 2250, 2600, 2950, 3300, 3700];
const HOLD = 5200, OUT_MS = 380;
/** One flow, end to end. */
const flowMs = (ask: string) => TYPE_AT + ask.length * TYPE_MS + SEND_PAUSE + REVEAL[REVEAL.length - 1]! + HOLD;

/** Run the flows in turn while `live`: home, typing, send, the build, hold. */
function useFlows(live: boolean) {
  const [flow, setFlow] = React.useState(0);
  const [typed, setTyped] = React.useState(0);
  const [stage, setStage] = React.useState(-2);   // -2 home, -1 sending, 0..8 the thread
  const [leaving, setLeaving] = React.useState(false);
  const [still, setStill] = React.useState(false);
  React.useEffect(() => { setStill(window.matchMedia("(prefers-reduced-motion: reduce)").matches); }, []);
  React.useEffect(() => {
    setLeaving(false);
    setTyped(0);
    if (still) return setStage(8);
    setStage(-2);
    if (!live) return;
    const ask = PLAYS[flow]!.ask, ts: number[] = [];
    const at = (ms: number, f: () => void) => { ts.push(window.setTimeout(f, ms)); };
    for (let i = 1; i <= ask.length; i++) at(TYPE_AT + i * TYPE_MS, () => setTyped(i));
    const sent = TYPE_AT + ask.length * TYPE_MS + SEND_PAUSE;
    at(sent - PRESS_MS, () => setStage(-1));
    REVEAL.forEach((ms, s) => at(sent + ms, () => setStage(s)));
    const done = flowMs(ask);
    at(done, () => setLeaving(true));
    at(done + OUT_MS, () => setFlow((f) => (f + 1) % PLAYS.length));
    return () => ts.forEach(clearTimeout);
  }, [flow, live, still]);
  return { flow, typed, stage, leaving };
}

/** Landing-only examples: rule previews, rather than live market results. */
const SHOWCASE = [
  {
    title: "Catch what matters.", icon: "bell", tone: "watch", label: "Volume alert",
    ask: "Alert me when Infosys closes with twice its usual volume.",
  },
  {
    title: "Build your own basket.", icon: "layers", tone: "basket", label: "Basket builder",
    ask: "Build an equal-weight basket of the five largest IT stocks.",
  },
  {
    title: "A plan before a position.", icon: "shield", tone: "trade", label: "Trading strategy",
    ask: "Buy ICICI Bank above yesterday’s high, with a 1% stop and 2% target.",
  },
];

function ExampleVisual({ tone }: { tone: string }) {
  if (tone === "watch") return <div className="py-alert-art">
    <div className="py-alert-source"><Logo s="INFY" cls="sm" /><span>INFY</span><span>Daily close</span></div>
    <div className="py-volume-rule"><strong>2<span>×</span></strong><div>the usual volume<span>vs. its 20-day average</span></div><Ic n="signal" /></div>
    <div className="py-alert-wire"><span /><Ic n="chevron" /></div>
    <div className="py-alert-destination"><Ic n="bell" /><div><b>Let the signal come to you.</b><span>Notify in Inbox when the rule is met</span></div></div>
  </div>;
  if (tone === "basket") return <div className="py-basket-art">
    <div className="py-basket-filter"><span>Indian IT</span><Ic n="chevron" /><span>Top 5 by market cap</span></div>
    <div className="py-allocation" aria-label="Five equal allocations of twenty percent">
      {[1, 2, 3, 4, 5].map((rank) => <div key={rank}><span>#{rank}</span><b>20<span>%</span></b></div>)}
    </div>
    <div className="py-basket-summary"><Ic n="scale" /><span>Five names. Equal conviction.</span><b>100%</b></div>
    <div className="py-basket-note">Equal weight, with the selection rule in view.</div>
  </div>;
  return <div className="py-trade-art">
    <div className="py-trade-symbol"><Logo s="ICICIBANK" cls="sm" /><b>ICICIBANK</b><span>Rule-based</span></div>
    <div className="py-risk-plan">
      <div className="py-risk-target"><span>Target</span><b>+2%</b></div>
      <div className="py-risk-entry"><span>Entry</span><span>Above yesterday’s high</span><i /></div>
      <div className="py-risk-stop"><span>Stop loss</span><b>−1%</b></div>
      <div className="py-risk-bracket" />
    </div>
    <div className="py-trade-note"><Ic n="shield" /><span>Entry, risk and exit. Defined together.</span></div>
  </div>;
}

function Showcase() {
  return (
    <div className="py-examples" aria-label="More ways to use Plays">
      {SHOWCASE.map(({ title, icon, tone, label, ask }) => (
        <a className={`py-example py-example--${tone}`} href="https://app.pivotnow.in/#plays" target="_blank" rel="noopener noreferrer" key={title}>
          <div className="py-example-stage">
            <div className="py-example-toolbar"><span><Ic n={icon} />{label}</span></div>
            <ExampleVisual tone={tone} />
          </div>
          <div className="py-example-content">
            <h3>{title}</h3>
            <blockquote>“{ask}”</blockquote>
            <div className="py-example-link"><span>Make it a play</span><span className="py-example-arrow"><Ic n="up" /></span></div>
          </div>
        </a>
      ))}
    </div>
  );
}

export function Plays(): React.ReactElement {
  const win = React.useRef<HTMLDivElement | null>(null);
  const [live, setLive] = React.useState(false);
  React.useEffect(() => {
    const el = win.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setLive(!!e?.isIntersecting), { threshold: 0.35 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  const { flow, typed, stage, leaving } = useFlows(live);
  const p = PLAYS[flow]!;
  return (
    <section id="plays" className="v3 py" data-nav-surface="white">
      <div className="v3-shell">
        <header className="v3-head" data-reveal>
          <h2>Turn ideas into plays that watch the market.</h2>
          <p>Describe your trade in plain English. Pivot turns it into a strategy, no code needed.</p>
        </header>
        <MacWindow title="Plays - Pivot" theme="light" className="py-win">
          <div className="ply py-app" ref={win}>
            {stage < 0
              ? <Home key={`h${flow}`} ask={p.ask} typed={typed} sending={stage === -1} />
              : <Thread key={`t${flow}`} p={p} stage={stage} leaving={leaving} />}
          </div>
        </MacWindow>
        <div className="ply py-show"><Showcase /></div>
      </div>
    </section>
  );
}
