/**
 * frame — the Mac window every product mockup sits in, and the halftone
 * objects the page uses sparingly.
 *
 *   MacWindow       traffic lights, a centred title, the content below. Dark
 *                   chrome for the dark workspace, light for the light one.
 *   HalftonePlanet  the affiliate page's closing planet: dot size is the
 *                   light, a fixed sun, a turning surface, two dotted orbits
 *                   and a moon that passes behind and in front.
 *
 * The canvas runs only while on screen and holds a still frame under
 * reduced motion.
 */
"use client";

import * as React from "react";
import type { IChartApi } from "lightweight-charts";

/** The page's phone width, the one v3.css lays its single column out at. */
export const PHONE = "(max-width: 720px)";

/** Frame a chart for the screen it is on. A wide window shows every bar; a
 *  phone shows only the last `last`, so the candles stay readable bodies
 *  rather than a hundred and twenty hairlines. */
export function fitBars(chart: IChartApi, n: number, last = 36): void {
  if (window.matchMedia(PHONE).matches && n > last) chart.timeScale().setVisibleLogicalRange({ from: n - last - 0.5, to: n + 1 });
  else chart.timeScale().fitContent();
}

export function MacWindow({
  title, theme = "dark", className, children,
}: { title: string; theme?: "dark" | "light"; className?: string; children: React.ReactNode }) {
  return (
    <div className={`mw mw-${theme} ${className ?? ""}`}>
      <div className="mw-bar">
        <span className="mw-lights" aria-hidden="true"><i /><i /><i /></span>
        <span className="mw-title">{title}</span>
      </div>
      <div className="mw-body">{children}</div>
    </div>
  );
}

/* ── canvas loop ─────────────────────────────────────────────────────────── */

function useCanvas(draw: (ctx: CanvasRenderingContext2D, t: number, w: number, h: number) => void) {
  const ref = React.useRef<HTMLCanvasElement | null>(null);
  const drawRef = React.useRef(draw);
  drawRef.current = draw;
  React.useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    let w = 0, h = 0, raf = 0, visible = false;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const fit = () => {
      w = canvas.clientWidth; h = canvas.clientHeight;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    // A canvas hidden by CSS (the planet is `display:none` under 720px)
    // measures 0×0; drawing into it is pointless and, for size-stepped loops,
    // never terminates.
    const paint = (t: number) => { ctx.clearRect(0, 0, w, h); if (w > 0 && h > 0) drawRef.current(ctx, t, w, h); };
    const t0 = performance.now();
    const frame = (now: number) => { paint((now - t0) / 1000); if (visible && !still) raf = requestAnimationFrame(frame); };
    fit();
    paint(5);
    const ro = new ResizeObserver(() => { fit(); paint(5); });
    ro.observe(canvas);
    const io = new IntersectionObserver(([e]) => {
      const was = visible;
      visible = !!e?.isIntersecting;
      if (still) return;
      if (visible && !was) raf = requestAnimationFrame(frame);
      if (!visible) cancelAnimationFrame(raf);
    });
    io.observe(canvas);
    return () => { io.disconnect(); ro.disconnect(); cancelAnimationFrame(raf); };
  }, []);
  return ref;
}

const BONE = "244, 241, 234";

/* ── the planet ──────────────────────────────────────────────────────────── */

const CRATERS = [
  { lon: 0.4, lat: 0.35, r: 0.32 }, { lon: 2.1, lat: -0.2, r: 0.42 },
  { lon: 3.6, lat: 0.55, r: 0.25 }, { lon: 4.7, lat: -0.5, r: 0.36 },
  { lon: 5.6, lat: 0.1, r: 0.22 }, { lon: 1.3, lat: -0.65, r: 0.2 },
];
const SUN = (() => { const v = [-0.62, -0.5, 0.6]; const m = Math.hypot(...v); return v.map((x) => x / m) as [number, number, number]; })();

/** Stars are placed once from a fixed seed so every paint agrees. */
const STARS = Array.from({ length: 30 }, (_, i) => ({
  x: (Math.sin(i * 12.9898) * 43758.5453) % 1, y: (Math.sin(i * 78.233) * 12345.678) % 1, a: 0.15 + ((i * 37) % 10) / 22,
})).map((s) => ({ x: Math.abs(s.x), y: Math.abs(s.y), a: s.a }));

