/** Number formats for the public pages: Indian grouping, rupees, crores. */

const IN = "en-IN";

export function inr(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `₹${v.toLocaleString(IN, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** Crores as a reader says them: ₹16,39,061 Cr, or ₹16.39 lakh Cr above
 *  one lakh crore when `compact`. */
export function crore(v: number | null | undefined, compact = false): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (compact && Math.abs(v) >= 100_000) {
    return `₹${(v / 100_000).toLocaleString(IN, { maximumFractionDigits: 2 })} lakh Cr`;
  }
  return `₹${Math.round(v).toLocaleString(IN)} Cr`;
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString(IN, { maximumFractionDigits: digits });
}

export function pct(v: number | null | undefined, digits = 1, signed = false): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const s = Math.abs(v).toLocaleString(IN, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (!signed) return `${v < 0 ? "−" : ""}${s}%`;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${s}%`;
}

export function times(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toLocaleString(IN, { maximumFractionDigits: digits })}×`;
}

/** "2026-10-16" → "16 Oct 2026". Dates are calendar days, so no time zone. */
export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${Number(m[3])} ${months[Number(m[2]) - 1]} ${m[1]}`;
}

/** "2026-06-30" → "Q1 FY27" — the Indian fiscal quarter a reader searches for. */
export function fiscalQuarter(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})/.exec(iso || "");
  if (!m) return iso || "—";
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const q = mo <= 3 ? 4 : mo <= 6 ? 1 : mo <= 9 ? 2 : 3;
  const fy = mo <= 3 ? y : y + 1;
  return `Q${q} FY${String(fy).slice(2)}`;
}

/** "2026-03-31" → "FY26". */
export function fiscalYear(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})/.exec(iso || "");
  if (!m) return iso || "—";
  const y = Number(m[1]);
  return `FY${String(Number(m[2]) <= 3 ? y : y + 1).slice(2)}`;
}

export function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
}

export function capBand(rank: number | null | undefined): string | null {
  // SEBI's categorisation: top 100 large, 101-250 mid, the rest small.
  if (!rank) return null;
  return rank <= 100 ? "large-cap" : rank <= 250 ? "mid-cap" : "small-cap";
}
