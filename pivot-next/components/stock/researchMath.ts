/** Pure calculations over reported statement cells and matching trading dates.
 * Missing values stay missing; no interpolated earnings or zero-filled cash flows.
 */
import type { BalanceSheetRow, OhlcResponse, StatementResponse } from "@/lib/api";

export const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const normalize = (s: string): string => s.toLowerCase().replace(/[^a-z0-9]/g, "");
export function findRow(rows: BalanceSheetRow[], aliases: readonly string[]): BalanceSheetRow | undefined {
  return aliases.map((name) => rows.find((r) => normalize(r.line_item) === normalize(name))).find(Boolean);
}
export const RATIO_METRICS = [
  { key: "pb", label: "P/B", name: "Price to book", aliases: ["Price/BV (X)", "Price To Book Value (X)", "Price To Book Value (%)"] },
  { key: "ev", label: "EV/EBITDA", name: "Enterprise value / EBITDA", aliases: ["EV/EBITDA (X)", "EV/EBITDA"] },
  { key: "sales", label: "EV/Sales", name: "Enterprise value / sales", aliases: ["EV/Net Operating Revenue (X)", "EV/Sales (X)"] },
  { key: "pe", label: "P/E", name: "Price to earnings", aliases: ["P/E (X)", "Price/Earnings (X)", "Price To Earnings (X)", "P/E Ratio"] },
] as const;
// MC uses the legacy (%) label for bank P/B multiples, also normalised by
// company_scores.py. Never invert rounded Earnings Yield to manufacture P/E.
export type RatioKey = typeof RATIO_METRICS[number]["key"];
export function quantile(values: number[], p: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  if (!sorted.length) return NaN;
  const i = (sorted.length - 1) * p, low = Math.floor(i), high = Math.ceil(i);
  return sorted[low]! + (sorted[high]! - sorted[low]!) * (i - low);
}
export function ratioHistory(grid: StatementResponse, key: RatioKey) {
  const metric = RATIO_METRICS.find((m) => m.key === key)!;
  const row = findRow(grid.rows, metric.aliases);
  // API periods are newest-first. Retain nulls so gaps remain visible.
  const points = [...grid.periods].reverse().map((period) => {
    const v = row?.values[period];
    return { period, value: finite(v) && v > 0 ? v : null };
  });
  const valid = points.filter((p): p is { period: string; value: number } => p.value !== null);
  const values = valid.map((p) => p.value);
  return { metric, row, points, valid, latest: valid.at(-1), median: quantile(values, .5), q1: quantile(values, .25), q3: quantile(values, .75), min: Math.min(...values), max: Math.max(...values) };
}

const CASH_ROWS = {
  opening: ["Cash And Cash Equivalents Begin of Year", "Cash And Cash Equivalents Beginning Of Year"],
  operating: ["Net CashFlow From Operating Activities", "Net Cash Flow From Operating Activities", "Net Cash from Operating Activities"],
  investing: ["Net Cash Used In Investing Activities", "Net Cash Flow From Investing Activities", "Net Cash From Investing Activities"],
  financing: ["Net Cash Used From Financing Activities", "Net Cash Flow From Financing Activities", "Net Cash From Financing Activities"],
  fx: ["Foreign Exchange Gains / Losses"],
  adjustments: ["Adjustments On Amalgamation Merger Demerger Others"],
  closing: ["Cash And Cash Equivalents End Of Year"],
} as const;
export type CashKey = keyof typeof CASH_ROWS;
export type CashStep = { label: string; start: number; end: number; value: number; total: boolean };
export function cashBridge(grid: StatementResponse, period: string) {
  const values = Object.fromEntries(Object.entries(CASH_ROWS).map(([key, aliases]) => {
    const v = findRow(grid.rows, aliases)?.values[period];
    return [key, finite(v) ? v : null];
  })) as Record<CashKey, number | null>;
  const steps: CashStep[] = [];
  const { opening, operating, investing, financing, closing } = values;
  if (opening === null || operating === null || investing === null || financing === null || closing === null) return { values, steps, residual: null };
  steps.push({ label: "Opening cash", start: 0, end: opening, value: opening, total: true });
  let running = opening;
  for (const [key, label] of [["operating", "Operating"], ["investing", "Investing"], ["financing", "Financing"], ["fx", "FX impact"], ["adjustments", "Adjustments"]] as const) {
    const value = values[key];
    if (value === null || (value === 0 && (key === "fx" || key === "adjustments"))) continue;
    steps.push({ label, start: running, end: running + value, value, total: false });
    running += value;
  }
  const residual = closing - running;
  // Explicit reconciliation, never falsely attribute the difference to capex,
  // dividends or FX. Tiny rounding is retained in the method note only.
  if (Math.abs(residual) > .05) steps.push({ label: "Unclassified", start: running, end: closing, value: residual, total: false });
  steps.push({ label: "Closing cash", start: 0, end: closing, value: closing, total: true });
  return { values, steps, residual };
}

