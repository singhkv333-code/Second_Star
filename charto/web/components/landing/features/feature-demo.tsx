/**
 * feature-demo — a cropped piece of the actual application, not a picture of
 * one.
 *
 * The section's opening visual used to be a chart in a rounded card with a
 * prompt pill floating over it. Nothing about that shape exists in the
 * product: charto's window is `header → main[ stage | splitter | chatpanel ]`
 * (`charto/preview/index.html`), and the chat is a COLUMN beside the chart,
 * not a bubble on top of it. So this is built to that architecture, at that
 * architecture's density:
 *
 *   ┌ toolbar ──────────────────────────────────────────────────┐
 *   │ RELIANCE NSE │ 1D │ Indicators              2 drawn        │
 *   ├──────────────────────────────────────┬────────────────────┤
 *   │ readout + candles + zones            │ the turn that drew │
 *   │                                      │ them, then the ask │
 *   └──────────────────────────────────────┴────────────────────┘
 *
 * Everything visible is quoted from the app rather than designed again here:
 *
 *   toolbar     → `header` — `.symbol` pill (30px, muted, semibold) with its
 *                 `.ex` venue, `.vsep`, the `.iv-pill` interval, `.btn`
 *   readout     → `.readout` — title row, then `<i>O</i><b>…</b>` OHLC
 *   zones       → `scene.js`'s `kind:"zone"` — a gradient band that fades
 *                 toward its middle between two solid edges, labelled by a
 *                 `.scene-chip`: quiet coloured text at the RIGHT, never a
 *                 filled badge at the left
 *   the turn    → `.turn.user .bubble` (muted, right-aligned) and then the
 *                 assistant as BARE PROSE — the app deliberately gives the
 *                 reply no container
 *   the wait    → `chat.js` `createWait()` — three bars, a seconds counter,
 *                 and step rows on a connector line, the live one shimmering
 *   composer    → `.composer` and its real placeholder, "Ask about this chart…"
 *
 * The OHLC figures are COMPUTED from the same `BARS` the chart plots, so the
 * readout and the candles cannot disagree.
 *
 * It is inert by construction: no inputs, no handlers, no fetches, no storage,
 * no app state. The sequence below is a fixed timeline, not a request.
 */
"use client";

import * as React from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { FeatureChart, type FeatureBar, type PriceBand } from "./feature-chart";
import {
  BARS,
  LAST,
  formatINR,
} from "@/components/landing/film/film-script";
import {
  DEMO_EXCHANGE,
  DEMO_LEVELS,
  DEMO_SESSION,
} from "./feature-data";

gsap.registerPlugin(useGSAP);

const FEATURE_SYMBOL = "NIFTY";
const NIFTY_LAST = {
  open: 24285.05,
  high: 24313,
  low: 24144.3,
  close: 24219.05,
  previousClose: 24252,
} as const;
const NIFTY_SCALE = NIFTY_LAST.close / LAST.close;
const NIFTY_BARS: readonly FeatureBar[] = BARS.map((bar, index) => index === BARS.length - 1
  ? { ...bar, open: NIFTY_LAST.open, high: NIFTY_LAST.high, low: NIFTY_LAST.low, close: NIFTY_LAST.close }
  : {
      ...bar,
      open: bar.open * NIFTY_SCALE,
      high: bar.high * NIFTY_SCALE,
      low: bar.low * NIFTY_SCALE,
      close: bar.close * NIFTY_SCALE,
    });

const levelPrice = (price: number) => formatINR(price * NIFTY_SCALE);

function NiftyMark(): React.ReactElement {
  return <span className="cfd-symbol-mark" aria-hidden="true">N</span>;
}

/** The two zones `get_levels` drew, in price space with the app's own label. */
const ZONES: readonly PriceBand[] = DEMO_LEVELS.filter((l) => l.band).map((l) => ({
  id: l.id,
  from: l.band!.from * NIFTY_SCALE,
  to: l.band!.to * NIFTY_SCALE,
  tone: l.role === "support" ? "support" : "resistance",
  label: `${l.role === "support" ? "S" : "R"} ${levelPrice((l.band!.from + l.band!.to) / 2)} · ${l.record.replace(/^Held (\d+) of (\d+)$/, "held $1/$2")}`,
}));

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = React.useState(false);
  React.useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const on = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** The bar under the crosshair is the last one — the app's resting state. */
