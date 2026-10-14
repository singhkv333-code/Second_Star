/** Formatting at the edge. The wire carries paise and unix seconds. */

import type { CatalogFeature, FeatureValue } from "./types";

/** ₹499 · ₹5,388 · ₹449.50 — whole rupees unless there are paise. */
export function inr(paise: number | null | undefined): string {
  if (paise == null) return "—";
  const rupees = paise / 100;
  const whole = Number.isInteger(rupees);
  return (
    "₹" +
    rupees.toLocaleString("en-IN", {
      minimumFractionDigits: whole ? 0 : 2,
      maximumFractionDigits: whole ? 0 : 2,
    })
  );
}

const DATE = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "Asia/Kolkata",
});
const SHORT = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  timeZone: "Asia/Kolkata",
});

/** 9 Nov 2026, in IST, which is where every window resets. */
export function fmtDate(unix: number | null | undefined): string {
  if (!unix) return "—";
  return DATE.format(new Date(unix * 1000));
}

export function fmtShortDate(unix: number | null | undefined): string {
  if (!unix) return "—";
  return SHORT.format(new Date(unix * 1000));
}

export function daysUntil(unix: number | null | undefined, now: number): number | null {
  if (!unix) return null;
  return Math.max(0, Math.ceil((unix - now) / 86400));
}

export const num = (v: number): string => v.toLocaleString("en-IN");

function months(days: number): string {
  if (days % 30 === 0) {
    const m = days / 30;
    return `${m} month${m === 1 ? "" : "s"}`;
  }
  return `${days} days`;
}

/**
 * One feature's worth, as a person reads it: "20", "Unlimited", "2 months",
 * "Never expires", "10,000 bars". `null` always means unlimited.
 */
export function fmtValue(key: string, f: CatalogFeature, v: FeatureValue): string {
  if (f.kind === "flag") return v ? "Included" : "Not included";
  if (key === "alerts.expiry_days") return v === null ? "Never expires" : months(Number(v));
  if (key === "chart.history_bars") return v === null ? "Full history" : `${num(Number(v))} bars`;
  if (v === null) return "Unlimited";
  if (f.kind === "quota") return `${num(Number(v))} / ${f.window ?? "month"}`;
  return num(Number(v));
}
