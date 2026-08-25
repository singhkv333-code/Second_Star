/**
 * film-script — everything the product film SAYS, with no GSAP and no DOM.
 *
 * Scene definitions, the demo price series, and the chart annotations each
 * prompt produces. Kept free of animation so the choreography in
 * `film-demo.tsx` stays the only place timing lives, and so this file can be
 * read as "what the demo claims" without reading a timeline.
 *
 * Everything here is illustrative product copy for the landing page — the same
 * status as the rest of the marketing surface, not output from the real agent.
 */

/**
 * Design-space sizes. The frame renders at one of these and scales with a
 * transform to fit its container.
 *
 * Two of them, not one: scaling the 1200px composition down to a 390px phone
 * puts the panel's 11px type under 4 CSS pixels, which is a smaller version of
 * the product rather than a legible one. Below the breakpoint the film swaps to
 * a deliberately composed narrow frame — chart band over agent panel — which
 * only has to scale ~0.9x instead of ~0.33x.
 */
export const FILM_W = 1200;
export const FILM_H = 720;
export const FILM_W_NARROW = 440;
export const FILM_H_NARROW = 720;
/** Container width at or below which the narrow composition takes over. */
export const NARROW_AT = 760;
export const TOPBAR_H = 52;
export const SIDEBAR_W = 372;

// ── the demo series ────────────────────────────────────────────────────────
// A 60-bar path shaped so every prompt has something real to point at: an
// uptrend, an ascending triangle under a flat ceiling, a failed breakout, and
// the decline the fourth prompt asks about. Closes are fixed (not generated at
// render) so server and client agree — a seeded random would still have to be
// identical across the boundary, and a literal is easier to reason about.
const CLOSES = [
  2362.0, 2376.2, 2370.3, 2369.6, 2387.6, 2395.7, 2387.5, 2393.5, 2412.1,
  2413.2, 2406.3, 2418.9, 2434.5, 2429.8, 2427.4, 2444.6, 2454.6, 2446.7,
  2446.0, 2485.9, 2472.1, 2427.8, 2415.4, 2451.0, 2485.9, 2475.5, 2438.4,
  2427.1, 2456.3, 2485.9, 2478.5, 2448.6, 2439.1, 2462.1, 2486.1, 2481.2,
  2458.5, 2451.1, 2468.3, 2486.4, 2483.5, 2468.0, 2463.2, 2474.9, 2486.8,
  2489.6, 2491.6, 2490.6, 2498.4, 2507.0, 2499.7, 2492.7, 2472.1, 2461.1,
  2461.2, 2448.8, 2428.5, 2422.8, 2421.3, 2404.1,
];

export type Bar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

/** Deterministic wobble — same value every call, so SSR and hydration match. */
function wob(i: number, salt: number): number {
  return Math.sin(i * 1.937 + salt * 2.611) * 0.5 + Math.sin(i * 0.613 + salt) * 0.5;
}

export const BARS: Bar[] = CLOSES.map((close, i) => {
  const prev = i === 0 ? close - 6 : CLOSES[i - 1]!;
  const open = +(prev + wob(i, 1) * 3.2).toFixed(2);
  const span = 6 + Math.abs(wob(i, 2)) * 7;
  const high = +(Math.max(open, close) + span * 0.62).toFixed(2);
  const low = +(Math.min(open, close) - span * 0.55).toFixed(2);
  // Weekdays only, so the axis reads like a real daily chart.
  const day = new Date(Date.UTC(2026, 4, 4));
  day.setUTCDate(day.getUTCDate() + i + Math.floor(i / 5) * 2);
  return {
    time: Math.floor(day.getTime() / 1000),
    open,
    high,
    low,
    close,
    volume: 1_650_000 + Math.abs(wob(i, 3)) * 3_900_000 + (i > 49 ? 2_400_000 : 0),
  };
});

/**
 * Indian digit grouping, written out rather than delegated to
 * `toLocaleString("en-IN")`. That call is rendered into SSR'd markup, and
 * Node's ICU and the browser's do not always agree on the output — which
 * surfaces as a hydration mismatch rather than a wrong number.
 */
