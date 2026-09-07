/**
 * Shared formatting for the Paper Trading dashboard — en-IN ₹, P&L signs,
 * compact crore/lakh, and the profit/loss color tokens. Use these so every
 * paper component renders money + P&L identically.
 */

const INR2 = new Intl.NumberFormat("en-IN", {
  maximumFractionDigits: 2,
  minimumFractionDigits: 2,
});
const INR0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

const DASH = "—";
const MINUS = "−"; // U+2212, matches the rest of the app

function bad(n: number | null | undefined): boolean {
  return n === null || n === undefined || Number.isNaN(n);
}

/** "₹1,23,456.78" (dp=2) or "₹1,23,457" (dp=0). */
export function inr(n: number | null | undefined, dp: 0 | 2 = 2): string {
  if (bad(n)) return DASH;
  return "₹" + (dp === 0 ? INR0 : INR2).format(n as number);
}

/** Compact "₹1.23Cr" / "₹4.56L" / "₹7.8K" for axis labels + tight cards. */
export function inrCompact(n: number | null | undefined): string {
  if (bad(n)) return DASH;
  const v = n as number;
  const a = Math.abs(v);
  const sign = v < 0 ? MINUS : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)}Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)}L`;
  if (a >= 1e3) return `${sign}₹${(a / 1e3).toFixed(1)}K`;
  return `${sign}₹${a.toFixed(0)}`;
}

/** Signed "+₹1,234.00" / "−₹1,234.00" for P&L. */
export function signedInr(n: number | null | undefined): string {
  if (bad(n)) return DASH;
  const v = n as number;
  return `${v >= 0 ? "+" : MINUS}₹${INR2.format(Math.abs(v))}`;
}

/** Signed "+12.34%" / "−1.20%". */
export function pct(n: number | null | undefined): string {
  if (bad(n)) return DASH;
  const v = n as number;
  return `${v >= 0 ? "+" : MINUS}${Math.abs(v).toFixed(2)}%`;
}

/** Plain integer count with grouping ("1,234"). */
export function qty(n: number | null | undefined): string {
  if (bad(n)) return DASH;
  return INR0.format(n as number);
}

/** The CSS color var for a P&L value: profit / loss / neutral. */
export function pnlColor(n: number | null | undefined): string {
  if (bad(n) || n === 0) return "var(--text-secondary)";
  return (n as number) > 0 ? "var(--color-profit)" : "var(--color-loss)";
}

/** "2h ago" / "3d ago" / "just now" from an ISO string (null -> "—"). */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return DASH;
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** "30 May 2026" from an ISO date string (null -> "—"). */
export function dateShort(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return DASH;
  return new Date(t).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}