export function HalftonePlanet({ className }: { className?: string }) {
  const ref = useCanvas((ctx, t, w, h) => {
    const cx = w * 0.5, cy = h * 0.5;
    const R = Math.min(w, h) * 0.33;
    const spin = t * 0.22;
    for (const s of STARS) {
      ctx.fillStyle = `rgba(${BONE}, ${s.a * (0.75 + 0.25 * Math.sin(t * 1.3 + s.x * 20))})`;
      ctx.fillRect(s.x * w, s.y * h, 1.4, 1.4);
    }
    const orbits = [
      { rx: R * 1.85, ry: R * 0.42, tilt: -0.3, speed: 0.38 },
      { rx: R * 1.3, ry: R * 0.3, tilt: 1.05, speed: -0.27 },
    ];
    const point = (o: (typeof orbits)[number], u: number): [number, number, number] => {
      const ex = Math.cos(u) * o.rx, ey = Math.sin(u) * o.ry;
      return [cx + ex * Math.cos(o.tilt) - ey * Math.sin(o.tilt), cy + ex * Math.sin(o.tilt) + ey * Math.cos(o.tilt), Math.sin(u)];
    };
    const hidden = (x: number, y: number) => Math.hypot(x - cx, y - cy) < R + 2;
    const orbitDots = (front: boolean) => {
      for (const o of orbits) {
        const steps = Math.round((o.rx + o.ry) / 2.4);
        for (let i = 0; i < steps; i++) {
          const u = (i / steps) * Math.PI * 2 + t * o.speed * 0.15;
          const [x, y, z] = point(o, u);
          if ((z >= 0) !== front || (!front && hidden(x, y))) continue;
          ctx.fillStyle = `rgba(${BONE}, ${front ? 0.85 : 0.32})`;
          ctx.beginPath(); ctx.arc(x, y, front ? 1.1 : 0.9, 0, Math.PI * 2); ctx.fill();
        }
      }
    };
    const moons = orbits.map((o, k) => point(o, t * o.speed + k * 2.4));
    const moon = (front: boolean) => moons.forEach(([x, y, z], k) => {
      if ((z >= 0) !== front || (!front && hidden(x, y))) return;
      ctx.fillStyle = `rgba(${BONE}, ${front ? 1 : 0.4})`;
      ctx.beginPath(); ctx.arc(x, y, k === 0 ? 3.2 : 2.2, 0, Math.PI * 2); ctx.fill();
    });
    orbitDots(false); moon(false);
    const s = R / 19;
    if (!(s > 0)) return;  // a zero step would spin the grid loop forever
    ctx.fillStyle = `rgb(${BONE})`;
    for (let gy = -R; gy <= R; gy += s) {
      for (let gx = -R; gx <= R; gx += s) {
        const nx = gx / R, ny = gy / R, rr = nx * nx + ny * ny;
        if (rr > 1) continue;
        const nz = Math.sqrt(1 - rr);
        const lambert = Math.max(0, nx * SUN[0] + ny * SUN[1] + nz * SUN[2]);
        const lon = Math.atan2(nx, nz) + spin, lat = Math.asin(ny);
        let tex = 0.82 + 0.18 * Math.sin(lat * 7 + Math.sin(lon * 2) * 1.5);
        for (const c of CRATERS) {
          let dl = ((lon - c.lon) % (Math.PI * 2) + Math.PI * 3) % (Math.PI * 2) - Math.PI;
          dl *= Math.cos(lat);
          const d = Math.hypot(dl, lat - c.lat) / c.r;
          if (d < 1) tex *= 0.5 + 0.5 * d * d;
          else if (d < 1.18) tex *= 1.12;
        }
        const b = Math.max(0.2 * tex, Math.min(1, lambert * tex * 1.08));
        const r = (s * 0.6) * Math.pow(b, 0.75);
        if (r < 0.35) continue;
        ctx.beginPath(); ctx.arc(cx + gx, cy + gy, r, 0, Math.PI * 2); ctx.fill();
      }
    }
    orbitDots(true); moon(true);
  });
  return <canvas ref={ref} className={className} aria-hidden="true" />;
}
