/**
 * Honest — "Honest by default." On black.
 *
 * The page's one dark section, about the two things Pivot refuses to fake.
 * The affiliate page's halftone planet turns beside the heading.
 *
 *   The ladder   an isometric stack of glass plates (the 30-seconds-of-code
 *                isometric card: rotateX 51°, rotateZ 43°), one plate per
 *                rung of the strategy engine's trust ladder, lifting in turn
 *                beside its numbered step. Under it, the verdict scale, with
 *                the real verdict for the backtest the desk shows lit.
 *   Three cards  server-checked alerts, the paper book, and an explicitly
 *                illustrative backtest comparison.
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
/** Illustrative card data, separate from the captured LAB backtest. */
const DEMO = {
  ret: 12.4,
  buyHold: 6.8,
  trades: 24,
  equity: [100, 101, 99.4, 102.1, 103.5, 101.8, 104.7, 106.3, 103.9, 107.1, 105.8, 108.6, 107.2, 110.4, 108.9, 112.4],
};
const DEMO_DRAWDOWN = (() => {
  let peak = DEMO.equity[0]!, worst = 0;
  for (const value of DEMO.equity) {
    peak = Math.max(peak, value);
    worst = Math.max(worst, (1 - value / peak) * 100);
  }
  return worst;
})();
const EQUITY = (() => {
  const values = DEMO.equity;
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo || 1;
  return values.map((value, index) => `${(index / (values.length - 1)) * 320},${76 - ((value - lo) / span) * 64}`).join(" ");
})();
const COMPARE_MAX = Math.max(Math.abs(DEMO.ret), Math.abs(DEMO.buyHold));

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
                <div key={s.n} className={`hn-plate${i === k ? " on" : ""}`} style={{ "--z": i } as React.CSSProperties}>
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

        <div className="hn-trio">
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
            <div className="hn-obj rings">
              <i /><i /><i /><i />
              <span className="hn-lock"><Lock /></span>
            </div>
            <h3>Watch strategies run on paper.</h3>
            <p>See simulated fills and positions in your paper book.</p>
          </article>
          <article className="hn-card">
            <div className="hn-obj comparison">
              <div className="hn-compare-head"><strong>Versus holding</strong><span>Illustrative · same window</span></div>
              <div className="hn-compare-row strategy"><span>Strategy</span><b>+{DEMO.ret.toFixed(1)}%</b><i><em style={{ width: `${(DEMO.ret / COMPARE_MAX) * 100}%` }} /></i></div>
              <div className="hn-compare-row hold"><span>Buy &amp; hold</span><b>+{DEMO.buyHold.toFixed(1)}%</b><i><em style={{ width: `${(DEMO.buyHold / COMPARE_MAX) * 100}%` }} /></i></div>
              <svg viewBox="0 0 320 84" preserveAspectRatio="none" role="img" aria-label="Strategy equity curve for the tested period">
                <line x1="0" x2="320" y1="76" y2="76" />
                <polyline points={EQUITY} />
              </svg>
              <div className="hn-compare-dates"><span>{LAB.window.split(" → ")[0]}</span><span>{LAB.window.split(" → ")[1]}</span></div>
              <div className="hn-compare-foot"><span>{DEMO.trades} trades · {DEMO_DRAWDOWN.toFixed(1)}% max drawdown</span><span>Illustrative example</span></div>
            </div>
            <h3>Backtests, put in context.</h3>
            <p>Compare the strategy with holding over the same period.</p>
          </article>
        </div>
      </div>
    </section>
  );
}
