/**
 * basket.ts — the model behind an *editable* basket.
 *
 * A curated basket ships with pre-decided quantities: either an authored share
 * count per name (holdings[].default_shares) or, failing that, a weight
 * (holdings[].weight_pct) that seeds a share count from the ticket amount.
 * From there SHARES ARE THE TRUTH: the reader sets how many of each name they
 * want, and weight is a readout of what that holding costs as a share of the
 * whole basket.
 *
 * An authored share count is absolute — the ticket amount does not size that
 * name, because the curator already decided the quantity. A basket authored
 * this way costs what those shares cost, whatever the reader types above.
 *
 * Consequences of shares-as-truth, all intentional:
 *   · Changing one name's quantity NEVER moves another name's quantity. Only
 *     the percentages move, because the denominator moved.
 *   · The basket's total cost is an OUTPUT, not the ticket amount. The amount
 *     seeds the opening quantities; after that the basket costs what it costs.
 *   · Removing a name doesn't redistribute anything — the others keep their
 *     quantities and simply become a larger share of a smaller basket.
 *
 * DESIGN LAW (inherited from ExpressionHero/StrategyCleanCard): never fabricate.
 * Every per-name return is the expression's real `holdings[].return_pct` and
 * every price is a real live quote. The headline stays anchored to the
 * backtested `strategy_total_pct` — an edit moves it by the *difference* the
 * reader's weighting makes, so an untouched basket still shows exactly the
 * number the backtest produced.
 */

import type { ExpressionDetail } from "@/lib/types";

export type BasketLeg = {
  /** Stable identity across edits — symbol when present, else the name. */
  key: string;
  name: string;
  symbol: string | null;
  /** The name's own backtested return, or null when the pack didn't carry one. */
  returnPct: number | null;
  /** The curated weight this basket was authored with, 0–100. */
  weightPct: number;
  /** The curated share count, when the basket was authored with quantities. */
  defaultShares: number | null;
};

/** A reader's changes to one basket: names dropped, quantities set. */
export type BasketEdit = {
  removed: string[];
  /** key → share count, only for names the reader actually set. */
  shares: Record<string, number>;
};

/** key → last known live price. */
export type PriceMap = Record<string, number>;

export const EMPTY_EDIT: BasketEdit = { removed: [], shares: {} };

const round1 = (v: number): number => Math.round(v * 10) / 10;

/** Scale a set of weights so they sum to exactly 100%. */
function normaliseWeights(legs: BasketLeg[]): BasketLeg[] {
  if (legs.length === 0) return [];
  const sum = legs.reduce((a, l) => a + l.weightPct, 0);
  return sum > 0
    ? legs.map((l) => ({ ...l, weightPct: (l.weightPct / sum) * 100 }))
    : legs.map((l) => ({ ...l, weightPct: 100 / legs.length }));
}

/**
 * The curated, pre-decided basket as authored — long legs that carry a real
 * weight. Hedge legs (a short index with no weight) and option legs are not
 * basket constituents and are excluded.
 */
export function recommendedLegs(e: ExpressionDetail): BasketLeg[] {
  const raw = (e.holdings ?? []).filter(
    (h) =>
      h.position !== "short" &&
      h.symbol != null &&
      h.weight_pct != null &&
      Number.isFinite(h.weight_pct) &&
      // A weight of exactly 0 is the optimiser saying "don't hold this" (the
      // renewable basket zeroes Tata Power and BHEL). Carrying it would render
      // a row of 0 shares / ₹0 / 0.0%, which reads as a bug rather than as the
      // deliberate exclusion it is.
      (h.weight_pct as number) > 0,
  );
  if (raw.length === 0) return [];
  return normaliseWeights(
    raw.map((h) => ({
      key: h.symbol ?? h.name,
      name: h.name,
      symbol: h.symbol ?? null,
      returnPct: Number.isFinite(h.return_pct as number)
        ? (h.return_pct as number)
        : null,
      weightPct: h.weight_pct as number,
      defaultShares:
        Number.isFinite(h.default_shares as number) &&
        (h.default_shares as number) >= 0
          ? Math.floor(h.default_shares as number)
          : null,
    })),
  );
}

/**
 * True when this expression is an editable basket of weighted names.
 *
 * One name is enough: a single-ETF option (own the gold ETF) is a legitimate
 * basket of one, and gating at two would drop it from the page entirely. What
 * this still excludes is the case that has nothing to weigh — an option
 * structure or a bare hedge, which carry no weighted long legs at all.
 */
export function isEditableBasket(e: ExpressionDetail): boolean {
  return recommendedLegs(e).length >= 1;
}

/** The names still in the basket, at their curated weights. */
export function activeLegs(e: ExpressionDetail, edit?: BasketEdit): BasketLeg[] {
  const base = recommendedLegs(e);
  if (!edit || edit.removed.length === 0) return base;
  const removed = new Set(edit.removed);
  return normaliseWeights(base.filter((l) => !removed.has(l.key)));
}

export type ResolvedLeg = BasketLeg & {
  /** Live price, or null when no quote is available. */
  price: number | null;
  /** Quantity held — the reader's own, else seeded from the curated weight. */
  shares: number | null;
  /** What that quantity costs, or null without a price. */
  cost: number | null;
  /** Share of the basket's actual cost. Falls back to the curated weight. */
  livePct: number;
  /** True when the reader set this quantity themselves. */
  explicit: boolean;
};

/**
 * The basket as it actually stands: quantities resolved, cost computed, and
 * weights derived from those costs.
 *
 * `amount` only seeds the opening quantities of names the reader hasn't touched
 * — it never rescales one they have.
 */