function Readout() {
  const change = NIFTY_LAST.close - NIFTY_LAST.previousClose;
  const changePct = (change / NIFTY_LAST.previousClose) * 100;
  const dir = change >= 0 ? "up" : "dn";
  const chg = `${change >= 0 ? "+" : "−"}${Math.abs(changePct).toFixed(2)}%`;
  const abs = `${change >= 0 ? "+" : "−"}${formatINR(Math.abs(change))}`;
  return (
    <div className="cfd-readout">
      <div className="cfd-ro-title">
        <NiftyMark />
        {FEATURE_SYMBOL}
        <span className="iv">1D</span>
        <span className="ex">{DEMO_EXCHANGE}</span>
      </div>
      <div className="cfd-ro-row">
        <span>
          <i>O</i>
          <b className={dir}>{formatINR(NIFTY_LAST.open)}</b>
        </span>
        <span>
          <i>H</i>
          <b className={dir}>{formatINR(NIFTY_LAST.high)}</b>
        </span>
        <span>
          <i>L</i>
          <b className={dir}>{formatINR(NIFTY_LAST.low)}</b>
        </span>
        <span>
          <i>C</i>
          <b className={dir}>{formatINR(NIFTY_LAST.close)}</b>
        </span>
        <span className={`chg abs ${dir}`}>{abs}</span>
        <span className={`chg ${dir}`}>({chg})</span>
      </div>
    </div>
  );
}