export type PricePair = { date: string; stock: number; benchmark: number };
export function tradingDate(raw: string): string | null {
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return Number.isFinite(Date.parse(raw)) ? raw : null;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? new Date(parsed + 330 * 60_000).toISOString().slice(0, 10) : null;
}
export function alignPrices(stock: OhlcResponse, benchmark: OhlcResponse): PricePair[] {
  // Enforce basis and provider parity. Mixed providers have different
  // corporate-action conventions even if both call their close unadjusted.
  if (stock.price_basis !== "unadjusted" || benchmark.price_basis !== "unadjusted" || stock.source !== benchmark.source) return [];
  const read = (response: OhlcResponse): Map<string, number> => new Map(response.bars.flatMap((bar) => {
    const date = tradingDate(bar.t);
    return date && finite(bar.c) && bar.c > 0 ? [[date, bar.c] as const] : [];
  }));
  const a = read(stock), b = read(benchmark);
  return [...a.entries()].filter(([date]) => b.has(date)).map(([date, value]) => ({ date, stock: value, benchmark: b.get(date)! })).sort((x, y) => x.date.localeCompare(y.date));
}
export type ReturnWindow = "1M" | "6M" | "1Y" | "3Y" | "5Y";
export const RETURN_WINDOWS: ReturnWindow[] = ["1M", "6M", "1Y", "3Y", "5Y"];
export function windowPrices(points: PricePair[], range: ReturnWindow): PricePair[] {
  const last = points.at(-1);
  if (!last) return [];
  const target = new Date(`${last.date}T00:00:00Z`);
  const months = { "1M": 1, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60 }[range];
  const day = target.getUTCDate();
  target.setUTCDate(1); target.setUTCMonth(target.getUTCMonth() - months);
  const monthEnd = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(day, monthEnd));
  const date = target.toISOString().slice(0, 10);
  let anchor = points.findLastIndex((p) => p.date <= date);
  // A five-year provider lookback can start a weekend after the target.
  if (anchor < 0 && points[0] && Date.parse(points[0].date) - target.getTime() <= 7 * 86400_000) anchor = 0;
  if (anchor < 0 || Math.abs(Date.parse(points[anchor]!.date) - target.getTime()) > 7 * 86400_000) return [];
  const selected = points.slice(anchor);
  return selected.length >= 2 ? selected : [];
}
export function performance(points: PricePair[], key: "stock" | "benchmark") {
  if (points.length < 2) return null;
  const first = points[0]!, last = points.at(-1)!;
  const days = (Date.parse(last.date) - Date.parse(first.date)) / 86400_000;
  if (days <= 0) return null;
  let peak = first[key], drawdown = 0;
  for (const point of points) { peak = Math.max(peak, point[key]); drawdown = Math.min(drawdown, (point[key] / peak - 1) * 100); }
  return { total: (last[key] / first[key] - 1) * 100, cagr: days >= 365 ? (Math.pow(last[key] / first[key], 365.25 / days) - 1) * 100 : null, drawdown };
}