export function resolveBasket(
  e: ExpressionDetail,
  edit: BasketEdit | undefined,
  prices: PriceMap | undefined,
  amount: number,
): { legs: ResolvedLeg[]; totalCost: number | null; fullyPriced: boolean } {
  // Seed from the ORIGINAL curated weight, not a weight renormalised across the
  // survivors: renormalising would make removing one name silently re-seed
  // every other name's quantity upward. Composition changes must never move a
  // quantity the reader didn't touch.
  const removed = new Set(edit?.removed ?? []);
  const active = recommendedLegs(e).filter((l) => !removed.has(l.key));
  const seedAmount = Number.isFinite(amount) && amount > 0 ? amount : 0;

  const legs: ResolvedLeg[] = active.map((l) => {
    const raw = prices?.[l.key];
    const price = Number.isFinite(raw) && (raw as number) > 0 ? (raw as number) : null;
    const set = edit?.shares[l.key];
    const explicit = Number.isFinite(set) && (set as number) >= 0;
    // Whole shares only — you cannot buy a fraction of an Indian equity.
    // An authored quantity wins over the amount-derived seed: the curator
    // picked that number, so it stands whatever the reader's ticket says (and
    // it holds even with no quote, where the seed arithmetic has no price to
    // divide by).
    const shares = explicit
      ? (set as number)
      : l.defaultShares != null
        ? l.defaultShares
        : price != null
          ? Math.floor((seedAmount * l.weightPct) / 100 / price)
          : null;
    const cost = shares != null && price != null ? shares * price : null;
    return { ...l, price, shares, cost, livePct: l.weightPct, explicit };
  });

  const costs = legs.map((l) => l.cost).filter((c): c is number => c != null);
  const fullyPriced = costs.length === legs.length && legs.length > 0;
  const totalCost = costs.length > 0 ? costs.reduce((a, c) => a + c, 0) : null;

  // Weight is a readout of cost share. Without a full set of prices (or with a
  // zero-cost basket) there is nothing real to divide by, so the curated
  // weights stand rather than a half-computed mix of the two.
  if (fullyPriced && totalCost != null && totalCost > 0) {
    legs.forEach((l) => {
      l.livePct = round1(((l.cost as number) / totalCost) * 100);
    });
  } else {
    // Fallback: the curated weights, renormalised across whoever is left so the
    // column still reads as percentages of this basket rather than of the
    // original one.
    const curated = legs.reduce((a, l) => a + l.weightPct, 0);
    legs.forEach((l) => {
      l.livePct =
        curated > 0 ? round1((l.weightPct / curated) * 100) : round1(100 / legs.length);
    });
  }

  return { legs, totalCost, fullyPriced };
}

/** Capital-weighted return across resolved legs, using their live weights. */
export function resolvedReturnPct(legs: ResolvedLeg[]): number | null {
  const usable = legs.filter((l) => l.returnPct != null);
  if (usable.length === 0) return null;
  const w = usable.reduce((a, l) => a + l.livePct, 0);
  if (w <= 0) return null;
  return usable.reduce((a, l) => a + l.livePct * (l.returnPct as number), 0) / w;
}

/** Capital-weighted return at the curated weights — the backtest's own mix. */
function recommendedReturnPct(e: ExpressionDetail): number | null {
  const legs = recommendedLegs(e);
  const usable = legs.filter((l) => l.returnPct != null);
  if (usable.length === 0) return null;
  const w = usable.reduce((a, l) => a + l.weightPct, 0);
  if (w <= 0) return null;
  return usable.reduce((a, l) => a + l.weightPct * (l.returnPct as number), 0) / w;
}

/**
 * The basket's headline return as the reader has it.
 *
 * Anchored to the real backtested `strategy_total_pct` and shifted only by what
 * their weighting changes, so an untouched basket reports the exact backtested
 * figure and an edited one moves honestly from it.
 */
export function editedTotalPct(
  e: ExpressionDetail,
  edit: BasketEdit | undefined,
  prices: PriceMap | undefined,
  amount: number,
): number | null {
  const { legs } = resolveBasket(e, edit, prices, amount);
  if (legs.length === 0) return null;
  const current = resolvedReturnPct(legs);
  if (current == null) return e.strategy_total_pct ?? null;
  const base = recommendedReturnPct(e);
  if (e.strategy_total_pct == null || base == null) return current;
  return e.strategy_total_pct + (current - base);
}

/** True when the reader has changed something. */
export function isEdited(edit?: BasketEdit): boolean {
  if (!edit) return false;
  return edit.removed.length > 0 || Object.keys(edit.shares).length > 0;
}

/**
 * Set one name's quantity. Nothing else moves — no other quantity is touched
 * and the ticket amount is left alone. Only the percentages shift, because the
 * basket's total cost has changed.
 */
export function setLegShares(
  edit: BasketEdit | undefined,
  key: string,
  shares: number,
): BasketEdit {
  if (!Number.isFinite(shares) || shares < 0) return edit ?? EMPTY_EDIT;
  return {
    removed: edit?.removed ?? [],
    shares: { ...(edit?.shares ?? {}), [key]: Math.floor(shares) },
  };
}

/**
 * Drop a name. Deliberately does NOT redistribute: the remaining names keep the
 * exact quantities the reader chose and simply become a larger share of a
 * smaller basket.
 */
export function removeLeg(edit: BasketEdit | undefined, key: string): BasketEdit {
  const removed = [...(edit?.removed ?? [])];
  if (!removed.includes(key)) removed.push(key);
  return { removed, shares: { ...(edit?.shares ?? {}) } };
}

/** Put a removed name back at whatever quantity it had. */
export function restoreLeg(edit: BasketEdit | undefined, key: string): BasketEdit {
  return {
    removed: (edit?.removed ?? []).filter((k) => k !== key),
    shares: { ...(edit?.shares ?? {}) },
  };
}
