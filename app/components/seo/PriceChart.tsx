/**
 * A year of closes, drawn on the server as plain SVG: no script, so it is in
 * the first paint and in what a crawler sees. The interactive chart is one
 * click away; this one only has to show the shape of the year honestly
 * (linear price axis, real high and low marked, the dates on the axis).
 */

import type { Bar } from "@/lib/seo/data";
import { day, inr } from "@/lib/seo/format";
import styles from "./public.module.css";

const W = 1000;
const H = 260;
const PAD = { t: 16, r: 8, b: 8, l: 8 };

function isoDay(t: number): string {
  return new Date((t + 19_800) * 1000).toISOString().slice(0, 10);
}

export function PriceChart({ bars, name }: { bars: Bar[]; name: string }): React.ReactElement | null {
  if (bars.length < 10) return null;
  const closes = bars.map((b) => b.c);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1;
  const x = (i: number): number => PAD.l + (i / (bars.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number): number => PAD.t + (1 - (v - min) / span) * (H - PAD.t - PAD.b);
  const line = closes.map((c, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(c).toFixed(1)}`).join("");
  const area = `${line}L${x(bars.length - 1).toFixed(1)},${H}L${x(0).toFixed(1)},${H}Z`;
  const up = closes[closes.length - 1]! >= closes[0]!;
  const stroke = up ? "#059669" : "#dc2626";
  const hiI = closes.indexOf(max);
  const loI = closes.indexOf(min);
  const first = bars[0]!;
  const last = bars[bars.length - 1]!;
  const change = ((last.c - first.c) / first.c) * 100;

  return (
    <figure className={styles.chart} aria-label={`${name} daily closing price, ${day(isoDay(first.t))} to ${day(isoDay(last.t))}`}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" preserveAspectRatio="none">
        <title>{`${name} closed at ${inr(last.c)} on ${day(isoDay(last.t))}, ${change >= 0 ? "up" : "down"} ${Math.abs(change).toFixed(1)}% from ${inr(first.c)} on ${day(isoDay(first.t))}`}</title>
        <defs>
          <linearGradient id="pc-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor={stroke} stopOpacity="0.14" />
            <stop offset="1" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#pc-fill)" />
        <path d={line} fill="none" stroke={stroke} strokeWidth="2" vectorEffect="non-scaling-stroke" />
        <circle cx={x(hiI)} cy={y(max)} r="4" fill="#0d0d0e" />
        <circle cx={x(loI)} cy={y(min)} r="4" fill="#0d0d0e" />
      </svg>
      <figcaption className={styles.chartFoot}>
        <span>{day(isoDay(first.t))}</span>
        <span>
          Closing high {inr(max)} ({day(isoDay(bars[hiI]!.t))}) · closing low {inr(min)} ({day(isoDay(bars[loI]!.t))})
        </span>
        <span>{day(isoDay(last.t))}</span>
      </figcaption>
    </figure>
  );
}
