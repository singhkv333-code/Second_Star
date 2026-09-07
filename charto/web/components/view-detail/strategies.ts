/**
 * strategies.ts — the single source of truth for the View-detail redesign.
 *
 * Everything numeric on the page (the returns graph, the calculator rows, the
 * strategies table) is derived from the STRATEGIES array + the pure functions
 * below. Swap the mock config for real backend data later without touching a
 * single component: the components only ever read `StrategyConfig` + call
 * `projectValue` / `strategyPath`.
 *
 * DESIGN LAW (mirrors components/views/*): en-IN ₹ formatting, no fabricated
 * single "you'll win ₹X" — the calculator shows an expected value AND a range.
 */

export type Risk = "Low" | "Moderate" | "High";

export interface StrategyConfig {
  id: string;
  /** Short display name. */
  name: string;
  /** One-line description for the table. */
  oneLiner: string;
  risk: Risk;
  /** Minimum ₹ needed to deploy this at all (lot-size / premium floor). */
  minAmount: number;
  /** Solid line color — a concrete color or a CSS var() string (both work in
   *  recharts stroke). */
  color: string;
  /** Probability-weighted expected return over the horizon (fraction). */
  expReturn: number;
  /** Pessimistic outcome (fraction). */
  lowReturn: number;
  /** Optimistic outcome (fraction). */
  highReturn: number;
  /** Plain-English "what actually happens" write-up for the explanation panel. */
  explanation: string[];
  /**
   * Shape of the expected-value path over the horizon. `t` is 0→1 (start→
   * year-end); returns a MULTIPLIER of the starting amount. Endpoint always
   * equals 1 + expReturn so the curve lands exactly on the projected value.
   */
  pathAt: (t: number) => number;
}

// ---------------------------------------------------------------------------
// Path shape helpers (pure, dependency-free)
// ---------------------------------------------------------------------------

/** Classic smoothstep ease (0→1). */
function smooth(t: number): number {
  const x = Math.min(1, Math.max(0, t));
  return x * x * (3 - 2 * x);
}

// ---------------------------------------------------------------------------
// The 3 mock strategies (replace with backend-generated data later)
// ---------------------------------------------------------------------------

export const NIFTY_COLOR = "var(--text-tertiary)";

export const STRATEGIES: StrategyConfig[] = [
  {
    id: "index",
    name: "Own the whole market",
    oneLiner: "An index fund that simply moves with the Nifty.",
    risk: "Low",
    minAmount: 500,
    color: "#0ea5e9",
    expReturn: 0.06,
    lowReturn: -0.08,
    highReturn: 0.2,
    explanation: [
      "You buy a Nifty index fund (or NIFTYBEES ETF), which holds all 50 Nifty companies in the same proportions as the index.",
      "Your money rises and falls almost exactly with the Nifty. If the index is up 6% by year-end, you are up roughly 6% — minus a tiny fund fee.",
      "There is no bet on 30,000 specifically. You are simply along for the ride, whatever the market does. This is the calmest, most diversified way to be invested, and the reference every other strategy is measured against.",
    ],
    // Gentle compounding curve landing on +6%.
    pathAt: (t) => Math.pow(1.06, Math.min(1, Math.max(0, t))),
  },
  {
    id: "call_spread",
    name: "Defined-risk call spread",
    oneLiner: "Pays if the Nifty climbs toward 30,000; loss is capped.",
    risk: "Moderate",
    minAmount: 5000,
    color: "#8b5cf6",
    expReturn: 0.15,
    lowReturn: -0.45,
    highReturn: 0.7,
    explanation: [
      "You buy one Nifty call option and sell another at a higher strike. The pair is a “call spread” — a defined-risk bet that the Nifty grinds higher.",
      "The most you can lose is the money you paid to enter (the net premium). The most you can make is also capped, at the gap between the two strikes.",
      "It pays off if the Nifty climbs toward 30,000. If the market drifts sideways or falls, the options lose value and you can lose most of what you put in — but never more than that fixed premium.",
      "Because options trade in lots, there is a real minimum ticket to enter — you can’t buy ₹500 of a spread the way you can an index fund.",
    ],
    // Slight early premium drag, then an S-curve up to +15%.
    pathAt: (t) => {
      const x = Math.min(1, Math.max(0, t));
      const dip = -0.05; // early theta / entry cost drag
      return 1 + dip + (0.15 - dip) * smooth(x);
    },
  },
  {
    id: "lottery",
    name: "Tiny lottery bet",
    oneLiner: "Cheap far-out call — huge payout only if 30,000 is hit.",
    risk: "High",
    minAmount: 1000,
    color: "#f59e0b",
    expReturn: -0.1,
    lowReturn: -1.0,
    highReturn: 8.0,
    explanation: [
      "You buy a single far out-of-the-money Nifty call — an option whose strike is well above today’s level, so it is very cheap.",
      "Almost every time, the Nifty doesn’t reach the strike and the option expires worthless: you lose the entire (small) amount you put in.",
      "But in the rare case the Nifty actually punches through 30,000, the payout is enormous — many times your stake. It is a lottery ticket: tiny cost, mostly zeros, occasionally life-changing.",
      "On average (probability-weighted) this loses a little money, which is why it should only ever be a small slice of capital you can afford to write off.",
    ],
    // Steady theta bleed toward the expected −10% endpoint.
    pathAt: (t) => {
      const x = Math.min(1, Math.max(0, t));
      return 1 + -0.1 * Math.pow(x, 0.65);
    },
  },
];