export function formatINR(n: number): string {
  const [int, dec = "00"] = n.toFixed(2).split(".");
  const last3 = int!.slice(-3);
  const rest = int!.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `${grouped}.${dec}`;
}

export const LAST = BARS[BARS.length - 1]!;
export const PREV = BARS[BARS.length - 2]!;
export const CHANGE_PCT = ((LAST.close - PREV.close) / PREV.close) * 100;

// ── annotations ────────────────────────────────────────────────────────────
// Declared in DATA space (bar index + price). `film-chart` converts these to
// pixels through the chart's own coordinate API, so a band sits on the price
// it names at any size — never on a hardcoded pixel.

export type Annotation =
  | {
      kind: "zone";
      id: string;
      from: number;
      to: number;
      i0: number;
      i1: number;
      tone: "resistance" | "support";
      label: string;
    }
  | {
      kind: "line";
      id: string;
      points: { i: number; price: number }[];
      tone: "ceiling" | "trend";
      label?: string;
      dashed?: boolean;
    }
  | {
      kind: "marker";
      id: string;
      i: number;
      label: string;
      detail: string;
    };

export type Scene = {
  id: string;
  prompt: string;
  /** Milliseconds of "thinking" — deliberately far shorter than a real turn. */
  thinkMs: number;
  answer: string;
  /** Chips under the answer. Empty renders no row. */
  tags: string[];
  annotations: Annotation[];
};

export const SCENES: Scene[] = [
  {
    id: "levels",
    prompt: "Mark the key support and resistance levels",
    thinkMs: 700,
    answer:
      "Resistance is ₹2,486–2,496 — price has been turned away there four times since May. Support sits at ₹2,404–2,428, where buyers stepped in on every pullback. A daily close outside either band is the decision point.",
    tags: ["Price structure", "Daily"],
    annotations: [
      {
        kind: "zone",
        id: "res",
        from: 2486,
        to: 2496,
        i0: 17,
        i1: 59,
        tone: "resistance",
        label: "RESISTANCE · ₹2,486–2,496",
      },
      {
        kind: "zone",
        id: "sup",
        from: 2404,
        to: 2428,
        i0: 17,
        i1: 59,
        tone: "support",
        label: "SUPPORT · ₹2,404–2,428",
      },
    ],
  },
  {
    id: "earnings",
    prompt: "Mark the points that coincide with the earnings release",
    thinkMs: 820,
    answer:
      "Three prints are in view. Q3 gapped up on a margin beat, Q4 was sold into despite an in-line quarter, and Q1 is the marker on the right — price is down 4.1% in the sessions since it landed.",
    tags: ["Events", "Fundamentals"],
    annotations: [
      { kind: "marker", id: "e1", i: 11, label: "Q3", detail: "Margin beat · +2.1%" },
      { kind: "marker", id: "e2", i: 34, label: "Q4", detail: "In line · sold into" },
      { kind: "marker", id: "e3", i: 51, label: "Q1", detail: "Margin miss · −4.1% since" },
    ],
  },
  {
    id: "patterns",
    prompt: "Mark all the recent patterns",
    thinkMs: 760,
    answer:
      "One clean structure: an ascending triangle from late May — rising lows pressing into a flat ₹2,489 ceiling. The breakout on bar 49 failed to hold, which is why the lower trendline now matters more than the top.",
    tags: ["Pattern", "Ascending triangle"],
    annotations: [
      {
        kind: "line",
        id: "ceil",
        points: [
          { i: 18, price: 2489 },
          { i: 49, price: 2489 },
        ],
        tone: "ceiling",
        label: "FLAT CEILING · ₹2,489",
      },
      {
        kind: "line",
        id: "trend",
        points: [
          { i: 22, price: 2404 },
          { i: 47, price: 2478 },
        ],
        tone: "trend",
        label: "RISING LOWS",
        dashed: true,
      },
    ],
  },
  {
    id: "why",
    prompt: "Why is the price falling",
    thinkMs: 880,
    answer:
      "Nothing to draw here — this one is context, not structure. Three things line up: the Q1 margin miss, heavy delivery-based selling in the two sessions after it, and the broader energy index down 3.8% over the same stretch. ₹2,404 is the level that decides whether this is a pullback or a trend change.",
    tags: ["Context", "No drawing"],
    annotations: [],
  },
];
