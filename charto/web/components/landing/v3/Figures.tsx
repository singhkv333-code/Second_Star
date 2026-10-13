/**
 * Figures — four numbers, Stripe's way: hairline columns, each figure
 * counting up once as it comes into view (after Magic UI's NumberTicker — an
 * ease-out written straight into the text node, no re-render per frame).
 *
 * The numbers are the platform's own: the minute store (~497M rows,
 * `docs/DATA_MAP.md`), the pattern library (34 candle + 22 chart kinds in
 * `data/patterns.py`, each with a base-rate control), the indicator engine's
 * catalogue (`/indicators`, fifty), and the facts read out of company filings.
 *
 * Above them, the page's ASCII piece: Bitcoin's last 120 daily closes on
 * Bybit typed out as characters — dense at the price, thinning below it —
 * with a slow shimmer running through. Drawn on a canvas in Inter, one glyph
 * per cell, under the same legend the chart wears — typed out too: the
 * Bitcoin mark sampled from its logo into glyphs, then the instrument, its
 * interval and venue, and the last bar's O H L C V with its change on the
 * bar before (`paintReadout` in preview/js/main.js). The legend decodes from
 * noise once, as it comes into view; the tape keeps clear of its box.
 */
"use client";

import * as React from "react";
import { logoSrc } from "./logos";
import { BTC_DAILY } from "./snapshot-btc";

const FIGS = [
  { to: 497, suffix: "M", label: "Minute bars behind every chart" },
  { to: 56, suffix: "", label: "Patterns, each with its base rate" },
  { to: 50, suffix: "", label: "Indicators, computed on the server" },
  { to: 577952, suffix: "", label: "Facts read from company filings" },
];

const fmt = new Intl.NumberFormat("en-IN");

function Ticker({ to, suffix }: { to: number; suffix: string }) {
  const ref = React.useRef<HTMLSpanElement | null>(null);
  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { el.textContent = fmt.format(to) + suffix; return; }
    el.textContent = `0${suffix}`;
    let raf = 0;
    const io = new IntersectionObserver(([e]) => {
      if (!e?.isIntersecting) return;
      io.disconnect();
      const t0 = performance.now(), dur = 1800;
      const tick = (now: number) => {
        const p = Math.min(1, (now - t0) / dur);
        const eased = 1 - Math.pow(1 - p, 4);
        el.textContent = fmt.format(Math.round(to * eased)) + suffix;
        if (p < 1) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    }, { threshold: 0.6 });
    io.observe(el);
    return () => { io.disconnect(); cancelAnimationFrame(raf); };
  }, [to, suffix]);
  return <span ref={ref} className="fg-n">{fmt.format(to) + suffix}</span>;
}

const RAMP = " .,:;-=+*%#@";
const NOISE = "#%*+=-:;@";

/* The chart's own legend. A crypto pair reads en-US to two places at most,
   as Sym.num quotes it; volume counts the base asset, BTC. */
