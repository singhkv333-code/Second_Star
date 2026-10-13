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
 *   Three cards  soft-lit objects on dark glass: the backtest against buy and
 *                hold (real figures), the paper book behind a lock, and alert
 *                switches with the order switch held off.
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
const pct = (n: number) => `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(1)}%`;

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
            <p>Every result comes with what it is measured against. Nothing is traded for real.</p>
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
            <div className="hn-obj bars">
              <div className="hn-barrow"><span>Strategy</span><i style={{ "--w": `${Math.abs(LAB.ret) / Math.abs(LAB.buy_hold)}` } as React.CSSProperties} /><b>{pct(LAB.ret)}</b></div>
              <div className="hn-barrow"><span>Buy & hold</span><i style={{ "--w": 1 } as React.CSSProperties} /><b>{pct(LAB.buy_hold)}</b></div>
            </div>
            <h3>Next to doing nothing.</h3>
            <p>Every backtest sits beside buy and hold. This one: RELIANCE, one year, {LAB.trades} trades.</p>
          </article>
          <article className="hn-card">
            <div className="hn-obj rings">
              <i /><i /><i /><i />
              <span className="hn-lock"><Lock /></span>
            </div>
            <h3>Simulated, never sent.</h3>
            <p>Strategies fill into a paper book you can watch. No live orders.</p>
          </article>
          <article className="hn-card">
            <div className="hn-obj toggles">
              <label><span>Close above 1,329.75</span><i className="sw on" /></label>
              <label><span>Close below 1,116.36</span><i className="sw on" /></label>
              <label className="off"><span>Place an order</span><i className="sw" /><Lock /></label>
            </div>
            <h3>Alerts tell you. They never trade.</h3>
            <p>Checked on every bar close, on the server, whether or not the tab is open.</p>
          </article>
        </div>
      </div>
    </section>
  );
}
