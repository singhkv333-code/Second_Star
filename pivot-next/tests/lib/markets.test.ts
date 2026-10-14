import { describe, expect, it } from "vitest";
import {
  LENSES,
  lensContext,
  moveTint,
  newsTagger,
  rangePosition,
  rebase,
  scoreBattle,
  squarify,
  type MarketSector,
  type MarketStock,
} from "@/lib/markets";

function stock(p: Partial<MarketStock> & { symbol: string }): MarketStock {
  return {
    name: p.symbol, sector: "it", mcap_cr: 1000, price: 100, change: 0, ret_1w: null, ret_1m: null,
    ret_3m: null, ret_1y: null, pe: null, roe: null, rsi: null, vol_ratio: null, turnover_cr: null,
    dist_high: null, dist_low: null, streak: null, bb_width: null, adx: null, sma50: null, sma200: null,
    breakout: null, vp_pos: null, vp_shift: null, logo: null, ...p,
  };
}

const lens = (id: string) => LENSES.find((l) => l.id === id)!;
const ctx = lensContext([{ key: "it", pe_median: 30 } as MarketSector]);

describe("lenses", () => {
  const xs = [
    stock({ symbol: "UP", change: 4 }),
    stock({ symbol: "UP2", change: 1 }),
    stock({ symbol: "DOWN", change: -3 }),
    stock({ symbol: "FLAT", change: 0 }),
  ];

  it("ranks gainers largest first and losers smallest first", () => {
    expect(lens("gainers").pick(xs, ctx).map((s) => s.symbol)).toEqual(["UP", "UP2"]);
    expect(lens("losers").pick(xs, ctx).map((s) => s.symbol)).toEqual(["DOWN"]);
  });

  it("cheaper than sector needs a low P/E against the sector median and real returns on equity", () => {
    const v = [
      stock({ symbol: "CHEAP", pe: 12, roe: 20 }),
      stock({ symbol: "CHEAPWEAK", pe: 12, roe: 8 }),
      stock({ symbol: "FAIR", pe: 28, roe: 25 }),
      stock({ symbol: "CHEAPER", pe: 9, roe: 16 }),
    ];
    expect(lens("cheap").pick(v, ctx).map((s) => s.symbol)).toEqual(["CHEAPER", "CHEAP"]);
    expect(lens("cheap").column.value(v[0]!, ctx)).toBe("12.0 · 30.0");
  });

  it("streaks rank by length in either direction", () => {
    const v = [stock({ symbol: "A", streak: 5 }), stock({ symbol: "B", streak: -7 }), stock({ symbol: "C", streak: 2 })];
    expect(lens("streaks").pick(v, ctx).map((s) => s.symbol)).toEqual(["B", "A"]);
  });
});

describe("rangePosition", () => {
  it("places the price between the 52-week low and high", () => {
    // low = 100 / 1.25 = 80, high = 100 / 0.8 = 125 → (100-80)/(45)
    expect(rangePosition({ price: 100, dist_low: 25, dist_high: -20 })).toBeCloseTo(20 / 45, 5);
    expect(rangePosition({ price: 100, dist_low: null, dist_high: -20 })).toBeNull();
  });
});

describe("battles", () => {
  it("scores each round, lower P/E winning valuation", () => {
    const a = stock({ symbol: "A", change: 1, ret_1m: 5, ret_1y: 10, pe: 20, roe: 15, sma200: 3 });
    const b = stock({ symbol: "B", change: 2, ret_1m: 1, ret_1y: null, pe: 30, roe: 25, sma200: -2 });
    const s = scoreBattle(a, b);
    expect(s.rounds.map((r) => r.winner)).toEqual(["b", "a", null, "a", "b", "a"]);
    expect([s.a, s.b]).toEqual([3, 2]);
  });

  it("rebases two series on their common days", () => {
    const r = rebase([{ t: 1, c: 50 }, { t: 2, c: 55 }, { t: 3, c: 60 }], [{ t: 2, c: 10 }, { t: 3, c: 9 }]);
    expect(r).toEqual([{ t: 2, a: 100, b: 100 }, { t: 3, a: (60 / 55) * 100, b: 90 }]);
  });
});

describe("newsTagger", () => {
  const tag = newsTagger([
    stock({ symbol: "SBIN", name: "State Bank of India" }),
    stock({ symbol: "RELIANCE", name: "Reliance Industries" }),
    stock({ symbol: "TITAN", name: "Titan Company" }),
    stock({ symbol: "INDIANB", name: "Indian Bank" }),
    stock({ symbol: "IOC", name: "Indian Oil Corporation" }),
  ]);

  it("finds companies by alias, by name and by a distinctive first word", () => {
    expect(tag("SBI raises deposit rates")).toEqual(["SBIN"]);
    expect(tag("Reliance Jio IPO opens next week")).toEqual(["RELIANCE"]);
    expect(tag("Titan shares rise after results")).toEqual(["TITAN"]);
  });

  it("does not tag generic words", () => {
    expect(tag("Indian markets slip as titan of tech falls")).toEqual([]);
  });
});

describe("squarify", () => {
  it("fills the rectangle without overlap", () => {
    const items = [6, 6, 4, 3, 2, 2, 1];
    const out = squarify(items, (v) => v, { x: 0, y: 0, w: 600, h: 400 });
    const area = out.reduce((s, r) => s + r.w * r.h, 0);
    expect(area).toBeCloseTo(600 * 400, 3);
    for (let i = 0; i < out.length; i++) {
      for (let j = i + 1; j < out.length; j++) {
        const a = out[i]!;
        const b = out[j]!;
        const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
        const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        expect(ox > 1e-6 && oy > 1e-6).toBe(false);
      }
    }
  });
});

describe("moveTint", () => {
  it("is neutral without a move and capped beyond it", () => {
    expect(moveTint(null)).toBe("var(--bg-secondary)");
    expect(moveTint(9)).toBe(moveTint(3));
    expect(moveTint(-1)).toContain("--color-loss");
  });
});