const usd = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });
const LAST = BTC_DAILY[BTC_DAILY.length - 1]!, PREV = BTC_DAILY[BTC_DAILY.length - 2]!;
const LAST_DAY = new Date(LAST[0] * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const UP = "#089981", DN = "#f23645", INK = "#0d0d0e", BTC_ORANGE = "#f7931a";

type Run = { text: string; color: string; alpha?: number };
const LEGEND = (() => {
  const [, o, h, l, c, v] = LAST;
  const bar = c >= o ? UP : DN;
  const d = c - PREV[4], sign = d >= 0 ? "+" : "-";
  const pair = (k: string, n: number): Run[] => [{ text: `${k} `, color: INK }, { text: `${usd(n)}   `, color: bar }];
  const chg: Run = { text: `${sign}${usd(Math.abs(d))} (${sign}${Math.abs((d / PREV[4]) * 100).toFixed(2)}%)`, color: d >= 0 ? UP : DN };
  return {
    title: [{ text: "BTCUSDT", color: INK }, { text: " · ", color: INK, alpha: 0.4 }, { text: "1D", color: INK }, { text: " · ", color: INK, alpha: 0.4 }, { text: "BYBIT", color: INK }] as Run[],
    full: [...pair("O", o), ...pair("H", h), ...pair("L", l), ...pair("C", c), ...pair("V", v), chg],
    short: [...pair("C", c), chg],
    said: `BTCUSDT, 1D, Bybit. Open ${usd(o)}, high ${usd(h)}, low ${usd(l)}, close ${usd(c)}, volume ${usd(v)} BTC, ${sign}${usd(Math.abs(d))} on the day.`,
  };
})();

/* the mark, as glyphs: LC × LR cells of LW × LH px */
const LC = 24, LR = 16, LW = 3.4, LH = 5;

function AsciiTape() {
  const ref = React.useRef<HTMLCanvasElement | null>(null);
  React.useEffect(() => {
    const cv = ref.current, ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    const closes = BTC_DAILY.map((b) => b[4]);
    const lo = Math.min(...closes), hi = Math.max(...closes);
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let w = 0, h = 0, raf = 0, on = false, shown = still ? 0 : -1;  // -1: not yet seen
    const CW = 9, CH = 14;

    // the logo sampled once into a glyph grid: coverage picks density, and
    // the white ₿ inside the disc stays empty
    let mark: (string | null)[] = [];
    const src = logoSrc("BTC");
    if (src) {
      const img = new Image();
      img.onload = () => {
        const oc = document.createElement("canvas");
        oc.width = LC; oc.height = LR;
        const o = oc.getContext("2d");
        if (!o) return;
        o.drawImage(img, 0, 0, LC, LR);
        const px = o.getImageData(0, 0, LC, LR).data;
        mark = Array.from({ length: LC * LR }, (_, i) => {
          const a = px[i * 4 + 3]! / 255;
          const lum = (0.299 * px[i * 4]! + 0.587 * px[i * 4 + 1]! + 0.114 * px[i * 4 + 2]!) / 255;
          if (a < 0.3 || lum > 0.74) return null;
          return RAMP[Math.max(3, Math.round(a * (1 - Math.max(0, lum - 0.6)) * (RAMP.length - 1)))]!;
        });
        if (!on) paint(performance.now() / 1000);
      };
      img.src = src;
    }

    const fit = () => {
      w = cv.clientWidth; h = cv.clientHeight;
      const d = Math.min(window.devicePixelRatio || 1, 2);
      cv.width = Math.round(w * d); cv.height = Math.round(h * d);
      ctx.setTransform(d, 0, 0, d, 0, 0);
    };

    // one glyph per cell; `k` is the legend's reveal (0..1) and `seed` the
    // glyph's place in it — before its turn a cell shows noise, then itself
    const glyph = (ch: string, x: number, y: number, seed: number, k: number, t: number) => {
      if (k >= 1 || k >= seed + 0.12) { ctx.fillText(ch, x, y); return; }
      if (k < seed || ch === " ") return;
      ctx.globalAlpha *= 0.55;
      ctx.fillText(NOISE[Math.floor((t * 24 + seed * 97) % NOISE.length)]!, x, y);
      ctx.globalAlpha /= 0.55;
    };
    const typeRuns = (runs: Run[], x: number, y: number, cw: number, k: number, t: number, s0: number, span: number) => {
      const n = runs.reduce((m, r) => m + r.text.length, 0);
      let i = 0;
      for (const r of runs) {
        ctx.fillStyle = r.color; ctx.globalAlpha = r.alpha ?? 1;
        for (const ch of r.text) { glyph(ch, x + i * cw + cw / 2, y, s0 + (i / n) * span, k, t); i++; }
      }
      ctx.globalAlpha = 1;
      return n * cw;
    };

    const paint = (t: number) => {
      ctx.clearRect(0, 0, w, h);
      const k = shown < 0 ? 0 : still ? 1 : Math.min(1, (performance.now() - shown) / 1300);
      // the legend's box, top-left on the page column
      const x0 = Math.max(24, (w - 1240) / 2), wide = w - x0 > 840;
      const tx = x0 + LC * LW + 18;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.font = "700 5.5px Inter, sans-serif"; ctx.fillStyle = BTC_ORANGE;
      mark.forEach((ch, i) => {
        if (!ch) return;
        const c = i % LC, r = Math.floor(i / LC);
        glyph(ch, x0 + c * LW + LW / 2, 4 + r * LH + LH / 2, (r / LR) * 0.35, k, t);
      });
      ctx.font = "600 22px Inter, sans-serif";
      const tw = typeRuns(LEGEND.title, tx, 22, 15, k, t, 0.05, 0.4);
      ctx.font = "500 14px Inter, sans-serif";
      const vw = typeRuns(wide ? LEGEND.full : LEGEND.short, tx, 60, 9.5, k, t, 0.3, 0.55);
      const keep = { x1: x0 - 14, x2: tx + Math.max(tw, vw) + 14, y2: 4 + LR * LH + 14 };

      // the tape
      ctx.fillStyle = INK;
      ctx.font = "500 12px Inter, sans-serif";
      const cols = Math.floor(w / CW), rows = Math.floor(h / CH);
      for (let c = 0; c < cols; c++) {
        const f = (c / (cols - 1)) * (closes.length - 1), i = Math.floor(f), fr = f - i;
        const v = closes[i]! + ((closes[Math.min(i + 1, closes.length - 1)]! - closes[i]!) * fr);
        const top = (1 - (v - lo) / (hi - lo)) * (rows - 3) + 1;
        const cx = c * CW + CW / 2;
        for (let r = 0; r < rows; r++) {
          const cy = r * CH + CH / 2;
          if (cx > keep.x1 && cx < keep.x2 && cy < keep.y2) continue;
          const d = r - top;
          if (d < -0.5) {
            // above the price: a few stars, twinkling
            const n = Math.sin(c * 12.9898 + r * 78.233) * 43758.5453;
            if (n - Math.floor(n) > 0.985 && Math.sin(t * 1.4 + c + r) > 0) ctx.fillText(".", cx, cy);
            continue;
          }
          const depth = Math.max(0, 1 - d / (rows * 0.75));
          const wave = 0.18 * Math.sin(c * 0.22 - t * 1.6 + r * 0.35);
          const g = Math.round(Math.max(0, Math.min(1, depth * 0.95 + wave + (d < 1 ? 0.25 : 0))) * (RAMP.length - 1));
          const ch = RAMP[g]!;
          if (ch !== " ") ctx.fillText(ch, cx, cy);
        }
      }
    };
    const t0 = performance.now();
    const frame = (now: number) => { paint((now - t0) / 1000); if (on) raf = requestAnimationFrame(frame); };
    fit(); paint(0);
    const ro = new ResizeObserver(() => { fit(); paint((performance.now() - t0) / 1000); });
    ro.observe(cv);
    const io = new IntersectionObserver(([e]) => {
      const vis = !!e?.isIntersecting;
      if (vis && shown < 0) shown = performance.now();
      if (vis && !on && !still) { on = true; raf = requestAnimationFrame(frame); }
      if (!vis) { on = false; cancelAnimationFrame(raf); }
    }, { threshold: 0.4 });
    io.observe(cv);
    return () => { io.disconnect(); ro.disconnect(); cancelAnimationFrame(raf); };
  }, []);
  return <canvas ref={ref} className="fg-ascii" aria-label={`Bitcoin's last 120 daily closes, drawn in characters. ${LEGEND.said}`} role="img" />;
}

export function Figures(): React.ReactElement {
  return (
    <section className="v3 fg" aria-label="Pivot in numbers">
      <AsciiTape />
      <p className="fg-cap">Bitcoin on Bybit, 120 daily closes to {LAST_DAY}, typed out.</p>
      <div className="v3-shell">
        <ul className="fg-row" data-reveal>
          {FIGS.map((f) => (
            <li key={f.label}>
              <Ticker to={f.to} suffix={f.suffix} />
              <span className="fg-l">{f.label}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
