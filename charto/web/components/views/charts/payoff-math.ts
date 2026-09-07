/**
 * payoff-math.ts — pure, dependency-free expiry-payoff builder for an option
 * structure. NO premium is modeled (the backend does not persist net
 * debit/credit), so the curve is the STRUCTURAL intrinsic-value shape at
 * expiry, anchored to a 0 reference. Callers must label it "structure only —
 * premium not priced".
 *
 * Payoff at expiry for one leg at underlying price s:
 *   call intrinsic = max(s − K, 0)
 *   put  intrinsic = max(K − s, 0)
 * signed by side (buy = +1, sell = −1), scaled by qtyLots.
 *
 * Role parsing is tolerant: "long_call"/"short_put"/"call"/"ce"/"pe" etc.
 */

export type Leg = {
  role: string;
  side: "buy" | "sell";
  strike: number;
  qtyLots?: number;
};

export type PayoffPoint = { s: number; pnl: number };

export type PayoffResult = {
  points: PayoffPoint[];
  breakevens: number[];
  maxProfit: number | null;
  maxLoss: number | null;
  /** Whether the profit (up) / loss (down) side is bounded within the span. */
  capped: { up: boolean; down: boolean };
};

/** Infer option type (call/put) from a leg's role string. */
function legIsPut(role: string): boolean {
  const r = role.toLowerCase();
  if (r.includes("put") || r.includes("pe")) return true;
  return false;
}

function legSign(side: "buy" | "sell"): number {
  return side === "sell" ? -1 : 1;
}

function legIntrinsic(leg: Leg, s: number): number {
  const intrinsic = legIsPut(leg.role)
    ? Math.max(leg.strike - s, 0)
    : Math.max(s - leg.strike, 0);
  const qty = leg.qtyLots && leg.qtyLots > 0 ? leg.qtyLots : 1;
  return legSign(leg.side) * intrinsic * qty;
}

/**
 * Build the expiry payoff curve over a price span centered on the strikes.
 *
 * @param legs   the option legs (need at least one with a finite strike).
 * @param opts.span   half-width fraction of the strike range to pad on each
 *                    side (default 0.4 of the strike spread, min one strike).
 * @param opts.steps  number of sample points (default 81).
 */
export function buildExpiryPayoff(
  legs: Leg[],
  opts?: { span?: number; steps?: number },
): PayoffResult {
  const valid = legs.filter(
    (l) => Number.isFinite(l.strike) && l.strike > 0,
  );
  if (valid.length === 0) {
    return {
      points: [],
      breakevens: [],
      maxProfit: null,
      maxLoss: null,
      capped: { up: false, down: false },
    };
  }

  const strikes = valid.map((l) => l.strike);
  const kMin = Math.min(...strikes);
  const kMax = Math.max(...strikes);
  const spread = kMax - kMin || kMin * 0.1 || 1;
  const spanFrac = opts?.span ?? 0.4;
  const pad = Math.max(spread * spanFrac, kMin * 0.08);
  const lo = Math.max(0, kMin - pad);
  const hi = kMax + pad;
  const steps = Math.max(11, opts?.steps ?? 81);

  // Sample points, but always include the kink points (strikes) so the
  // piecewise-linear curve renders exactly.
  const xs = new Set<number>();
  for (let i = 0; i < steps; i++) {
    xs.add(lo + ((hi - lo) * i) / (steps - 1));
  }
  for (const k of strikes) xs.add(k);
  const sorted = [...xs].sort((a, b) => a - b);

  const points: PayoffPoint[] = sorted.map((s) => {
    let pnl = 0;
    for (const leg of valid) pnl += legIntrinsic(leg, s);
    return { s, pnl };
  });

  const pnls = points.map((p) => p.pnl);
  const maxProfit = Math.max(...pnls);
  const maxLoss = Math.min(...pnls);

  // Capped if the extreme isn't reached at the span boundary (i.e. the curve
  // flattens before the edge). Approximate: compare boundary slope to 0.
  const n = points.length;
  const upSlopeAtHi = points[n - 1]!.pnl - points[n - 2]!.pnl;
  const downSlopeAtLo = points[1]!.pnl - points[0]!.pnl;
  const capped = {
    up: Math.abs(upSlopeAtHi) < 1e-9,
    down: Math.abs(downSlopeAtLo) < 1e-9,
  };

  // Breakevens: sign changes of pnl between adjacent points → linear root.
  const breakevens: number[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!;
    const b = points[i]!;
    if ((a.pnl <= 0 && b.pnl > 0) || (a.pnl >= 0 && b.pnl < 0)) {
      const t = a.pnl / (a.pnl - b.pnl || 1);
      breakevens.push(a.s + t * (b.s - a.s));
    }
  }

  return { points, breakevens, maxProfit, maxLoss, capped };
}