// ---------------------------------------------------------------------------
// Pure projection math — the ONE place amount → outcome lives.
// ---------------------------------------------------------------------------

export interface Projection {
  /** Expected (probability-weighted) ending value. */
  expected: number;
  /** Pessimistic ending value. */
  low: number;
  /** Optimistic ending value. */
  high: number;
}

/**
 * Turn an input amount + a strategy config into a projected outcome.
 * Deliberately trivial and side-effect free so it is easy to swap for a real
 * model and easy to unit-test.
 */
export function projectValue(amount: number, s: StrategyConfig): Projection {
  return {
    expected: amount * (1 + s.expReturn),
    low: amount * (1 + s.lowReturn),
    high: amount * (1 + s.highReturn),
  };
}

/** True when `amount` is enough to actually deploy this strategy. */
export function meetsMinimum(amount: number, s: StrategyConfig): boolean {
  return amount >= s.minAmount;
}

/**
 * Build the expected-value ₹ path for a strategy across the horizon.
 * Returns `steps + 1` points; `day` is a synthetic "days in market" index.
 */
export function strategyPath(
  amount: number,
  s: StrategyConfig,
  horizonDays: number,
  steps: number,
): { day: number; value: number }[] {
  const out: { day: number; value: number }[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    out.push({ day: Math.round(t * horizonDays), value: amount * s.pathAt(t) });
  }
  return out;
}

/** The flat Nifty baseline path (the reference line) — ~ the index return. */
export function niftyBaselinePath(
  amount: number,
  horizonDays: number,
  steps: number,
): { day: number; value: number }[] {
  const out: { day: number; value: number }[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    // A calm ~5.5% drift — the "own the market" reference the page is about.
    out.push({ day: Math.round(t * horizonDays), value: amount * Math.pow(1.055, t) });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Formatting (en-IN, matches components/views/*)
// ---------------------------------------------------------------------------

export function inr(v: number): string {
  const r = Math.round(v);
  const sign = r < 0 ? "−" : "";
  return `${sign}₹${Math.abs(r).toLocaleString("en-IN")}`;
}

export function inrCompact(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(a >= 1e8 ? 0 : 1)}Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(a >= 1e6 ? 0 : 1)}L`;
  if (a >= 1e3) return `${sign}₹${Math.round(a / 1e3)}k`;
  return `${sign}₹${Math.round(a)}`;
}
