/**
 * Honest — "Honest by default." On black.
 *
 * The page's one dark section, about the two things Pivot refuses to fake.
 * The affiliate page's halftone planet turns beside the heading.
 *
 *   The ladder   an isometric stack of glass plates (the 30-seconds-of-code
 *                isometric card: rotateX 51°, rotateZ 43°), one plate per
 *                rung of the strategy engine's trust ladder. The stack opens
 *                at the lit step: the plates above it rise to make room and
 *                the lit plate lifts into the gap. (Lifting it alone, by half
 *                a gap, pushed it under the blurred glass above, where it
 *                could not be seen.) Under it, the verdict scale, with
 *                the real verdict for the backtest the desk shows lit.
 *   Two cards    server-checked alerts, and the desk's real backtest set
 *                beside holding. The paper book is the Plays section's.
 */
"use client";

import * as React from "react";
import { Lock } from "lucide-react";
import { HalftonePlanet } from "./frame";
import { LAB } from "./snapshot";

const STEPS = [
  { n: "01", t: "Backtest", d: "The rule runs on real bars. Signals fill at the next bar's open." },
  { n: "02", t: "Benchmark", d: "Set beside buy and hold, on the same bars." },
  { n: "03", t: "Deflate", d: "Discounted for every variation you tried." },
  { n: "04", t: "Resample", d: "Trades reshuffled to show the spread, not one lucky path." },
  { n: "05", t: "Verdict", d: "Insufficient data, no edge, unproven or promising. In those words." },
];
const VERDICTS = ["Insufficient data", "No edge", "Unproven", "Promising"];
/** The desk's own backtest (`snapshot` LAB): the strategy engine's run of an
 *  EMA 20/50 cross on RELIANCE daily, after costs, beside holding over the
 *  same window. Real numbers, so the card needs no "illustrative" label. */
const RUN = { ret: LAB.ret, buyHold: LAB.buy_hold, trades: LAB.trades, maxDd: LAB.max_dd, equity: LAB.equity as readonly number[] };
const signed = (n: number) => `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(1)}%`;
/** The equity line over its start (100), which is the dashed baseline. */
const START = RUN.equity[0] ?? 100;
const EQ_LO = Math.min(START, ...RUN.equity), EQ_HI = Math.max(START, ...RUN.equity);
const eqY = (v: number) => 76 - ((v - EQ_LO) / (EQ_HI - EQ_LO || 1)) * 64;
const EQUITY = RUN.equity.map((v, i) => `${(i / (RUN.equity.length - 1)) * 320},${eqY(v)}`).join(" ");
const COMPARE_MAX = Math.max(Math.abs(RUN.ret), Math.abs(RUN.buyHold));

export function Honest(): React.ReactElement {
  const ref = React.useRef<HTMLDivElement | null>(null);
  const [k, setK] = React.useState(STEPS.length - 1);
  const [live, setLive] = React.useState(false);
  const [seen, setSeen] = React.useState(false);

  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setSeen(true); return; }
    let first = true;
    const io = new IntersectionObserver(([e]) => {
      const on = !!e?.isIntersecting;
      if (on && first) { first = false; setK(0); setSeen(true); }
      setLive(on);
    }, { threshold: 0.35 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  React.useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => setK((v) => (v + 1) % STEPS.length), 2200);
    return () => clearInterval(id);
  }, [live]);

  return (
    <section id="honest" className={`v3 hn${seen ? " seen" : ""}`} data-nav-theme="dark">
      <div className="v3-shell">
        <div className="hn-top">
          <header className="v3-head" data-reveal>
            <h2>Honest by default.</h2>
            <p>Every result comes with what it is measured against.</p>
          </header>
          <HalftonePlanet className="hn-planet" />
        </div>

        <div className="hn-ladder" ref={ref}>
          <div className="hn-steps">
            <h3>The trust ladder</h3>
            <ol>
              {STEPS.map((s, i) => (
                <li key={s.n} className={i === k ? "on" : undefined}>
                  <button type="button" onClick={() => setK(i)}>
                    <span className="hn-n">{s.n}</span>
                    <span className="hn-t">{s.t}</span>
                  </button>
                  <p>{s.d}</p>
                </li>
              ))}
            </ol>
          </div>
          <div className="hn-iso" aria-hidden="true">
            <div className="hn-stack">
              {STEPS.map((s, i) => (
                <div key={s.n} className={`hn-plate${i === k ? " on" : i > k ? " up" : ""}`} style={{ "--z": i } as React.CSSProperties}>
                  <span className="hn-pt"><i>{s.n}</i>{s.t}</span>
                </div>
              ))}
            </div>
            <p className="hn-scale">
              {VERDICTS.map((v, i) => (
                <React.Fragment key={v}>
                  {i > 0 && <i>→</i>}
                  <span className={v.toLowerCase() === LAB.verdict.toLowerCase() ? "on" : undefined}>{v}</span>
                </React.Fragment>
              ))}
            </p>
          </div>
        </div>

        <div className="hn-trio duo">
          <article className="hn-card">
            <div className="hn-obj toggles">
              <label><span>Close above 1,329.75</span><i className="sw on" /></label>
              <label><span>Close below 1,116.36</span><i className="sw on" /></label>
              <label className="off"><span>Place an order</span><i className="sw" /><Lock /></label>
            </div>
            <h3>Alerts checked on every bar.</h3>
            <p>On the server, whether or not the tab is open.</p>
          </article>
          <article className="hn-card">
            <div className="hn-obj comparison">
              <div className="hn-compare-head"><strong>Versus holding</strong><span>RELIANCE</span></div>
              <div className={`hn-compare-row strategy${RUN.ret < 0 ? " dn" : ""}`}><span>Strategy</span><b>{signed(RUN.ret)}</b><i><em style={{ width: `${(Math.abs(RUN.ret) / COMPARE_MAX) * 100}%` }} /></i></div>
              <div className={`hn-compare-row hold${RUN.buyHold < 0 ? " dn" : ""}`}><span>Buy &amp; hold</span><b>{signed(RUN.buyHold)}</b><i><em style={{ width: `${(Math.abs(RUN.buyHold) / COMPARE_MAX) * 100}%` }} /></i></div>
              <svg viewBox="0 0 320 84" preserveAspectRatio="none" role="img" aria-label="Strategy equity curve for the tested period">
                <line x1="0" x2="320" y1={eqY(START)} y2={eqY(START)} />
                <polyline points={EQUITY} />
              </svg>
              <div className="hn-compare-dates"><span>{LAB.window.split(" → ")[0]}</span><span>{LAB.window.split(" → ")[1]}</span></div>
              <div className="hn-compare-foot"><span>{RUN.trades} trades · {RUN.maxDd.toFixed(1)}% max drawdown</span></div>
            </div>
            <h3>Backtests, put in context.</h3>
            <p>Compare the strategy with holding over the same period.</p>
          </article>
        </div>
      </div>
    </section>
  );
}
