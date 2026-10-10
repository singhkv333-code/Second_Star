"use client";

import { useEffect, useRef } from "react";

/**
 * Two small canvas animations for /affiliate, in the page's one ink:
 *
 *   PixelGrid       a 9 × 9 dot-matrix that redraws in steps (the reference's
 *                   "running grid"): a broadcast ring, a live bar chart, coins
 *                   stacking up.
 *   HalftonePlanet  a halftone sphere — dot size is the light — turning under
 *                   a fixed sun, crossed by two dotted orbits and a moon that
 *                   passes behind it and in front.
 *
 * Both run only while on screen, and hold one still frame for anyone who
 * prefers reduced motion.
 */

const BONE = "244, 241, 234";

function useCanvasLoop(
  draw: (ctx: CanvasRenderingContext2D, t: number, w: number, h: number) => void,
  width: number,
  height: number,
): React.RefObject<HTMLCanvasElement | null> {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawRef = useRef(draw);
  drawRef.current = draw;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let visible = false;
    const t0 = performance.now();
    const frame = (now: number): void => {
      ctx.clearRect(0, 0, width, height);
      drawRef.current(ctx, (now - t0) / 1000, width, height);
      if (visible && !still) raf = requestAnimationFrame(frame);
    };
    if (still) {
      ctx.clearRect(0, 0, width, height);
      drawRef.current(ctx, 6, width, height);
      return;
    }
    const io = new IntersectionObserver(([entry]) => {
      const was = visible;
      visible = !!entry?.isIntersecting;
      if (visible && !was) raf = requestAnimationFrame(frame);
      if (!visible) cancelAnimationFrame(raf);
    });
    io.observe(canvas);
    return () => { io.disconnect(); cancelAnimationFrame(raf); };
  }, [width, height]);

  return ref;
}

// ── PixelGrid ──────────────────────────────────────────────────────────────

type Pattern = "share" | "trade" | "paid";
type Cells = number[][]; // 0 off · 1 half · 2 full

const N = 9;
const STEP_MS = 140;

function blank(): Cells {
  return Array.from({ length: N }, () => Array<number>(N).fill(0));
}

/** One step of each pattern. State lives in a closure so a pattern can remember. */
function makePattern(kind: Pattern): (step: number) => Cells {
  const c = (N - 1) / 2;
  if (kind === "share") {
    // A link at the centre, rings leaving it.
    return (step) => {
      const g = blank();
      const period = 9;
      for (let k = 0; k < 2; k++) {
        const r = ((step / 2 + k * (period / 2)) % period) * 0.62;
        for (let y = 0; y < N; y++) {
          for (let x = 0; x < N; x++) {
            const d = Math.hypot(x - c, y - c);
            if (Math.abs(d - r) < 0.5) g[y]![x] = Math.max(g[y]![x]!, r > 4.2 ? 1 : 2);
            else if (Math.abs(d - (r - 1)) < 0.45 && r > 1) g[y]![x] = Math.max(g[y]![x]!, 1);
          }
        }
      }
      g[c]![c] = 2;
      return g;
    };
  }
  if (kind === "trade") {
    // A live bar chart scrolling left, drifting up, mean-reverting.
    const bars: number[] = [2, 3, 3, 4, 3, 5, 5, 6, 7];
    let last = -1;
    return (step) => {
      if (step !== last && step % 3 === 0) {
        const prev = bars[bars.length - 1]!;
        let next = prev + (Math.random() < 0.58 ? 1 : -1) * (Math.random() < 0.7 ? 1 : 2);
        if (next > N - 1) next = N - 3;
        if (next < 2) next = 3;
        bars.shift();
        bars.push(next);
      }
      last = step;
      const g = blank();
      bars.forEach((h, x) => {
        for (let k = 0; k < h; k++) g[N - 1 - k]![x] = k === h - 1 ? 2 : 1;
      });
      return g;
    };
  }
  // "paid": coins drop and stack; a full purse empties and starts again.
  const stacks = Array<number>(N).fill(0);
  let coins: { x: number; y: number }[] = [];
  let last = -1;
  return (step) => {
    if (step !== last) {
      last = step;
      coins = coins.flatMap((coin) => {
        const floor = N - 1 - stacks[coin.x]!;
        if (coin.y + 1 >= floor) { stacks[coin.x] = stacks[coin.x]! + 1; return []; }
        return [{ x: coin.x, y: coin.y + 1 }];
      });
      if (step % 2 === 0) {
        const open = stacks.map((s, x) => (s < N - 3 ? x : -1)).filter((x) => x >= 0);
        if (open.length) coins.push({ x: open[Math.floor(Math.random() * open.length)]!, y: 0 });
      }
      if (stacks.reduce((a, b) => a + b, 0) > N * 4.2) { stacks.fill(0); coins = []; }
    }
    const g = blank();
    stacks.forEach((s, x) => { for (let k = 0; k < s; k++) g[N - 1 - k]![x] = k === s - 1 ? 2 : 1; });
    coins.forEach((coin) => { g[coin.y]![coin.x] = 2; });
    return g;
  };
}

export function PixelGrid({ pattern, size = 96 }: { pattern: Pattern; size?: number }): React.ReactElement {
  const stepFn = useRef<((s: number) => Cells) | null>(null);
  if (!stepFn.current) stepFn.current = makePattern(pattern);

  const ref = useCanvasLoop((ctx, t, w) => {
    const g = stepFn.current!(Math.floor((t * 1000) / STEP_MS));
    const cell = w / N;
    const gap = Math.max(1.5, cell * 0.16);
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        const v = g[y]![x]!;
        ctx.fillStyle = v === 2 ? `rgb(${BONE})` : v === 1 ? `rgba(${BONE}, .42)` : `rgba(${BONE}, .07)`;
        ctx.fillRect(x * cell + gap / 2, y * cell + gap / 2, cell - gap, cell - gap);
      }
    }
  }, size, size);

  return <canvas ref={ref} className="aff-pixels" style={{ width: size, height: size }} aria-hidden="true" />;
}