export function FeatureDemo(): React.ReactElement {
  const rootRef = React.useRef<HTMLDivElement | null>(null);
  const reduced = usePrefersReducedMotion();
  const [ready, setReady] = React.useState(false);
  const [seen, setSeen] = React.useState(false);

  // The bands can only be tweened once the chart has told the overlay where a
  // price is; until then there is nothing in the DOM to tween.
  const onBandsReady = React.useCallback(() => setReady(true), []);

  // The whole point is a cause and its effect, so the turn has to run WHILE
  // someone is looking at it. On mount it would be over a page and a half
  // before the section arrives, and all anyone would ever see is the result.
  React.useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold: 0.4 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // The resting state, set before the browser paints so nothing flashes in
  // and back out. The chat turns are server-rendered and therefore already in
  // the DOM; the bands are not, and take their hidden state from CSS instead.
  React.useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root || reduced) return;
    gsap.set(gsap.utils.selector(root)("[data-d]"), { autoAlpha: 0, y: 6 });
    gsap.set(gsap.utils.selector(root)("[data-d-drawn]"), { autoAlpha: 0 });
  }, [reduced]);

  useGSAP(
    () => {
      const root = rootRef.current;
      if (!root || !ready) return;
      const q = gsap.utils.selector(root);

      // Reduced motion gets the finished surface, not a timeline racing it:
      // the question asked, both zones drawn, the answer given.
      if (reduced) {
        gsap.set(q("[data-d]"), { autoAlpha: 1, y: 0 });
        gsap.set(q("[data-d-band], [data-d-chip]"), { autoAlpha: 1, scaleY: 1, x: 0 });
        gsap.set(q("[data-d-wait]"), { autoAlpha: 0, height: 0, margin: 0 });
        return;
      }
      if (!seen) return;

      const tl = gsap.timeline({ defaults: { ease: "power2.out" } });

      tl.set(q("[data-d-band]"), { autoAlpha: 0, scaleY: 0.12, transformOrigin: "50% 50%" })
        .set(q("[data-d-chip]"), { autoAlpha: 0, x: 6 })
        // the question
        .to(q("[data-d-ask]"), { autoAlpha: 1, y: 0, duration: 0.4 }, 0.15)
        // the wait, then its steps one at a time — the app pushes a row as
        // each tool lands, so they arrive apart rather than together
        .to(q("[data-d-wait]"), { autoAlpha: 1, y: 0, duration: 0.3 }, 0.5)
        .to(q("[data-d-step]"), { autoAlpha: 1, y: 0, duration: 0.28, stagger: 0.5 }, 0.7)
        // the drawing: a zone opens from its own middle, which is how a band
        // whose fill fades to its centre wants to arrive
        .to(
          q("[data-d-band='res']"),
          { autoAlpha: 1, scaleY: 1, duration: 0.5, ease: "power3.out" },
          1.85,
        )
        .to(q("[data-d-chip='res']"), { autoAlpha: 1, x: 0, duration: 0.35 }, 2.15)
        .to(
          q("[data-d-band='sup']"),
          { autoAlpha: 1, scaleY: 1, duration: 0.5, ease: "power3.out" },
          2.25,
        )
        .to(q("[data-d-chip='sup']"), { autoAlpha: 1, x: 0, duration: 0.35 }, 2.55)
        // the toolbar gains the control that only exists once chat has drawn
        .to(q("[data-d-drawn]"), { autoAlpha: 1, duration: 0.3 }, 2.6)
        // the wait folds away and the answer takes its place
        .to(q("[data-d-wait]"), { autoAlpha: 0, height: 0, marginTop: 0, duration: 0.34 }, 2.7)
        .to(q("[data-d-reply]"), { autoAlpha: 1, y: 0, duration: 0.45 }, 2.85);

      return () => {
        tl.kill();
      };
    },
    { dependencies: [ready, reduced, seen], scope: rootRef },
  );

  return (
    <div className="cfd" ref={rootRef}>
      {/* ── toolbar ─────────────────────────────────────────────────────── */}
      <div className="cfd-bar">
        <span className="cfd-symbol">
          <NiftyMark />
          {FEATURE_SYMBOL}
          <em>{DEMO_EXCHANGE}</em>
        </span>
        <i className="cfd-vsep" aria-hidden="true" />
        <span className="cfd-pill">1D</span>
        <i className="cfd-vsep" aria-hidden="true" />
        <span className="cfd-btn">Indicators</span>
        <span className="cfd-grow" />
        {/* `sceneClear` exists in the app only once the chat has put something
            on the chart, so it arrives with the zones. */}
        <span className="cfd-btn quiet" data-d-drawn>
          {DEMO_SESSION.drawn}
        </span>
      </div>

      {/* ── stage + conversation ────────────────────────────────────────── */}
      <div className="cfd-main">
        <div className="cfd-stage">
          <Readout />
          <FeatureChart bands={ZONES} bars={NIFTY_BARS} height="100%" onBandsReady={onBandsReady} />
        </div>

        <aside className="cfd-chat">
          <div className="cfd-thread">
            <div className="cfd-turn user" data-d data-d-ask>
              <span className="cfd-bubble">{DEMO_SESSION.ask}</span>
            </div>

            <div className="cfd-wait" data-d data-d-wait>
              <div className="cfd-wait-head">
                <span className="cfd-ticker" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                <span className="cfd-secs">{DEMO_SESSION.elapsed}</span>
              </div>
              <div className="cfd-steps">
                {DEMO_SESSION.steps.map((s, i) => (
                  <div className="cfd-step" key={s.word + s.detail} data-d data-d-step>
                    <i className="dot" aria-hidden="true" />
                    <span className={`lbl${i === DEMO_SESSION.steps.length - 1 ? " live" : ""}`}>
                      <b>{s.word}</b> {s.detail}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="cfd-turn assistant" data-d data-d-reply>
              <p>
                Two zones carry a record worth the ink. {levelPrice(2486)}–{levelPrice(2496)} has held 3 of its 4 tests; {levelPrice(2404)}–{levelPrice(2428)} has held 5 of 6, and price is sitting on it now.
              </p>
            </div>
          </div>

          <div className="cfd-composer" aria-hidden="true">
            <span className="ph">Ask about this chart…</span>
            <span className="send">
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path d="m5 12 7-7 7 7" />
                <path d="M12 19V5" />
              </svg>
            </span>
          </div>
        </aside>
      </div>
    </div>
  );
}