// ── HalftonePlanet ─────────────────────────────────────────────────────────

const CRATERS = [
  { lon: 0.4, lat: 0.35, r: 0.32 }, { lon: 2.1, lat: -0.2, r: 0.42 },
  { lon: 3.6, lat: 0.55, r: 0.25 }, { lon: 4.7, lat: -0.5, r: 0.36 },
  { lon: 5.6, lat: 0.1, r: 0.22 }, { lon: 1.3, lat: -0.65, r: 0.2 },
];
const LIGHT = (() => { const v = [-0.62, -0.5, 0.6]; const m = Math.hypot(...v); return v.map((x) => x / m) as [number, number, number]; })();

export function HalftonePlanet({ width = 380, height = 290 }: { width?: number; height?: number }): React.ReactElement {
  const stars = useRef<{ x: number; y: number; a: number }[] | null>(null);
  if (!stars.current) {
    stars.current = Array.from({ length: 26 }, () => ({ x: Math.random(), y: Math.random(), a: 0.15 + Math.random() * 0.45 }));
  }

  const ref = useCanvasLoop((ctx, t, w, h) => {
    const cx = w * 0.5, cy = h * 0.5;
    const R = Math.min(w, h) * 0.33;
    const spin = t * 0.22;

    for (const s of stars.current!) {
      ctx.fillStyle = `rgba(${BONE}, ${s.a * (0.75 + 0.25 * Math.sin(t * 1.3 + s.x * 20))})`;
      ctx.fillRect(s.x * w, s.y * h, 1.4, 1.4);
    }

    // Orbits: points on a tilted ellipse; sin(u) < 0 is the far side.
    const orbits = [
      { rx: R * 1.85, ry: R * 0.42, tilt: -0.3, speed: 0.38 },
      { rx: R * 1.3, ry: R * 0.3, tilt: 1.05, speed: -0.27 },
    ];
    const point = (o: (typeof orbits)[number], u: number): [number, number, number] => {
      const ex = Math.cos(u) * o.rx, ey = Math.sin(u) * o.ry;
      return [cx + ex * Math.cos(o.tilt) - ey * Math.sin(o.tilt), cy + ex * Math.sin(o.tilt) + ey * Math.cos(o.tilt), Math.sin(u)];
    };
    // The body occludes the far side by geometry, not a filled disc: the
    // planet's edge is drawn by its dots alone, on any background.
    const hidden = (x: number, y: number): boolean => Math.hypot(x - cx, y - cy) < R + 2;
    const orbitDots = (front: boolean): void => {
      for (const o of orbits) {
        const steps = Math.round((o.rx + o.ry) / 2.4);
        for (let i = 0; i < steps; i++) {
          const u = (i / steps) * Math.PI * 2 + t * o.speed * 0.15;
          const [x, y, z] = point(o, u);
          if ((z >= 0) !== front) continue;
          if (!front && hidden(x, y)) continue;
          ctx.fillStyle = `rgba(${BONE}, ${front ? 0.85 : 0.32})`;
          ctx.beginPath(); ctx.arc(x, y, front ? 1.1 : 0.9, 0, Math.PI * 2); ctx.fill();
        }
      }
    };
    const moons = orbits.map((o, k) => point(o, t * o.speed + k * 2.4));
    const moon = (front: boolean): void => {
      moons.forEach(([x, y, z], k) => {
        if ((z >= 0) !== front) return;
        if (!front && hidden(x, y)) return;
        ctx.fillStyle = `rgba(${BONE}, ${front ? 1 : 0.4})`;
        ctx.beginPath(); ctx.arc(x, y, k === 0 ? 3.2 : 2.2, 0, Math.PI * 2); ctx.fill();
      });
    };

    orbitDots(false);
    moon(false);

    const s = R / 19;
    ctx.fillStyle = `rgb(${BONE})`;
    for (let gy = -R; gy <= R; gy += s) {
      for (let gx = -R; gx <= R; gx += s) {
        const nx = gx / R, ny = gy / R, rr = nx * nx + ny * ny;
        if (rr > 1) continue;
        const nz = Math.sqrt(1 - rr);
        const lambert = Math.max(0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]);
        // Surface coordinates, turning with the spin.
        const lon = Math.atan2(nx, nz) + spin, lat = Math.asin(ny);
        let tex = 0.82 + 0.18 * Math.sin(lat * 7 + Math.sin(lon * 2) * 1.5);
        for (const c of CRATERS) {
          let dl = ((lon - c.lon) % (Math.PI * 2) + Math.PI * 3) % (Math.PI * 2) - Math.PI;
          dl *= Math.cos(lat);
          const d = Math.hypot(dl, lat - c.lat) / c.r;
          if (d < 1) tex *= 0.5 + 0.5 * d * d;
          else if (d < 1.18) tex *= 1.12;
        }
        // Ambient floor: the night side keeps a fine stipple, so the whole disc
        // reads on a light ground and not only its lit crescent.
        const b = Math.max(0.2 * tex, Math.min(1, lambert * tex * 1.08));
        const r = (s * 0.6) * Math.pow(b, 0.75);
        if (r < 0.35) continue;
        ctx.beginPath(); ctx.arc(cx + gx, cy + gy, r, 0, Math.PI * 2); ctx.fill();
      }
    }

    orbitDots(true);
    moon(true);
  }, width, height);

  return <canvas ref={ref} className="aff-planet" style={{ width, aspectRatio: `${width} / ${height}` }} aria-hidden="true" />;
}
